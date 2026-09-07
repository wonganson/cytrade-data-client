import time
from datetime import datetime, timezone
from typing import Callable, Dict, Optional, Tuple, Union
from urllib.parse import parse_qsl

import pandas as pd
import requests

from . import _display
from .exceptions import CytradeAPIError, QuotaExceededError
from .watcher import MultiWatcher, Watcher

DEFAULT_BASE_URL = "https://api.alphaxllama.com"  # the deployed gateway; pass base_url="http://localhost:8420" to hit a local checkout instead
DEFAULT_RANGE_MS = 7 * 24 * 3600 * 1000  # used only when start_time/end_time are both omitted


def _parse_route(route: str) -> Tuple[str, str, Dict[str, str]]:
    """'provider|path?k=v&k2=v2' -> (provider, path, params)."""
    if "|" not in route:
        raise ValueError(f"Route must be 'provider|path?params', got: '{route}'")
    provider, path_query = route.split("|", 1)
    path, _, query = path_query.partition("?")
    params = dict(parse_qsl(query, keep_blank_values=True)) if query else {}
    return provider.strip(), path.strip(), params


def _fmt_ts(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


_TIME_STRING_FORMATS = ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S")


def _parse_time(value: Optional[Union[int, str]], *, param_name: str) -> Optional[int]:
    """Normalize a `start_time`/`end_time` argument to milliseconds since epoch.

    Accepts an `int` unchanged (the original, still-supported contract — nothing
    passing raw ms breaks), or a human-readable string: `"now"` (case-insensitive),
    `"YYYY-MM-DD"`, `"YYYY-MM-DD HH:MM:SS"`, or `"YYYY-MM-DDTHH:MM:SS"`. A date-only
    string means midnight of that day. String inputs are always interpreted as
    **UTC**, deliberately — never the caller's local system timezone — so
    `start_time="2023-01-01"` means the same real moment regardless of where the
    calling machine happens to be, which matters for a quant data SDK where a
    silently-shifted range would be a correctness bug, not a display quirk.
    """
    if value is None or isinstance(value, int):
        return value
    if isinstance(value, str):
        text = value.strip()
        if text.lower() == "now":
            return int(time.time() * 1000)
        for fmt in _TIME_STRING_FORMATS:
            try:
                return int(datetime.strptime(text, fmt).replace(tzinfo=timezone.utc).timestamp() * 1000)
            except ValueError:
                continue
        raise ValueError(
            f"{param_name}={value!r} isn't a recognized format — use an int (ms since epoch), "
            f"'YYYY-MM-DD', 'YYYY-MM-DD HH:MM:SS', 'YYYY-MM-DDTHH:MM:SS', or 'now'"
        )
    raise TypeError(f"{param_name} must be an int, str, or None — got {type(value).__name__}")


class DataFetcher:
    """
    Client for the Cytrade data gateway. One call shape for both backtest and
    live use, so switching a strategy from one to the other is a one-line change:

        df = fetcher.get(
            "bybit-direct|/v5/market/kline?category=linear&symbol=BTCUSDT&interval=60",
            mode="backtest", start_time=one_week_ago, end_time=now,
        )

        df = fetcher.get(
            "bybit-direct|/v5/market/kline?category=linear&symbol=BTCUSDT&interval=60",
            mode="live", length=700,
        )

    mode="live" doesn't hold a connection open — each call is a single request that
    asks the gateway for the last `length` bars. Call it again (e.g. in your own loop,
    on whatever cadence you want) to get an up-to-date DataFrame; the gateway's cache
    means repeated calls are cheap; only the newest bar(s) since your last call ever
    trigger a real fetch from the underlying provider.

    `start_time`/`end_time` (backtest only) accept either an `int` (ms since epoch,
    the original contract — still works unchanged) or a human-readable string:
    `"2023-01-01"`, `"2023-01-01 00:00:00"`, `"2023-01-01T00:00:00"`, or `"now"`
    (case-insensitive). String inputs are always interpreted as UTC, never the
    calling machine's local timezone — see `_parse_time` for why that matters here.

    Set verbose=False to silence the progress lines this prints for every request.
    """

    def __init__(self, api_key: str, base_url: str = DEFAULT_BASE_URL, timeout: float = 30.0, verbose: bool = True):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.verbose = verbose
        self._session = requests.Session()
        self._session.headers.update({"X-API-Key": api_key, "Content-Type": "application/json"})

    def get(
        self,
        route: str,
        mode: str = "backtest",
        start_time: Optional[Union[int, str]] = None,
        end_time: Optional[Union[int, str]] = None,
        length: Optional[int] = None,
    ) -> pd.DataFrame:
        provider, path, params = _parse_route(route)

        if mode == "backtest":
            if length is not None:
                raise ValueError("length is only used with mode='live' — backtest uses start_time/end_time")
            start_time = _parse_time(start_time, param_name="start_time")
            end_time = _parse_time(end_time, param_name="end_time")
            return self._get_backtest(route, provider, path, params, start_time, end_time)

        if mode == "live":
            if start_time is not None or end_time is not None:
                raise ValueError("start_time/end_time are only used with mode='backtest' — live uses length")
            if length is None:
                raise ValueError("mode='live' requires length (the number of most recent bars to return)")
            started_at = time.perf_counter()
            self._header_live(route, length)
            df = self._get_live(provider, path, params, length, verbose_note=True)
            self._footer_live(df, time.perf_counter() - started_at)
            return df

        raise ValueError(f"Unknown mode '{mode}', expected 'backtest' or 'live'")

    def _fetch_live_quiet(self, route: str, length: int) -> pd.DataFrame:
        """Same request as .get(mode="live", ...), but silent — no header/footer lines.
        Watcher/MultiWatcher use this and print their own compact, session-style output
        instead (see Watcher._start/_poll) — printing the full "Fetching: {route} /
        length / rows" block on every single poll of a long-running watch would be far
        noisier than the one-line-per-update format a live session actually wants."""
        provider, path, params = _parse_route(route)
        return self._get_live(provider, path, params, length, verbose_note=False)

    def _get_live(
        self,
        provider: str,
        path: str,
        params: Dict[str, str],
        length: int,
        verbose_note: bool,
    ) -> pd.DataFrame:
        """The `length` counterpart to `_get_backtest`'s chunking — same "the caller
        asks for what they want, the SDK handles however many round trips that takes"
        contract, just for "last N bars" instead of an explicit range.

        The gateway resolves `length` into a time range itself (see client.py's module
        docstring / CLAUDE.md — this SDK deliberately doesn't duplicate that parsing),
        so unlike a range-exceeded rejection, a length-exceeded rejection only carries
        `max_allowed_rows`, never `max_allowed_ms` — there's no interval for the SDK to
        chunk by until it has real data to infer one from. So: try the full request
        first; on a "too many rows" rejection, fetch one probe chunk at the largest
        allowed size (real bars, so the interval can be inferred from their spacing —
        same technique Watcher._compute_delay already uses), then walk further chunks
        backward from the probe's oldest bar — same dedup/stop-on-empty shape
        `_fetch_chunked` uses for backtest — until enough rows are in hand or history
        runs out, and trim to exactly `length` most recent rows.
        """
        try:
            return self._request(provider, path, params, length=length)
        except CytradeAPIError as e:
            if e.max_allowed_rows is None or length <= e.max_allowed_rows:
                raise  # not a "too many rows" rejection, or nothing here to chunk
            max_rows = e.max_allowed_rows

        probe = self._request(provider, path, params, length=max_rows)
        if len(probe) < 2 or "datetime" not in probe.columns:
            return probe  # can't infer bar spacing from <2 rows — best effort, hand back what we have

        times = pd.to_datetime(probe["datetime"])
        interval_ms = int((times.iloc[-1] - times.iloc[-2]).total_seconds() * 1000)
        if interval_ms <= 0:
            return probe  # non-evenly-spaced rows — same fallback Watcher._compute_delay uses

        if verbose_note:
            self._line(
                f"  length={length:,} exceeds {max_rows:,}/call — stitching requests",
                _display.LIGHT_GREEN,
            )

        oldest_ms = int(times.iloc[0].timestamp() * 1000)
        remaining = length - len(probe)
        start_time = oldest_ms - remaining * interval_ms
        chunk_end = oldest_ms
        frames = [probe]

        # Walk backward exactly like _fetch_chunked — an empty chunk means we've
        # reached the start of this symbol's history, so stop instead of firing off
        # further, guaranteed-empty requests.
        while remaining > 0 and chunk_end > start_time:
            chunk_start = max(chunk_end - max_rows * interval_ms, start_time)
            df = self._request(provider, path, params, start_time=chunk_start, end_time=chunk_end)
            if df.empty:
                break
            frames.append(df)
            remaining -= len(df)
            chunk_end = chunk_start

        combined = pd.concat(frames, ignore_index=True)
        if "datetime" in combined.columns:
            combined = combined.drop_duplicates(subset="datetime").sort_values("datetime").reset_index(drop=True)
        return combined.tail(length).reset_index(drop=True)

    def watch(
        self,
        route: str,
        length: int,
        poll_interval: float = 30.0,
        on_update: Optional[Callable[[pd.DataFrame], None]] = None,
    ) -> Watcher:
        """
        Start a background poller for `route` — non-blocking, your program keeps
        running after this returns. The first fetch happens synchronously (so
        `.data` is already populated, or a real error already raised, by the time
        this call returns); a background thread then keeps `.data` fresh. Pass
        on_update to get pushed a callback with each new DataFrame instead of reading
        `.data` yourself.

        Polling is interval-aware, not a fixed `poll_interval` cadence: a closed bar
        never changes, so once the latest one is in hand, polling again before the
        NEXT bar is even due can only ever re-return exactly what's already cached —
        wasted requests, not fresher data. Each poll instead schedules the next one
        for just after the next bar is expected to close (inferred from the spacing
        between the two most recent bars already returned). `poll_interval` becomes a
        fallback: used only before there's enough data to infer that spacing (the
        very first fetch), and as the retry cadence after a failed poll.

        The returned object is a `Watcher`, not a DataFrame — call it `live`, not
        `df`, since its contents change over time from a background thread. `.data`
        is a lock-protected, single, stable read: everything you do with it is safe
        from being torn across two different points in time by a poll landing
        mid-use, which a "make it look like a DataFrame" proxy could not guarantee.

            live = fetcher.watch(route, length=700, poll_interval=30)
            ...
            df = live.data   # always the latest snapshot, instant
            ...
            live.stop()

        Or as a context manager, to stop it automatically:

            with fetcher.watch(route, length=700, poll_interval=30) as live:
                while trading:
                    df = live.data

        If your script has nothing else to do but watch and react — no other loop
        keeping it alive — pair this with on_update and .run_forever() so you never
        write a loop at all, just what happens per update:

            fetcher.watch(route, length=700, poll_interval=30, on_update=my_fn).run_forever()
        """
        return Watcher(self, route, length, poll_interval, on_update)._start()

    def watch_many(
        self,
        routes: Dict[str, str],
        length: int,
        poll_interval: float = 30.0,
        on_update: Optional[Callable[[Dict[str, pd.DataFrame]], None]] = None,
    ) -> MultiWatcher:
        """
        Like .watch(), but for several named routes at once — each gets its own
        independent, interval-aware background poller, but on_update only fires once
        every route's latest bar agrees on the SAME datetime, not on each individual
        feed's update. Feeds close at slightly different real times even on the same
        nominal interval (network latency, one provider being a little slower) — so
        reacting per-feed would hand your strategy a half-updated snapshot (e.g.
        today's BTC bar next to yesterday's ETH one) far more often than a genuinely
        synchronized one:

            ROUTES = {
                "btc": "bybit-direct|/v5/market/kline?category=linear&symbol=BTCUSDT&interval=60",
                "eth": "bybit-direct|/v5/market/kline?category=linear&symbol=ETHUSDT&interval=60",
            }

            def on_update(data):
                btc, eth = data["btc"], data["eth"]
                ...

            fetcher.watch_many(ROUTES, length=700, on_update=on_update).run_forever()

        Works best when every route shares the same bar interval — that's what makes
        "the same period" a meaningful, exact datetime match; mixing intervals means
        on_update would rarely or never fire, so watch those separately with .watch()
        instead. `.data` (per-route, not necessarily aligned) and `.aligned_data`
        (the last snapshot that WAS aligned) are both available for pull-style use,
        same as `.data` on a plain Watcher.
        """
        return MultiWatcher(self, routes, length, poll_interval, on_update)

    def available_providers(self):
        resp = self._session.get(f"{self.base_url}/v1/providers", timeout=self.timeout)
        self._raise_for_status(resp)
        return resp.json()["providers"]

    def limits(self):
        """The gateway's current per-provider page sizes and round-trip budget — see /v1/limits."""
        resp = self._session.get(f"{self.base_url}/v1/limits", timeout=self.timeout)
        self._raise_for_status(resp)
        return resp.json()

    def endpoints(self, provider: str):
        """
        Known endpoints for a 'raw' passthrough provider (bybit-direct, binance-direct) —
        each one's path, params, an example route you can copy directly, and its output
        columns. Providers with one uniform calling convention (ccxt, the Cybotrade-backed
        ones) return a `note` explaining that convention instead of a per-path list.
        """
        resp = self._session.get(f"{self.base_url}/v1/providers/{provider}/endpoints", timeout=self.timeout)
        self._raise_for_status(resp)
        return resp.json()

    def _get_backtest(
        self,
        route: str,
        provider: str,
        path: str,
        params: Dict[str, str],
        start_time: Optional[int],
        end_time: Optional[int],
    ) -> pd.DataFrame:
        now = int(time.time() * 1000)
        end_time = now if end_time is None else end_time
        start_time = end_time - DEFAULT_RANGE_MS if start_time is None else start_time
        started_at = time.perf_counter()

        try:
            df = self._request(provider, path, params, start_time=start_time, end_time=end_time)
        except CytradeAPIError as e:
            if e.max_allowed_ms is None:
                raise  # not a "range too large" rejection — nothing to chunk, surface it as-is
            chunk_ms, max_rows_per_chunk = e.max_allowed_ms, e.max_allowed_rows  # unbound once `except` ends
        else:
            # Fit in one call — no chunking needed, so no chunk breakdown to show, just
            # the same header/footer shape as the chunked path for a consistent look.
            self._header(route, start_time, end_time)
            self._footer(len(df), 1, time.perf_counter() - started_at, start_time, end_time)
            return df

        # The gateway told us exactly how large a single call is allowed to be for this
        # specific provider/interval (it isn't a fixed number — see /v1/limits) — split
        # into chunks of that size and stitch the results back into one DataFrame.
        return self._fetch_chunked(
            route, provider, path, params, start_time, end_time, chunk_ms, max_rows_per_chunk, started_at
        )

    def _fetch_chunked(
        self,
        route: str,
        provider: str,
        path: str,
        params: Dict[str, str],
        start_time: int,
        end_time: int,
        chunk_ms: int,
        max_rows_per_chunk: Optional[int],
        started_at: float,
    ) -> pd.DataFrame:
        max_chunks = -(-(end_time - start_time) // chunk_ms)  # ceiling division

        self._header(route, start_time, end_time)
        if max_rows_per_chunk is not None:
            # An upper bound, not a precise count: the SDK deliberately doesn't know the
            # provider's actual bar interval (that stays gateway-side — see CLAUDE.md), so
            # it can't know exactly how many rows exist in the range ahead of time, only
            # the most a single chunk could ever return.
            self._line(
                f"  up to {max_rows_per_chunk * max_chunks:,} records  |  "
                f"API limit: {max_rows_per_chunk:,}/request",
                _display.LIGHT_GREEN,
            )
        self._line(f"  Splitting range {_display.ARROW_RIGHT} {max_chunks} requests", _display.LIGHT_GREEN)

        # Newest chunk first, walking backward toward start_time — not just for symmetry
        # with how the gateway/providers themselves paginate (also newest-to-oldest), but
        # because it makes "ran out of history" detectable and cheap: once a chunk comes
        # back empty, every chunk further back is guaranteed empty too (there's nothing
        # before the start of a symbol's history), so we stop instead of burning requests
        # and quota on chunks we already know will be empty.
        frames = []
        chunk_end = end_time
        chunk_num = 1
        while chunk_end > start_time:
            chunk_start = max(chunk_end - chunk_ms, start_time)
            chunk_started_at = time.perf_counter()
            df = self._request(provider, path, params, start_time=chunk_start, end_time=chunk_end)
            chunk_elapsed = time.perf_counter() - chunk_started_at
            will_continue = not df.empty and chunk_start > start_time
            connector = _display.TREE_MID if will_continue else _display.TREE_END

            if df.empty:
                self._line(
                    f"  {connector} {chunk_num}/{max_chunks} {_display.CROSS} empty — reached start of history, stopping"
                    f"  ({chunk_elapsed:.2f}s)",
                    _display.RED,
                )
                break

            # The elapsed time here is the tell for whether the gateway's cache actually
            # served this chunk or had to hit the provider for real — a cache hit is
            # consistently ~0.05-0.3s; a real upstream fetch is consistently 1s+. Printed
            # per chunk (not just a total) specifically so a re-run of the same/overlapping
            # range makes it visible WHICH chunks were cheap and which weren't, rather than
            # one aggregate number that hides a mix of both.
            frames.append(df)
            self._line(
                f"  {connector} {chunk_num}/{max_chunks} {_display.CHECK} {len(df):>6,}   "
                f"{_display.fmt_date(chunk_start)} {_display.ARROW_RIGHT} {_display.fmt_date(chunk_end)}"
                f"   ({chunk_elapsed:.2f}s)",
                _display.LIGHT_GREEN,
            )
            chunk_end = chunk_start
            chunk_num += 1
            if not will_continue:
                break

        if not frames:
            self._line(f"{_display.CROSS} No data in range", _display.RED)
            return pd.DataFrame()

        combined = pd.concat(frames, ignore_index=True)
        if "datetime" in combined.columns:
            combined = combined.drop_duplicates(subset="datetime").sort_values("datetime").reset_index(drop=True)

        self._footer(len(combined), len(frames), time.perf_counter() - started_at, start_time, end_time)
        return combined

    def _header(self, route: str, start_time: int, end_time: int) -> None:
        # The exact route as passed to .get() — not a paraphrase — so what's printed is
        # always precisely reproducible: copy this line, it's a valid call on its own.
        self._line(_display.colored(f"Fetching: {route}", _display.CYAN, _display.BOLD))
        self._line(
            f"  {_display.fmt_date(start_time)} {_display.ARROW_RIGHT} {_display.fmt_date(end_time)}  |  backtest",
            _display.LIGHT_GREEN,
        )

    def _footer(self, rows: int, n_requests: int, elapsed: float, start_time: int, end_time: int) -> None:
        request_word = "request" if n_requests == 1 else "requests"
        summary = f"{_display.CHECK} Complete | {rows:,} rows | {n_requests} {request_word} | {elapsed:.1f}s"
        self._line(summary, _display.LIGHT_GREEN)
        self._line(
            f"  Coverage: {_display.fmt_date(start_time)} {_display.ARROW_RIGHT} {_display.fmt_date(end_time)}",
            _display.LIGHT_GREEN,
        )

    def _header_live(self, route: str, length: int) -> None:
        # Same route-header shape as backtest's _header — the point is one consistent
        # visual language regardless of mode, so a live trading log reads the same way
        # a backtest log does. length is known before the request; the actual covered
        # range isn't (a live "last N bars" window has no fixed start/end going in,
        # unlike backtest's), so that part waits for _footer_live once data is back.
        self._line(_display.colored(f"Fetching: {route}", _display.CYAN, _display.BOLD))
        self._line(f"  length={length:,}  |  live", _display.LIGHT_GREEN)

    def _footer_live(self, df: pd.DataFrame, elapsed: float) -> None:
        if df.empty or "datetime" not in df.columns:
            self._line(f"{_display.CROSS} No data", _display.RED)
            return
        first_dt = _display.fmt_dt_str(df["datetime"].iloc[0])
        last_dt = _display.fmt_dt_str(df["datetime"].iloc[-1])
        self._line(
            f"{_display.CHECK} {len(df):,} rows | {first_dt} {_display.ARROW_RIGHT} {last_dt} | {elapsed:.2f}s",
            _display.LIGHT_GREEN,
        )

    def _line(self, text: str, *codes: str) -> None:
        """`text` may already be pre-colored (nothing further to apply) or plain (apply `codes`)."""
        if not self.verbose:
            return
        print(f"[cytrade] {_display.colored(text, *codes)}")

    def _log_inline(self, text: str) -> None:
        """Start a line without ending it — for a "Loading history..." style prefix
        that a matching `_log_inline_done` finishes on the same line once the work it
        describes completes (`WATCH  ✓` on one line reads as a session status, not two
        disconnected log lines)."""
        if not self.verbose:
            return
        print(f"[cytrade] {_display.colored(text, _display.LIGHT_GREEN)}", end="", flush=True)

    def _log_inline_done(self, mark: str, *codes: str) -> None:
        """Finish a line started by `_log_inline` — always call exactly one of these
        per `_log_inline`, success or failure, or the terminal is left mid-line."""
        if not self.verbose:
            return
        print(f" {_display.colored(mark, *codes)}")

    def _request(
        self,
        provider: str,
        path: str,
        params: Dict[str, str],
        start_time: Optional[int] = None,
        end_time: Optional[int] = None,
        length: Optional[int] = None,
    ) -> pd.DataFrame:
        body = {"provider": provider, "path": path, "params": params}
        if length is not None:
            body["length"] = length
        else:
            if start_time is not None:
                body["start_time"] = start_time
            if end_time is not None:
                body["end_time"] = end_time

        resp = self._session.post(f"{self.base_url}/v1/fetch", json=body, timeout=self.timeout)
        self._raise_for_status(resp)
        return pd.DataFrame(resp.json()["data"])

    def _raise_for_status(self, resp: requests.Response) -> None:
        if resp.status_code == 200:
            return
        try:
            body = resp.json().get("detail", resp.text)
        except ValueError:
            body = resp.text

        if isinstance(body, dict):
            message = body.get("message", str(body))
            max_allowed_ms = body.get("max_allowed_ms")
            max_allowed_rows = body.get("max_allowed_rows")
        else:
            message, max_allowed_ms, max_allowed_rows = body, None, None

        error_cls = QuotaExceededError if resp.status_code == 429 else CytradeAPIError
        raise error_cls(resp.status_code, message, max_allowed_ms=max_allowed_ms, max_allowed_rows=max_allowed_rows)

    def _log(self, message: str) -> None:
        if self.verbose:
            print(f"[cytrade] {message}")
