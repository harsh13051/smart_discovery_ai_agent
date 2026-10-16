# Test Evidence

Generated automatically by `scripts/generate_evidence.py` on 2026-10-16 20:40 (Python 3.12.3, Linux). Every result below comes from an actual run; nothing was typed in by hand. Re-run the script to regenerate it on your machine.

Mode: **DEMO** (no LLM key). The LLM-path tests (TC-50 to TC-58) use a scripted fake provider, so they verify our parsing, validation and fallback code but **not** a live LLM.

## 1. Automated test summary: 64/64 passed

| Test | Result |
|---|---|
| `test_agent::test_tc30_valid_request_gives_ranked_recommendations` | PASS |
| `test_agent::test_tc31_strict_budget_is_respected` | PASS |
| `test_agent::test_tc32_missing_area_triggers_clarification_then_completes` | PASS |
| `test_agent::test_tc33_bare_number_answers_budget_question` | PASS |
| `test_agent::test_tc34_no_results_is_explained` | PASS |
| `test_agent::test_tc35_invalid_inputs_are_rejected_gracefully` | PASS |
| `test_agent::test_tc36_off_topic_request_is_declined` | PASS |
| `test_agent::test_tc37_uncovered_city_gets_a_helpful_question` | PASS |
| `test_agent::test_tc38_relaxation_widens_area_but_keeps_preferences` | PASS |
| `test_agent::test_tc39_preference_relaxed_only_when_nothing_matches` | PASS |
| `test_agent::test_tc40_data_source_failure_is_handled` | PASS |
| `test_agent::test_tc41_trace_shows_every_workflow_stage` | PASS |
| `test_agent::test_tc42_clarification_is_capped` | PASS |
| `test_agent::test_tc43_diet_is_a_hard_constraint_end_to_end` | PASS |
| `test_app::test_tc60_streamlit_ui_runs_a_request` | PASS |
| `test_extractor::test_tc01_full_request_is_structured` | PASS |
| `test_extractor::test_tc02_per_person_budget_becomes_total` | PASS |
| `test_extractor::test_tc03_non_veg_is_not_vegetarian` | PASS |
| `test_extractor::test_tc04_area_aliases_are_normalised` | PASS |
| `test_extractor::test_tc05_unusable_input_is_rejected[   ]` | PASS |
| `test_extractor::test_tc05_unusable_input_is_rejected[None]` | PASS |
| `test_extractor::test_tc05_unusable_input_is_rejected[]` | PASS |
| `test_extractor::test_tc05_unusable_input_is_rejected[xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx]` | PASS |
| `test_extractor::test_tc06_budget_numbers_are_not_confused` | PASS |
| `test_extractor::test_tc07_invalid_values_are_reported` | PASS |
| `test_extractor::test_tc08_bare_amount_only_matches_lone_numbers` | PASS |
| `test_extractor::test_tc09_off_topic_request_is_flagged` | PASS |
| `test_llm::test_tc50_llm_output_is_validated_before_use` | PASS |
| `test_llm::test_tc51_llm_failure_falls_back_to_rules` | PASS |
| `test_llm::test_tc52_malformed_llm_json_falls_back` | PASS |
| `test_llm::test_tc53_hallucinated_restaurant_is_blocked` | PASS |
| `test_llm::test_tc54_invented_price_is_blocked` | PASS |
| `test_llm::test_tc55_faithful_llm_summary_is_used` | PASS |
| `test_llm::test_tc56_anthropic_response_shape_is_supported` | PASS |
| `test_llm::test_tc57_demo_mode_needs_no_credentials` | PASS |
| `test_llm::test_tc58_json_parser_rejects_garbage` | PASS |
| `test_recommender::test_tc20_weights_and_scores_are_well_formed` | PASS |
| `test_recommender::test_tc21_occasion_and_rating_drive_ranking` | PASS |
| `test_recommender::test_tc22_explanations_match_the_data` | PASS |
| `test_recommender::test_tc23_unknown_feature_is_reported_as_missing_information` | PASS |
| `test_recommender::test_tc24_ranking_is_deterministic` | PASS |
| `test_tools::test_tc10_cost_estimate_includes_tax` | PASS |
| `test_tools::test_tc11_nearby_areas_use_shortest_path` | PASS |
| `test_tools::test_tc12_unknown_area_is_a_tool_error` | PASS |
| `test_tools::test_tc13_hard_constraints_are_never_violated[1500-any]` | PASS |
| `test_tools::test_tc13_hard_constraints_are_never_violated[1500-jain]` | PASS |
| `test_tools::test_tc13_hard_constraints_are_never_violated[1500-vegan]` | PASS |
| `test_tools::test_tc13_hard_constraints_are_never_violated[1500-vegetarian]` | PASS |
| `test_tools::test_tc13_hard_constraints_are_never_violated[300-any]` | PASS |
| `test_tools::test_tc13_hard_constraints_are_never_violated[300-jain]` | PASS |
| `test_tools::test_tc13_hard_constraints_are_never_violated[300-vegan]` | PASS |
| `test_tools::test_tc13_hard_constraints_are_never_violated[300-vegetarian]` | PASS |
| `test_tools::test_tc13_hard_constraints_are_never_violated[4000-any]` | PASS |
| `test_tools::test_tc13_hard_constraints_are_never_violated[4000-jain]` | PASS |
| `test_tools::test_tc13_hard_constraints_are_never_violated[4000-vegan]` | PASS |
| `test_tools::test_tc13_hard_constraints_are_never_violated[4000-vegetarian]` | PASS |
| `test_tools::test_tc13_hard_constraints_are_never_violated[800-any]` | PASS |
| `test_tools::test_tc13_hard_constraints_are_never_violated[800-jain]` | PASS |
| `test_tools::test_tc13_hard_constraints_are_never_violated[800-vegan]` | PASS |
| `test_tools::test_tc13_hard_constraints_are_never_violated[800-vegetarian]` | PASS |
| `test_tools::test_tc14_check_budget_reports_headroom` | PASS |
| `test_tools::test_tc15_missing_data_file_retries_then_fails` | PASS |
| `test_tools::test_tc16_corrupt_data_file_is_detected` | PASS |
| `test_tools::test_tc17_unknown_tool_is_rejected` | PASS |

