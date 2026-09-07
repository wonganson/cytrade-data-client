# Changelog

## 0.1.0

Initial release.

- `DataFetcher.get()` — one call shape for both `mode="backtest"` (a fixed historical
  range, chunked automatically against the gateway's per-request limit) and
  `mode="live"` (the last N bars).
- `DataFetcher.watch()` / `.watch_many()` — background pollers for live feeds, with
  interval-aware scheduling and pull (`.data`) or push (`on_update`) access.
- `DataFetcher.limits()` / `.endpoints(provider)` — gateway capability discovery.
- Typed exceptions: `CytradeAPIError`, `QuotaExceededError`.
