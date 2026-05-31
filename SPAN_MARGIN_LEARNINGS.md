# SPAN Margin — Engineering Knowledge Base

> Working notes for building the margin SDK. Captures the SPAN file format,
> the margin algorithm, and validated learnings (incl. a broker cross-check).
> Status: **experimental**. Treat rates/assumptions as defaults to confirm
> against live exchange circulars.

---

## 1. The big picture

A broker's **upfront initial margin** for F&O is:

```
Initial margin = SPAN margin  +  Exposure (ELM)  +  Adhoc (when imposed)
```

- **SPAN margin** = the *risk-based* margin. Computed entirely from the
  exchange's daily **SPAN parameter file** (`.spn`). This is the hard part and
  the part our SDK owns.
- **ELM / Exposure** = a flat % of notional. **NOT in the SPAN file** — set by
  exchange circular. Index ≈ **2%**, stock ≈ **3.5%**.
- **Adhoc / additional** = extra % imposed case-by-case in volatile conditions.
  Also not in the file.

> VAR vs SPAN: In the **equity cash** segment the risk margin is an explicit
> *VAR margin*. In **F&O** the SPAN scan risk *is* the VAR-equivalent (scenario
> based). So there is no separate "VAR" line for F&O — SPAN replaces it.

---

## 2. The files

NSCCL (NSE Clearing) publishes daily SPAN files; we received a zipped set:

| File | Meaning |
|---|---|
| `nsccl.YYYYMMDD.s.spn`   | **Settlement** (end-of-day) file |
| `nsccl.YYYYMMDD.i1..i5`  | **Intraday** snapshots (5 per day) |
| `spanrisk.xml`           | The **XSD schema** documenting the `.spn` format |

**SPAN is revised 6× per trading day** (exchange re-scans volatility each time):
1 before market open (`i01`, generated the night before) + 4 intraday every
~1.5h (`i02`–`i05`) + 1 EOD settlement (`s`). Use the `<created>` tag to know a
file's snapshot time. **Which file to load = whichever revision is current at
calc time**; on a weekend/holiday the latest is the prior session's `s` file.
Empirically the *same* NIFTY future scanned across the 6 files spans ~₹2,200
(₹1,43,720 settlement → ₹1,45,928 i01) purely from intraday price/scan drift —
**this snapshot choice is the main source of any gap vs a broker calculator.**

- Format: **XML, `fileFormat 4.00`** (CME SPAN format). Plain tags, no
  namespaces, CRLF line endings.
- Size: settlement file ≈ **50 MB**, ~2.2 million risk-array values → must be
  **streamed** (`iterparse` + clear), never fully DOM-parsed casually.
- Broker calculators may use a specific intraday snapshot; expect tiny diffs
  (~0.04%) between `.s` and `.i*` because the underlying/scan price differs.

---

## 3. XML structure (the parts that matter)

```
spanFile
├─ fileFormat, created
├─ definitions            (currencies, account types, groups)
└─ pointInTime
   ├─ date, isSetl
   └─ clearingOrg (ec=NSE…)
      ├─ pointDef           (scan point / delta point defs — scenario grid)
      ├─ exchange
      │  ├─ phyPf  …        PHYsical (cash/underlying) portfolio
      │  ├─ futPf  …        FUTures portfolio  → <fut> contracts
      │  └─ oopPf  …        Options-On-Physical → <series> → <opt> contracts
      ├─ ccDef  (×N)        Combined-Commodity definitions (margining unit)
      └─ interSpreads       Inter-commodity spreads (EMPTY/template for NSCCL)
```

### Contract record (futures) — `futPf > fut`
- `pfCode` = trading symbol (e.g. `HDFCLIFE`); `pfId` = SPAN portfolio id.
- Per `fut`: `cId` (contract id), `pe` (expiry `YYYYMMDD`), `p` (price),
  `d` (delta=1), `v` (vol), `cvf` (contract value factor = **1.00** for NSE eq),
  `scanRate` (`priceScan`, `volScan`), and `ra` = **16-value risk array** + a
  trailing `d` = **composite delta**.

