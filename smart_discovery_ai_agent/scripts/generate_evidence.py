"""Builds docs/test_evidence.md from REAL runs.

1. Runs the pytest suite and records the console output and per-test results.
2. Runs the key scenarios through the agent and records what it actually did.

Run from the project root:  python scripts/generate_evidence.py
"""
from __future__ import annotations

import datetime as dt
import platform
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent import ConversationState, DiscoveryAgent  # noqa: E402
from agent.tools import JsonRestaurantSource  # noqa: E402
from config import Settings  # noqa: E402

# id, objective, input(s), expected behaviour, pytest test name
SCENARIOS = [
    ("TC-30", "Valid request gives ranked, explained recommendations",
     ["veg birthday dinner for 4 in Wakad under 3000"],
     "status ok; <=3 pure-veg options within ₹3,000; ranked by score; reasons shown", "test_tc30"),
    ("TC-31", "Strict budget is respected",
     ["dinner for two in Baner under ₹400"],
     "Only options with estimated bill <= ₹400", "test_tc31"),
    ("TC-32", "Missing information triggers clarification (multi-turn)",
     ["find me a good place to eat for my family", "Kothrud", "1500 total, we are 4"],
     "Asks for area, then budget; then recommends using merged preferences", "test_tc32"),
    ("TC-34", "No result satisfies all constraints",
     ["jain japanese food in Koregaon Park for 6 under 1000"],
     "status no_results; explains blocking constraint and the closest option", "test_tc34"),
    ("TC-35", "Invalid input is rejected gracefully",
     ["dinner for 2 in Aundh under -500"],
     "status invalid_input with a clear message; no crash", "test_tc35"),
    ("TC-36", "Off-topic request",
     ["what's the weather tomorrow"],
     "status unsupported with guidance", "test_tc36"),
    ("TC-37", "Location outside coverage",
     ["pizza in Mumbai under 800 for 2"],
     "Does not guess; asks user to pick a covered area", "test_tc37"),
    ("TC-38", "Agent re-plans when too few matches",
     ["italian date night in Akurdi under 1500"],
     "Widens search to nearby areas, keeps 'Italian', reports the compromise", "test_tc38"),
    ("TC-39", "Preference relaxed only when nothing matches",
     ["rooftop place with live music and parking in Pimpri for 3, budget 3000, 4.5+ rated"],
     "Drops features (not rating), tells the user, lists trade-offs", "test_tc39"),
]


def run_pytest() -> tuple[str, dict[str, str]]:
    junit = ROOT / "logs" / "pytest_results.xml"
    junit.parent.mkdir(exist_ok=True)
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-v", "-p", "no:cacheprovider", f"--junitxml={junit}"],
        cwd=ROOT, capture_output=True, text=True,
    )
    results = {}
    for case in ET.parse(junit).getroot().iter("testcase"):
        status = "PASS"
        if case.find("failure") is not None or case.find("error") is not None:
            status = "FAIL"
        elif case.find("skipped") is not None:
            status = "SKIPPED"
        results[f"{case.get('classname')}::{case.get('name')}"] = status
    return proc.stdout, results


def observe(agent: DiscoveryAgent, inputs: list[str]) -> str:
    state = ConversationState()
    lines = []
    for text in inputs:
        r = agent.run(text, state)
        lines.append(f"> {text}")
        lines.append(f"status={r.status}: {r.message}")
        for i, rec in enumerate(r.recommendations, 1):
            lines.append(f"  {i}. {rec.restaurant.name} ({rec.restaurant.area}) est. ₹{rec.estimated_total:,}, "
                         f"rating {rec.restaurant.rating}, score {rec.score}")
        for x in r.relaxations:
            lines.append(f"  relaxation: {x}")
    return "\n".join(lines)


def main() -> None:
    output, results = run_pytest()
    passed = sum(v == "PASS" for v in results.values())
    agent = DiscoveryAgent(Settings())
    now = dt.datetime.now().strftime("%Y-%m-%d %H:%M")

    md = [
        "# Test Evidence",
        "",
        f"Generated automatically by `scripts/generate_evidence.py` on {now} "
        f"(Python {platform.python_version()}, {platform.system()}). Every result below comes from an "
        "actual run; nothing was typed in by hand. Re-run the script to regenerate it on your machine.",
        "",
        "Mode: **DEMO** (no LLM key). The LLM-path tests (TC-50 to TC-58) use a scripted fake provider, "
        "so they verify our parsing, validation and fallback code but **not** a live LLM.",
        "",
        f"## 1. Automated test summary: {passed}/{len(results)} passed",
        "",
        "| Test | Result |",
        "|---|---|",
    ]
    md += [f"| `{name.split('.')[-1]}` | {status} |" for name, status in sorted(results.items())]
    md += ["", "## 2. Scenario evidence (observed agent behaviour)", ""]
    for tc, objective, inputs, expected, test_name in SCENARIOS:
        status = "PASS" if all(v == "PASS" for k, v in results.items() if test_name in k) else "FAIL"
        md += [
            f"### {tc}: {objective}",
            "",
            f"- **Input:** " + " → ".join(f"`{i}`" for i in inputs),
            f"- **Expected:** {expected}",
            f"- **Automated test:** `{test_name}...` = **{status}**",
            "- **Actual observed output:**",
            "",
            "```",
            observe(agent, inputs),
            "```",
            "",
        ]
    broken = DiscoveryAgent(Settings(), source=JsonRestaurantSource(ROOT / "data" / "does_not_exist.json"))
    r = broken.run("dinner for 2 in Baner under 1500")
    md += [
        "### TC-40: Data source unavailable",
        "",
        "- **Input:** `dinner for 2 in Baner under 1500` with the data file missing",
        "- **Expected:** retry once, then status `tool_error` and an honest message (no invented results)",
        "- **Actual observed output:**",
        "",
        "```",
        f"status={r.status}: {r.message}",
        *[f"[{t.stage}] {t.detail}" for t in r.trace],
        "```",
        "",
        "## 3. Full pytest console output",
        "",
        "```",
        output.strip(),
        "```",
    ]
    out = ROOT / "docs" / "test_evidence.md"
    out.write_text("\n".join(md), encoding="utf-8")
    print(f"wrote {out} ({passed}/{len(results)} tests passed)")


if __name__ == "__main__":
    main()
