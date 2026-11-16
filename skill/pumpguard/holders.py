"""
PumpGuard holder check: who owns the coin?

Finds a coin's contract addresses (CoinGecko), then looks up its biggest
holders on each blockchain it lives on:
  - EVM chains (Ethereum, BNB Chain, Base, Arbitrum, ...) and Solana: GoPlus
  - TON: TonAPI
Also reports contract risks GoPlus can see (honeypot, owner can change balances).

Uses only the Python standard library and free public APIs, no keys.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request

UA = "pumpguard-holders/0.1 (+https://github.com/iqbal-ops123/pumpguard)"
TIMEOUT = 20

# CoinGecko platform id -> GoPlus chain id
EVM_CHAINS = {
    "ethereum": ("1", "Ethereum"),
    "binance-smart-chain": ("56", "BNB Chain"),
    "base": ("8453", "Base"),
    "arbitrum-one": ("42161", "Arbitrum"),
    "polygon-pos": ("137", "Polygon"),
    "optimistic-ethereum": ("10", "Optimism"),
    "avalanche": ("43114", "Avalanche"),
    "linea": ("59144", "Linea"),
    "zksync": ("324", "zkSync"),
    "mantle": ("5000", "Mantle"),
    "blast": ("81457", "Blast"),
    "sonic": ("146", "Sonic"),
}

# Well-known exchange wallets (public labels from block explorers). Lower-case for EVM.
# Add more over time; a missing entry only means that wallet stays unlabeled.
KNOWN_WALLETS = {
    "0x3f5ce5fbfe3e9af3971dd833d26ba9b5c936f0be": "Binance (exchange)",
    "0xbe0eb53f46cd790cd13851d5eff43d12404d33e8": "Binance (exchange)",
    "0xf977814e90da44bfa03b6295a0616a897441acec": "Binance (exchange)",
    "0x28c6c06298d514db089934071355e5743bf21d60": "Binance (exchange)",
    "0x21a31ee1afc51d94c2efccaa2092ad1028285549": "Binance (exchange)",
    "0xdfd5293d8e347dfe59e90efd55b2956a1343963d": "Binance (exchange)",
    "0x5a52e96bacdabb82fd05763e25335261b270efcb": "Binance (exchange)",
    "0x8894e0a0c962cb723c1976a4421c95949be2d4e3": "Binance (exchange)",
    "0x6cc5f688a315f3dc28a7781717a9a798a59fda7b": "OKX (exchange)",
    "0x0d0707963952f2fba59dd06f2b425ace40b492fe": "Gate (exchange)",
    "0xf89d7b9c864f589bbf53a82105107622b35eaa40": "Bybit (exchange)",
    "9WzDXwBbmkg8ZTbNMqUxvQRAyrZzDsGYdLVL9zYtAWWM": "Binance (exchange)",
    "5tzFkiKscXHK5ZXCGbXZxdw7gTjjD1mBwuoFbhUvuAi9": "Binance (exchange)",
    "2ojv9BAiHUrvsm9gxDe7fJSzbNZSJcxZvf8dqmWGHG8S": "Binance (exchange)",
    "AC5RDfQFmDS1deWZos921JfqscXdByf8BKHs5ACWjtW2": "Bybit (exchange)",
    "5VCwKtCXgCJ6kit5FybXjvriW3xELsFDhYrPSqtJNmcD": "OKX (exchange)",
    "H8sMJSCQxfKiFTCfDR3DUMLPwcRbM61LGFJ8N4dK3WjS": "Coinbase (exchange)",
}


def known_label(address) -> str:
    if not address:
        return ""
    return KNOWN_WALLETS.get(address) or KNOWN_WALLETS.get(str(address).lower(), "")


# Holder labels that are not "a person who can dump": exchanges, bridges, pools, burns, locks.
NOT_INSIDER_WORDS = ("exchange", "binance", "okx", "bybit", "bitget", "gate", "kucoin", "mexc",
                     "htx", "huobi", "coinbase", "kraken", "bridge", "pool", "pancake", "uniswap",
                     "dedust", "ston", "burn", "dead", "null", "lock", "vesting", "timelock")


def _get(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            if e.code == 429:  # free tiers are rate limited; wait and retry
                time.sleep(10 * (attempt + 1))
                continue
            return None
        except (urllib.error.URLError, TimeoutError, OSError, ValueError):
            time.sleep(2 * (attempt + 1))
    return None


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _not_insider(label: str) -> bool:
    label = (label or "").lower()
    return any(w in label for w in NOT_INSIDER_WORDS)


# ---------- find the coin ----------

def find_coin(symbol: str, binance_price: float | None):
    """Return (coingecko_id, name) for a ticker, using the Binance price to pick the right coin."""
    data = _get("https://api.coingecko.com/api/v3/search?query=" + urllib.parse.quote(symbol))
    if not data:
        return None, None
    matches = [c for c in data.get("coins", []) if c.get("symbol", "").upper() == symbol.upper()]
    if not matches:
        return None, None
    matches.sort(key=lambda c: c.get("market_cap_rank") or 10**9)
    if binance_price and len(matches) > 1:
        ids = ",".join(c["id"] for c in matches[:10])
        prices = _get(f"https://api.coingecko.com/api/v3/simple/price?ids={ids}&vs_currencies=usd") or {}
        for c in matches[:10]:
            p = (prices.get(c["id"]) or {}).get("usd")
            if p and abs(p / binance_price - 1) < 0.25:
                return c["id"], c.get("name")
    return matches[0]["id"], matches[0].get("name")


def get_platforms(coin_id: str):
    d = _get(
        f"https://api.coingecko.com/api/v3/coins/{coin_id}"
        "?localization=false&tickers=false&market_data=false&community_data=false&developer_data=false"
    )
    if not d:
        return None, {}
    platforms = {k: v for k, v in (d.get("platforms") or {}).items() if k and v}
    return d.get("asset_platform_id"), platforms


# ---------- holders per chain ----------

def evm_holders(platform: str, address: str):
    chain_id, chain_name = EVM_CHAINS[platform]
    d = _get(f"https://api.gopluslabs.io/api/v1/token_security/{chain_id}?contract_addresses={address}")
    res = list(((d or {}).get("result") or {}).values())
    if not res:
        return None
    r = res[0]
    holders = []
    for h in r.get("holders") or []:
        label = h.get("tag") or ""
        if h.get("is_locked") == 1 or h.get("is_locked") == "1":
            label = (label + " locked").strip()
        holders.append({
            "address": h.get("address"),
            "percent": _f(h.get("percent")),
            "label": label,
            "is_contract": str(h.get("is_contract")) == "1",
        })
    risks = []
    if r.get("is_honeypot") == "1":
        risks.append("Honeypot: the code may stop people from selling")
    if r.get("owner_change_balance") == "1":
        risks.append("The owner can change people's balances")
    if r.get("hidden_owner") == "1":
        risks.append("The contract has a hidden owner")
    if r.get("cannot_sell_all") == "1":
        risks.append("Holders cannot sell all their coins")
    if _f(r.get("sell_tax")) and _f(r.get("sell_tax")) >= 0.1:
        risks.append(f"Sell tax of {float(r['sell_tax']) * 100:.0f}%")
    if r.get("is_open_source") == "0":
        risks.append("The contract code is not public")
    return {
        "chain": chain_name,
        "address": address,
        "total_supply": _f(r.get("total_supply")),
        "holder_count": int(r["holder_count"]) if str(r.get("holder_count", "")).isdigit() else None,
        "top_holders": holders[:10],
        "risks": risks,
        "mintable": r.get("is_mintable") == "1",
    }


def solana_holders(address: str):
    d = _get(f"https://api.gopluslabs.io/api/v1/solana/token_security?contract_addresses={address}")
    res = list(((d or {}).get("result") or {}).values())
    if not res:
        return None
    r = res[0]
    holders = [{
        "address": h.get("account") or h.get("token_account"),
        "percent": _f(h.get("percent")),
        "label": h.get("tag") or "",
        "is_contract": False,
    } for h in (r.get("holders") or [])]
    risks = []
    if (r.get("balance_mutable_authority") or {}).get("status") == "1":
        risks.append("Someone can change people's balances")
    if (r.get("freezable") or {}).get("status") == "1":
        risks.append("Someone can freeze people's coins")
    return {
        "chain": "Solana",
        "address": address,
        "total_supply": _f(r.get("total_supply")),
        "holder_count": int(r["holder_count"]) if str(r.get("holder_count", "")).isdigit() else None,
        "top_holders": holders[:10],
        "risks": risks,
        "mintable": (r.get("mintable") or {}).get("status") == "1",
    }


def ton_holders(address: str):
    info = _get(f"https://tonapi.io/v2/jettons/{address}")
    data = _get(f"https://tonapi.io/v2/jettons/{address}/holders?limit=10")
    if not info or not data:
        return None
    decimals = int((info.get("metadata") or {}).get("decimals") or 9)
    supply = _f(info.get("total_supply"))
    supply = supply / 10**decimals if supply else None
    holders = []
    for a in data.get("addresses") or []:
        bal = _f(a.get("balance"))
        bal = bal / 10**decimals if bal is not None else None
        owner = a.get("owner") or {}
        holders.append({
            "address": owner.get("address"),
            "percent": (bal / supply) if bal is not None and supply else None,
            "label": owner.get("name") or "",
            "is_contract": owner.get("is_wallet") is False,
        })
    return {
        "chain": "TON",
        "address": address,
        "total_supply": supply,
        "holder_count": info.get("holders_count"),
        "top_holders": holders,
        "risks": [],
        "mintable": info.get("mintable"),
    }


# ---------- summary ----------

def summarize(chain_data: dict) -> dict:
    top = chain_data["top_holders"]
    for h in top:
        if not h["label"]:
            h["label"] = known_label(h["address"])
    top10 = sum(h["percent"] or 0 for h in top)
    insiders = [h for h in top if not _not_insider(h["label"])]
    chain_data["top10_percent"] = top10
    chain_data["top10_unlabeled_percent"] = sum(h["percent"] or 0 for h in insiders)
    chain_data["biggest_unlabeled_percent"] = max((h["percent"] or 0 for h in insiders), default=0)
    return chain_data


def check(symbol: str, binance_price: float | None = None) -> dict:
    """Holder report for one coin. Never raises; returns notes when data is missing."""
    out = {"coingecko_id": None, "main_chain": None, "chains": [], "notes": []}
    coin_id, name = find_coin(symbol, binance_price)
    if not coin_id:
        out["notes"].append("Could not find this coin on CoinGecko, so holders were not checked.")
        return out
    out["coingecko_id"], out["name"] = coin_id, name
    main, platforms = get_platforms(coin_id)
    out["main_chain"] = main
    if not platforms:
        out["notes"].append("This coin has its own blockchain (no token contract), so holder lists are not available here.")
        return out
    order = sorted(platforms, key=lambda p: p != main)  # main chain first
    for platform in order:
        address = platforms[platform]
        if platform in EVM_CHAINS:
            data = evm_holders(platform, address)
        elif platform == "solana":
            data = solana_holders(address)
        elif platform == "the-open-network":
            data = ton_holders(address)
        else:
            out["notes"].append(f"Holders on {platform} are not supported yet.")
            continue
        if data:
            data["is_main_chain"] = platform == main
            out["chains"].append(summarize(data))
        else:
            out["notes"].append(f"Could not load holders on {platform}.")
        time.sleep(1)
    return out


def score_holders(report: dict):
    """Points and plain-English reasons from the holder report."""
    pts, reasons = 0, []
    chains = report.get("chains") or []
    main = next((c for c in chains if c.get("is_main_chain")), chains[0] if chains else None)
    if main:
        u = main["top10_unlabeled_percent"]
        if u >= 0.8:
            pts += 15
            reasons.append(f"On {main['chain']}, the top 10 wallets (not counting known exchanges, bridges or pools) hold {u * 100:.0f}% of the coins there")
        elif u >= 0.6:
            pts += 8
            reasons.append(f"On {main['chain']}, the top 10 wallets (not counting known exchanges, bridges or pools) hold {u * 100:.0f}% of the coins there")
        b = main["biggest_unlabeled_percent"]
        if b >= 0.25:
            pts += 5
            reasons.append(f"One unlabeled wallet on {main['chain']} holds {b * 100:.0f}% of the coins there")
    for c in chains:
        for risk in c["risks"]:
            pts += 25 if "Honeypot" in risk else 10
            reasons.append(f"{c['chain']} contract: {risk}")
    return pts, reasons
