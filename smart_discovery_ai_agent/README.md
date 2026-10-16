# Table Scout — AI restaurant discovery (`smart_discovery_ai_agent`)

Describe what you want to eat ("vegetarian dinner for two in Baner under ₹1,000"). A tool-using AI agent
searches **real OpenStreetMap restaurant data**, applies your constraints, ranks the results by relevance and
explains every pick — including what it could **not** verify. The product is **chat-first**: signing in opens a chat, restaurant cards appear inside replies with Save, Compare and Details
buttons, and a new conversation is created on your first message. Accounts, persistent chat history, favourites and
side-by-side comparison are backed by a database. (The filter-form page still exists at `#/discover` for direct use but is no longer linked.)

> **Honesty notes (read first)**
> * OpenStreetMap has **no prices, ratings, review counts or photos**. The app shows those fields as
>   "unavailable" and labels budget/rating matches as *unverified*; it never invents them.
> * Records from `data/restaurants.json` are **fictional DEMO data**. They only appear when you tick
>   *Use DEMO data*, and are labelled `DEMO DATA` everywhere.
> * The app **cannot make reservations** and says so. Cost figures are estimates (listed average × party size), never a bill.
> * The original Streamlit prototype (`app.py`, `main.py`, `agent/`) is kept unchanged as a legacy demo.

## Architecture

```
web/ (HTML + CSS + vanilla JS SPA, no build step)
   │  fetch /api/*  (HttpOnly session cookie + CSRF header)
discovery/api.py  FastAPI ── auth · conversations · search · favourites · compare · profile
   ├─ discovery/db.py         SQLAlchemy models + versioned, additive-only migrations (SQLite default)
   ├─ discovery/agent.py      LLM tool-use loop (Claude) ─► rule-based fallback using the SAME tools
   ├─ discovery/tools.py      14 tools: validated input, structured output, never raise
   ├─ discovery/ranking.py    transparent weighted scoring + fact-based explanations
   ├─ discovery/prefs.py      validated preferences, per-person vs group budget maths
   └─ discovery/providers/    osm.py (Overpass + Nominatim), demo.py, cache.py
```

### AI-agent workflow
`request → intent/preference extraction → validation → search → filter → rank → explain`

| Step | Who does it |
|---|---|
| Understand the request, choose tools and arguments, ask clarifying questions, write the summary | **LLM** (Anthropic Messages API, tool use) |
| Validate preferences, geocode, fetch provider data, normalise, filter, budget maths, ranking, cards | **Plain Python tools** (deterministic) |
| Fact-check the model's prose | Code: prices/ratings/distances in the text must appear in tool results, else a template summary is used |

Tools: `extract_restaurant_preferences`, `retrieve_conversation_context`, `search_restaurants`, `filter_by_location`,
`filter_by_cuisine`, `filter_by_dietary_preferences`, `filter_by_amenities`, `filter_by_rating`, `calculate_group_budget`,
`filter_by_budget`, `check_opening_hours`, `rank_restaurants`, `get_restaurant_details`, `compare_restaurants`.
Restaurant cards come from `rank_restaurants` results held server-side, so the model cannot fabricate a card.
Each assistant message stores a collapsible trace of the tool calls that produced it.

If `ANTHROPIC_API_KEY` is missing, or the LLM call fails (timeout, 4xx/5xx, bad payload), the response is produced by a
rule-based extractor feeding the same tools and is labelled **"Rule-based answer"** with the reason.

### Search and ranking
* Hard constraints remove only **known conflicts** (e.g. `diet:vegetarian=no`, a listed price over budget, a different listed cuisine).
  Missing data is kept and flagged **unverified** — with OSM data most places lack diet/price tags, so dropping unknowns would return nothing.
* Score = weighted sum over the criteria that apply (weights renormalised): cuisine 22 %, distance 16 %, diet 14 %, amenities 14 %,
  budget 12 %, rating 10 % (shrunk by review count), open-now 7 %, data completeness 5 %. Requested-but-unverified criteria score 0.3.
  Relevance to the request outranks rating alone.
* No match → the response says which filters removed candidates and shows the closest alternatives, clearly marked as *not* exact matches.
* Budgets are explicit: `per_person` vs `total` (group). Unknown party size is assumed to be 2 and the assumption is shown.

## Data providers
| Provider | Status | Supplies | Needs |
|---|---|---|---|
| **OpenStreetMap — Overpass** (primary) | integrated | name, coordinates, address parts, cuisine, opening_hours, diet tags, some amenities, website/phone/menu link | no key; public servers are rate-limited — set `OSM_CONTACT` |
| **OpenStreetMap — Nominatim** | integrated (geocoding + automatic fallback POI search, max 40 results/category, 1 req/s) | same tags, fewer results | no key |
| Demo data | integrated, opt-in, labelled | fictional prices/ratings | — |
| Google Places, Foursquare | **not integrated**; I did not verify their current pricing or terms | would add ratings/prices | API key (+ billing for Google) |

Implementation: identifying `User-Agent`, per-mirror failover, 8 s timeout, `Retry-After` handling, a 2-minute circuit breaker after total
Overpass failure, DB-backed response cache (6 h; geocodes 30 days), normalisation into one `Restaurant` schema, source URL and retrieval time on every record.
Overpass has no pagination; results are ranked and the API paginates the ranked list. Data © OpenStreetMap contributors (ODbL).
During this build the public Overpass servers were returning 5xx, so live verification ran through the Nominatim fallback.

