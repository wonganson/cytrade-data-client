# API Reference

Complete method-by-method reference for `cytrade_client`. For the design rationale
behind *why* things work this way, see [`CLAUDE.md`](../CLAUDE.md); for a five-minute
intro, see the [README](../README.md).

## Contents

- [Setup](#setup)
- [Route strings](#route-strings)
- [`DataFetcher`](#datafetcher)
  - [`__init__`](#datafetcherinit)
  - [`.get()`](#get)
  - [`.watch()`](#watch)
  - [`.watch_many()`](#watch_many)
  - [`.available_providers()`](#available_providers)
  - [`.limits()`](#limits)
  - [`.endpoints()`](#endpoints)
- [`Watcher`](#watcher)
- [`MultiWatcher`](#multiwatcher)
- [Exceptions](#exceptions)
- [Guide: common patterns](#guide-common-patterns)
- [Troubleshooting](#troubleshooting)

## Setup

```bash
pip install git+https://github.com/wonganson/cytrade-data-client.git@v0.1.0
```

```python
from cytrade_client import DataFetcher

fetcher = DataFetcher(api_key="your-api-key")
```

`base_url` defaults to the deployed gateway (`https://api.alphaxllama.com`) — you only
need to pass it if you're pointing at a local `cytrade-data` checkout instead
(`base_url="http://localhost:8420"`).

## Route strings

Every method that fetches data takes a **route**: one string identifying provider +
endpoint + params, in the same shape the gateway itself uses:

```
"<provider>|<path>?<param>=<value>&<param2>=<value2>"
```

```python
"bybit-direct|/v5/market/kline?category=linear&symbol=BTCUSDT&interval=60"
"ccxt|bybit/candle_history?symbol=BTC/USDT:USDT&interval=1h"
"cybotrade-glassnode|indicators/sopr?a=BTC&i=1h"
```

- `provider` — e.g. `bybit-direct`, `binance-direct`, `bitget-direct`,
  `hyperliquid-direct`, `ccxt`, `cybotrade-glassnode`, `cybotrade-cryptoquant`. Call
  [`.available_providers()`](#available_providers) for the current full list.
- `path` — the provider's own endpoint path/name. For the `*-direct` providers, call
  [`.endpoints(provider)`](#endpoints) to get a catalog of every known path with a
  ready-to-copy example route.
- `?param=value&...` — passed straight through to the provider, so param names/formats
  follow whatever that provider's own API expects (`interval=60` vs `i=1h` vs
  `window=hour` — the SDK doesn't normalize these; see `.endpoints()` for the exact
  shape a given provider wants).

## `DataFetcher`

### `DataFetcher.__init__`

```python
DataFetcher(
    api_key: str,
    base_url: str = "https://api.alphaxllama.com",
    timeout: float = 30.0,
    verbose: bool = True,
)
```

| Param | Meaning |
|---|---|
| `api_key` | Your Cytrade API key. Sent as the `X-API-Key` header on every request. |
| `base_url` | Gateway host. Defaults to the deployed instance. Override to point at a local checkout (`http://localhost:8420`). |
| `timeout` | Per-HTTP-request timeout in seconds (`requests` timeout, not a total-call budget — a chunked backtest fetch applies this per chunk). |
| `verbose` | When `True` (default), prints `[cytrade] ...` progress lines for every fetch/poll. Set `False` for quiet/scripted use. |

One `DataFetcher` holds one `requests.Session` (connection reuse) and one `api_key` —
create one instance per key/environment, reuse it for every call.

### `.get()`

```python
fetcher.get(
    route: str,
    mode: str = "backtest",              # "backtest" | "live"
    start_time: int | None = None,       # ms since epoch — backtest only
    end_time: int | None = None,         # ms since epoch — backtest only
    length: int | None = None,           # bars — live only
) -> pandas.DataFrame
```

One call shape, two modes:

**`mode="backtest"`** — a fixed historical range.

```python
import time
now = int(time.time() * 1000)
one_week_ago = now - 7 * 24 * 3600 * 1000

df = fetcher.get(ROUTE, mode="backtest", start_time=one_week_ago, end_time=now)
```

- `start_time`/`end_time` are **milliseconds since epoch** (`int`). Either or both may
  be omitted — a flat 7-day default range is used when both are missing.
- Ranges larger than the gateway allows in one call are **chunked automatically**:
  the SDK tries the full range first, and if the gateway rejects it for being too
  large, reads the *exact* per-request limit out of that rejection and splits into
  chunks of that size — newest chunk first, walking backward. You never see the raw
  400 or the chunking; you get back one concatenated, deduplicated,
  datetime-sorted `DataFrame`.
- Requesting further back than a symbol's real history returns just what exists —
  no padding, no `NaN` rows, no error.

**`mode="live"`** — the last N bars, as of right now.

```python
df = fetcher.get(ROUTE, mode="live", length=700)
```

- `length` is required, `start_time`/`end_time` must not be passed.
- Does **not** hold a connection open or loop by itself. Call it again yourself (or
  use [`.watch()`](#watch)) to keep a feed current.
- The gateway resolves `length` bars into an actual time range itself — the SDK
  doesn't parse provider interval notation, so `length` support/behavior follows
  whatever that endpoint supports server-side.
- `length` bigger than the endpoint's per-call row cap is **chunked automatically**,
  same as an oversized backtest range: the SDK tries the full `length` in one call,
  and on rejection, infers the bar interval from real returned data and walks further
  chunks backward until it has enough rows or runs out of history, trimmed to exactly
  `length` most recent rows. This can mean more than one HTTP request for a large
  `length`. You still won't see a `CytradeAPIError` for this case — only for an
  endpoint that doesn't support `length` at all (its interval can't be inferred even
  from one probe chunk).

**Returned `DataFrame` columns** depend on the endpoint — check
`.endpoints(provider)`'s `output_columns` for `*-direct` providers, or the gateway's
`/docs` for others. Kline-style routes generally return
`datetime, open, high, low, close, volume, ...`.

### `.watch()`

```python
fetcher.watch(
    route: str,
    length: int,
    poll_interval: float = 30.0,
    on_update: Callable[[pandas.DataFrame], None] | None = None,
) -> Watcher
```

Starts a background poller for one feed and returns immediately (non-blocking). The
first fetch happens synchronously — by the time you get the `Watcher` back, `.data` is
already populated (or a real error has already been raised for a structurally broken
call). Every fetch after that runs on a background thread; see [`Watcher`](#watcher)
for reading/consuming it.

```python
live = fetcher.watch(ROUTE, length=700, poll_interval=30)
df = live.data          # latest snapshot, instant, no network wait
live.stop()
```

`poll_interval` is a *fallback* cadence, not the real polling rate — see
[`Watcher`](#watcher) for how the actual schedule is computed.

### `.watch_many()`

```python
fetcher.watch_many(
    routes: dict[str, str],
    length: int,
    poll_interval: float = 30.0,
    on_update: Callable[[dict[str, pandas.DataFrame]], None] | None = None,
) -> MultiWatcher
```

Like `.watch()`, but for several named routes at once, with `on_update` firing only
once every route's latest bar agrees on the same timestamp (see
[`MultiWatcher`](#multiwatcher)).

```python
ROUTES = {
    "btc": "bybit-direct|/v5/market/kline?category=linear&symbol=BTCUSDT&interval=60",
    "eth": "bybit-direct|/v5/market/kline?category=linear&symbol=ETHUSDT&interval=60",
}

def on_update(data):
    btc, eth = data["btc"], data["eth"]
    ...

fetcher.watch_many(ROUTES, length=700, on_update=on_update).run_forever()
```

### `.available_providers()`

```python
fetcher.available_providers() -> list[str]
```

The providers your API key currently has access to.

### `.limits()`

```python
fetcher.limits() -> dict
```

The gateway's current per-provider page sizes and round-trip budget — the same data
`/v1/limits` exposes, without leaving Python.

### `.endpoints()`

```python
fetcher.endpoints(provider: str) -> dict
```

For a "raw passthrough" provider (`bybit-direct`, `binance-direct`, `bitget-direct`,
`hyperliquid-direct`) — every known endpoint's path, description, a ready-to-copy
`example_route`, its `output_columns`, and whether it `supports_length`:

```python
>>> fetcher.endpoints("bybit-direct")["endpoints"][0]
{
    "path": "/v5/market/kline",
    "description": "OHLCV candlestick data.",
    "example_route": "bybit-direct|/v5/market/kline?category=linear&symbol=BTCUSDT&interval=60",
    "output_columns": ["datetime", "open", "high", "low", "close", "volume", "turnover"],
    "supports_length": true,
    "max_page_size": 1000
}
```

Providers with one uniform calling convention (`ccxt`, the Cybotrade-backed
providers) return a `note` explaining that convention instead of a per-path list —
there's nothing per-endpoint to enumerate.

## `Watcher`

Returned by `.watch()` — not constructed directly.

| Member | Meaning |
|---|---|
| `.data` | Property. The latest `DataFrame`, lock-protected — a single, stable read that can never be torn mid-update by a background poll. |
| `.last_error` | The most recent exception from a background poll, or `None`. Polling keeps retrying after a failure; this is how you notice persistent failure without it killing the watcher. |
| `.stop(timeout=10)` | Stops the background thread, joins it (up to `timeout` seconds). |
| `.run_forever()` | Blocks the calling thread until `Ctrl+C` or another thread calls `.stop()`. For a script whose only job is watching — see [Guide](#guide-common-patterns). |
| Context manager | `with fetcher.watch(...) as live:` calls `.stop()` automatically on exit. |

**Polling cadence:** not a fixed `poll_interval` tick. After each fetch, the next poll
is scheduled for just after the *next* bar is expected to close (inferred from the
spacing between the two most recent bars already fetched) — a closed bar never
changes, so polling before the next one is due would just re-return what you already
have. `poll_interval` is used only as a fallback: before there's enough data to infer
spacing (the first fetch), and as the retry cadence after a failed poll.

## `MultiWatcher`

Returned by `.watch_many()` — not constructed directly.

| Member | Meaning |
|---|---|
| `.data` | Property. `dict[name, DataFrame]` — latest snapshot per route, **not necessarily aligned**. |
| `.aligned_data` | Property. The last snapshot where every route's latest bar agreed on the same datetime — exactly what `on_update` was most recently called with. |
| `.last_error` | `dict[name, Exception]` — routes currently failing to poll, read live from each underlying `Watcher`. A route missing from this dict has never failed or has since recovered. |
| `.stop(timeout=10)` | Stops every underlying `Watcher`. |
| `.run_forever()` | Same as `Watcher.run_forever()`, for the whole group. |
| Context manager | `with fetcher.watch_many(...) as live:` stops everything automatically on exit. |

Use `.data` when you want each feed's own latest bar regardless of sync; use
`.aligned_data`/`on_update` when your logic needs every feed on the same period (a
cross-asset signal, a pairs trade).

## Exceptions

```python
from cytrade_client import CytradeAPIError, QuotaExceededError
```

| Exception | When | Extra attributes |
|---|---|---|
| `CytradeAPIError` | Any non-200 response from the gateway. | `.status_code`, `.detail`, `.max_allowed_ms`, `.max_allowed_rows` |
| `QuotaExceededError` | Subclass of `CytradeAPIError`, raised specifically on HTTP 429 (monthly call limit reached). | same as above |

`.max_allowed_ms`/`.max_allowed_rows` are set **only** when the gateway rejected the
call for exceeding its per-request size limit — that limit is computed per
provider/interval on the gateway side, not a fixed constant. Both `mode="backtest"`
and `mode="live"`/`.watch()`/`.watch_many()` now handle this rejection for you
automatically (chunked into multiple requests, stitched back into one `DataFrame`) —
you'll only see `CytradeAPIError` directly for an endpoint that doesn't support the
call shape you asked for at all (e.g. `length` on an endpoint whose bar interval can't
be inferred), or for other non-200 responses like a quota rejection.

```python
try:
    df = fetcher.get(ROUTE, mode="backtest", start_time=start, end_time=end)
except QuotaExceededError as e:
    print(f"monthly limit reached: {e.detail}")
except CytradeAPIError as e:
    print(f"[{e.status_code}] {e.detail}")
```

## Guide: common patterns

**Backtest a strategy, then flip it to live with a one-line change:**

```python
ROUTE = "bybit-direct|/v5/market/kline?category=linear&symbol=BTCUSDT&interval=60"

# backtest
df = fetcher.get(ROUTE, mode="backtest", start_time=one_week_ago, end_time=now)

# live — same route, same downstream code, just a different call
df = fetcher.get(ROUTE, mode="live", length=700)
```

**A script whose only job is watching one feed (no loop to write):**

```python
def on_update(df):
    print(df.tail(3))

fetcher.watch(ROUTE, length=700, poll_interval=30, on_update=on_update).run_forever()
```

**Reading a watched feed from your own existing loop** (a trading loop, a web
server — something already keeping the process alive):

```python
live = fetcher.watch(ROUTE, length=700, poll_interval=30)
while trading:
    df = live.data
    ...
```

**Multiple independent feeds** (no shared multi-route API needed — just multiple
`.watch()` calls):

```python
price = fetcher.watch(PRICE_ROUTE, length=700, poll_interval=30)
ratio = fetcher.watch(RATIO_ROUTE, length=700, poll_interval=30)
```

**Merging two feeds on different intervals** — a plain `.merge(on="datetime")` only
matches exact-identical timestamps and silently drops almost everything when
intervals differ. Use `merge_asof` instead:

```python
import pandas as pd

merged = pd.merge_asof(
    price.data.sort_values("datetime"),
    ratio.data.sort_values("datetime"),
    on="datetime",
    direction="backward",  # match each row to the most recent PRECEDING value
)
```

**Several feeds that must be in sync before you react** (a cross-asset signal, a
pairs trade):

```python
ROUTES = {"btc": BTC_ROUTE, "eth": ETH_ROUTE}

def on_update(data):
    btc, eth = data["btc"], data["eth"]
    ...

fetcher.watch_many(ROUTES, length=700, poll_interval=30, on_update=on_update).run_forever()
```

**Quiet/scripted use** (no `[cytrade] ...` progress lines):

```python
fetcher = DataFetcher(api_key="...", verbose=False)
```

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| `ValueError: Route must be 'provider\|path?params'` | Route string is missing the `\|` separator between provider and path. |
| `ValueError: length is only used with mode='live'` | Passed `length` together with `mode="backtest"` — use `start_time`/`end_time` instead. |
| `ValueError: mode='live' requires length` | Called `.get(..., mode="live")` without `length`. |
| `QuotaExceededError` | Monthly call limit reached for this API key — see `.detail` for specifics. |
| `CytradeAPIError: length isn't supported for this endpoint` | This endpoint's bar interval can't be inferred at all (see `.endpoints(provider)`'s `supports_length`) — use `mode="backtest"` with `start_time`/`end_time` instead; a `length` bigger than the per-call cap is *not* this error, that's chunked automatically. |
| `.watch()`/`.watch_many()` feels slow to start with a large `length` | A `length` above the endpoint's per-call cap now takes several HTTP round trips (chunked automatically, see [`.get()`](#get)) instead of one — expected for the first fetch of a large window; subsequent polls are usually fast since the gateway caches closed bars. |
| `.watch()` raises immediately | The *first* fetch is fail-fast by design (bad route, unsupported `length` on this endpoint, bad key). Fix the underlying call before retrying. |
| A running `Watcher`'s `.data` looks stale but no error | Check `.last_error` — polling after the first fetch is resilient, not fail-fast, so a persistent failure (revoked key, network issue) keeps the last good `.data` instead of raising. |
| `watch_many()`'s `on_update` never fires | Routes are on different bar intervals — their closed bars essentially never land on the same timestamp. Watch each separately with `.watch()` instead. |
| Connection errors against `https://api.alphaxllama.com` | Confirm you're not accidentally overriding `base_url` to `localhost` from a copied local-dev example. |
