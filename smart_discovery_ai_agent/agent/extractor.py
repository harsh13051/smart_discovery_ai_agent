"""Stage 1 - Understand the request.

Turns free text such as
    "veg birthday dinner for 4 in Wakad under 2500"
into a validated Preferences object.

Two interchangeable extractors exist:
* RuleBasedExtractor - deterministic regex/keyword matching (DEMO mode, and
  the fallback whenever the LLM fails).
* LLMExtractor       - asks the LLM for JSON, then *validates* that JSON with
  the same vocabulary rules, so a model can never inject an unknown area,
  cuisine or negative budget into the pipeline.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Optional

from .llm import LLMClient, LLMError
from .models import DIET_VALUES, FEATURE_VALUES, OCCASION_VALUES, Preferences

log = logging.getLogger("agent.extractor")

MAX_INPUT_CHARS = 500
MAX_BUDGET = 100_000
MAX_PARTY = 30

CUISINE_SYNONYMS: dict[str, list[str]] = {
    "north_indian": ["north indian", "punjabi", "paneer", "tandoor", "naan"],
    "south_indian": ["south indian", "dosa", "idli", "udupi"],
    "maharashtrian": ["maharashtrian", "misal", "kolhapuri", "vada pav", "marathi"],
    "gujarati": ["gujarati", "thali"],
    "chinese": ["chinese", "noodles", "manchurian", "hakka", "szechuan"],
    "pan_asian": ["pan asian", "pan-asian", "asian", "thai"],
    "japanese": ["japanese", "sushi", "ramen"],
    "italian": ["italian", "pizza", "pasta"],
    "continental": ["continental", "european", "steak"],
    "mexican": ["mexican", "taco", "burrito"],
    "mughlai": ["mughlai", "kebab", "kabab"],
    "biryani": ["biryani"],
    "seafood": ["seafood", "fish", "malvani", "konkani", "coastal", "prawn"],
    "cafe": ["cafe", "café", "coffee"],
    "street_food": ["street food", "chaat", "pani puri"],
    "desserts": ["dessert", "desserts", "sweets", "ice cream", "waffle"],
}
OCCASION_SYNONYMS: dict[str, list[str]] = {
    "birthday": ["birthday", "bday", "celebration", "celebrate"],
    "date": ["date", "anniversary", "romantic", "girlfriend", "boyfriend", "wife", "husband"],
    "business": ["business", "client", "meeting", "office lunch", "work lunch", "colleague"],
    "family": ["family", "parents", "kids", "children"],
    "friends": ["friends", "hangout", "gang", "buddies"],
    "quick_bite": ["quick", "snack", "on the go", "lunch break", "quick bite"],
}
FEATURE_SYNONYMS: dict[str, list[str]] = {
    "parking": ["parking", "park the car"],
    "outdoor_seating": ["outdoor", "rooftop", "open air", "terrace", "garden seating"],
    "wifi": ["wifi", "wi-fi", "laptop"],
    "live_music": ["live music", "live band", "music"],
    "wheelchair_access": ["wheelchair", "accessible", "elderly"],
    "serves_alcohol": ["beer", "drinks", "bar", "alcohol", "cocktail", "wine"],
}
WORD_NUMBERS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "twelve": 12,
}
FOOD_WORDS = [
    "restaurant", "eat", "food", "dinner", "lunch", "breakfast", "brunch",
    "meal", "cafe", "café", "dine", "dining", "place", "hungry", "treat",
]


def bare_amount(text: str) -> Optional[float]:
    """A lone number such as "1500" or "2k" - used only when the agent has just
    asked for a budget, so the context tells us what the number means."""
    m = re.fullmatch(r"\s*(?:₹|rs\.?|inr)?\s*(-?\d+(?:\.\d+)?)\s*(k)?\s*", text.lower().replace(",", ""))
    if not m:
        return None
    return float(m.group(1)) * (1000 if m.group(2) else 1)


class InputValidationError(ValueError):
    """Raised when the request itself is unusable (empty, too long, ...)."""


def validate_text(text: Optional[str]) -> str:
    if text is None or not str(text).strip():
        raise InputValidationError("Please describe what you are looking for.")
    text = str(text).strip()
    if len(text) > MAX_INPUT_CHARS:
        raise InputValidationError(
            f"Your request is {len(text)} characters long; please keep it under {MAX_INPUT_CHARS}."
        )
    return text


class AreaVocabulary:
    def __init__(self, areas: list[str], aliases: dict[str, str]):
        self.areas = list(areas)
        self.aliases = {k.lower(): v for k, v in aliases.items()}

    def normalise(self, name: Optional[str]) -> Optional[str]:
        if not name:
            return None
        key = name.strip().lower()
        for area in self.areas:
            if area.lower() == key:
                return area
        return self.aliases.get(key)

    def find_in_text(self, text: str) -> Optional[str]:
        lowered = text.lower()
        names = [(a.lower(), a) for a in self.areas] + list(self.aliases.items())
        # longest names first so "koregaon park" beats "koregaon"
        for needle, canonical in sorted(names, key=lambda x: -len(x[0])):
            if re.search(rf"\b{re.escape(needle)}\b", lowered):
                return canonical
        return None


def _contains(text: str, phrase: str) -> bool:
    return re.search(rf"(?<![a-z]){re.escape(phrase)}(?![a-z])", text) is not None


class RuleBasedExtractor:
    """Deterministic extractor. No AI involved - pattern matching only."""

    name = "rule-based"

    def __init__(self, vocabulary: AreaVocabulary):
        self.vocab = vocabulary

    def extract(self, text: str) -> Preferences:
        text = validate_text(text)
        low = text.lower().replace(",", "")
        prefs = Preferences()

        prefs.area = self.vocab.find_in_text(low)
        if not prefs.area:
            m = re.search(r"\b(?:in|at|near|around)\s+([a-z][a-z ]{2,25}?)(?:\s+(?:under|below|for|with|within|budget)\b|[.!?]|$)", low)
            if m and m.group(1).strip() not in ("me", "my area", "here", "town", "the city"):
                prefs.raw_area = m.group(1).strip().title()

        self._extract_budget(low, prefs)
        prefs.party_size = self._extract_party(low)

        # diet: check the more specific words first; "non-veg" must not match "veg"
        if _contains(low, "jain"):
            prefs.diet = "jain"
        elif _contains(low, "vegan"):
            prefs.diet = "vegan"
        elif re.search(r"(?<!non[- ])(?<!non)\b(veg|vegetarian|pure veg)\b", low):
            prefs.diet = "vegetarian"

        prefs.cuisines = [c for c, words in CUISINE_SYNONYMS.items() if any(_contains(low, w) for w in words)]
        for occasion, words in OCCASION_SYNONYMS.items():
            if any(_contains(low, w) for w in words):
                prefs.occasion = occasion
                break
        prefs.features = [f for f, words in FEATURE_SYNONYMS.items() if any(_contains(low, w) for w in words)]

        m = re.search(r"(\d(?:\.\d)?)\s*(?:\+|\s*stars?|\s*and above|\s*or above|\s*or more)", low) or re.search(
            r"rat(?:ed|ing)\s*(?:of\s*)?(?:above|over|at least|min(?:imum)?|>=?)?\s*(\d(?:\.\d)?)", low
        )
        if m:
            prefs.min_rating = float(m.group(1))
        elif re.search(r"highly rated|top rated|best rated|good reviews", low):
            prefs.min_rating = 4.0

        prefs.is_restaurant_request = bool(
            any(_contains(low, w) for w in FOOD_WORDS)
            or prefs.area or prefs.cuisines or prefs.budget_amount is not None
            or prefs.occasion or prefs.diet != "any"
        )
        return prefs

    @staticmethod
    def _extract_budget(low: str, prefs: Preferences) -> None:
        money = r"(-?\d+(?:\.\d+)?)\s*(k)?"
        patterns = [
            rf"(?:under|below|within|less than|max(?:imum)?|upto|up to|budget(?: of| is)?|around|about)\s*(?:₹|rs\.?|inr)?\s*{money}",
            rf"(?:₹|rs\.?|inr)\s*{money}",
            rf"{money}\s*(?:rupees|rs\b|inr|/-|total|in total|overall|budget|per person|per head|each)",
        ]
        for pattern in patterns:
            for m in re.finditer(pattern, low):
                amount = float(m.group(1)) * (1000 if m.group(2) else 1)
                # Tiny positive numbers ("budget, 4 of us") are not budgets; keep looking.
                # Zero and negative numbers are kept so validation can report them.
                if 0 < amount < 50:
                    continue
                prefs.budget_amount = amount
                window = low[m.start(): m.end() + 20]
                if re.search(r"per (person|head)|each|/person|pp\b", window):
                    prefs.budget_type = "per_person"
                return

    @staticmethod
    def _extract_party(low: str) -> Optional[int]:
        m = re.search(r"(?:for|of|party of|group of|family of|table for)\s+(\d+|" + "|".join(WORD_NUMBERS) + r")\b(?!\s*(?:rs|₹|k\b|rupees))", low)
        if m:
            token = m.group(1)
            value = int(token) if token.isdigit() else WORD_NUMBERS[token]
            # "for 1500" is a budget, not a party of 1500, so ignore large numbers here
            if value <= MAX_PARTY:
                return value
        m = re.search(r"(\d+|" + "|".join(WORD_NUMBERS) + r")\s+(?:people|persons|guests|of us|pax|friends|adults)", low)
        if m:
            token = m.group(1)
            return int(token) if token.isdigit() else WORD_NUMBERS[token]
        m = re.search(r"\bwe(?: are|'re| r)\s+(\d+|" + "|".join(WORD_NUMBERS) + r")\b", low)
        if m:
            token = m.group(1)
            return int(token) if token.isdigit() else WORD_NUMBERS[token]
        if re.search(r"\b(alone|solo|just me|myself)\b", low):
            return 1
        if re.search(r"\b(couple|date|anniversary|my wife|my husband|girlfriend|boyfriend)\b", low):
            return 2
        return None


LLM_SYSTEM_PROMPT = """You extract restaurant-search preferences for a Pune restaurant discovery agent.
Return ONLY a JSON object, no prose, with exactly these keys:
{"is_restaurant_request": bool, "area": string|null, "budget_amount": number|null,
 "budget_type": "total"|"per_person", "party_size": integer|null, "cuisines": [string],
 "diet": "any"|"vegetarian"|"vegan"|"jain", "occasion": string|null,
 "min_rating": number|null, "features": [string]}
