# marginism

**SPAN margin calculator for Indian F&O — computed locally, offline, from the
exchange's daily SPAN parameter file.**

Give it a SPAN `.spn` file (NFO / CDS / MCX) and a set of positions, and it
returns the SPAN margin, exposure (ELM) margin, total initial margin, and the
margin benefit from hedging — the same figures a broker's margin calculator
shows. Pure Python, **zero third-party dependencies**, no network calls.

```python
from span_margin import RiskEngine

eng = RiskEngine.from_file("nsccl.20260529.s.spn")   # parse once, reuse

res = eng.basket([
    {"tradingsymbol": "NIFTY26JUN23700CE", "transaction_type": "SELL", "quantity": 65},
])
print(res["data"]["final"]["total"])      # total initial margin
```

## Why it works without an option pricer

Each contract in a `.spn` file ships a **precomputed 16-scenario risk array**
(profit/loss under price moves of ±1/3, ±2/3, ±3/3 of the scan range, plus two
extreme moves, each at volatility up/down). SPAN margin is therefore pure
arithmetic over those arrays — no Black-Scholes at calculation time.

```
SPAN margin = max(0, scan_risk + calendar_spread_charge − net_option_value)
initial margin = SPAN margin + Exposure (ELM) margin + Adhoc
```

## Features

- **Futures + options netted together** (combined-commodity portfolio margining).
- **Single leg or multi-leg** baskets — straddles, strangles, spreads, covered
  calls, protective puts, iron condors, calendar spreads.
- **Margin benefit** from hedging is reported.
- **Two symbol formats** accepted (compact `NIFTY26JUN23700CE` and full-date
  `NIFTY30JUN2623700CE`), or specify `symbol`/`expiry`/`strike` fields directly.
- **Quantity entered directly in units** (NIFTY lot 65 → `65` = 1 lot, `130` = 2).
- **Exchange-agnostic** — load an NFO, CDS, or MCX SPAN file.
- **Load once, reuse**: parsing is the only expensive step (~seconds); each
  calculation is sub-millisecond.

## Install

```bash
pip install -e .          # from a clone
# or drop the span_margin/ folder on your PYTHONPATH (stdlib only)
```

Requires Python 3.8+.

## Getting a SPAN file

SPAN parameter files are published by the exchange clearing corporation
(e.g. NSE Clearing's `nsccl.YYYYMMDD.*.spn`) and revised 6 times each trading
day. Point the engine at whichever revision you need; the latest end-of-day
(settlement) file matches broker calculators most closely.

## Documentation & examples

- `example.py` — 10 worked trader strategies with margins and benefit.
- `span_margin/README.md` — full API reference, file-path tips (macOS/Windows),
  and the SPAN algorithm details.

## Disclaimer

This software is provided "as is" for informational and educational purposes.
Margin figures are estimates derived from the supplied SPAN file and configured
exposure rates; always verify against your broker before trading. Not financial
advice. See [LICENSE](LICENSE) (MIT).