## 2. Scenario evidence (observed agent behaviour)

### TC-30: Valid request gives ranked, explained recommendations

- **Input:** `veg birthday dinner for 4 in Wakad under 3000`
- **Expected:** status ok; <=3 pure-veg options within ₹3,000; ranked by score; reasons shown
- **Automated test:** `test_tc30...` = **PASS**
- **Actual observed output:**

```
> veg birthday dinner for 4 in Wakad under 3000
status=ok: I found 3 options near Wakad within ₹3,000 for 4 people. My top pick is Rooftop Rasoi (Wakad, rated 4.3, about ₹2,730). To find enough options I had to compromise: Expanded the search beyond Wakad to areas within 8 km.
  1. Rooftop Rasoi (Wakad) est. ₹2,730, rating 4.3, score 67.9
  2. Wakad Chaat Corner (Wakad) est. ₹500, rating 3.9, score 49.3
  3. Morning Misal House (Chinchwad) est. ₹630, rating 4.5, score 48.9
  relaxation: Expanded the search beyond Wakad to areas within 8 km.
```

### TC-31: Strict budget is respected

- **Input:** `dinner for two in Baner under ₹400`
- **Expected:** Only options with estimated bill <= ₹400
- **Automated test:** `test_tc31...` = **PASS**
- **Actual observed output:**

```
> dinner for two in Baner under ₹400
status=ok: I found 1 option near Baner within ₹400 for 2 people. My top pick is Wakad Chaat Corner (Wakad, rated 3.9, about ₹250). To find enough options I had to compromise: Expanded the search beyond Baner to areas within 8 km.
  1. Wakad Chaat Corner (Wakad) est. ₹250, rating 3.9, score 45.8
  relaxation: Expanded the search beyond Baner to areas within 8 km.
```

### TC-32: Missing information triggers clarification (multi-turn)

- **Input:** `find me a good place to eat for my family` → `Kothrud` → `1500 total, we are 4`
- **Expected:** Asks for area, then budget; then recommends using merged preferences
- **Automated test:** `test_tc32...` = **PASS**
- **Actual observed output:**

```
> find me a good place to eat for my family
status=needs_clarification: Which area of Pune should I search? For example: Pimpri, Chinchwad, Akurdi, Wakad, Hinjewadi, Baner, ...
> Kothrud
status=needs_clarification: What's your budget? You can give a total (e.g. 'under ₹1500') or per person (e.g. '₹400 per person').
> 1500 total, we are 4
status=ok: I found 3 options near Kothrud within ₹1,500 for 4 people. My top pick is Kothrud Sweet Mart & Snacks (Kothrud, rated 4.1, about ₹550). To find enough options I had to compromise: Expanded the search beyond Kothrud to areas within 8 km.
  1. Kothrud Sweet Mart & Snacks (Kothrud) est. ₹550, rating 4.1, score 70.3
  2. Paneer & Pepper (Kothrud) est. ₹1,470, rating 4.0, score 62.7
  3. FC Road Bun Maska Co. (Shivajinagar) est. ₹670, rating 4.4, score 45.0
  relaxation: Expanded the search beyond Kothrud to areas within 8 km.
```

### TC-34: No result satisfies all constraints

- **Input:** `jain japanese food in Koregaon Park for 6 under 1000`
- **Expected:** status no_results; explains blocking constraint and the closest option
- **Automated test:** `test_tc34...` = **PASS**
- **Actual observed output:**

```
> jain japanese food in Koregaon Park for 6 under 1000
status=no_results: No restaurant meets all your requirements, even after relaxing the flexible ones. The closest option is Udupi Grand (Viman Nagar) at about ₹1,450, which is ₹450 over your ₹1,000 budget. Few places nearby have confirmed jain options in the data. Try a higher budget, a different area, or fewer requirements.
```