Allowed areas: AREAS
Allowed cuisines: CUISINES
Allowed occasions: OCCASIONS
Allowed features: FEATURES
Rules: never guess values the user did not state; use null or [] instead. If the user names
a place that is not an allowed area, put their text in "area" anyway. Budget is in INR."""


class LLMExtractor:
    """LLM-powered extractor. Its output is validated before use."""

    name = "llm"

    def __init__(self, client: LLMClient, vocabulary: AreaVocabulary):
        self.client = client
        self.vocab = vocabulary
        self.system_prompt = (
            LLM_SYSTEM_PROMPT.replace("AREAS", ", ".join(vocabulary.areas))
            .replace("CUISINES", ", ".join(CUISINE_SYNONYMS))
            .replace("OCCASIONS", ", ".join(OCCASION_VALUES))
            .replace("FEATURES", ", ".join(FEATURE_VALUES))
        )

    def extract(self, text: str) -> Preferences:
        text = validate_text(text)
        data = self.client.complete_json(self.system_prompt, text)
        return self.validate_llm_output(data)

    def validate_llm_output(self, data: dict[str, Any]) -> Preferences:
        """Convert untrusted LLM JSON into Preferences, rejecting bad values."""
        prefs = Preferences()
        prefs.is_restaurant_request = bool(data.get("is_restaurant_request", True))

        area_text = data.get("area")
        if isinstance(area_text, str) and area_text.strip():
            prefs.area = self.vocab.normalise(area_text)
            if not prefs.area:
                prefs.raw_area = area_text.strip()

        amount = data.get("budget_amount")
        if amount is not None:
            try:
                prefs.budget_amount = float(amount)
            except (TypeError, ValueError) as exc:
                raise LLMError("budget_amount was not a number") from exc
        if data.get("budget_type") == "per_person":
            prefs.budget_type = "per_person"

        party = data.get("party_size")
        if party is not None:
            try:
                prefs.party_size = int(party)
            except (TypeError, ValueError) as exc:
                raise LLMError("party_size was not an integer") from exc

        prefs.cuisines = [c for c in (data.get("cuisines") or []) if c in CUISINE_SYNONYMS]
        diet = data.get("diet") or "any"
        prefs.diet = diet if diet in DIET_VALUES else "any"
        occasion = data.get("occasion")
        prefs.occasion = occasion if occasion in OCCASION_VALUES else None
        rating = data.get("min_rating")
        if rating is not None:
            try:
                prefs.min_rating = float(rating)
            except (TypeError, ValueError):
                prefs.min_rating = None
        prefs.features = [f for f in (data.get("features") or []) if f in FEATURE_VALUES]
        return prefs


def check_constraints(prefs: Preferences) -> list[str]:
    """Return human-readable problems with the extracted values (empty = OK)."""
    problems = []
    if prefs.budget_amount is not None:
        if prefs.budget_amount <= 0:
            problems.append("The budget must be a positive amount in rupees.")
        elif prefs.budget_amount > MAX_BUDGET:
            problems.append(f"A budget above ₹{MAX_BUDGET:,} is outside this agent's range.")
    if prefs.party_size is not None and not (1 <= prefs.party_size <= MAX_PARTY):
        problems.append(f"Party size must be between 1 and {MAX_PARTY}.")
    if prefs.min_rating is not None and not (0 <= prefs.min_rating <= 5):
        problems.append("Minimum rating must be between 0 and 5.")
    return problems


class HybridExtractor:
    """Uses the LLM when available; falls back to rules on any LLM failure."""

    def __init__(self, rules: RuleBasedExtractor, llm: Optional[LLMExtractor] = None):
        self.rules = rules
        self.llm = llm
        self.last_source = "rule-based"
        self.last_fallback_reason: Optional[str] = None

    def extract(self, text: str) -> Preferences:
        self.last_fallback_reason = None
        if self.llm is not None:
            try:
                prefs = self.llm.extract(text)
                self.last_source = "llm"
                return prefs
            except InputValidationError:
                raise
            except LLMError as exc:
                log.warning("LLM extraction failed, using rule-based fallback: %s", exc)
                self.last_fallback_reason = str(exc)
        self.last_source = "rule-based"
        return self.rules.extract(text)
