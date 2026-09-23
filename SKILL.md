---
name: travel-plan-pro
description: "Use when selecting a destination, building or revising a multi-day itinerary, checking travel feasibility, rechecking an old plan before departure, or delivering an offline travel H5."
---

# Travel Plan Pro

Use this skill for destination selection, multi-day itinerary planning, travel-plan revision, pre-departure rechecks, and generation of a self-contained offline travel H5/HTML.

## Operating contract

- Treat `trip.json` as the only business source. Markdown, HTML, manifests, and cards are renderings of one `plan_version`; never re-plan independently while rendering.
- Separate user statements, verified facts, experience reports, estimates, unknowns, and conflicts. A social post is a lead or experience evidence, never automatic proof of opening, booking, price, or availability.
- Preserve hard constraints, locked bookings, must-go items, return boundaries, budget semantics, and mobility needs. If they conflict, report the conflict and ask for a choice; do not silently delete or “make the math work”.
- Do not claim a route, forecast, reservation, or source was used unless the corresponding provider call or readable source actually succeeded in this run.
- Never request or store credentials in `trip.json`, prompts, output files, URLs, or logs. TikHub is optional but remains a first-class Xiaohongshu provider when the user has authorized a request budget and configured a secure token.
- No booking, payment, or message sending. A booking link is a user action unless the user separately authorizes an external mutation.
- Treat the offline HTML as a reading shell around the same source: section navigation, day filtering, a visual journey skeleton, and checklist progress may improve scanning, but must not create or mutate business facts.

## Workflow

1. Read [references/intake.md](references/intake.md) and build a minimal requirement profile; preserve what the user already supplied. Before research or itinerary drafting, stop at the provider gate in [references/provider-onboarding.md](references/provider-onboarding.md): the user must explicitly choose TikHub “接入已有配置 / 需要配置指导 / 本次不接入”. Record that choice with `python3 scripts/provider_gate.py`; do not silently default to either choice and do not ask for secrets in chat.
2. Discover host capabilities with [references/capabilities.md](references/capabilities.md), then select providers using [references/providers.md](references/providers.md). Prefer Amap for map/geocode/route when configured, and a configured weather provider for forecasts; use documented fallbacks when unavailable. Record the user's provider decision and the resulting limitation in `decisions[]`, `capabilities[]`, and the delivery notes.
3. Build a research checklist and evidence records using [references/research.md](references/research.md) and [references/evidence.md](references/evidence.md). Recheck date-sensitive facts before delivery.
4. Plan by hard constraints, place clusters, time windows, transport buffers, budget, and rest using [references/planning.md](references/planning.md). Alternatives must include a trigger, replacement, rejoin point, prerequisites, and time/cost delta.
5. Run `python3 scripts/validate_trip.py <trip.json>` and resolve blocking issues. Use [references/audit.md](references/audit.md) for model-level checks that code cannot prove.
6. Render with `python3 scripts/render_outputs.py <trip.json>` and then run `validate_trip.py --check-outputs`. Use [references/delivery.md](references/delivery.md) for offline HTML, accessibility, privacy, and version rules.
7. For edits, use `state_io.py save --from`, invalidate affected evidence, recheck dependencies, and render a new version. Do not overwrite immutable snapshots.
8. Before packaging or handing off the Skill, read [references/provenance.md](references/provenance.md), run `python3 -m unittest discover -s scripts/tests -p 'test_*.py'`, and verify that the Skill runs without the bys reference directory. If the audit still identifies copied core modules, tests, or guidance, it is not an independently releasable package. Reference comparison is an audit step, never a runtime dependency.

## Advanced lifecycle

- Migrate old data with `python3 scripts/migrate_trip.py <old.json> --out <new.json>`.
- Build a deterministic pre-departure queue with `python3 scripts/recheck_trip.py <trip.json> --out <recheck.json> --trip-out <candidate.json>`; use the candidate for the next version so the queue is visible in MD/H5.
- Export field/source/item relationships with `python3 scripts/evidence_graph.py <trip.json> --out <evidence-graph.json>` and comparable route choices with `python3 scripts/route_matrix.py <trip.json> --out <route-matrix.json>`.
- Create delivery/public projections with `python3 scripts/privacy_export.py <trip.json> --scope delivery|public --out <file.json>`.
- Apply a successful normalized provider response to a new candidate with `python3 scripts/provider_pipeline.py <trip.json> <provider-result.json> --target-id <place-or-leg-id> --out <candidate.json>`; weather does not need a target ID, and TikHub social-experience results are imported as `experience` facts. Validate and save the candidate instead of editing the private source in place.
- Record the mandatory interface choice with `python3 scripts/provider_gate.py <trip.json> --choice decline --out <candidate.json>` or `--choice connect|configure --budget <limit> --out <candidate.json>` before any TikHub or social-experience research.
- Run the twelve product contracts with `python3 scripts/verify_acceptance.py <trip.json>`; this complements, not replaces, output validation and model review.
- Read [references/lifecycle.md](references/lifecycle.md) for migration, dependency invalidation, privacy, and recheck rules, and [references/acceptance.md](references/acceptance.md) for the proof required by each contract.

## Provider defaults

- `amap`: Amap Web Service APIs for geocoding, place identity, and route estimates when `AMAP_API_KEY` is securely configured and the user has authorized use. Keep all calls HTTPS, domain-allowlisted, budgeted, and redacted.
- `weather`: provider selected at runtime. The built-in Open-Meteo adapter is keyless and suitable for forecast/seasonal fallback; a user-configured Chinese weather provider may be used when available. Never fabricate forecasts beyond the returned coverage.
- `tikhub_xhs`: TikHub Xiaohongshu App V2 adapter in `scripts/tikhub_client.py`, with request ledger, pagination guards, structure checks, and author-field redaction. Read [references/tikhub.md](references/tikhub.md) before using it.

TikHub is optional. It can add first-hand experience clues such as queues, family-friendliness, food and recent changes; it cannot replace official opening, ticket, price, route or weather verification. If the user declines it, continue with ordinary web research and state that social experience coverage may be thinner and some on-the-ground friction remains unknown.

## Data and delivery references

- Contract and field meanings: [assets/schemas/README.md](assets/schemas/README.md)
- Host capability discovery and degradation: [references/capabilities.md](references/capabilities.md)
- Provider contracts and fallback: [references/providers.md](references/providers.md)
- Evidence freshness and conflicts: [references/evidence.md](references/evidence.md)
- Migration, recheck, privacy, and acceptance: [references/lifecycle.md](references/lifecycle.md)
- Acceptance evidence and non-claims: [references/acceptance.md](references/acceptance.md)
- Confirmed product scope and implementation status: [references/confirmed-scope.md](references/confirmed-scope.md)
- Provenance and reference boundary: [references/provenance.md](references/provenance.md)
- Versioning and outputs: [references/delivery.md](references/delivery.md)
