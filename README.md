# PumpGuard

An open-source scanner that flags crypto tokens showing signs of a leverage-driven pump-and-dump setup.

## Why

Many tokens on exchanges like Binance and Bitget pump hard for no clear reason, then crash 80 to 99%. Most of these moves follow the same pattern: a small real float held by a few wallets, a perpetual futures market, crowded shorts, a forced short squeeze, and then insiders selling into the squeeze. Retail traders end up as the exit liquidity, whether they bought the top or shorted too early.

PumpGuard does not predict prices. It scores how fragile a token's market looks and which stage of this pattern it seems to be in, and it explains every flag in plain words so you can check the evidence yourself.

## What it looks at

Leverage pressure: open interest vs market cap, futures vs spot volume, funding rates, long/short ratios, liquidation levels.

Supply control: top holder concentration, real circulating float, upcoming token unlocks, contract risks.

Wallet behaviour: insider transfers to exchanges and clusters of wallets that act together.

Market quality: volume vs order book depth, price gaps between exchanges, signs of wash trading.

Price shape and context: size and speed of the move, lack of news, exchange risk tags, and similarity to past cases.

## Status

Early stage. First step: collect hourly derivatives data for all Binance perpetual futures and build a library of past cases (OM, MYX, COAI, JELLYJELLY, RAVE and others) to test against.

## Disclaimer

This is a research and education tool. It is not financial advice. A high score means a market looks fragile based on public data. It is not a claim that any project or person did anything wrong. Always do your own research.

## License

MIT
