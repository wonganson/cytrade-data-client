"""
Example: live trading in "push" style — zero loop code of your own. You give
.watch() an on_update callback and call .run_forever(); every time the
background poller fetches a fresh bar, your callback runs. Use this when
reacting to new data IS your whole program — nothing else needs to happen
between updates.

Contrast with live_watch_pull.py, where you keep your own loop and just read
.data from it whenever you decide to check. Requires the gateway running and
a valid API key.
"""

from cytrade_client import DataFetcher

fetcher = DataFetcher(api_key="REDACTED-ROTATED-KEY", base_url="http://localhost:8420")

ROUTE = "bybit-direct|/v5/market/kline?category=linear&symbol=BTCUSDT&interval=60"

position = "flat"


def place_order(side: str) -> None:
    # Placeholder — wire this up to your own exchange SDK (ccxt, or Bybit/
    # Binance's official SDK) using your OWN trading API keys. This gateway's
    # API key is read-only/data-only by design: it fetches market data, it
    # never touches your exchange account or places orders.
    print(f"  -> would place order: {side}")


def on_update(df) -> None:
    global position

    if df.empty:
        return

    fast = df["close"].rolling(10).mean().iloc[-1]
    slow = df["close"].rolling(50).mean().iloc[-1]
    signal = "long" if fast > slow else "short" if fast < slow else "flat"

    latest = df.iloc[-1]
    print(f"[{latest['datetime']}] close={latest['close']}  fast={fast:.2f}  slow={slow:.2f}  signal={signal}")

    if signal != position and signal != "flat":
        place_order(signal)
        position = signal


print("starting live trading (push mode, Ctrl+C to stop)...")
fetcher.watch(ROUTE, length=700, poll_interval=5, on_update=on_update).run_forever()
