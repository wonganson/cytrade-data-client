"""
Example: fetch every known bitget-direct endpoint. Uses the gateway's own catalog
(GET /v1/providers/bitget-direct/endpoints, via fetcher.endpoints()) as the source of
truth, rather than a hardcoded list here — so this always covers whatever's currently
wired up on the backend, with nothing in this file to fall out of sync as new
endpoints get added. Requires the gateway running and a valid API key.

Note: several bitget-direct endpoints (spot-whale-flow, spot-net-flow,
margin-long-short, spot-fund-flow) have no real pagination — they always return
Bitget's own current rolling window regardless of the range asked for here. See
cytrade-data's CLAUDE.md ("bitget-direct" section) for exactly which.
"""

import time

from cytrade_client import DataFetcher

fetcher = DataFetcher(api_key="YOUR_API_KEY", base_url="https://api.alphaxllama.com")

PROVIDER = "bitget-direct"
now = int(time.time() * 1000)
one_week_ago = now - 7 * 24 * 3600 * 1000


def main() -> None:
    catalog = fetcher.endpoints(PROVIDER)
    endpoints = catalog["endpoints"]
    print(f"{PROVIDER}: {len(endpoints)} known endpoints\n")

    for entry in endpoints:
        print(f"--- {entry['path']} ---")
        print(entry["description"])
        print("route:", entry["example_route"])
        print("supports_length:", entry["supports_length"], " max_page_size:", entry["max_page_size"])
        try:
            df = fetcher.get(entry["example_route"], mode="backtest", start_time=one_week_ago, end_time=now)
            print(f"OK: {len(df)} rows, columns={list(df.columns)}")
            if not df.empty:
                print(df.tail(2))
        except Exception as e:
            print(f"FAILED: {e}")
        print()


if __name__ == "__main__":
    main()
