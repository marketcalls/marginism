# span_margin

Compute **NSE / NSCCL SPAN margins** directly from the exchange's daily
CME-SPAN risk-parameter files (`.spn`, XML `fileFormat 4.00`), the same inputs a
broker's margin calculator uses.

`spanrisk.xml` in this folder is the **XSD schema** that documents the `.spn`
format; this library implements the SPAN algorithm against files that conform to
it (e.g. `nsccl.YYYYMMDD.s.spn`).

## Why this works without an option pricer

Each contract in a `.spn` file ships a **precomputed 16-scenario risk array** —
the per-unit profit/loss under 16 combinations of price move (±1/3, ±2/3, ±3/3
of the scan range, plus two "extreme" moves) and volatility up/down. SPAN margin
is therefore pure arithmetic over those arrays; no Black-Scholes is needed at
calculation time.

## The SPAN calculation

Per **combined commodity** (one underlying — futures + options margined
together):

```
span_risk = max( scan_risk
                 + calendar (intra-commodity) spread charge
                 + spot/delivery charge          # 0 in NSCCL files
                 - inter-commodity spread credit  # 0 in NSCCL files
               , short_option_minimum )
```

* **Scan risk** — the largest portfolio loss across the 16 scenarios:
  `max_j Σ (signed_qty × risk_array[j])`.
* **Calendar spread charge** — scan assumes all expiries move together, so a
  flat charge is added back for the basis risk of long-near / short-far
  positions (`dSpread` definitions, method `F`).
* **Short option minimum (SOM)** — a floor for short-option books
  (`som_rate = 0` in these NSCCL files).

A broker's **initial margin = SPAN margin + Exposure (ELM) margin**. Exposure
margin is *not* in the SPAN file (it's an exchange % of notional), so it is
configured in `ExposureConfig` and applied to futures and short options.

## Install / layout

Pure standard library (Python 3.8+), no dependencies. Drop the `span_margin/`
folder on your path (or `pip install -e .`).

## Pointing to your `.spn` file

You just give the path. Same folder or a different folder, macOS or Windows:

```python
# Same folder as your script
SPN = "nsccl.20260529.s.spn"

# Different folder — macOS / Linux (forward slashes)
SPN = "/Users/you/Downloads/nsccl.20260529.s.spn"

# Different folder — Windows (use a raw string r"..." or forward slashes)
SPN = r"C:\Users\you\Downloads\nsccl.20260529.s.spn"
SPN = "C:/Users/you/Downloads/nsccl.20260529.s.spn"
```

## Quick start

```python
from span_margin import SpanCalculator, Position

calc = SpanCalculator.from_file(
    SPN,
    symbols=["NIFTY", "RELIANCE"],   # parse only what you need (fast / light)
)

result = calc.calculate([
    # quantity is entered DIRECTLY in units: NIFTY lot size 65 -> 65 = 1 lot,
    # 130 = 2 lots. long +, short -
    Position("NIFTY", "CE", quantity=-65, expiry="20260630", strike=24000),
    Position("NIFTY", "PE", quantity=-65, expiry="20260630", strike=24000),
])

print(result.summary())
print(result.span_margin, result.exposure_margin, result.total_margin)
```

## Order-style API (single or multiple legs)

`RiskEngine` accepts orders by `tradingsymbol` and returns a broker-style dict
(per-leg + consolidated `initial`/`final` + `margin_benefit`). **Pure local
computation — no network, no service.**

```python
from span_margin import RiskEngine

eng = RiskEngine.from_file(SPN)

# one leg or many — a single order is just a basket of size one
res = eng.basket([
    {"exchange": "NFO", "tradingsymbol": "NIFTY26JUNFUT",
     "transaction_type": "BUY", "quantity": 65},
    {"exchange": "NFO", "tradingsymbol": "NIFTY26JUN23000PE",
     "transaction_type": "BUY", "quantity": 65},
])
data = res["data"]
print(data["final"]["total"], data["margin_benefit"])
```

`quantity` is entered **directly in units** (e.g. 65 for one NIFTY lot, 130 for
two); `transaction_type` is `BUY`/`SELL`. The engine is exchange-agnostic — load
an NFO, CDS, or MCX `.spn` file.

### Two symbol formats, plus explicit fields

A contract can be named two equivalent ways, and both resolve automatically:

| Style | Future | Option |
|---|---|---|
| compact   | `NIFTY26JUNFUT`    | `NIFTY26JUN23700CE` (monthly), `NIFTY2660223700CE` (weekly) |
| full-date | `NIFTY30JUN26FUT`  | `NIFTY30JUN2623700CE` |

Or skip tradingsymbols and pass fields directly:

```python
eng.basket([
    {"symbol": "NIFTY", "instrument": "CE", "expiry": "2026-06-30",
     "strike": 23700, "transaction_type": "SELL", "quantity": 65},
])
```

## Command line

```bash
python -m span_margin <file.spn> --list                 # all symbols
python -m span_margin <file.spn> --info NIFTY            # contracts/expiries
python -m span_margin <file.spn> \
    --pos NIFTY:FUT:-65:20260630 \
    --pos NIFTY:CE:65:20260630:24000                     # margin for positions
```

## Important notes

* **Lot sizes are not in the SPAN file.** It works in underlying units, so enter
  `quantity` directly in units (NIFTY 65 = 1 lot, 130 = 2 lots).
* **No exchange tokens in the file.** Instruments are keyed by *trading symbol*
  (`cc` / `pfCode`, e.g. `RELIANCE`), with internal `pfId`/`cId` ids that are
  **not** NSE tokens. Map a token (e.g. `2885` → `RELIANCE`) via a separate
  instrument master before calling this library.
* **Exposure rates** in `ExposureConfig` are NSE defaults (index 2%, stock
  ~3.5%); override per circular via `overrides={"RELIANCE": 0.05}`.
* **Long options** carry no exposure margin (risk capped at premium); their
  risk array still participates in the portfolio scan so hedges net correctly.
* `net_option_value` is the mark-to-market value of option legs (premium):
  negative when net short (premium received), positive when net long.

## Module map

| Module          | Responsibility                                            |
|-----------------|-----------------------------------------------------------|
| `parser.py`     | streaming `iterparse` of `.spn` → data model (symbol filter) |
| `model.py`      | dataclasses: `SpanFile`, `CombinedCommodity`, contracts, risk arrays |
| `algorithm.py`  | SPAN math: scan risk, calendar spreads, SOM, net option value |
| `portfolio.py`  | `Position` input + expiry normalisation                   |
| `exposure.py`   | exposure / ELM configuration (index 2% / stock 3.5%)      |
| `calculator.py` | `SpanCalculator` — load once, evaluate many portfolios    |
| `symbols.py`    | tradingsymbol ⇄ SPAN contract resolution                  |
| `api.py`        | `RiskEngine` — `basket()`/`orders()`, single or many legs |
| `cli.py`        | `python -m span_margin`                                   |

100% standard library, runs fully offline — give it a `.spn` file and call a
function.
```
