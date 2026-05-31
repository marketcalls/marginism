# marginism

**SPAN margin calculator for Indian F&O — computed locally, offline, from the
exchange's daily SPAN parameter file.**

Give it a SPAN `.spn` file (NFO / CDS / MCX) and a set of positions, and it
returns the SPAN margin, exposure (ELM) margin, total initial margin, and the
margin benefit from hedging — the same figures a broker's margin calculator
shows. Pure Python, **zero third-party dependencies**, no network calls.

```python
from marginism import RiskEngine

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

## Examples

All examples reuse one engine (parse once):

```python
from marginism import RiskEngine
eng = RiskEngine.from_file("nsccl.20260529.s.spn")   # NIFTY lot size = 65
```

`quantity` is entered directly in units (65 = 1 lot, 130 = 2 lots).
`transaction_type` is `BUY` or `SELL`. The response carries per-leg `span` /
`exposure` / `option_premium`, a consolidated `final`, and `margin_benefit`
(the margin saved by hedging vs holding each leg outright). Numbers below are
from the 29-May-2026 settlement file.

### Two ways to specify a leg

Not every trader uses the same tradingsymbol format. So a leg can be given
**either** by tradingsymbol **or** by separate fields — both are accepted in the
same basket, and produce identical results.

**A) By tradingsymbol** (two formats both resolve automatically):

```python
{"tradingsymbol": "NIFTY26JUN23700CE", "transaction_type": "SELL", "quantity": 65}   # compact
{"tradingsymbol": "NIFTY30JUN2623700CE", "transaction_type": "SELL", "quantity": 65} # full-date
```

**B) By separate fields** — `symbol`, `expiry`, `strike`, `instrument` (`FUT`/`CE`/`PE`):

```python
{"symbol": "NIFTY", "instrument": "CE", "expiry": "2026-06-30", "strike": 23700,
 "transaction_type": "SELL", "quantity": 65}
```

`expiry` accepts `2026-06-30`, `20260630`, or `30-06-2026` — all equivalent.
For a future, drop `strike` and use `"instrument": "FUT"`. Example output:

```
  NIFTY 2026-06-30 23700 CE    span=141,673   exp=30,612   prem=0
  SPAN=141,673   Exposure=30,612   TOTAL=172,285
```

The examples below use tradingsymbols, but you can swap any leg for the
field-based form.

### Single-leg

**Long 1 lot NIFTY future**
```python
eng.basket([
  {"tradingsymbol": "NIFTY26JUNFUT", "transaction_type": "BUY", "quantity": 65},
])
```
```
  NIFTY26JUNFUT        span=143,720   exp=30,873   prem=0
  SPAN=143,720   Exposure=30,873   TOTAL=174,593
```

**Sell 1 lot NIFTY 23700 CE (naked short call)**
```python
eng.basket([
  {"tradingsymbol": "NIFTY26JUN23700CE", "transaction_type": "SELL", "quantity": 65},
])
```
```
  NIFTY26JUN23700CE    span=141,673   exp=30,612   prem=0
  SPAN=141,673   Exposure=30,612   TOTAL=172,285
```

### Multi-leg (futures + options netted, with margin benefit)

**Short straddle — sell 23700 CE + 23700 PE**
```python
eng.basket([
  {"tradingsymbol": "NIFTY26JUN23700CE", "transaction_type": "SELL", "quantity": 65},
  {"tradingsymbol": "NIFTY26JUN23700PE", "transaction_type": "SELL", "quantity": 65},
])
```
```
  NIFTY26JUN23700CE    span=141,673   exp=30,612   prem=0
  NIFTY26JUN23700PE    span=138,269   exp=30,612   prem=0
  SPAN=141,673   Exposure=61,224   TOTAL=202,898   | margin_benefit=138,269
```

**Protective put — buy future + buy 23000 PE** (a hedge: margin drops sharply)
```python
eng.basket([
  {"tradingsymbol": "NIFTY26JUNFUT",    "transaction_type": "BUY", "quantity": 65},
  {"tradingsymbol": "NIFTY26JUN23000PE","transaction_type": "BUY", "quantity": 65},
])
```
```
  NIFTY26JUNFUT        span=143,720   exp=30,873   prem=0
  NIFTY26JUN23000PE    span=0         exp=0        prem=10,212   (long option: premium only)
  SPAN=48,030    Exposure=30,873   TOTAL=78,904    | margin_benefit=95,689
```

**Iron condor — sell 24000 CE / 23000 PE, buy 24500 CE / 22500 PE** (4 legs)
```python
eng.basket([
  {"tradingsymbol": "NIFTY26JUN24000CE", "transaction_type": "SELL", "quantity": 65},
  {"tradingsymbol": "NIFTY26JUN24500CE", "transaction_type": "BUY",  "quantity": 65},
  {"tradingsymbol": "NIFTY26JUN23000PE", "transaction_type": "SELL", "quantity": 65},
  {"tradingsymbol": "NIFTY26JUN22500PE", "transaction_type": "BUY",  "quantity": 65},
])
```
```
  NIFTY26JUN24000CE    span=123,105   exp=30,612   prem=0
  NIFTY26JUN24500CE    span=0         exp=0        prem=8,135
  NIFTY26JUN23000PE    span=100,744   exp=30,612   prem=0
  NIFTY26JUN22500PE    span=0         exp=0        prem=5,038
  SPAN=31,244    Exposure=61,224   TOTAL=92,468    | margin_benefit=192,604
```

> See `example.py` for 10 strategies (adds short strangle, covered call,
> calendar spread, explicit-field input, and 2-lot sizing).

## Install

```bash
pip install -e .          # from a clone
# or drop the marginism/ folder on your PYTHONPATH (stdlib only)
```

Requires Python 3.8+.

## Getting a SPAN file

SPAN parameter files are published by the exchange clearing corporation
(e.g. NSE Clearing's `nsccl.YYYYMMDD.*.spn`) and revised 6 times each trading
day. Point the engine at whichever revision you need; the latest end-of-day
(settlement) file matches broker calculators most closely.

## Documentation & examples

- `example.py` — 10 worked trader strategies with margins and benefit.
- `marginism/README.md` — full API reference, file-path tips (macOS/Windows),
  and the SPAN algorithm details.

## Disclaimer

This software is provided "as is" for informational and educational purposes.
Margin figures are estimates derived from the supplied SPAN file and configured
exposure rates; always verify against your broker before trading. Not financial
advice. See [LICENSE](LICENSE) (MIT).
