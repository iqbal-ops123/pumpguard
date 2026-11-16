---
name: pumpguard
description: Check whether a crypto coin on Binance futures shows signs of a leverage-driven pump-and-dump setup, or scan the market for the most fragile coins. Use when the user asks to check a coin (e.g. "check PEPE", "is RAVE risky?"), asks what looks like a pump and dump, or mentions PumpGuard.
---

# PumpGuard

PumpGuard scores how fragile a Binance USDT perpetual futures market looks, from 0 to 100, and labels which stage of the pump-and-dump pattern it seems to be in. It uses live public Binance data, and for named coins it also checks who holds the coin on the blockchain (CoinGecko, GoPlus and TonAPI, all free). No API key and no installs are needed.

Source and documentation: https://github.com/iqbal-ops123/pumpguard

## Running the scanner

The scanner is `scan.py`, in the same folder as this file. It needs Python 3.8 or newer and nothing else.

Always use `--json` and read the result yourself:

```
python3 <this skill's folder>/scan.py --json PEPE
python3 <this skill's folder>/scan.py --json RAVE MYX BTC
python3 <this skill's folder>/scan.py --json --top 10     # whole market, about 90 seconds
```

- Pass coin tickers as the user wrote them (e.g. `PEPE`, `rave`). The scanner finds names like `1000PEPEUSDT` on its own. If a coin is "Not found on Binance futures", say so.
- For a whole-market scan, tell the user it takes about a minute and a half.
- Checking named coins includes the holder check, which can take up to a minute because the free data sources limit how fast we can ask. Add `--no-holders` only if the user wants a quick answer.
- `scan.py` imports `holders.py` from the same folder, so keep both files together.
- Binance blocks some countries and some cloud servers. If the scanner says Binance refused the request or could not be reached, tell the user plainly: PumpGuard has to run on a computer whose location Binance allows (for example through Claude Code or the Claude desktop app on their own computer). Never make up numbers.

## Explaining the result

Write for someone who is not a trader: plain, simple words, no jargon. For each coin give:

1. **The score and stage** in one line.
2. **A short table** of what was checked, the coin's number, and what it means in everyday words:
   - `price`, `change_24h`: price now and its move in 24 hours.
   - `oi_to_market_cap`: "bets on this coin compared with the coin's whole value". Under 20% is normal for big coins; 40% and up is very high.
   - `futures_to_spot`: how much more futures trading there is than normal buying and selling. Over 10x is a warning. `null` means Binance has no regular market for it, so futures alone set the price.
   - `funding_8h`: who pays whom to keep their bet open. Strongly negative means people betting on a fall are crowded (fuel for a squeeze). Strongly positive means people betting on a rise are crowded.
   - `oi_change_24h`: how fast borrowed money is coming in or leaving.
   - `top_trader_long_short` and `all_account_long_short`: above 1 means leaning towards price going up, below 1 towards down. Point out when big accounts and most accounts lean opposite ways.
3. **What the stage means:**
   - **Squeeze setup / Squeeze in progress**: many people are betting on a fall. Betting on a fall now is risky, because a squeeze can push the price up first.
   - **Pumping**: rising fast on borrowed money. Late buyers are at risk.
   - **Possible distribution**: borrowed money is leaving after a pump; a drop may be starting.
   - **Post-dump**: a crash has probably already happened.
   - **Fragile / Watch / Quiet**: from more to less concern.
4. **Who holds the coin** (from the `holders` part of the result, when present):
   - For each blockchain in `holders.chains`, give the number of wallets, what share the top 10 hold, and the biggest holders with their labels. The `is_main_chain` one matters most.
   - Shares are of the coins on that one blockchain, not of the whole coin's supply. Say so.
   - Labels like "Binance (exchange)", bridges and pools usually hold coins for many users, so they are not counted as insiders. An unlabeled "wallet" is unknown: it could be the team, an investor, a fund or an exchange we have not labeled yet. Do not claim it belongs to anyone.
   - Mention any contract risks listed in `risks` (for example a honeypot or an owner who can change balances), and any `notes`.
5. If a coin crashed long ago it can look "Quiet" now, because the scanner only looks at the last 24 hours. Say so when relevant.

## Rules

- This is research, not financial advice. Never tell the user to buy, sell, long or short. End with one short line saying so.
- A high score means the market looks fragile based on public data. Never call a project a scam or say anyone committed fraud.
- Scores use early (v0.1) thresholds that have not yet been tested against past crashes. Mention this when the user seems about to rely on a score.
- Report only the numbers the scanner returned. Do not fill gaps from memory.
- The scanner sees Binance futures data and the current top holders. It does not yet track wallet movements, such as big holders sending coins to exchanges.
