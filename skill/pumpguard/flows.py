"""
PumpGuard wallet movements: are the big holders sending coins to exchanges?

Takes the holder report from holders.py and, for the biggest unlabeled wallets:
  1. Works out what kind of wallet each one is (exchange-like, multisig, normal).
  2. Looks at its transfers of this coin over the last 7 days.
  3. Flags coins sent to exchanges, since people usually move coins to an
     exchange before selling.

Supported (free, no keys): TON via TonAPI, Ethereum via Blockscout.
Uses only the Python standard library.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

import holders

UA = "pumpguard-flows/0.1 (+https://github.com/iqbal-ops123/pumpguard)"
TIMEOUT = 25
MAX_WALLETS = 5          # how many top unlabeled wallets to follow
MIN_SHARE = 0.01         # only follow wallets holding at least 1% of the coins on that chain
DAY, WEEK = 86400, 7 * 86400
EXCHANGE_WORDS = ("exchange", "hot wallet", "deposit", "binance", "okx", "bybit", "bitget", "gate",
                  "kucoin", "mexc", "htx", "huobi", "coinbase", "kraken", "robinhood", "crypto.com",
                  "upbit", "bithumb", "bitfinex", "bitstamp", "gemini", "wallet in telegram")

_last_call = {"ton": 0.0}


def _get(url: str, host: str = ""):
    if host == "ton":  # TonAPI free tier: about one request per second
        wait = 1.1 - (time.time() - _last_call["ton"])
        if wait > 0:
            time.sleep(wait)
        _last_call["ton"] = time.time()
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            if e.code == 429:
                time.sleep(3 * (attempt + 1))
                continue
            return None
        except (urllib.error.URLError, TimeoutError, OSError, ValueError):
            time.sleep(2 * (attempt + 1))
    return None


def _looks_like_exchange(text: str) -> bool:
    text = (text or "").lower()
    return any(w in text for w in EXCHANGE_WORDS)


# ---------------- TON ----------------

_ton_kind_cache: dict = {}


def ton_wallet_kind(address: str):
    """Returns (label, is_exchange_like) for a TON account."""
    if address in _ton_kind_cache:
        return _ton_kind_cache[address]
    d = _get(f"https://tonapi.io/v2/accounts/{address}", "ton") or {}
    name = d.get("name") or ""
    interfaces = " ".join(d.get("interfaces") or [])
    if name and _looks_like_exchange(name):
        out = (f"{name} (exchange)", True)
    elif "highload" in interfaces:
        out = ("high-volume wallet, usually an exchange or service", True)
    elif "multisig" in interfaces:
        out = ("multisig wallet, often a team or treasury", False)
    elif name:
        out = (name, False)
    else:
        out = ("", False)
    _ton_kind_cache[address] = out
    return out


def ton_movements(holder: dict, jetton: str, supply: float, now: float):
    addr = holder["address"]
    data = _get(f"https://tonapi.io/v2/accounts/{addr}/jettons/{jetton}/history?limit=100", "ton") or {}
    decimals = 9
    senders, sent = set(), []
    for ev in data.get("events") or []:
        ts = ev.get("timestamp") or 0
        if now - ts > WEEK:
            continue
        for act in ev.get("actions") or []:
            j = act.get("JettonTransfer")
            if not j:
                continue
            dec = int(((j.get("jetton") or {}).get("decimals")) or decimals)
            amount = int(j.get("amount") or 0) / 10**dec
            s = (j.get("sender") or {}).get("address")
            r = j.get("recipient") or {}
            if r.get("address") == addr:
                senders.add(s)
            elif s == addr:
                sent.append({"to": r.get("address"), "to_name": r.get("name") or "", "amount": amount, "ts": ts})
    return senders, sent


# ---------------- Ethereum ----------------

def _bs_tags(a: dict) -> str:
    tags = ((a.get("metadata") or {}).get("tags")) or []
    return ", ".join(t.get("name", "") for t in tags if t.get("name")) or (a.get("name") or "")


def eth_labels(contract: str) -> dict:
    """Blockscout's public labels for the top holders of a token."""
    d = _get(f"https://eth.blockscout.com/api/v2/tokens/{contract}/holders") or {}
    out = {}
    for it in d.get("items") or []:
        a = it.get("address") or {}
        tags = _bs_tags(a)
        if a.get("hash") and tags:
            out[a["hash"].lower()] = tags
    return out


