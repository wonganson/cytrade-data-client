"""
Example: live trading with .watch() in "pull" style — a background thread
keeps the feed fresh, and YOUR trading loop decides when to read it via
`.data`. Use this when you already have (or want) your own loop structure —
e.g. it also checks account balance, manages open positions, handles
multiple unrelated things per tick — and .watch() should just be one fresh
data source it reads from, not something driving the loop itself.

Contrast with live_watch_push.py, where new data arriving IS what drives
the loop (on_update callback + run_forever(), no loop code of your own).
Requires the gateway running and a valid API key.
"""

import time

from cytrade_client import DataFetcher

fetcher = DataFetcher(api_key="YOUR_API_KEY", base_url="https://api.alphaxllama.com")

ROUTE = "bybit-direct|/v5/market/kline?category=linear&symbol=BTCUSDT&interval=60"

# Starts immediately (non-blocking) — by the time this line returns, live.data
# is already populated from a first synchronous fetch. A background thread then
# re-fetches every poll_interval seconds and keeps .data current.
live = fetcher.watch(ROUTE, length=700, poll_interval=30)


def compute_signal(df) -> str:
    # Toy example: simple moving-average crossover. Replace with your own logic.
    fast = df["close"].rolling(10).mean().iloc[-1]
    slow = df["close"].rolling(50).mean().iloc[-1]
    if fast > slow:
        return "long"
    if fast < slow:
        return "short"
    return "flat"


position = "flat"

print("starting live trading loop (Ctrl+C to stop)...")
try:
    while True:
        df = live.data  # instant, lock-protected snapshot — no network wait here

        if live.last_error is not None:
            # The background poll failed (network blip, transient 502, ...) —
            # .data still holds the last good snapshot, so trading can continue
            # on slightly stale data, or you can choose to pause here instead.
            print(f"warning: last poll failed: {live.last_error}")

        if not df.empty:
            signal = compute_signal(df)
            if signal != position:
                print(f"{df['datetime'].iloc[-1]}  signal changed: {position} -> {signal}")
                # --- place/close orders via your own exchange SDK here -----
                position = signal

        time.sleep(5)  # how often THIS loop checks in — independent of poll_interval
except KeyboardInterrupt:
    live.stop()
    print("stopped")