### Contract record (options) — `oopPf > series > opt`
- `series` carries `pe` (expiry) for all its options.
- Per `opt`: `cId`, `o` (`C`/`P`), `k` (strike), `p` (premium), `d` (BS delta),
  `v` (vol), `ra` (16 values + composite delta).
- `priceModel = BS`, `exercise = EURO`.

### Combined commodity — `ccDef`
- `cc` = the commodity code = **trading symbol** (on NSE `cc == pfCode`).
- `pfLink` entries tie the PHY + FUT + OOP portfolios of one underlying together
  → **futures and options of the same underlying are margined as one unit.**
- `somMeth` (GROSS), `somTiers` → Short Option Minimum rate (**0** in our files).
- `dSpread` (×many) = **calendar (intra-commodity) spread** definitions.
- `adjRate` = scaling factors per account/scenario type (all **1.0** here → identity).

### Calendar spread — `dSpread`
```
spread        priority / evaluation order
chargeMeth    'F' flat | 'P' per-month | 'W' weighted-price   (NSCCL uses 'F')
rate.val      charge per spread (currency, per matched delta unit)
pLeg × 2      cc, pe (expiry), rs ('A'/'B' side), i (ratio, =1.0)
```
e.g. NIFTY ₹420/spread, BANKNIFTY ₹969, RELIANCE ₹30, HDFCLIFE ₹14.

---

## 4. The SPAN algorithm (per combined commodity)

```
commodity_risk = scan_risk
               + calendar_spread_charge
               + spot_charge            # 0 in NSCCL files
               - intercommodity_credit  # 0 in NSCCL files
span_margin = max( 0,
                   max(commodity_risk, short_option_minimum)   # SOM=0 in NSCCL
                   - net_option_value )                        # <-- critical!
```

> **The Net Option Value subtraction is essential** (see 4.5). Without it, short
> options are under-margined by the premium and long options are over-margined.
> Validated against a broker calculator (see §6).

### 4.1 The 16 scenarios (risk array order)
Each `ra` holds the per-1-unit-long P/L (**positive = loss**) under:

| # | Price move | Vol | | # | Price move | Vol |
|---|---|---|---|---|---|---|
| 1 | unch | up   | | 9  | −2/3 | up   |
| 2 | unch | down | | 10 | −2/3 | down |
| 3 | +1/3 | up   | | 11 | +3/3 | up   |
| 4 | +1/3 | down | | 12 | +3/3 | down |
| 5 | −1/3 | up   | | 13 | −3/3 | up   |
| 6 | −1/3 | down | | 14 | −3/3 | down |
| 7 | +2/3 | up   | | 15 | +extreme (cover fraction baked in) |
| 8 | +2/3 | down | | 16 | −extreme (cover fraction baked in) |

"Move" = fraction of the **price-scan range**. The extreme rows (15/16) already
include the exchange cover fraction (~35%), so just take them as-is.

### 4.2 Scan risk
```
loss[j] = Σ_positions ( signed_qty × ra[j] )      for j in 0..15
scan_risk = max(0, max_j loss[j])
```
- `signed_qty`: long +, short −. Short loss is the negation of the long array,
  which `signed_qty × ra` handles automatically.
- The **same scenario index** is applied across all contracts in the commodity
  simultaneously (perfectly correlated underlying move) — this is why summing
  `ra[j]` across expiries/strikes is valid.
- **No option pricing needed** — the arrays are precomputed by the exchange.

### 4.3 Calendar (intra-commodity) spread charge
Scan assumes all expiries move identically, so a long-near / short-far book
shows ≈0 scan; the charge adds back basis risk.
```
net_delta[pe] = Σ_positions ( signed_qty × composite_delta )   per expiry
for spread in spreads (priority order):
    A, B = the two legs (by side 'A'/'B')
    dA, dB = net_delta[A.pe], net_delta[B.pe]
    if signs of dA, dB are OPPOSITE:
        n = min(|dA|/ratioA, |dB|/ratioB)     # spreads formed (delta units)
        charge += n × rate                    # method 'F'
        consume n from each leg's net_delta (toward zero)
```

