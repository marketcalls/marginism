# marginism

[![PyPI](https://img.shields.io/pypi/v/marginism.svg)](https://pypi.org/project/marginism/)
[![Python](https://img.shields.io/pypi/pyversions/marginism.svg)](https://pypi.org/project/marginism/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

**Know the exact margin for any F&O trade — on your own computer, instantly.**

Give it the exchange's daily SPAN file and your position(s); it returns the
**SPAN margin**, **Exposure margin**, **total margin**, and the **margin you save
by hedging** — the same numbers a broker's calculator shows. Offline, no login,
pure Python.

## Install

```bash
pip install marginism
```

## Quick start

```python
from marginism import RiskEngine

eng = RiskEngine.from_file("nsccl.20260529.s.spn")   # load once, reuse

result = eng.basket([
    {"tradingsymbol": "NIFTY26JUN23700CE", "transaction_type": "SELL", "quantity": 65},
])
print(result["data"]["final"]["total"])     # 172285
```

`quantity` is in units (lots × lot size; NIFTY 65 = 1 lot). `transaction_type`
is `BUY`/`SELL`. Pass one leg or many.

## Examples

```python
# Short straddle — sell 23700 CE + 23700 PE
eng.basket([
  {"tradingsymbol": "NIFTY26JUN23700CE", "transaction_type": "SELL", "quantity": 65},
  {"tradingsymbol": "NIFTY26JUN23700PE", "transaction_type": "SELL", "quantity": 65},
])
# TOTAL = 2,02,898   |   margin benefit = 1,38,269

# Protective put — buy future + buy 23000 PE (hedge lowers margin)
eng.basket([
  {"tradingsymbol": "NIFTY26JUNFUT",     "transaction_type": "BUY", "quantity": 65},
  {"tradingsymbol": "NIFTY26JUN23000PE", "transaction_type": "BUY", "quantity": 65},
])
# TOTAL = 78,904    |   margin benefit = 95,689
```

See [`example.py`](example.py) for 10 ready-made strategies (straddle, strangle,
covered call, calendar spread, iron condor, …).

## Don't use a tradingsymbol? Pass fields instead

```python
{"symbol": "NIFTY", "instrument": "CE", "expiry": "2026-06-30", "strike": 23700,
 "transaction_type": "SELL", "quantity": 65}
```

## Getting the SPAN file

Download the latest daily SPAN file from your exchange clearing house
(NSE F&O: `nsccl.YYYYMMDD.s.spn`) and point the engine at it:

```python
eng = RiskEngine.from_file("nsccl.20260529.s.spn")             # same folder
eng = RiskEngine.from_file(r"C:\Users\you\Downloads\file.spn") # Windows
```

Works for NFO, currency (CDS), and commodity (MCX) files.

## Disclaimer

Margin figures are estimates — always confirm with your broker before trading.
Not financial advice. MIT licensed. Full API details in
[`marginism/README.md`](marginism/README.md).
