"""
Example: fetch 3 years of historical data for every provider you have access
to, and save each one to its own CSV. This is both a template for your own
backtest data pulls and a good end-to-end check that the gateway, the SDK,
and your API key are all wired up correctly.

Requires the gateway running (see the cytrade-data repo's `main.py`/`start.sh`)
and a valid API key (create one there with `python manage_keys.py add "name"`).

Usage:
    python examples/fetch_all_providers_to_csv.py
"""

import time
from pathlib import Path

from cytrade_client import DataFetcher
from cytrade_client.exceptions import CytradeAPIError

API_KEY = "G76J5VDEANQ6mobYGNsQzoJ57ZAcGwg9ZROGo17oAgk"
BASE_URL = "http://localhost:8420"

OUTPUT_DIR = Path(__file__).resolve().parent / "output"
THREE_YEARS_MS = 5 * 365 * 24 * 3600 * 1000

# One route per provider. A route is "provider|path?param=value&param2=value2" —
# the same string format the gateway itself uses, so anything that works here
# with fetcher.get(route) also works as a raw call against /v1/fetch. Edit the
# symbols/params below to match what you actually want; these are just examples.
ROUTES = {
    # "bybit-direct": "bybit-direct|/v5/market/kline?category=linear&symbol=BTCUSDT&interval=60",
    "ccxt": "ccxt|bybit/candle_history?symbol=BTC/USDT:USDT&interval=1h",
    # "cybotrade-glassnode": "cybotrade-glassnode|indicators/sopr?a=BTC&i=1h",
    # "cybotrade-cryptoquant": "cybotrade-cryptoquant|btc/exchange-flows/inflow?exchange=binance&window=hour",
}

def main() -> None:
    fetcher = DataFetcher(api_key=API_KEY, base_url=BASE_URL)
    OUTPUT_DIR.mkdir(exist_ok=True)

    while True:
        now = int(time.time() * 1000)
        three_years_ago = now - THREE_YEARS_MS

        for label, route in ROUTES.items():
            print(f"Fetching {label}...")

            try:
                df = fetcher.get(
                    route,
                    mode="backtest",
                    start_time=three_years_ago,
                    end_time=now,
                )

                out_path = OUTPUT_DIR / f"{label}.csv"
                df.to_csv(out_path, index=False)

                print(f"OK: {len(df)} rows")

            except CytradeAPIError as e:
                print(f"FAILED: [{e.status_code}] {e.detail}")

        print("Cycle completed. Restarting...\n")

        time.sleep(5)


if __name__ == "__main__":
    main()