### 4.4 Short Option Minimum (SOM)
`SOM = som_rate × (total short option units)`. **`som_rate = 0` in our NSCCL
files**, so SOM is inactive — but keep the term for other exchanges/dates.

### 4.5 Net Option Value (NOV) — and why it's subtracted ⚠️
`NOV = Σ ( signed_qty × option_price × cvf )`.
- **Net short** options → NOV **< 0** (you owe the premium).
- **Net long** options → NOV **> 0** (you own the premium).

CME SPAN's final step is **`margin = max(0, SPAN_risk − NOV)`**:
- Short call ATM: `113,226 − (−28,447) = 141,673` → premium *raises* margin.
- Long call: `risk(≈premium) − (+premium) ≈ 0` → long options need only the
  premium, no SPAN/exposure. This falls out automatically.

This subtraction was **initially missed** and is the single most important
correction. The premium ("premium receivable" on broker screens) is shown separately by
brokers but is *already inside* the SPAN number via −NOV.

---

## 5. Exposure (ELM) & Adhoc — the non-SPAN add-ons

```
exposure_margin = elm_rate × CONTRACT VALUE
adhoc_margin    = adhoc_rate × CONTRACT VALUE
```
**Contract value = price × qty, but the *price* differs by instrument** (per
broker/NSE convention — "Spot price × Lot size"):
- **Futures** → use the **futures price** × qty. (Matched the broker calculator *exactly*.)
- **Options** → use the **underlying SPOT price** × qty — **NOT the premium.**
  (Spot comes from the commodity's PHY portfolio; another early bug was using
  premium notional, which under-charged options ~50×.)

Rates (validated): index **2%**, stock **3.5%** (stock alt: 1.5σ of 6-mo log
returns). Applied to **futures and SHORT options**; **long options carry no ELM**
(their risk array still nets in the scan, but `SPAN−NOV` already zeroes them).
**Adhoc** defaults to 0; supply per-symbol when imposed.

---

## 6. Validation — cross-check vs a broker calculator

Position: **1 lot NIFTY future, lot size 65, expiry 2026-06-30**, settlement file.

| Component | Our SDK | Broker | Diff |
|---|---|---|---|
| SPAN     | ₹1,43,720 | ₹1,43,666 | ₹54 (0.04%) |
| Exposure | ₹30,873   | ₹30,873   | **₹0 ✓** |
| Total    | ₹1,74,593 | ₹1,74,539 | ₹54 |

- Exposure matched **exactly** once ELM was set to **2%** (we had wrongly used
  3% initially — that was the key correction).
- The ₹54 SPAN gap is the `.s` vs intraday snapshot price difference; within
  tolerance. **The algorithm is faithful.**

Position 2: **SELL 1 lot NIFTY 23700 CE, 30-Jun-2026** (lot 65):

| Component | Our SDK | Broker | Diff |
|---|---|---|---|
| SPAN     | ₹1,41,673 | ₹1,41,962 | 0.2% |
| Exposure | ₹30,612   | ₹31,197   | 1.9% |
| Premium  | ₹28,447   | ₹28,447   | **exact** |
| Total    | ₹1,72,285 | ₹1,73,159 | 0.5% |

- Premium matched to the rupee → contract/strike/expiry matching is correct.
- The SPAN match **only worked after adding the −NOV step** (§4.5).
- Residual diffs are price-snapshot: exposure implies the broker used spot ≈
  23,997.7 vs the settlement file's PHY spot 23,547.75 (live vs EOD). Method is
  confirmed correct.
- Sanity: **BUY** the same call → SDK returns **₹0** margin (premium only). ✓

---

## 7. Hard-won gotchas (read before extending)

0. **Subtract Net Option Value** from SPAN risk (`margin = max(0, risk − NOV)`),
   and **charge option exposure on SPOT × qty, not premium × qty.** These two
   were the biggest bugs found via the 23700 CE cross-check (§4.5, §5).