## Configuration
```bash
cp .env.example .env      # Windows: copy .env.example .env
```
See `.env.example` for every variable that is actually read. Nothing is required to start the app: without
`ANTHROPIC_API_KEY` it runs in rule-based mode, and `GET /api/status` reports exactly what is configured.
Get a key at https://console.anthropic.com/.

## Install & run (Windows PowerShell shown; Linux/macOS: `source .venv/bin/activate`)
```bash
cd smart_discovery_ai_agent
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python -m uvicorn discovery.asgi:app --port 8000 --reload
# open http://localhost:8000
```
The database (`discovery.db`, SQLite) and tables are created automatically on startup by `discovery/db.py:run_migrations`
(a `schema_migrations` table records applied versions; migrations are additive and never drop or truncate data).
Set `DATABASE_URL` to use another database (e.g. `postgresql+psycopg://…`; the PostgreSQL path is **untested** and needs its driver installed).

Legacy prototype: `streamlit run app.py` · `python main.py -q "veg dinner for 4 in Wakad under 3000" --trace`.

## Tests
```bash
python -m pytest            # 171 tests: 64 legacy + 107 for the web product
python -m pytest tests/web  # only the new suites
```
Network access is never used by the tests: OpenStreetMap and Anthropic are replaced by scripted `httpx.MockTransport` fakes
(`tests/web/helpers.py`). Covered: registration/login/logout/CSRF/session expiry/rate limiting/user isolation, provider success, empty,
timeout, 429, 5xx, failover, cache and normalisation, budget/cuisine/diet/amenity filtering, ranking and explanations, the LLM tool loop,
LLM failure fallbacks, conversation persistence (restart, re-login, delete, search, pagination), favourites and comparison.
**Not covered by automated tests:** live Claude calls, live OSM calls, PostgreSQL, and browser UI (checked manually, see limitations).

## API (JSON; all state-changing requests need `X-CSRF-Token` = value of the `csrf_token` cookie; interactive docs at `/api/docs`)
| Method & path | Purpose |
|---|---|
| `GET /api/status` | which integrations are configured |
| `GET /api/auth/csrf` · `POST /api/auth/register` · `POST /api/auth/login` · `POST /api/auth/logout` · `GET /api/auth/me` | auth |
| `PUT /api/profile` | name, default location, diet, typical budget |
| `GET/POST /api/conversations` · `GET/PATCH/DELETE /api/conversations/{id}` | list (`q`, `limit`, `before`), create, read, rename, delete |
| `GET/POST /api/conversations/{id}/messages` | paginated history (`limit`, `before_id`); send a message and run the agent |
| `GET /api/restaurants/search` | deterministic search/filter/rank (no LLM) with filters and pagination |
| `GET /api/restaurant?id=osm:node/123` | detail |
| `GET/POST/DELETE /api/favourites` | save / list / remove (`DELETE ?id=`) |
| `POST /api/compare` | compare 2–5 restaurant ids |

## Security
scrypt password hashes; opaque server-side sessions (only a SHA-256 of the token is stored) in an HttpOnly, SameSite=Lax cookie
(`COOKIE_SECURE=true` behind HTTPS); double-submit CSRF; ownership checks on every conversation endpoint (404 for other users' ids);
server-side validation; login and chat/search rate limits (in-memory, per process); CSP and other security headers; all untrusted text is rendered
with `textContent`; API keys stay on the server and are never logged or returned.

## Deployment
Run behind HTTPS with `COOKIE_SECURE=true`, a real `OSM_CONTACT`, and one worker process (rate limiters are in-memory),
e.g. `python -m uvicorn discovery.asgi:app --host 0.0.0.0 --port 8000`. Back up the database file (or use a managed database).
This has **not** been load-tested or security-audited; do not treat it as production-ready.

## Known limitations
* Live Claude calls were **not** tested (no API key in the build environment); the loop is tested against a scripted fake of the Messages API.
* Live OSM data was verified only through Nominatim, because Overpass was down at the time. Public Nominatim limits apply (1 req/s); heavy use needs your own instance.
* No prices/ratings/photos from OSM, so budget and rating requests are usually *unverified*; ambience requests (quiet, romantic) cannot be verified at all.
  A provider with ratings (Google Places/Foursquare) would need a new adapter in `discovery/providers/`.
* Opening-hours parsing supports the common OSM subset (day ranges, time ranges, overnight, `off`, `24/7`); anything else is "cannot verify". Times are evaluated in IST.
* The rule-based fallback is English-only and knows Pune locality aliases; the LLM path handles other phrasing.
* No email verification or password reset; one session cookie per browser; rate limiters reset on restart.
* The UI was exercised manually in a browser pane (landing, register, discover with live and demo data, favourites, detail dialog, chat, new chat,
  mobile layout) — there are no automated browser tests, and keyboard/screen-reader behaviour was not audited with assistive technology.
* Favourites store a snapshot of the record when saved; it is flagged as possibly stale after 30 days but not auto-refreshed.

## Project structure
```
discovery/   web backend + agent (new)          web/       SPA frontend (new)
tests/web/   tests for the new product          tests/     64 legacy tests
agent/ app.py main.py config.py data/           legacy Streamlit prototype (sample data)
docs/ report/ scripts/                          legacy evidence, diagrams and report
```
