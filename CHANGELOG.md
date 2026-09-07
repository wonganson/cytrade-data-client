# Changelog

## 0.2.0

- `mode="live"` (and `.watch()`/`.watch_many()`, which share the same fetch path) now
  **chunk a `length` request that exceeds an endpoint's per-call row cap automatically**,
  the same "ask for what you want, the SDK handles however many requests that takes"
  contract `mode="backtest"` already had. Previously this raised `CytradeAPIError` with
  `.max_allowed_rows` set and left splitting it up to the caller.
- Default `base_url` now points at the deployed gateway (`https://api.alphaxllama.com`)
  instead of `localhost:8420`; pass `base_url="http://localhost:8420"` explicitly for
  local development against a checkout.
- Added `docs/API.md` — a complete method-by-method SDK reference.

## 0.1.0

Initial release.

- `DataFetcher.get()` — one call shape for both `mode="backtest"` (a fixed historical
  range, chunked automatically against the gateway's per-request limit) and
  `mode="live"` (the last N bars).
- `DataFetcher.watch()` / `.watch_many()` — background pollers for live feeds, with
  interval-aware scheduling and pull (`.data`) or push (`on_update`) access.
- `DataFetcher.limits()` / `.endpoints(provider)` — gateway capability discovery.
- Typed exceptions: `CytradeAPIError`, `QuotaExceededError`.