def eth_movements(holder: dict, contract: str, now: float):
    addr = holder["address"].lower()
    d = _get(
        f"https://eth.blockscout.com/api/v2/addresses/{holder['address']}/token-transfers"
        f"?type=ERC-20&token={contract}"
    ) or {}
    senders, sent = set(), []
    for it in d.get("items") or []:
        try:
            ts = datetime.fromisoformat(it["timestamp"].replace("Z", "+00:00")).timestamp()
        except (KeyError, ValueError):
            continue
        if now - ts > WEEK:
            continue
        total = it.get("total") or {}
        try:
            amount = int(total.get("value")) / 10 ** int(total.get("decimals"))
        except (TypeError, ValueError):
            continue
        f, t = it.get("from") or {}, it.get("to") or {}
        if (t.get("hash") or "").lower() == addr:
            senders.add(f.get("hash"))
        elif (f.get("hash") or "").lower() == addr:
            sent.append({"to": t.get("hash"), "to_name": _bs_tags(t), "amount": amount, "ts": ts})
    return senders, sent


# ---------------- main ----------------

def enrich(report: dict) -> dict:
    """Adds wallet types and exchange-bound movements to a holders.check() report."""
    now = time.time()
    report.setdefault("notes", [])
    for chain in report.get("chains") or []:
        name = chain["chain"]
        if name not in ("TON", "Ethereum"):
            continue
        supply = chain.get("total_supply") or 0
        if name == "Ethereum":
            labels = eth_labels(chain["address"])
            for h in chain["top_holders"]:
                tag = labels.get((h["address"] or "").lower())
                if tag and not h["label"]:
                    h["label"] = tag

        moves = []
        followed = 0
        for h in chain["top_holders"]:
            if followed >= MAX_WALLETS:
                break
            if holders._not_insider(h["label"]) or (h["percent"] or 0) < MIN_SHARE:
                continue
            followed += 1
            if name == "TON":
                kind, exchange_like = ton_wallet_kind(h["address"])
                if kind:
                    h["label"] = kind
                if exchange_like:
                    continue
                senders, sent = ton_movements(h, chain["address"], supply, now)
            else:
                senders, sent = eth_movements(h, chain["address"], now)
            # A wallet that receives from many different wallets behaves like an exchange or service.
            if len(senders) >= 15 and not h["label"]:
                h["label"] = "receives from many wallets, likely an exchange or service"
                continue
            to_exchange_24h = to_exchange_7d = 0.0
            biggest = {}
            for s in sent:
                if name == "TON":
                    label = s["to_name"] if _looks_like_exchange(s["to_name"]) else ""
                    if not label and s["amount"] >= supply * 0.001:
                        kind, ex = ton_wallet_kind(s["to"])
                        label = kind if ex else ""
                else:
                    label = s["to_name"] if _looks_like_exchange(s["to_name"]) else ""
                if not label:
                    continue
                to_exchange_7d += s["amount"]
                if now - s["ts"] <= DAY:
                    to_exchange_24h += s["amount"]
                biggest[label] = biggest.get(label, 0) + s["amount"]
            if to_exchange_7d > 0:
                moves.append({
                    "wallet": h["address"],
                    "wallet_share": h["percent"],
                    "wallet_label": h["label"],
                    "to_exchanges_24h": to_exchange_24h,
                    "to_exchanges_7d": to_exchange_7d,
                    "to_exchanges_24h_share": to_exchange_24h / supply if supply else None,
                    "to_exchanges_7d_share": to_exchange_7d / supply if supply else None,
                    "destinations": sorted(biggest, key=biggest.get, reverse=True)[:3],
                })
        chain["exchange_moves"] = moves
        chain["wallets_followed"] = followed
        holders.summarize(chain)  # recount concentration now that more wallets are labeled
    return report


def score_flows(report: dict):
    pts, reasons = 0, []
    for chain in report.get("chains") or []:
        day = sum(m["to_exchanges_24h_share"] or 0 for m in chain.get("exchange_moves") or [])
        week = sum(m["to_exchanges_7d_share"] or 0 for m in chain.get("exchange_moves") or [])
        if day >= 0.01:
            pts += 15
            reasons.append(f"On {chain['chain']}, big wallets sent {day * 100:.1f}% of the coins there to exchanges in the last 24h (often before selling, sometimes bait)")
        elif week >= 0.02:
            pts += 8
            reasons.append(f"On {chain['chain']}, big wallets sent {week * 100:.1f}% of the coins there to exchanges in the last 7 days")
    return pts, reasons
