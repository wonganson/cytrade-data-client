import threading
import time
from typing import TYPE_CHECKING, Callable, Dict, Optional

import pandas as pd

from . import _display

if TYPE_CHECKING:
    from .client import DataFetcher

# Once a bar closes, it never changes (see cytrade-data's CLAUDE.md) — so re-polling
# before the NEXT one is due can only ever return the exact same rows the gateway
# cache already gave us, for free, but the client still doesn't know that in advance.
# Sleeping until just after the next bar is expected to close, instead of on a fixed
# cadence, cuts that down to ~1 real check per bar instead of dozens: an hourly candle
# polled every 30s wastes about 119 of every 120 requests waiting on data that hasn't
# changed. A small cushion past the exact boundary absorbs local clock drift and
# gateway/network latency, so we don't ask a few hundred ms too early and get told the
# bar we're after still hasn't closed.
_ARRIVAL_BUFFER_S = 3.0
_MIN_POLL_S = 1.0  # floor, however tight the inferred bar spacing turns out to be


class Watcher:
    """
    A background poller for one live feed, returned by DataFetcher.watch() —
    not constructed directly. Non-blocking: your program keeps running after
    watch() returns. Read `.data` whenever you want the latest snapshot (always
    fresh, no network wait — a background thread already fetched it), or pass
    on_update to DataFetcher.watch() to get pushed a callback each time new
    data lands instead of polling `.data` yourself.

    Call it `live`, not `df` — it's a Watcher, not a DataFrame, precisely because
    its contents change over time from a background thread; `.data` is the explicit,
    lock-protected read that keeps that visible instead of hidden behind a proxy.

    Polling is interval-aware, not a fixed cadence: after each fetch, the next poll is
    scheduled for just after the NEXT bar is expected to close (inferred from the
    spacing between the two most recent bars already in the response), not blindly
    every `poll_interval` seconds. A closed bar never changes, so polling again before
    the next one is even due could only ever re-return what you already have —
    `poll_interval` is a fallback, used only when there isn't yet enough data to infer
    that spacing (e.g. the very first fetch), and as the retry cadence after a failed
    poll.

    Prints a compact session log rather than repeating a full "Fetching: {route}"
    block on every poll — a header naming the route once, then one line per update:

        [cytrade] WATCH  bybit-direct|/v5/market/kline?...&interval=60 · poll=30s · length=700
        [cytrade] Loading history... ✓
        [cytrade] Live ✓
        [cytrade] [14:00:00] ✓ 700 rows
        [cytrade] [15:00:00] ✓ 700 rows

    Use as a context manager to stop it automatically:

        with fetcher.watch(route, length=700, poll_interval=30) as live:
            while trading:
                df = live.data
                ...
    """

    def __init__(
        self,
        fetcher: "DataFetcher",
        route: str,
        length: int,
        poll_interval: float,
        on_update: Optional[Callable[[pd.DataFrame], None]],
        quiet: bool = False,
    ):
        self._fetcher = fetcher
        self._route = route
        self._length = length
        self._poll_interval = poll_interval
        self._on_update = on_update
        # Set by MultiWatcher, which constructs its own Watcher per route and prints
        # its own combined session log instead — a lone Watcher always prints for
        # itself, so `quiet` only ever comes from that one internal caller.
        self._quiet = quiet

        self._lock = threading.Lock()
        self._data = pd.DataFrame()
        self.last_error: Optional[Exception] = None
        self._next_delay = poll_interval

        self._stop_event = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _start(self) -> "Watcher":
        # The first fetch happens synchronously, in the caller's own thread — so
        # .data is already populated (or a real error already raised) by the time
        # DataFetcher.watch() returns, and a structurally broken call (bad route,
        # length unsupported on this endpoint, ...) fails fast instead of silently
        # degrading into an empty, perpetually-failing background poller.
        if not self._quiet:
            self._fetcher._line(
                _display.colored(
                    f"WATCH  {self._route} {_display.DOT} poll={self._poll_interval:g}s {_display.DOT} length={self._length:,}",
                    _display.CYAN,
                    _display.BOLD,
                )
            )
            self._fetcher._log_inline("Loading history...")
        try:
            df = self._fetcher._fetch_live_quiet(self._route, self._length)
        except Exception:
            if not self._quiet:
                self._fetcher._log_inline_done(_display.CROSS, _display.RED)
            raise
        if not self._quiet:
            self._fetcher._log_inline_done(_display.CHECK, _display.LIGHT_GREEN)
            self._fetcher._line(f"Live {_display.CHECK}", _display.LIGHT_GREEN)
        with self._lock:
            self._data = df
        self._next_delay = self._compute_delay(df)
        if not self._quiet:
            self._print_update(df)
        self._emit(df)
        self._thread.start()
        return self

    def _print_update(self, df: pd.DataFrame) -> None:
        if df.empty or "datetime" not in df.columns:
            self._fetcher._line(f"{_display.CROSS} no data", _display.RED)
            return
        ts = _display.fmt_time_only(df["datetime"].iloc[-1])
        self._fetcher._line(f"[{ts}] {_display.CHECK} {len(df):,} rows", _display.LIGHT_GREEN)

    def _compute_delay(self, df: pd.DataFrame) -> float:
        """Seconds to wait before the next poll — until just after the next bar is
        expected to close, not a fixed cadence. Needs at least 2 rows to infer the bar
        spacing; falls back to poll_interval when there isn't enough data yet (a brand
        new feed) or the inferred spacing doesn't make sense (e.g. an endpoint whose
        rows aren't evenly spaced, however unlikely for anything .watch()-able)."""
        if df is None or len(df) < 2 or "datetime" not in df.columns:
            return self._poll_interval
        try:
            times = pd.to_datetime(df["datetime"])
            interval_s = (times.iloc[-1] - times.iloc[-2]).total_seconds()
            if interval_s <= 0:
                return self._poll_interval
            next_bar_closes_at = times.iloc[-1].timestamp() + 2 * interval_s
        except (KeyError, ValueError, TypeError):
            return self._poll_interval
        return max(next_bar_closes_at - time.time() + _ARRIVAL_BUFFER_S, _MIN_POLL_S)

    @property
    def data(self) -> pd.DataFrame:
        with self._lock:
            return self._data

    def stop(self, timeout: float = 10) -> None:
        self._stop_event.set()
        if self._thread.is_alive():
            self._thread.join(timeout=timeout)

    def run_forever(self) -> None:
        """
        Block the calling thread until Ctrl+C (or another thread calls .stop()),
        then stop cleanly. For a script whose only job is "watch this feed and react
        via on_update" — nothing else going on, nothing else keeping the process
        alive. This is the one piece a background thread can never do for you in
        plain Python: something has to stop the main thread from reaching the end
        of the script, or the whole process (including this watcher) exits with it.

        Don't call this if your program already has its own main loop (a trading
        loop, a web server, ...) — that loop is already what keeps the process
        alive; just read `.data` from it directly instead of also calling this.
        """
        try:
            while not self._stop_event.wait(1):
                pass
        except KeyboardInterrupt:
            pass
        finally:
            self.stop()

    def __enter__(self) -> "Watcher":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.stop()

    def _run(self) -> None:
        # wait() returns True only if stop() set the event during the wait — False
        # means it timed out normally, i.e. "time to poll again". Interruptible, so
        # stop() doesn't have to wait out the full delay to take effect. The delay
        # itself is recomputed after every poll (see _compute_delay) — not the fixed
        # poll_interval on every iteration — so this naturally re-times itself to
        # whatever the feed's real bar spacing turns out to be.
        while not self._stop_event.wait(self._next_delay):
            self._poll()

    def _poll(self) -> None:
        # Unlike the first fetch in _start(), a failure here must not kill the
        # background thread — log it, keep the last good `.data`, try again next
        # interval. A poll failing once (a network blip, a transient 502) shouldn't
        # end the whole watch; `last_error` lets a caller notice persistent failure.
        try:
            df = self._fetcher._fetch_live_quiet(self._route, self._length)
        except Exception as e:
            self.last_error = e
            if not self._quiet:
                self._fetcher._line(f"poll failed: {e}", _display.RED)
            # Don't trust whatever delay was computed from older, possibly-stale data —
            # retry at the plain fallback cadence instead of potentially waiting out a
            # whole bar interval before finding out the feed is broken.
            self._next_delay = self._poll_interval
            return

        self.last_error = None
        with self._lock:
            self._data = df
        self._next_delay = self._compute_delay(df)
        if not self._quiet:
            self._print_update(df)
        self._emit(df)

    def _emit(self, df: pd.DataFrame) -> None:
        if self._on_update is None:
            return
        try:
            self._on_update(df)
        except Exception:
            self._fetcher._log("on_update callback raised, ignoring")


