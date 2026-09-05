"""
Example: live trading across several symbols where your strategy needs them all
in sync — e.g. a pairs trade or a cross-asset signal that only makes sense once
every symbol's latest bar is for the SAME period. Plain .watch() per symbol
(see live_multi_symbol.py) reacts to each feed independently, which can hand
your logic a half-updated snapshot when one feed's poll lands a second before
another's. watch_many() waits for every route to agree before calling you.

Requires the gateway running and a valid API key.
"""

from cytrade_client import DataFetcher

fetcher = DataFetcher(api_key="REDACTED-ROTATED-KEY", base_url="http://localhost:8420")

ROUTES = {
    "btc": "bybit-direct|/v5/market/kline?category=linear&symbol=BTCUSDT&interval=1",
    "eth": "bybit-direct|/v5/market/kline?category=linear&symbol=ETHUSDT&interval=1",
    "sol": "bybit-direct|/v5/market/kline?category=linear&symbol=SOLUSDT&interval=1",
}


def on_update(data) -> None:
    btc, eth, sol = data["btc"], data["eth"], data["sol"]
    print(f"[{btc['datetime'].iloc[-1]}] BTC close={btc['close'].iloc[-1]}  ETH close={eth['close'].iloc[-1]}  SOL close={sol['close'].iloc[-1]}")

    # --- your cross-asset strategy logic goes here, using both DataFrames -----
    # e.g. ratio = btc['close'].iloc[-1] / eth['close'].iloc[-1]; compare to its
    # own moving average, trade the spread — safe to do here because both
    # symbols' latest bar is guaranteed to be for the same period.


print("starting synchronized multi-symbol watch (Ctrl+C to stop)...")
fetcher.watch_many(ROUTES, length=700, poll_interval=5, on_update=on_update).run_forever()
