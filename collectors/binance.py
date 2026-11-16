"""
PumpGuard: Binance USD-M perpetual futures collector.

Takes one hourly snapshot of every USDT perpetual on Binance and appends it
to a daily CSV file in data/binance/. Uses only public endpoints, so no API
key is needed.

What it saves for each coin:
  - price, 24h price change, 24h futures volume and 24h spot volume
  - funding rate and funding interval
  - open interest (coins and USD) and Binance's circulating supply figure
  - top-trader long/short ratio (positions) and all-account long/short ratio
  - taker buy/sell volume ratio

Run:  python collectors/binance.py
"""

from __future__ import annotations

import csv
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

FAPI = "https://fapi.binance.com"
SPOT = "https://api.binance.com"
DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "binance"

# Binance allows about 1,000 requests per 5 minutes on the /futures/data
# endpoints. We make 4 per coin, so we pace ourselves to stay well under.
REQUEST_PAUSE_SECONDS = 0.35
TIMEOUT_SECONDS = 15
MAX_RETRIES = 3

FIELDS = [
    "snapshot_utc",
    "symbol",
    "base_asset",
    "price",
    "price_change_pct_24h",
    "futures_quote_volume_24h",
    "spot_quote_volume_24h",
    "futures_to_spot_volume",
    "funding_rate",
    "funding_interval_hours",
    "open_interest",
    "open_interest_usd",
    "circulating_supply",
    "market_cap_usd",
    "oi_to_market_cap",
    "top_trader_long_short_ratio",
    "all_account_long_short_ratio",
    "taker_buy_sell_ratio",
]

session = requests.Session()
session.headers["User-Agent"] = "pumpguard-collector/0.1 (+https://github.com/iqbal-ops123/pumpguard)"


def get(base: str, path: str, params: dict | None = None):
    """GET a Binance endpoint with simple retries. Returns parsed JSON or None."""
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            r = session.get(base + path, params=params, timeout=TIMEOUT_SECONDS)
        except requests.RequestException as e:
            print(f"  network error on {path}: {e}", file=sys.stderr)
            time.sleep(2 * attempt)
            continue
        if r.status_code == 200:
            return r.json()
        if r.status_code in (451, 403):
            sys.exit(
                f"Binance refused the request ({r.status_code}). This usually means the "
                "machine's location is blocked. Run the collector from an allowed location."
            )
        if r.status_code in (418, 429):
            wait = int(r.headers.get("Retry-After", 60))
            print(f"  rate limited, waiting {wait}s", file=sys.stderr)
            time.sleep(wait)
            continue
        print(f"  {path} returned {r.status_code}: {r.text[:200]}", file=sys.stderr)
        time.sleep(2 * attempt)
    return None


def to_float(x) -> float | None:
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def ratio(a: float | None, b: float | None) -> float | None:
    if a is None or not b:
        return None
    return a / b


def latest(rows) -> dict:
    """The /futures/data endpoints return a list; we want the newest item."""
    if isinstance(rows, list) and rows:
        return rows[-1]
    return {}


def list_perpetuals() -> list[dict]:
    info = get(FAPI, "/fapi/v1/exchangeInfo")
    if not info:
        sys.exit("Could not load Binance exchange info.")
    return [
        s
        for s in info["symbols"]
        if s.get("contractType") == "PERPETUAL"
        and s.get("quoteAsset") == "USDT"
        and s.get("status") == "TRADING"
    ]


def main() -> None:
    started = time.time()
    snapshot = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    snapshot_str = snapshot.strftime("%Y-%m-%dT%H:00:00Z")

    perps = list_perpetuals()
    print(f"{len(perps)} USDT perpetuals found")
    # For a quick test run, set PUMPGUARD_LIMIT=5 to only collect a few coins.
    limit = int(os.environ.get("PUMPGUARD_LIMIT", "0"))
    if limit:
        perps = perps[:limit]

    # Bulk calls: one request each for every coin.
    premium = {p["symbol"]: p for p in (get(FAPI, "/fapi/v1/premiumIndex") or [])}
    fut_ticker = {t["symbol"]: t for t in (get(FAPI, "/fapi/v1/ticker/24hr") or [])}
    spot_ticker = {t["symbol"]: t for t in (get(SPOT, "/api/v3/ticker/24hr") or [])}
    funding_info = {f["symbol"]: f for f in (get(FAPI, "/fapi/v1/fundingInfo") or [])}

    rows = []
    for i, p in enumerate(perps, 1):
        sym = p["symbol"]
        base = p.get("baseAsset", "")
        per_coin = {"symbol": sym, "period": "1h", "limit": 1}

        oi = latest(get(FAPI, "/futures/data/openInterestHist", per_coin))
        time.sleep(REQUEST_PAUSE_SECONDS)
        top_ls = latest(get(FAPI, "/futures/data/topLongShortPositionRatio", per_coin))
        time.sleep(REQUEST_PAUSE_SECONDS)
        all_ls = latest(get(FAPI, "/futures/data/globalLongShortAccountRatio", per_coin))
        time.sleep(REQUEST_PAUSE_SECONDS)
        taker = latest(get(FAPI, "/futures/data/takerlongshortRatio", per_coin))
        time.sleep(REQUEST_PAUSE_SECONDS)

        prem = premium.get(sym, {})
        ft = fut_ticker.get(sym, {})
        # Futures symbols like 1000PEPEUSDT have no exact spot pair; leave spot blank then.
        st = spot_ticker.get(sym, {})

        price = to_float(prem.get("markPrice")) or to_float(ft.get("lastPrice"))
        fut_vol = to_float(ft.get("quoteVolume"))
        spot_vol = to_float(st.get("quoteVolume"))
        oi_usd = to_float(oi.get("sumOpenInterestValue"))
        supply = to_float(oi.get("CMCCirculatingSupply"))
        mcap = price * supply if price and supply else None
        interval = funding_info.get(sym, {}).get("fundingIntervalHours", 8)

        rows.append(
            {
                "snapshot_utc": snapshot_str,
                "symbol": sym,
                "base_asset": base,
                "price": price,
                "price_change_pct_24h": to_float(ft.get("priceChangePercent")),
                "futures_quote_volume_24h": fut_vol,
                "spot_quote_volume_24h": spot_vol,
                "futures_to_spot_volume": ratio(fut_vol, spot_vol),
                "funding_rate": to_float(prem.get("lastFundingRate")),
                "funding_interval_hours": interval,
                "open_interest": to_float(oi.get("sumOpenInterest")),
                "open_interest_usd": oi_usd,
                "circulating_supply": supply,
                "market_cap_usd": mcap,
                "oi_to_market_cap": ratio(oi_usd, mcap),
                "top_trader_long_short_ratio": to_float(top_ls.get("longShortRatio")),
                "all_account_long_short_ratio": to_float(all_ls.get("longShortRatio")),
                "taker_buy_sell_ratio": to_float(taker.get("buySellRatio")),
            }
        )
        if i % 50 == 0:
            print(f"  {i}/{len(perps)} done")

    out = DATA_DIR / f"{snapshot.strftime('%Y-%m-%d')}.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    new_file = not out.exists()
    with out.open("a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        if new_file:
            writer.writeheader()
        writer.writerows(rows)

    print(f"Saved {len(rows)} rows to {out} in {time.time() - started:.0f}s")


if __name__ == "__main__":
    main()
