"""
PumpGuard live scanner (version 0.1).

Looks at Binance USDT perpetual futures right now and scores how "fragile"
each market looks: how easy it would be for a few big players to squeeze
the price up and then dump it.

Usage:
  python scan.py                 # top 10 most fragile coins right now
  python scan.py --top 20        # top 20
  python scan.py RAVE MYX        # check specific coins
  python scan.py --json RAVE     # machine-readable output
  python scan.py --no-holders X  # skip the (slower) holder check

When you name coins, PumpGuard also checks who holds them on the blockchain.

No installs needed: uses only Python's standard library (Python 3.8+).

Research tool, not financial advice. A high score means the market looks
fragile based on public data. It is not proof that anyone did anything wrong.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import holders  # noqa: E402  (holder check lives next to this file)

FAPI = "https://fapi.binance.com"
SPOT = "https://api.binance.com"
TIMEOUT = 15
PAUSE = 0.25
CANDIDATES = 60  # how many coins get a deep check in market-wide mode

USER_AGENT = "pumpguard-scanner/0.1 (+https://github.com/iqbal-ops123/pumpguard)"


def get(base, path, params=None):
    """GET a public Binance endpoint using only the Python standard library."""
    url = base + path + ("?" + urllib.parse.urlencode(params) if params else "")
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            if e.code in (451, 403):
                sys.exit(
                    f"Binance refused the request ({e.code}). Binance blocks some countries and "
                    "cloud servers. Run PumpGuard on a computer in a location Binance allows."
                )
            if e.code in (418, 429):
                time.sleep(int(e.headers.get("Retry-After", 30)))
                continue
            return None
        except (urllib.error.URLError, TimeoutError, OSError):
            time.sleep(2 * (attempt + 1))
    return None


def f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def pct(x, digits=1):
    return "n/a" if x is None else f"{x * 100:.{digits}f}%"


def load_market():
    info = get(FAPI, "/fapi/v1/exchangeInfo") or {}
    perps = {
        s["symbol"]: s
        for s in info.get("symbols", [])
        if s.get("contractType") == "PERPETUAL"
        and s.get("quoteAsset") == "USDT"
        and s.get("status") == "TRADING"
    }
    premium = {p["symbol"]: p for p in (get(FAPI, "/fapi/v1/premiumIndex") or [])}
    fut = {t["symbol"]: t for t in (get(FAPI, "/fapi/v1/ticker/24hr") or [])}
    spot = {t["symbol"]: t for t in (get(SPOT, "/api/v3/ticker/24hr") or [])}
    finfo = {x["symbol"]: x for x in (get(FAPI, "/fapi/v1/fundingInfo") or [])}
    return perps, premium, fut, spot, finfo


def basic_row(sym, perps, premium, fut, spot, finfo):
    p, ft, st = premium.get(sym, {}), fut.get(sym, {}), spot.get(sym, {})
    interval = f(finfo.get(sym, {}).get("fundingIntervalHours")) or 8.0
    funding = f(p.get("lastFundingRate"))
    fut_vol = f(ft.get("quoteVolume"))
    spot_vol = f(st.get("quoteVolume"))
    return {
        "symbol": sym,
        "base": perps[sym].get("baseAsset", sym),
        "price": f(p.get("markPrice")) or f(ft.get("lastPrice")),
        "change_24h": (f(ft.get("priceChangePercent")) or 0) / 100,
        "futures_volume_24h": fut_vol,
        "spot_volume_24h": spot_vol,
        "futures_to_spot": (fut_vol / spot_vol) if fut_vol and spot_vol else None,
        "funding_rate": funding,
        "funding_interval_hours": interval,
        # Funding scaled to an 8-hour period so coins can be compared fairly.
        "funding_8h": funding * (8 / interval) if funding is not None else None,
    }


def deep_check(row):
    """Adds open interest, market cap and long/short data (several API calls)."""
    sym = row["symbol"]
    oi_hist = get(FAPI, "/futures/data/openInterestHist", {"symbol": sym, "period": "1h", "limit": 25}) or []
    time.sleep(PAUSE)
    top = get(FAPI, "/futures/data/topLongShortPositionRatio", {"symbol": sym, "period": "1h", "limit": 1}) or []
    time.sleep(PAUSE)
    glob = get(FAPI, "/futures/data/globalLongShortAccountRatio", {"symbol": sym, "period": "1h", "limit": 1}) or []
    time.sleep(PAUSE)

    if oi_hist:
        now, first = oi_hist[-1], oi_hist[0]
        oi_usd = f(now.get("sumOpenInterestValue"))
        supply = f(now.get("CMCCirculatingSupply"))
        oi_now, oi_then = f(now.get("sumOpenInterest")), f(first.get("sumOpenInterest"))
        row["open_interest_usd"] = oi_usd
        row["market_cap_usd"] = row["price"] * supply if row["price"] and supply else None
        row["oi_to_market_cap"] = oi_usd / row["market_cap_usd"] if oi_usd and row["market_cap_usd"] else None
        row["oi_change_24h"] = (oi_now / oi_then - 1) if oi_now and oi_then else None
    row["top_trader_long_short"] = f(top[-1].get("longShortRatio")) if top else None
    row["all_account_long_short"] = f(glob[-1].get("longShortRatio")) if glob else None
    return row


def score(row):
    """Simple, transparent rules. Thresholds are starting guesses, to be tuned by backtests."""
    pts, reasons = 0, []

    oim = row.get("oi_to_market_cap")
    if oim is not None:
        if oim >= 0.6:
            pts += 25; reasons.append(f"Open interest is {pct(oim, 0)} of market cap (extreme; top-100 coins are usually under 20%)")
        elif oim >= 0.4:
            pts += 18; reasons.append(f"Open interest is {pct(oim, 0)} of market cap (very high)")
        elif oim >= 0.2:
            pts += 10; reasons.append(f"Open interest is {pct(oim, 0)} of market cap (high)")

    fs = row.get("futures_to_spot")
    if fs is None and row.get("futures_volume_24h"):
        pts += 4; reasons.append("No spot market on Binance to compare with, so futures may be driving the price")
    elif fs is not None:
        if fs >= 25:
            pts += 20; reasons.append(f"Futures volume is {fs:.0f}x spot volume (extreme)")
        elif fs >= 10:
            pts += 14; reasons.append(f"Futures volume is {fs:.0f}x spot volume (high)")

    fr = row.get("funding_8h")
    if fr is not None:
        if fr <= -0.001:
            pts += 15; reasons.append(f"Funding {pct(fr, 3)} per 8h: shorts are crowded, which is fuel for a squeeze")
        elif fr <= -0.0003:
            pts += 8; reasons.append(f"Funding {pct(fr, 3)} per 8h: shorts are paying longs")
        elif fr >= 0.001:
            pts += 10; reasons.append(f"Funding {pct(fr, 3)} per 8h: longs are crowded")
    if row.get("funding_interval_hours", 8) <= 1:
        pts += 4; reasons.append(f"Funding is charged every {row['funding_interval_hours']:.0f}h (exchanges do this when a market is under stress)")

    ch = row.get("change_24h") or 0
    if ch >= 1.0:
        pts += 15; reasons.append(f"Price up {pct(ch, 0)} in 24h")
    elif ch >= 0.5:
        pts += 10; reasons.append(f"Price up {pct(ch, 0)} in 24h")
    elif ch >= 0.25:
        pts += 5; reasons.append(f"Price up {pct(ch, 0)} in 24h")
    elif ch <= -0.4:
        pts += 5; reasons.append(f"Price down {pct(-ch, 0)} in 24h")

    oic = row.get("oi_change_24h")
    if oic is not None:
        if oic >= 1.0:
            pts += 10; reasons.append(f"Open interest up {pct(oic, 0)} in 24h (leverage piling in)")
        elif oic >= 0.5:
            pts += 6; reasons.append(f"Open interest up {pct(oic, 0)} in 24h")

    top, allr = row.get("top_trader_long_short"), row.get("all_account_long_short")
    if top and allr:
        if top < 1 and allr > 1.5:
            pts += 8; reasons.append(f"Big accounts lean short ({top:.2f}) while most accounts lean long ({allr:.2f})")
        elif top > 1.5 and allr < 1:
            pts += 8; reasons.append(f"Big accounts lean long ({top:.2f}) while most accounts lean short ({allr:.2f})")

    row["score"] = min(pts, 100)
    row["reasons"] = reasons
    row["stage"] = stage(row)
    return row


def stage(row):
    ch = row.get("change_24h") or 0
    fr = row.get("funding_8h") or 0
    oic = row.get("oi_change_24h")
    if ch <= -0.4:
        return "Post-dump"
    if ch >= 0.25 and fr < 0:
        return "Squeeze in progress (shorting now is risky)"
    if ch >= 0.25 and oic is not None and oic < -0.1:
        return "Possible distribution (leverage leaving after a pump)"
    if ch >= 0.25:
        return "Pumping"
    if row["score"] >= 40 and fr < 0:
        return "Squeeze setup (crowded shorts, fragile market)"
    if row["score"] >= 40:
        return "Fragile"
    if row["score"] >= 25:
        return "Watch"
    return "Quiet"


def resolve(names, perps):
    out = []
    for n in names:
        n = n.upper()
        for cand in (n, n + "USDT", "1000" + n + "USDT"):
            if cand in perps:
                out.append(cand)
                break
        else:
            print(f"Not found on Binance futures: {n}", file=sys.stderr)
    return out


def add_holders(row):
    """Adds the blockchain holder check to a scored row."""
    symbol, price = row["base"], row.get("price")
    # Binance lists some cheap coins per 1,000 units, e.g. 1000PEPE. Use the real ticker and unit price.
    digits = len(symbol) - len(symbol.lstrip("0123456789"))
    if digits >= 4 and symbol[:digits].strip("0") == "1":
        symbol, price = symbol[digits:], (price / int(symbol[:digits]) if price else None)
    rep = holders.check(symbol, price)
    pts, reasons = holders.score_holders(rep)
    row["holders"] = rep
    row["score"] = min(row["score"] + pts, 100)
    row["reasons"] += reasons
    row["stage"] = stage(row)
    return row


def print_holders(rep):
    for c in rep.get("chains", []):
        main = " (main chain)" if c.get("is_main_chain") else ""
        print(f"   Holders on {c['chain']}{main}: {c.get('holder_count') or '?'} wallets, "
              f"top 10 hold {c['top10_percent'] * 100:.0f}% of the coins on this chain")
        for h in c["top_holders"][:5]:
            label = h["label"] or ("contract" if h["is_contract"] else "wallet")
            pct = f"{h['percent'] * 100:.1f}%" if h["percent"] is not None else "?"
            print(f"     {pct:>6}  {label}  {str(h['address'])[:12]}...")
    for n in rep.get("notes", []):
        print(f"   Note: {n}")


def print_report(rows):
    print("PumpGuard v0.1  |  Binance USDT perpetuals  |  research only, not financial advice\n")
    for i, r in enumerate(rows, 1):
        print(f"{i}. {r['base']}  score {r['score']}/100  [{r['stage']}]")
        print(
            f"   price {r['price']:.6g}  24h {pct(r['change_24h'])}  funding/8h {pct(r.get('funding_8h'), 3)}"
            f"  OI/mcap {pct(r.get('oi_to_market_cap'))}"
            f"  fut/spot {('%.1fx' % r['futures_to_spot']) if r.get('futures_to_spot') else 'no spot'}"
        )
        for reason in r["reasons"] or ["Nothing unusual found"]:
            print(f"   - {reason}")
        if r.get("holders"):
            print_holders(r["holders"])
        print()


def main():
    ap = argparse.ArgumentParser(description="PumpGuard live fragility scanner")
    ap.add_argument("coins", nargs="*", help="coins to check, e.g. RAVE MYX (default: scan the market)")
    ap.add_argument("--top", type=int, default=10)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--no-holders", action="store_true", help="skip the blockchain holder check")
    a = ap.parse_args()

    perps, premium, fut, spot, finfo = load_market()
    if not perps:
        sys.exit("Could not reach Binance. Check the internet connection, or the network may block Binance.")

    if a.coins:
        symbols = resolve(a.coins, perps)
    else:
        # Cheap first pass on every coin, then a deep check on the most suspicious ones.
        quick = [score(basic_row(s, perps, premium, fut, spot, finfo)) for s in perps]
        quick.sort(key=lambda r: r["score"], reverse=True)
        symbols = [r["symbol"] for r in quick[:CANDIDATES]]

    rows = [score(deep_check(basic_row(s, perps, premium, fut, spot, finfo))) for s in symbols]
    if a.coins and not a.no_holders:
        rows = [add_holders(r) for r in rows]
    rows.sort(key=lambda r: r["score"], reverse=True)
    if not a.coins:
        rows = rows[: a.top]

    if a.json:
        print(json.dumps(rows, indent=2))
    else:
        print_report(rows)


if __name__ == "__main__":
    main()
