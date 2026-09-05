"""
Example: the simplest possible live-trading loop — no Watcher, no background
thread, just a plain `while True` that calls .get(mode="live", ...) on
whatever cadence you pick. Good starting point if you want full control over
when each fetch happens (e.g. you also do other work in the same loop and
don't want a second thread involved at all).

`length=N` means "give me the last N closed bars" — the gateway resolves
that to a time range itself, and its cache means repeated calls are cheap:
only the bar(s) that closed since your last call ever trigger a real
upstream fetch. Requires the gateway running and a valid API key.
"""

import time

from cytrade_client import DataFetcher

fetcher = DataFetcher(api_key="YOUR_API_KEY", base_url="http://localhost:8420")

ROUTE = "bybit-direct|/v5/market/kline?category=linear&symbol=BTCUSDT&interval=60"
POLL_INTERVAL_S = 30  # how often THIS script re-fetches — match it to your bar interval,
                      # not shorter: interval=60 is 1h bars, so polling every 30s just
                      # re-asks the gateway for a cache hit most of the time


def on_new_data(df) -> None:
    latest = df.iloc[-1]
    print(f"[{latest['datetime']}] close={latest['close']}  (last {len(df)} bars)")

    # --- your strategy logic goes here -------------------------------------
    # e.g. compute an indicator from `df`, decide buy/sell/hold, then call out
    # to your exchange SDK (ccxt, ByBit/Binance's own SDK, ...) to place the
    # order. This SDK only gets you the data — it deliberately doesn't place
    # trades, since execution needs your own account credentials and risk
    # logic, not the read-only gateway key used here.


print("starting manual live poll (Ctrl+C to stop)...")
try:
    while True:
        df = fetcher.get(ROUTE, mode="live", length=700)
        on_new_data(df)
        time.sleep(POLL_INTERVAL_S)
except KeyboardInterrupt:
    print("stopped")
