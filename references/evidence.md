# Evidence graph and freshness

The plan should be auditable at field level. A `FactRecord` points to one or more `SourceRecord` IDs and to the exact subject field it supports. Keep claim text short, preserve applicability dates, retrieval time, method, and conflicts.

## Statuses

- `verified`: a readable first-party or directly returned provider result supports the claim for the target date.
- `experience`: social or user experience; useful for comfort, queues, and pitfalls, not a rule.
- `estimate`: a conservative calculation with its assumptions stated.
- `unknown`: not checked, unavailable, or outside provider coverage.
- `conflict`: credible sources disagree and the conflict is unresolved.

## Freshness

Store `retrieved_at`, `valid_for`, and `stale_after` for date-sensitive facts. Before delivery, recheck locked items, must-go items, first/last-day transport, weather in forecast range, and all facts marked stale or conflict. A date change invalidates date-dependent facts and downstream verification statuses.

## Provenance requirements

Every route result records provider, mode, query time, origin/destination identity, and min/max interpretation. Every forecast records coordinates, timezone, requested range, returned range, and source URL. Every social result records platform, note ID, sampling scope, and privacy-redacted excerpt; do not save author IDs or full responses.

## Conflict handling

First check same-name locations, branches, entrances, seasons, holidays, and publication dates. If unresolved, retain both sources, set `conflict`, downgrade the affected itinerary item, and present the user with a decision or fallback. Never resolve a conflict by majority vote alone.