1. **Lot sizes are NOT in the SPAN file.** It works in *underlying units*
   (cvf=1). Sourced separately from **`NSE LotSize.csv`** (month-wise columns,
   e.g. `Lot Size (Jun 2026)`), loaded by `lotsize.py` → `LotSizeTable`. Pass
   `lot_size_csv=` to `SpanCalculator.from_file` and specify positions in
   `lots=`; the SDK resolves `lots × lot_size(symbol, expiry-month)`. Confirmed
   NIFTY=65, BANKNIFTY=30, RELIANCE=500. (`quantity=` raw units still works.)
2. **No exchange tokens in the file.** Instruments keyed only by *trading
   symbol* (`cc`/`pfCode`). `pfId`/`cId` are SPAN-internal, **not** NSE tokens
   (e.g. RELIANCE token 2885 is nowhere in the file). Need an external
   instrument master to map token → symbol.
3. **`cc == pfCode`** on NSE → safe to group PHY/FUT/OOP by code.
4. **Composite delta** (trailing `d` in `ra`) is what drives spreads — *not* the
   BS delta field on the option.
5. **Stream the file.** 50 MB / 2.2M values. Parse with a **symbol filter** when
   only a few underlyings are needed (≈2.8s for 4 symbols vs much more for all).
6. **NSCCL inter-commodity spreads & spot charges are empty** in these files —
   keep the terms in code (other exchanges/dates may populate them).
7. `adjRate` are all 1.0 (identity) here, but represent account-type/scenario
   scaling — honor them if a future file is non-trivial.
8. The scan **price-scan range can be wide** (~9% NIFTY on this date) — don't
   "sanity-cap" it; trust the file's arrays.

---

## 8. What the SDK does NOT model yet (funding / settlement side)

The SDK computes the **requirement**. The following govern **available funds and
timing** (per SEBI upfront-margin & peak-margin rules) and are out of scope today:

- **Available margin / collateral** (cash, pledged holdings + haircuts).
- **Peak-margin snapshots & penalty** (upfront margin mandatory since 2020-09-01;
  shortfall at any intraday snapshot → penalty).
- **Settlement-timing of credits:** holdings-sale proceeds 100% same-day (from
  2024-10-07, early pay-in of shares), intraday profit only after T+1, option
  sell-credit usable same-day only to buy options in the same segment.

These change *which money you can post and when* — **not** the SPAN/ELM number.

---

## 9. Roadmap / open questions for the SDK

- [ ] Token → symbol adapter (wire to an external instrument master).
- [x] Lot-size master — `lotsize.py` loads `NSE LotSize.csv` (month-wise). TODO:
      automate refresh / source from a live feed.
- [ ] Confirm ELM rates per segment from live circulars; per-symbol stock ELM
      (some stocks > 3.5% by volatility).
- [ ] Confirm calendar-spread charge unit (per delta-unit vs per "lot-spread") on
      more cases; we currently treat `rate` as **per matched delta unit** and it
      reproduced sensible NIFTY/stock numbers — validate vs broker spread quotes.
- [ ] Delivery margin / physical-settlement ramp-up near expiry for stocks
      (separate NSCCL rule, not in scan).
- [ ] Cross-validate options (short straddle/strangle, ratio, hedged) vs broker.
- [ ] Decide which `.spn` snapshot the SDK should pull to match broker exactly.
- [ ] Optional funding/peak-margin module (sec. 8) as a separate layer.

---

## 10. Current code layout (`marginism/`)

| Module | Responsibility |
|---|---|
| `parser.py`     | streaming `iterparse` of `.spn` → model (symbol filter) |
| `model.py`      | dataclasses: `SpanFile`, `CombinedCommodity`, contracts, risk arrays |
| `algorithm.py`  | SPAN math: scan risk, calendar spreads, SOM, NOV |
| `portfolio.py`  | `Position` input + expiry normalisation |
| `exposure.py`   | ELM + adhoc config (index 2% / stock 3.5%) |
| `calculator.py` | `SpanCalculator` — load once, evaluate many portfolios |
| `symbols.py`    | NFO tradingsymbol <-> SPAN contract resolution (generated index) |
| `api.py`        | `RiskEngine` — `basket()`/`orders()`, single or many legs |
| `cli.py`        | `python -m marginism` |

