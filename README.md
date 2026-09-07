# cytrade-data-client

Python SDK for the Cytrade data gateway. One call shape works for both a backtest and
a live strategy, so switching between them is a one-line change, not a rewrite. A thin
HTTP client — no provider logic, no credentials, nothing sensitive.

## Install

Internal repo — not published to PyPI. Install straight from GitHub, pinned to a
release tag so everyone on the team is on a known, reproducible version:

```bash
pip install git+https://github.com/wonganson/cytrade-data-client.git@v0.1.0
```

To pick up a later release, upgrade to a newer tag the same way:

```bash
pip install --upgrade git+https://github.com/wonganson/cytrade-data-client.git@v0.2.0
```

In a `requirements.txt`, pin it the same way:
`git+https://github.com/wonganson/cytrade-data-client.git@v0.1.0#egg=cytrade-data-client`

Avoid installing without a `@tag` (i.e. plain `.git` with no ref) — that floats on
whatever `main` currently has, so two teammates installing on different days can end
up on different, unpinned code with no way to tell which version either one has.

If you're developing against a local gateway checkout and want changes picked up
without reinstalling, install this package editable instead:

```bash
pip install -e /path/to/cytrade-data-client
```

## Quickstart

```python
from cytrade_client import DataFetcher

fetcher = DataFetcher(api_key="your-api-key", base_url="https://api.alphaxllama.com")

ROUTE = "bybit-direct|/v5/market/kline?category=linear&symbol=BTCUSDT&interval=60"

# --- backtest: a fixed historical range ------------------------------------
df = fetcher.get(ROUTE, mode="backtest", start_time=one_week_ago, end_time=now)

# --- live: no loop to write, just what happens on each update -------------
def on_update(df):
    print(df.tail(3))

fetcher.watch(ROUTE, length=700, poll_interval=30, on_update=on_update).run_forever()
```

More complete, runnable examples are in [`examples/`](examples/) —
[`example_usage.py`](examples/example_usage.py) for the basics,
[`fetch_all_providers_to_csv.py`](examples/fetch_all_providers_to_csv.py) for a
multi-year, multi-provider export, [`multi_feed_merge.py`](examples/multi_feed_merge.py)
for watching several feeds at once and merging them by timestamp.

## Full API reference

For every `DataFetcher`/`Watcher`/`MultiWatcher` method, parameter, return shape, and
exception, see **[docs/API.md](docs/API.md)** — a complete reference with usage
examples for each one.

For the complete, always-accurate list of every gateway endpoint, parameter, and
response shape (route strings, provider params), open the gateway's own interactive
docs:

**[https://api.alphaxllama.com/docs](https://api.alphaxllama.com/docs)** (swap the
host for `http://localhost:8420/docs` if you're pointed at a local checkout instead)

That page is generated straight from the gateway's code, so it can't fall out of sync
the way a hand-written doc can — treat it as the source of truth for what routes
exist and what params each one takes.

For `bybit-direct`/`binance-direct` specifically — where, unlike the Cybotrade-backed
providers, every endpoint has its own params and columns — call
`fetcher.endpoints("bybit-direct")` (or query `GET /v1/providers/{provider}/endpoints`
directly) for a catalog with a ready-to-copy example route for each one:

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

`fetcher.limits()` shows the same page-size/round-trip numbers the gateway's
`/v1/limits` exposes, if you want them without leaving Python.

## Handling errors

```python
from cytrade_client import CytradeAPIError, QuotaExceededError

try:
    df = fetcher.get(ROUTE, mode="backtest", start_time=start, end_time=end)
except QuotaExceededError as e:
    print(f"monthly limit reached: {e.detail}")
except CytradeAPIError as e:
    print(f"[{e.status_code}] {e.detail}")
```

A request for more data than one call allows is handled for you automatically — the
SDK reads the gateway's actual limit from its response and splits the request into
chunks, so you never see that error yourself for an oversized range.

## `mode="backtest"` vs `mode="live"` vs `.watch()`

- **`mode="backtest"`** — a fixed historical range (`start_time`/`end_time`). Ranges
  larger than the gateway allows per call are chunked automatically, newest chunk
  first, and stitched back into one DataFrame.
- **`mode="live"`** — a single snapshot of the last N bars (`length`). Doesn't hold a
  connection open; call it again yourself whenever you want fresh data.
- **`.watch()`** — a background poller that keeps re-fetching for you, so you don't
  write that loop yourself. Read `.data` whenever you want the latest snapshot, or
  pass `on_update` to get pushed a callback on every new fetch.

The full reasoning behind each of these (why `.watch()` blocks the way it does, how
the cache makes repeated calls cheap, what changed and why) is in
[CLAUDE.md](CLAUDE.md) — this file is the quickstart, that one's the detailed record.
