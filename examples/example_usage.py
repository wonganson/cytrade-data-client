"""
Example: the same route string, fetched two ways — a historical range for a
backtest, and a feed that stays fresh on its own for live use. Requires the
gateway running (see the cytrade-data repo's start.sh) and a valid API key.
"""

import time

from cytrade_client import DataFetcher

fetcher = DataFetcher(api_key="YOUR_API_KEY", base_url="http://localhost:8420")

ROUTE = "bybit-direct|/v5/market/kline?category=linear&symbol=BTCUSDT&interval=60"

# --- backtest: a fixed historical range -----------------------------------
now = int(time.time() * 1000)
one_week_ago = now - 7 * 24 * 3600 * 1000

df = fetcher.get(ROUTE, mode="backtest", start_time=one_week_ago, end_time=now)
print("backtest:", len(df), "rows")
print(df.tail(3))

# --- live: a background poller you pull the latest data from ---------------
# .watch() starts immediately (non-blocking) and returns a Watcher, not a
# DataFrame — call it `live`, not `df`, since its contents change over time from
# a background thread. live.data is a lock-protected, single, stable read: grab
# it, and everything you then do with that DataFrame is safe from being torn
# across two different points in time by a poll landing mid-use.
live = fetcher.watch(ROUTE, length=700, poll_interval=30)

print("\nwatching live (Ctrl+C to stop)...")
try:
    while True:
        df = live.data
        print(f"live: {len(df)} rows, latest close={df['close'].iloc[-1] if not df.empty else 'n/a'}")
        time.sleep(5)  # how often *this script* checks in — independent of poll_interval
except KeyboardInterrupt:
    live.stop()