Quantity is entered directly in units (lots × lot size); pure stdlib, runs
offline. (Lot-size CSV and HTTP-server layers were removed — local Python only.)

**Segment scope:** the engine is **exchange-agnostic** — the `.spn` is a generic
CME-SPAN file. The same `RiskEngine` works for **NFO, CDS (currency), and MCX
(commodity)**; just load that segment's SPAN file. Per-segment differences to
configure: ELM rates, lot sizes, and the tradingsymbol convention. The file used
during development (`nsccl.*`) is **NFO only** (239 equity/index commodities; no
currency pairs).

---

## 11. Official NSE Clearing SPAN methodology (reference)

Condensed from NSE Clearing's NSCCL SPAN page
(https://www.nseclearing.in/risk-management/equity-derivatives/nsccl-span).
This is the authoritative description the SDK implements against.

**Objective.** SPAN identifies the largest loss a futures+options portfolio
might reasonably suffer from one day to the next, and sets the margin to cover
that one-day loss. It treats futures and options uniformly while capturing
option-specific risks (deep-OTM shorts, inter-month, inter-commodity).

**Risk arrays.** The complex option pricing (Black-Scholes; rate = relevant
MIBOR or specified) is done by the Clearing Corporation, not by members. The
results — how each contract gains/loses over the one-day "look-ahead" under each
risk scenario — are the **risk arrays**, shipped daily in the **SPAN Risk
Parameter file**. Members just apply these arrays to their portfolio. **Losses
are positive, gains are negative**, expressed in INR.

**16 risk scenarios** = last settlement price scanned ±1/3, ±2/3, ±3/3 of the
**price scan range**, each at volatility up/down (14), plus two **extreme** moves
of **double** the price scan range covering only **35%** of the loss (15, 16).
The extreme rows exist so deep-OTM short options that could jump into-the-money
are still charged. Scanning Risk Charge = the **largest loss** across the 16.

**Price scan range** (probable price move; "6σ scaled by √2" basis):
- **Index**: 6σ × √2, **min 9.3%** of underlying (index options with residual
  maturity > 9 months: **min 17.7%**).
- **Stock**: 6σ × √2, **min 14.2%** of underlying.
- **Liquidity scale-up**: if mean impact cost for a ₹5 lakh order > 1%, the
  price scan range is scaled by **√3** (computed on the 15th monthly, rolling
  6-month order-book snapshots; effective 3rd working day after the 15th).
- These ranges are already baked into the file's risk arrays — the SDK does not
  recompute them, it consumes them.

**Composite delta.** SPAN uses one delta per contract — the probability-weighted
average of the deltas at each price scan point, estimated *after* the look-ahead
day. Futures delta = 1.0; option deltas ∈ [−1, +1]. Used to form spreads.

**Calendar (inter-month) spread charge.** Scanning assumes perfect correlation
across contract months; since that doesn't truly hold, SPAN adds a per-spread
charge on the deltas spread across months. Calendar treatment applies until the
near-month expiry. (In the NSCCL files: flat charge, method `F`.)

**Net Option Value (NOV).** Only short positions are margined for scan risk;
long positions give offsetting benefit via the NOV. **Total SPAN margin = SPAN
Risk Requirement − net option value** (MTM value of long minus short options).

**Initial margin** collected by the member = SPAN margin + Exposure (ELM)
[+ adhoc/additional when imposed]. (Exposure/ELM and lot sizes are *not* in the
SPAN file — they come from exchange circulars.)

---

## Reference & trademark

Methodology reference: NSE Clearing — NSCCL SPAN,
https://www.nseclearing.in/risk-management/equity-derivatives/nsccl-span

This is an independent project by an independent developer — **not affiliated
with, sponsored by, endorsed by, or connected to** NSE, NSE Clearing (NSCCL),
the Chicago Mercantile Exchange (CME), or any broker or exchange.

SPAN® is a registered trademark of Chicago Mercantile Exchange Inc. All other
trademarks are the property of their respective owners. Any names are used only
for identification/descriptive purposes (nominative use) and do not imply any
affiliation, endorsement, or license.
