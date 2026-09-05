"""
Example: live trading across several symbols at once, each on its own
independent poll interval — e.g. a 1h feed you check every 30s, alongside a
15m feed you check every 10s, because faster bars need fresher polling.
Each .watch() call runs its own background thread, so they never block each
other. Requires the gateway running and a valid API key.
"""

import time

from cytrade_client import DataFetcher

fetcher = DataFetcher(api_key="YOUR_API_KEY", base_url="http://localhost:8420")

# route -> poll_interval (seconds). Faster bars get polled more often; a 1h
# feed gains nothing from being checked every second, it just burns requests
# waiting on a bar that hasn't closed yet.
SYMBOLS = {
    "bybit-direct|/v5/market/kline?category=linear&symbol=BTCUSDT&interval=60": 30,
    "bybit-direct|/v5/market/kline?category=linear&symbol=ETHUSDT&interval=15": 10,
    "bybit-direct|/v5/market/kline?category=linear&symbol=SOLUSDT&interval=15": 10,
}

live_feeds = {
    route: fetcher.watch(route, length=700, poll_interval=poll_s)
    for route, poll_s in SYMBOLS.items()
}


def compute_signal(df) -> str:
    fast = df["close"].rolling(10).mean().iloc[-1]
    slow = df["close"].rolling(50).mean().iloc[-1]
    return "long" if fast > slow else "short" if fast < slow else "flat"


positions = {route: "flat" for route in SYMBOLS}

print(f"watching {len(SYMBOLS)} symbols (Ctrl+C to stop)...")
try:
    while True:
        for route, live in live_feeds.items():
            df = live.data
            if df.empty:
                continue

            signal = compute_signal(df)
            if signal != positions[route]:
                symbol = route.split("symbol=")[1].split("&")[0]
                print(f"{df['datetime'].iloc[-1]}  {symbol}: {positions[route]} -> {signal}")
                # --- place/close orders via your own exchange SDK here -----
                positions[route] = signal

        time.sleep(1)  # this loop just checks each feed's already-fresh .data —
                        # cheap regardless of how many symbols are being watched
except KeyboardInterrupt:
    for live in live_feeds.values():
        live.stop()
    print("stopped")
