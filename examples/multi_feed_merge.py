"""
Example: watching multiple feeds at once and merging them by datetime on each
tick — e.g. price + long/short ratio, aligned into one DataFrame for your
trading logic. Requires the gateway running and a valid API key.
"""

import pandas as pd

from cytrade_client import DataFetcher

fetcher = DataFetcher(api_key="YOUR_API_KEY", base_url="https://api.alphaxllama.com")

PRICE_ROUTE = "bybit-direct|/v5/market/kline?category=linear&symbol=BTCUSDT&interval=60"
RATIO_ROUTE = "bybit-direct|/v5/market/account-ratio?category=linear&symbol=BTCUSDT&period=1h"

# A feed you don't need a callback for — just kept fresh in the background, and
# pulled from (via .data) inside whichever feed's on_update actually drives your
# logic. Add as many of these as you have extra data sources.
ratio = fetcher.watch(RATIO_ROUTE, length=700, poll_interval=30)


def merge_nearest(base_df: pd.DataFrame, other_df: pd.DataFrame, tolerance: pd.Timedelta = None) -> pd.DataFrame:
    """
    Merge two feeds by nearest-preceding datetime, for feeds on DIFFERENT
    intervals (e.g. 1h price with daily funding) — an exact merge would drop
    almost every row, since timestamps rarely land on the exact same instant.
    `tolerance` (a pd.Timedelta) caps how far apart two rows may be and still
    match — pass one if you want to know "there's no fresh news yet" instead of
    silently pairing today's price with a much older row.
    """
    base = base_df.assign(_dt=pd.to_datetime(base_df["datetime"])).sort_values("_dt")
    other = other_df.assign(_dt=pd.to_datetime(other_df["datetime"])).sort_values("_dt")
    merged = pd.merge_asof(base, other, on="_dt", direction="backward", tolerance=tolerance, suffixes=(None, "_other"))
    return merged.drop(columns="_dt")


def on_tick(price_df: pd.DataFrame) -> None:
    # Same interval on both feeds here (1h/1h) -> an exact merge on 'datetime' is
    # correct and simpler than merge_asof. Use merge_nearest() instead (above)
    # the moment your feeds are on different intervals.
    merged = price_df.merge(ratio.data, on="datetime", how="inner", suffixes=(None, "_ratio"))

    print(f"\nmerged: {len(merged)} rows, columns={list(merged.columns)}")
    print(merged.tail(3))

    # your trading logic goes here, using `merged`


print("starting multi-feed watch (Ctrl+C to stop)...")
fetcher.watch(PRICE_ROUTE, length=700, poll_interval=30, on_update=on_tick).run_forever()
