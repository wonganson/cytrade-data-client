"""
Stress test for the cytrade-data gateway: fires a batch of requests across
several distinct endpoints, twice each (cold, then repeated) to prove out
the cache-hit speedup, then hammers the gateway with a burst of concurrent
requests to see how it holds up under real load.

Requires the gateway running and a valid API key.

Usage:
    python stress_test.py
"""

import statistics
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from cytrade_client import DataFetcher
from cytrade_client.exceptions import CytradeAPIError

API_KEY = "REDACTED-ROTATED-KEY"
BASE_URL = "http://localhost:8420"

THIRTY_DAYS_MS = 30 * 24 * 3600 * 1000

# A spread of distinct endpoints/symbols/intervals across every provider — enough
# to exercise different feeds (each gets its own cache entry) without the multi-
# minute runtime a full 3-year pull would take per endpoint.
ROUTES = {
    "bybit-direct BTC 1h": "bybit-direct|/v5/market/kline?category=linear&symbol=BTCUSDT&interval=60",
    "bybit-direct ETH 1h": "bybit-direct|/v5/market/kline?category=linear&symbol=ETHUSDT&interval=60",
    "bybit-direct SOL 15m": "bybit-direct|/v5/market/kline?category=linear&symbol=SOLUSDT&interval=15",
    "binance-direct BTC 1h": "binance-direct|/fapi/v1/klines?symbol=BTCUSDT&interval=1h",
    "binance-direct ETH 1h": "binance-direct|/fapi/v1/klines?symbol=ETHUSDT&interval=1h",
    "ccxt bybit BTC 1h": "ccxt|bybit/candle_history?symbol=BTC/USDT:USDT&interval=1h",
    "cybotrade-glassnode SOPR": "cybotrade-glassnode|indicators/sopr?a=BTC&i=1h",
    "cybotrade-cryptoquant inflow": "cybotrade-cryptoquant|btc/exchange-flows/inflow?exchange=binance&window=hour",
}


def timed_fetch(fetcher: DataFetcher, route: str, start: int, end: int):
    t0 = time.perf_counter()
    try:
        df = fetcher.get(route, mode="backtest", start_time=start, end_time=end)
        return time.perf_counter() - t0, len(df), None
    except CytradeAPIError as e:
        return time.perf_counter() - t0, 0, str(e)


def part1_cold_vs_cached(fetcher: DataFetcher, start: int, end: int) -> None:
    print("=" * 78)
    print("PART 1 — cold fetch vs. cached repeat, per endpoint")
    print("=" * 78)
    print(f"{'endpoint':<30} {'cold (s)':>10} {'cached (s)':>12} {'speedup':>10}")
    print("-" * 78)
    for label, route in ROUTES.items():
        cold_s, cold_rows, cold_err = timed_fetch(fetcher, route, start, end)
        if cold_err:
            print(f"{label:<30} FAILED: {cold_err}")
            continue
        cached_s, cached_rows, cached_err = timed_fetch(fetcher, route, start, end)
        if cached_err:
            print(f"{label:<30} cold OK, cached FAILED: {cached_err}")
            continue
        speedup = cold_s / cached_s if cached_s > 0 else float("inf")
        print(f"{label:<30} {cold_s:>10.2f} {cached_s:>12.3f} {speedup:>9.1f}x")


def part2_bulk_concurrent(fetcher_factory, start: int, end: int, n_requests: int) -> None:
    print()
    print("=" * 78)
    print(f"PART 2 — {n_requests} concurrent requests, mixed across {len(ROUTES)} already-cached endpoints")
    print("=" * 78)

    routes = list(ROUTES.values())
    jobs = [routes[i % len(routes)] for i in range(n_requests)]

    def worker(route: str):
        fetcher = fetcher_factory()  # one Session per thread, avoid cross-thread reuse
        return timed_fetch(fetcher, route, start, end)

    t0 = time.perf_counter()
    results = []
    with ThreadPoolExecutor(max_workers=n_requests) as ex:
        futures = [ex.submit(worker, route) for route in jobs]
        for f in as_completed(futures):
            results.append(f.result())
    total_elapsed = time.perf_counter() - t0

    durations = [r[0] for r in results]
    failures = [r for r in results if r[2] is not None]
    print(f"total wall time: {total_elapsed:.2f}s for {n_requests} requests")
    print(f"succeeded: {n_requests - len(failures)}/{n_requests}")
    if failures:
        print(f"sample failure: {failures[0][2]}")
    print(f"per-request latency — min: {min(durations):.3f}s  "
          f"avg: {statistics.mean(durations):.3f}s  "
          f"median: {statistics.median(durations):.3f}s  "
          f"max: {max(durations):.3f}s")


def main() -> None:
    fetcher = DataFetcher(api_key=API_KEY, base_url=BASE_URL, verbose=False)
    now = int(time.time() * 1000)
    start = now - THIRTY_DAYS_MS

    part1_cold_vs_cached(fetcher, start, now)
    part2_bulk_concurrent(
        lambda: DataFetcher(api_key=API_KEY, base_url=BASE_URL, verbose=False),
        start, now, n_requests=100,
    )


if __name__ == "__main__":
    main()