### TC-35: Invalid input is rejected gracefully

- **Input:** `dinner for 2 in Aundh under -500`
- **Expected:** status invalid_input with a clear message; no crash
- **Automated test:** `test_tc35...` = **PASS**
- **Actual observed output:**

```
> dinner for 2 in Aundh under -500
status=invalid_input: The budget must be a positive amount in rupees.
```

### TC-36: Off-topic request

- **Input:** `what's the weather tomorrow`
- **Expected:** status unsupported with guidance
- **Automated test:** `test_tc36...` = **PASS**
- **Actual observed output:**

```
> what's the weather tomorrow
status=unsupported: I can only help find restaurants in Pune (e.g. 'veg dinner for 2 in Baner under ₹1200').
```

### TC-37: Location outside coverage

- **Input:** `pizza in Mumbai under 800 for 2`
- **Expected:** Does not guess; asks user to pick a covered area
- **Automated test:** `test_tc37...` = **PASS**
- **Actual observed output:**

```
> pizza in Mumbai under 800 for 2
status=needs_clarification: I don't have data for 'Mumbai' yet. Which of these areas works for you? Pimpri, Chinchwad, Akurdi, Wakad, Hinjewadi, Baner, Aundh, Shivajinagar, Kothrud, Camp, Koregaon Park, Viman Nagar
```

### TC-38: Agent re-plans when too few matches

- **Input:** `italian date night in Akurdi under 1500`
- **Expected:** Widens search to nearby areas, keeps 'Italian', reports the compromise
- **Automated test:** `test_tc38...` = **PASS**
- **Actual observed output:**

```
> italian date night in Akurdi under 1500
status=ok: I found 1 option near Akurdi within ₹1,500 for 2 people. My top pick is Crust & Crumb (Chinchwad, rated 4.1, about ₹1,010). To find enough options I had to compromise: Expanded the search beyond Akurdi to areas within 8 km.
  1. Crust & Crumb (Chinchwad) est. ₹1,010, rating 4.1, score 70.3
  relaxation: Expanded the search beyond Akurdi to areas within 8 km.
```

### TC-39: Preference relaxed only when nothing matches

- **Input:** `rooftop place with live music and parking in Pimpri for 3, budget 3000, 4.5+ rated`
- **Expected:** Drops features (not rating), tells the user, lists trade-offs
- **Automated test:** `test_tc39...` = **PASS**
- **Actual observed output:**

```
> rooftop place with live music and parking in Pimpri for 3, budget 3000, 4.5+ rated
status=ok: I found 1 option near Pimpri within ₹3,000 for 3 people. My top pick is Morning Misal House (Chinchwad, rated 4.5, about ₹470). To find enough options I had to compromise: Expanded the search beyond Pimpri to areas within 8 km. Dropped the requirement for: parking, outdoor seating, live music.
  1. Morning Misal House (Chinchwad) est. ₹470, rating 4.5, score 58.9
  relaxation: Expanded the search beyond Pimpri to areas within 8 km.
  relaxation: Dropped the requirement for: parking, outdoor seating, live music.
```

### TC-40: Data source unavailable

- **Input:** `dinner for 2 in Baner under 1500` with the data file missing
- **Expected:** retry once, then status `tool_error` and an honest message (no invented results)
- **Actual observed output:**

```
status=tool_error: Sorry, the restaurant data service is unavailable right now, so I can't make a reliable recommendation. Please try again shortly.
[extract] Preferences extracted by rule-based extractor: area=Baner, budget=1500.0, party=2, diet=any
[decide] search: All essential information is available.
[plan] search_restaurants - Fetch restaurants in Baner.
[plan] estimate_cost - Estimate each bill for 2 people incl. 5% tax.
[plan] filter_candidates - Apply diet and budget (hard) plus cuisine/rating/feature (soft) constraints.
[plan] check_budget - Confirm each shortlisted bill is within ₹1,500.
[tool] search_restaurants: failed (Restaurant data file not found: does_not_exist.json); retrying once
[tool_error] Restaurant data file not found: does_not_exist.json
```

## 3. Full pytest console output

```
============================= test session starts ==============================
platform linux -- Python 3.12.3, pytest-9.1.1, pluggy-1.7.0
rootdir: /home/claude/smart_discovery_ai_agent
configfile: pytest.ini
testpaths: tests
plugins: anyio-4.15.1
collected 64 items

tests/test_agent.py ..............                                       [ 21%]
tests/test_app.py .                                                      [ 23%]
tests/test_extractor.py ............                                     [ 42%]
tests/test_llm.py .........                                              [ 56%]
tests/test_recommender.py .....                                          [ 64%]
tests/test_tools.py .......................                              [100%]

- generated xml file: /home/claude/smart_discovery_ai_agent/logs/pytest_results.xml -
============================== 64 passed in 0.48s ==============================
```