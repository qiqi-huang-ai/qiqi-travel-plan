# Provider contracts

Provider adapters are optional, replaceable, and never allowed to invent facts. Each adapter returns normalized JSON with `provider`, `capability`, `retrieved_at`, `items`, `warnings`, and `usage`. The planner only consumes normalized fields. Successful results enter a new candidate trip through `scripts/provider_pipeline.py`; adapters never edit the private source directly.

## Amap

`AMAP_API_KEY` is read only from the environment or a host secret store. It is never written to a trip file or printed. The adapter uses HTTPS requests only to `restapi.amap.com`, rejects redirects, applies a persistent per-trip request budget, and redacts the key from errors. CLI use requires explicit `--live` or `--dry-run`; live use also requires `--usage-file`.

- geocode: `/v3/geocode/geo`
- place search: `/v3/place/text`
- driving route: `/v3/direction/driving`
- walking route: `/v3/direction/walking`
- transit route: `/v3/direction/transit/integrated`

Returned route durations are evidence for the queried mode and departure context only. They are not a reservation, live traffic guarantee, or proof that a user can board a service. If the key is absent, the adapter returns a structured `unavailable` result and the workflow falls back to public route material or a conservative estimate.

## Weather

The default keyless adapter uses Open-Meteo for forecast data. It requires coordinates and a forecast date range, stores the provider URL and retrieval time, and reports the returned coverage. CLI use requires explicit `--live` or `--dry-run`. A date outside coverage becomes `seasonal` or `unavailable`; it must not be filled with invented daily values. A host-configured provider may replace it through the same normalized contract.

## Apply normalized results

```bash
python3 scripts/provider_pipeline.py trip.json amap-route.json \
  --target-id leg-station-hotel --out candidate.json
python3 scripts/provider_pipeline.py candidate.json weather.json \
  --out candidate-with-weather.json
```

Only `availability=available` results with usable items may update verified provider fields. The pipeline creates a `map_tool` source record, appends the provider result, updates the selected place/leg or forecast, validates the candidate, and leaves the private source untouched.

## TikHub Xiaohongshu

Use the existing `scripts/tikhub_client.py` only after reading `references/tikhub.md`. Keep it first-class, not a decorative option: request budgets, persistent ledgers, inner business-error checks, pagination guards, and privacy redaction are part of the contract. Its normalized output can be applied with `provider_pipeline.py`; imported facts remain `experience`, and official rules still require independent verification.

## Capability and cost rules

Provider availability, permission, request count, and failures must be recorded in `trip.json.capabilities` or the provider result. A provider failure must not block a text-only draft. Never silently switch to a paid provider, raise a user's budget, or retry a timeout whose charge state is unknown.
