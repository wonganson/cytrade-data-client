# cytrade-data-client

The internal SDK for the **cytrade-data** gateway, used by the team to pull both
backtest and live market data. A thin HTTP client — no provider logic, no
credentials, nothing sensitive.

The whole point: one call shape works for both a backtest and a live strategy, so
switching between them is a one-line change, not a rewrite.

The gateway is deployed at `https://api.alphaxllama.com` — that's `DEFAULT_BASE_URL`
in `client.py`, so it's used automatically when `base_url` is omitted. Pass
`base_url="http://localhost:8420"` explicitly to point at a local checkout instead
(see "Local development against a local gateway" below).

```python
from cytrade_client import DataFetcher

fetcher = DataFetcher(api_key="...")  # base_url defaults to the deployed gateway

# backtest: a fixed historical range
df = fetcher.get(
    "bybit-direct|/v5/market/kline?category=linear&symbol=BTCUSDT&interval=60",
    mode="backtest", start_time=one_week_ago, end_time=now,
)

# live: the last N bars, kept fresh by calling this again
df = fetcher.get(
    "bybit-direct|/v5/market/kline?category=linear&symbol=BTCUSDT&interval=60",
    mode="live", length=700,
)
```

## Layout

- `cytrade_client/client.py` — the whole SDK. `DataFetcher.get()` dispatches to
  `_get_backtest()` (range-based, adapts to the gateway's per-request limit) or
  `_request(..., length=...)` (live). `_parse_route()` splits `"provider|path?k=v"`
  into the three fields the gateway's `/v1/fetch` body wants. `.watch()` (see below)
  is the third entry point, for a feed that stays fresh without you writing a loop.
  `.limits()` and `.endpoints(provider)` are thin wrappers over `GET /v1/limits` and
  `GET /v1/providers/{provider}/endpoints` — for `bybit-direct`/`binance-direct`
  specifically, `.endpoints(...)` is how you discover what paths exist and what
  params/columns each one uses, since unlike the Cybotrade-backed providers there's no
  single convention to just know — see cytrade-data's `CLAUDE.md` for how that catalog
  is built (hand-maintained descriptions, live-derived size/length info).
- `cytrade_client/exceptions.py` — `CytradeAPIError` (any non-200) and its subclass
  `QuotaExceededError` (429 specifically), so calling code can `except
  QuotaExceededError` without parsing status codes or response text. On a "range/length
  too large" rejection, `.max_allowed_ms`/`.max_allowed_rows` carry the gateway's exact
  limit *for that specific request* — see below, this isn't a fixed number.
- `cytrade_client/watcher.py` — `Watcher`, returned by `DataFetcher.watch()`. A small
  background-thread poller; see its own section below.
- `examples/example_usage.py`, `examples/fetch_all_providers_to_csv.py`,
  `examples/multi_feed_merge.py` — usage templates against the deployed gateway.
- `docs/API.md` — full method-by-method SDK reference (params, return shapes,
  exceptions, examples for every public method). This file (`CLAUDE.md`) is the
  detailed design record — why things work the way they do; `docs/API.md` is the
  lookup reference — what to call and what you get back.

## `start_time`/`end_time` accept human-readable strings, not just raw ms

`_parse_time()` (top of `client.py`) runs both through before anything else touches
them — `.get()` calls it right after the `mode="backtest"` branch, so `_get_backtest`
and everything downstream still only ever sees an `int` or `None`, unchanged from
before this existed. Accepts an `int` as-is (the original contract — nothing passing
raw ms breaks), or a string: `"2023-01-01"` (midnight that day), `"2023-01-01
00:00:00"` / `"2023-01-01T00:00:00"` (either separator), or `"now"`
(case-insensitive — `.strip().lower() == "now"`, so `"NOW"`/`" now "` both work too).

**String inputs are always parsed as UTC, deliberately, never the calling machine's
own local timezone** — `datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)`,
not `datetime.now()`/a naive local `strptime`. This matters specifically because the
alternative failure mode is silent, not a crash: a naive-local parse of
`start_time="2023-01-01"` would mean midnight in whatever timezone the *script*
happens to run in, so the exact same call could quietly request a different real
range depending on whether it ran from a machine in UTC, UTC+8, or UTC-5 — for a
quant data SDK, a silently-shifted historical range is a correctness bug, not a
display quirk, and one that would never announce itself with an error. Anchoring to
UTC in the parser itself removes that variable entirely: the same string always means
the same real moment, everywhere this SDK runs. A caller who genuinely needs a
non-UTC offset still can — pass an `int` ms value computed however they like; only
the string convenience path is UTC-fixed.

An unrecognized string raises `ValueError` naming every accepted format (not a vague
parse error) and a non-`int`/`str`/`None` value raises `TypeError` — both fail at the
`.get()` call site, before any HTTP request goes out, same "fail fast on a
structurally broken call" philosophy `Watcher._start()` already uses for its first
fetch.

## `mode="backtest"` vs `mode="live"` — what actually differs

Both modes hit the exact same `POST /v1/fetch` endpoint. The only difference is which
fields go in the request body:

- **backtest** sends `start_time`/`end_time` (see `_parse_time()` above for the
  accepted formats) — if either is omitted, `_get_backtest()`
  fills in a flat 7-day default locally (`DEFAULT_RANGE_MS`, the SDK's only remaining
  hardcoded constant, purely a UX default, not a safety cap). The SDK does **not**
  hardcode a max-range constant — it tries the full request first; if the gateway
  rejects it (400) for exceeding its per-call size cap (which can happen even for that
  7-day default, on a very fine interval), the response includes the *exact*
  `max_allowed_ms` for that specific provider/interval (the gateway's cap is computed
  per-request from each provider's real page size, not one fixed number — see
  cytrade-data's `CLAUDE.md`), and `_fetch_chunked()` uses that real number to split
  into chunks. **Chunks are fetched newest-first, walking backward toward `start_time`**
  — this isn't arbitrary: since every provider's own upstream pagination also walks
  backward, an empty chunk reliably means "reached the start of this symbol's history,"
  so `_fetch_chunked` stops immediately instead of firing off further, guaranteed-empty
  chunks toward `start_time`. A range that requests further back than a symbol's real
  history returns only what actually exists, cleanly (no NaN, no padding) — verified by
  requesting 10 years of BTCUSDT (which only has data back to ~March 2020): it stopped
  after the first empty chunk instead of completing all planned chunks. Results are
  concatenated + deduped + re-sorted by `datetime` before returning, so fetch order
  never affects the final row order. The caller never sees the chunking or the raw 400.
- **live** sends `length` instead — "the last N bars, ending now." The gateway (not
  this SDK) turns that into an actual time range, because only the gateway knows how
  to read each provider's own interval notation (`interval=60` vs `i=1h` vs
  `window=hour`, ...). This SDK deliberately does **not** duplicate that parsing —
  keeping interval knowledge in one place (the gateway) was a specific design choice
  to avoid the two repos drifting out of sync on provider-specific formats.

  **`length` requests are now chunked automatically too, not just backtest ranges**
  (`DataFetcher._get_live` in `client.py`) — this used to raise `CytradeAPIError`
  with `.max_allowed_rows` set and stop there, on the reasoning that "a live call
  generally shouldn't be silently shrunk." That held right up until `.watch_many()`
  needed it too: a caller building a rolling window bigger than one endpoint's
  per-call cap (`length=2000` against a 1000-row-max endpoint) had to hand-roll their
  own chunking loop just to get the same "ask for what you want" ergonomics
  `mode="backtest"` already had — exactly the loop-writing this SDK exists to save
  people from. The catch: unlike a range-exceeded rejection (which carries the exact
  `max_allowed_ms` to chunk by), a length-exceeded rejection only ever carries
  `max_allowed_rows` — the gateway has no time range to hand back for a request that
  was never framed as one, and per the interval-in-one-place decision above, this SDK
  still won't parse the endpoint's own interval notation to compute one itself.
  `_get_live` instead **infers** the interval from real data: on a "too many rows"
  rejection, it fetches one probe chunk at the largest single-call size (returns real
  bars), infers `interval_ms` from the spacing between the two most recent ones (same
  technique `Watcher._compute_delay` already uses for poll scheduling — see below),
  then walks further chunks backward from the probe's oldest bar — same
  dedup/stop-on-an-empty-chunk shape `_fetch_chunked` uses for backtest — until enough
  rows are in hand or history runs out, then trims to exactly `length` most recent
  rows. Applies uniformly to `mode="live"`, `.watch()` (every poll, not just the
  first fetch), and `.watch_many()`, since all three funnel through the same
  `_fetch_live_quiet` → `_get_live` choke point — one fix, three entry points.
  Verified with a mocked `_request` covering: multi-chunk stitching to the exact
  requested length, stopping cleanly at the real start of history (fewer rows
  returned than requested, no padding/error), falling back to the un-chunked result
  when there's too little data to infer an interval from (<2 rows), and confirming a
  429/other-400 still propagates untouched rather than being swallowed by this path
  (`tests/test_get_live.py`). An endpoint whose interval genuinely can't be inferred
  at all (`length` unsupported there in the first place) still fails exactly as
  before — this only changes what happens once an interval-having endpoint's `length`
  exceeds the per-call cap, not endpoints that never supported `length` to begin with.

**`fetcher.get(..., mode="live", ...)` does not hold a connection open or run a
background thread.** Each call is one HTTP request, and returns immediately — it does
not loop or block. There's no WebSocket in this design (the gateway used to have one;
it was removed in favor of this — see cytrade-data's `CLAUDE.md` for why). Calling it
repeatedly is what makes a feed "live"; the gateway caches closed bars, so repeated
calls to the same feed are cheap — only bars that are genuinely new since your last
call ever trigger a real fetch from the upstream provider on the gateway's side. If
you want that repetition handled for you instead of writing the loop yourself, that's
what `.watch()` is for.

## `.watch()` — a background poller, for when you don't want to write the loop

`fetcher.watch(route, length=700, poll_interval=30, on_update=None)` returns a
`Watcher` and does **not** block — your program keeps running immediately after this
returns. Internally it's just a small `threading.Thread` calling the exact same
`get(mode="live", length=...)` on a timer; nothing new was invented for this, it's the
same request-a-fresh-window call this file already documents, just re-invoked on a
schedule instead of by hand.

- **The first fetch happens synchronously**, before `.watch()` returns — so
  `.data` is already populated (or a genuine error, e.g. `length` unsupported on this
  endpoint, has already been raised) by the time you get the `Watcher` back. This
  is deliberate fail-fast: a structurally broken call (wrong route, `length` on an
  endpoint with no inferable interval) surfaces immediately rather than silently
  starting a poller that will never succeed.
- **Every fetch *after* the first is resilient, not fail-fast** — a poll that raises
  (a network blip, a momentarily revoked key, whatever) is caught, logged via the
  fetcher's normal `[cytrade]` verbose output, and recorded in `.last_error`;
  `.data` keeps its last good value rather than going stale-to-empty, and the
  background thread keeps trying on the next `poll_interval` tick rather than dying.
  Verified by testing: revoking the API key mid-run left the thread alive and `.data`
  intact, then reactivating it let the very next poll recover on its own — no restart
  needed. This asymmetry (strict on the first call, forgiving after) mirrors how you'd
  want a long-running process to behave: refuse to start broken, but don't let a
  transient blip kill something that was already working.
- **Pull or push, your choice.** Call the returned object `live`, not `df` — it's a
  `Watcher`, not a DataFrame, precisely because its contents change over time from a
  background thread. Read `live.data` whenever you want it (a lock-protected, single,
  stable read — instant, no network wait, since the background thread already did the
  fetching; and because it's one explicit read rather than a "looks like a DataFrame"
  proxy, everything you then do with that DataFrame can't be torn across two different
  points in time by a poll landing mid-use). Or pass `on_update=my_callback` to get
  pushed a call with each new DataFrame instead — this also fires once immediately for
  the synchronous first fetch, then again on every successful background poll. A
  raising `on_update` is caught and logged the same way a failed poll is, so a bug in
  your callback can't kill the poller either.
- `live.stop()` when done, or use it as a context manager (`with fetcher.watch(...)
  as live:`) so `stop()` happens automatically.
- **`live.run_forever()`** blocks the calling thread until Ctrl+C or another
  thread calls `.stop()`, then stops cleanly. It exists because `.watch()` being
  non-blocking has one unavoidable consequence in plain Python: the background
  thread is a daemon, so it dies the instant the main thread reaches the end of the
  script — *something* has to stop that from happening, or a script whose only job
  is "watch and react" exits immediately after starting it. Pairing
  `on_update` with `.run_forever()` gets you the closest thing to zero loop-writing
  Python actually allows for this shape of program:
  `fetcher.watch(route, length=700, poll_interval=30, on_update=my_fn).run_forever()`.
  Verified: an external `.stop()` call from another thread cleanly unblocks it (this
  is what a real signal handler would do), and `on_update` keeps firing normally
  while it's blocking. **Don't use this if the calling program already has its own
  main loop** (a trading loop, a web server, ...) — that loop is already what keeps
  the process alive; just read `.data` from it directly instead.
- Deliberately *not* built: a fully async/`asyncio` version. Nothing else in this SDK
  is async (`requests`, not `httpx`), and a plain thread was enough to make this
  non-blocking without pulling in a different concurrency model just for this feature.

**Multiple feeds at once** (e.g. price + long/short ratio) is just multiple
independent `.watch()` calls — there's no multi-route API, because none is needed:
start one per route, give `on_update` to whichever one should drive your logic, and
pull `.data` from the rest inside that callback (see
`examples/multi_feed_merge.py`). **Merging them on `datetime` needs care if the
feeds are on different intervals** — a plain `.merge(on="datetime")` only matches
exact-identical timestamps, so 1-minute price merged with 1-hour ratio silently
collapses to almost nothing (verified: 120 rows of 1-min data exact-merged with 10
rows of 1-hour data produced only 2 matching rows). Use `pd.merge_asof(...,
direction="backward")` instead for mismatched intervals — matches each row to the
most recent *preceding* value in the other feed rather than requiring an exact
timestamp match; the same test with `merge_asof` correctly kept all 120 rows.

## Progress printing (`verbose=True` by default)

Every request logs to stdout as `[cytrade] ...`. `mode="live"`/`.watch()` use plain
`_log()` lines. Backtest fetching (`_get_backtest`/`_fetch_chunked` in `client.py`)
uses a richer, structured display instead — `cytrade_client/_display.py` — built
around what a chunked multi-year pull actually needs to communicate: `Fetching: ` +
the **exact, original route string** (not a paraphrased provider name — `route` is
threaded down from `.get()` untouched specifically so what's printed is always
precisely reproducible, including any special characters in param values, e.g.
ccxt's `symbol=BTC/USDT:USDT`) and date range up front, an upper-bound row estimate
and the gateway's actual per-call limit (from `CytradeAPIError.max_allowed_rows`, not
guessed), then one tree-formatted line per chunk (`├─`/`└─`) with its own row count
and range, and a final summary with total rows/requests/elapsed time — all printed
back-to-back with no blank-line spacing, deliberately, so the whole block reads as one
dense log entry per call rather than a spaced-out report. Pass `verbose=False` to
`DataFetcher(...)` to silence all of this for production/scripted use where you don't
want stdout noise.

**Color is intentionally two-tone, not per-element.** Only the `Fetching: {route}`
line is cyan+bold — everything else (date range, row estimate, the splitting notice,
every chunk line, the completion summary, coverage) is one uniform light green
(`\033[92m`), the classic "log output" color, so the eye reads the whole block as one
piece rather than hunting across several accent colors. Red is reserved for genuine
failure states (an empty chunk / no data at all) specifically so a real problem still
stands out against that otherwise-uniform green — verified directly (forced color +
inspected the raw ANSI: the header keeps `\x1b[36m\x1b[1m`, normal lines are
`\x1b[92m`, a failure line is `\x1b[31m`).

**Both color and the Unicode symbols (`→ ✓ ✗ ├─ └─`) auto-detect and fall back
safely, not just cosmetically.** Color is gated on `sys.stdout.isatty()` (plain when
piped to a file). The Unicode fallback is not optional politeness — it's a real crash
fix: this was tested and reproduced a hard `UnicodeEncodeError` crash (not just mangled
output) the first time this ran (the header used a `↓` back then, since replaced by a
plain `Fetching: ` label — the same underlying encoding problem applies to any of
these symbols), because that shell's stdout was on `cp1252`, a legacy Windows codepage
that cannot encode most of them. `_display.py` probes `sys.stdout.encoding` once at
import and swaps in plain-ASCII equivalents (`->`, `OK`, `x`, `|-`, `'-`) whenever the
real symbols can't be encoded, so this can never crash regardless of what
terminal/encoding it ends up running under.

The row-estimate line depends on the gateway including `max_allowed_rows` in a
range-exceeded 400 body (`api/main.py` in cytrade-data) — added specifically to make
this display possible, since the SDK still deliberately doesn't infer intervals
itself; without it, this line is silently omitted rather than guessed.

## No more manually-synced range constant (used to be a known gap — resolved)

There used to be a `MAX_RANGE_MS` constant here that had to be kept in sync by hand
with the gateway's own constant. It's gone — the SDK never guesses the limit anymore,
it reads the real one from the gateway's rejection response every time (see above).
This also fixed a real correctness gap, not just the duplication: the old flat 90-day
constant was unsafe for fine-grained data (90 days of 1-minute bars is 129,600 rows in
one call) and needlessly restrictive for coarse data (90 days of daily bars is 90
rows) — the gateway's per-request limit now scales with actual interval and provider
page size, and this SDK just follows whatever it's told.

## Local development against a local gateway

Internal repo, not published to PyPI — team installs pin to a git tag (see README's
Install section). While developing against a local `cytrade-data` checkout, install
this editable instead so changes are picked up without reinstalling:

```bash
pip install -e /path/to/cytrade-data-client
```

Then run the gateway (`./start.sh` in `cytrade-data`, or
`uvicorn api.main:app --port 8420`) and pass `base_url="http://localhost:8420"`
explicitly — `DataFetcher`'s default now points at the deployed gateway
(`https://api.alphaxllama.com`), so a local instance is opt-in, not the default.
There's no mock/stub server here — testing this SDK means testing it against a real
running gateway instance, which is how it was verified when built (real HTTP calls,
not just a TestClient).

## Extending this SDK

Keep it thin. Anything that needs to know a provider's param format, how to compute
an interval, how caching works, or how quota is spent belongs in the gateway
(`cytrade-data`), not here. This repo's job is: turn a route string + mode into the
right HTTP request, and turn the response into a `DataFrame` or a typed exception.