class MultiWatcher:
    """
    Watches several named routes at once, returned by DataFetcher.watch_many() — not
    constructed directly. Each route gets its own background Watcher (so each feed is
    independently interval-aware, polling at its own bar's pace), but on_update fires
    only once every feed's latest bar is for the SAME period — not on every individual
    feed's update.

    This is the point of watch_many() over running several plain .watch() calls
    yourself: feeds close at slightly different real times even on the same nominal
    interval — network latency, a slower provider round trip, one feed's poll landing
    a second before another's — so reacting on every single feed's update would run
    your strategy logic against a half-updated snapshot (e.g. today's BTC bar next to
    yesterday's ETH one) far more often than not. MultiWatcher waits for every route to
    agree before calling you:

        ROUTES = {
            "btc": "bybit-direct|/v5/market/kline?category=linear&symbol=BTCUSDT&interval=60",
            "eth": "bybit-direct|/v5/market/kline?category=linear&symbol=ETHUSDT&interval=60",
        }

        def on_update(data):
            btc, eth = data["btc"], data["eth"]
            ...

        fetcher.watch_many(ROUTES, length=700, on_update=on_update).run_forever()

    Works best when every route shares the same bar interval — that's what makes "the
    same period" a meaningful, exact datetime match. Mixing intervals (e.g. one route
    on 1h, another on 15m) means their closed bars essentially never land on the same
    timestamp, so on_update would rarely or never fire; watch each such route
    separately with its own .watch() instead.

    `.data` is a dict of the latest snapshot per route name — not necessarily aligned,
    same as reading each underlying Watcher's `.data` yourself. `.aligned_data` is the
    last snapshot where every route *did* agree — exactly what on_update was last
    called with.

    Prints one compact session log for the whole group rather than each route's own
    full block — the route list is shown once, up front, so it stays reproducible
    without repeating on every line:

        [cytrade] WATCH  2 routes · poll=30s · length=700
        [cytrade]   btc → bybit-direct|/v5/market/kline?category=linear&symbol=BTCUSDT&interval=60
        [cytrade]   eth → bybit-direct|/v5/market/kline?category=linear&symbol=ETHUSDT&interval=60
        [cytrade] Loading history... ✓ 2/2
        [cytrade] Live ✓
        [cytrade] [14:00:00] btc ✓ 700 rows │ eth ✓ 700 rows
        [cytrade] [15:00:00] btc ✓ 700 rows │ eth ✓ 700 rows
    """

    def __init__(
        self,
        fetcher: "DataFetcher",
        routes: Dict[str, str],
        length: int,
        poll_interval: float,
        on_update: Optional[Callable[[Dict[str, pd.DataFrame]], None]],
    ):
        if not routes:
            raise ValueError("watch_many() needs at least one route")
        self._fetcher = fetcher
        self._on_update = on_update

        self._lock = threading.Lock()
        self._latest: Dict[str, pd.DataFrame] = {}
        self._aligned: Dict[str, pd.DataFrame] = {}
        # An update can align (and would normally print) WHILE the construction loop
        # below is still running — the second route's synchronous first fetch often
        # lands on the same bar as the first's, satisfying alignment immediately. Only
        # this display print needs to wait: printing mid-construction would land in
        # the middle of the still-open "Loading history..." line below and garble the
        # terminal. Not gating _emit() here — the caller's on_update fires as soon as
        # data genuinely aligns, same as it always has; only our own status line waits.
        self._ready = False

        self._stop_event = threading.Event()
        self._names = list(routes.keys())

        route_word = "route" if len(routes) == 1 else "routes"
        fetcher._line(
            _display.colored(
                f"WATCH  {len(routes)} {route_word} {_display.DOT} poll={poll_interval:g}s {_display.DOT} length={length:,}",
                _display.CYAN,
                _display.BOLD,
            )
        )
        for name, route in routes.items():
            fetcher._line(f"  {name} {_display.ARROW_RIGHT} {route}", _display.LIGHT_GREEN)
        fetcher._log_inline("Loading history...")

        # Each name's Watcher fetches synchronously in turn before its own _start()
        # returns — so by the time this loop finishes every feed has real data, or
        # one has already raised, failing the whole group fast (same as a single
        # .watch() call would for a structurally broken route) rather than silently
        # starting a partially-broken group. Each sub-Watcher is quiet=True — it never
        # prints for itself; only the combined line in _print_update below does.
        try:
            self._watchers = {
                name: Watcher(
                    fetcher,
                    route,
                    length,
                    poll_interval,
                    on_update=lambda df, name=name: self._on_feed_update(name, df),
                    quiet=True,
                )._start()
                for name, route in routes.items()
            }
        except Exception:
            fetcher._log_inline_done(_display.CROSS, _display.RED)
            raise
        fetcher._log_inline_done(f"{_display.CHECK} {len(routes)}/{len(routes)}", _display.LIGHT_GREEN)
        fetcher._line(f"Live {_display.CHECK}", _display.LIGHT_GREEN)
        with self._lock:
            self._ready = True
            already_aligned = dict(self._aligned) if self._aligned else None
        # Print whatever aligned during construction now that it's safe to — the state
        # was already correct (readable via .aligned_data) the whole time, this is
        # purely catching up the display.
        if already_aligned is not None:
            self._print_update(already_aligned)

    def _on_feed_update(self, name: str, df: pd.DataFrame) -> None:
        with self._lock:
            self._latest[name] = df
            snapshot = self._check_alignment()
            ready = self._ready
        if snapshot is not None:
            if ready:
                self._print_update(snapshot)
            self._emit(snapshot)

    def _print_update(self, snapshot: Dict[str, pd.DataFrame]) -> None:
        any_df = next(iter(snapshot.values()))
        ts = _display.fmt_time_only(any_df["datetime"].iloc[-1]) if "datetime" in any_df.columns else "?"
        parts = [f"{name} {_display.CHECK} {len(snapshot[name]):,} rows" for name in self._names]
        self._fetcher._line(f"[{ts}] " + f" {_display.PIPE} ".join(parts), _display.LIGHT_GREEN)

    def _check_alignment(self) -> Optional[Dict[str, pd.DataFrame]]:
        """Call with `self._lock` held. Returns the aligned snapshot if every route's
        latest bar now agrees on the same datetime, else None (some feed hasn't caught
        up yet — nothing to do until its own next poll lands)."""
        if len(self._latest) < len(self._names):
            return None
        latest_dts = []
        for df in self._latest.values():
            if df.empty or "datetime" not in df.columns:
                return None
            latest_dts.append(df["datetime"].iloc[-1])
        if len(set(latest_dts)) != 1:
            return None
        self._aligned = dict(self._latest)
        return dict(self._aligned)

    def _emit(self, snapshot: Dict[str, pd.DataFrame]) -> None:
        if self._on_update is None:
            return
        try:
            self._on_update(snapshot)
        except Exception:
            self._fetcher._log("watch_many on_update callback raised, ignoring")

    @property
    def data(self) -> Dict[str, pd.DataFrame]:
        """Latest snapshot per route — not necessarily aligned. See `.aligned_data`."""
        with self._lock:
            return dict(self._latest)

    @property
    def aligned_data(self) -> Dict[str, pd.DataFrame]:
        """The last snapshot where every route's latest bar agreed on the same
        datetime — exactly what on_update was most recently called with."""
        with self._lock:
            return dict(self._aligned)

    @property
    def last_error(self) -> Dict[str, Exception]:
        """Route names currently failing to poll, mapped to their most recent error —
        read live from each underlying Watcher, not tracked separately. A route
        missing from this dict has either never failed or has since recovered."""
        return {name: w.last_error for name, w in self._watchers.items() if w.last_error is not None}

    def stop(self, timeout: float = 10) -> None:
        self._stop_event.set()
        for watcher in self._watchers.values():
            watcher.stop(timeout)

    def run_forever(self) -> None:
        """Block the calling thread until Ctrl+C (or another thread calls .stop()),
        then stop every underlying Watcher cleanly. See Watcher.run_forever() — same
        reasoning, just for the whole group at once."""
        try:
            while not self._stop_event.wait(1):
                pass
        except KeyboardInterrupt:
            pass
        finally:
            self.stop()

    def __enter__(self) -> "MultiWatcher":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.stop()
