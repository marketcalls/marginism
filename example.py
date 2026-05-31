"""span_margin — worked examples for traders.

Run:  python example.py

Every strategy below is just a list of orders (by tradingsymbol, like a broker
order window). The engine nets futures + options of the same underlying and
shows SPAN, exposure, total margin, and the MARGIN BENEFIT from hedging.

quantity is entered DIRECTLY in units (NIFTY lot size = 65, so 65 = 1 lot,
130 = 2 lots, ...).
"""

import os

from span_margin import RiskEngine

# ----------------------------------------------------------------------
# WHERE IS YOUR .spn FILE?  Pick one of these.
# ----------------------------------------------------------------------
# (a) File in the SAME folder as this script:
#         SPN = "nsccl.20260529.s.spn"
#
# (b) Robust "same folder as this script" (works regardless of CWD):
SPN = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                   "nsccl.20260529.s", "nsccl.20260529.s.spn")
#
# (c) A DIFFERENT folder — macOS / Linux (forward slashes):
#         SPN = "/Users/you/Downloads/nsccl.20260529.s.spn"
#
# (d) A DIFFERENT folder — Windows. Use a raw string r"..." so backslashes
#     are not treated as escapes (or use forward slashes, which also work):
#         SPN = r"C:\Users\you\Downloads\nsccl.20260529.s.spn"
#         SPN = "C:/Users/you/Downloads/nsccl.20260529.s.spn"
# ----------------------------------------------------------------------

# Load the SPAN file once; reuse it for every calculation.
eng = RiskEngine.from_file(SPN)

LOT = 65  # NIFTY lot size (enter quantity directly; 2 lots -> 130)


def B(ts, qty=LOT):   # BUY  leg
    return {"exchange": "NFO", "tradingsymbol": ts, "transaction_type": "BUY", "quantity": qty}


def S(ts, qty=LOT):   # SELL leg
    return {"exchange": "NFO", "tradingsymbol": ts, "transaction_type": "SELL", "quantity": qty}


def show(title, orders):
    d = eng.basket(orders)["data"]
    print("\n" + "=" * 64)
    print(title)
    print("=" * 64)
    for o in d["orders"]:
        print(f"  {o['tradingsymbol']:<22} span={o['span']:>11,.0f}  "
              f"exp={o['exposure']:>9,.0f}  prem={o['option_premium']:>8,.0f}")
    f = d["final"]
    print("  " + "-" * 60)
    print(f"  SPAN margin     : {f['span']:>14,.2f}")
    print(f"  Exposure margin : {f['exposure']:>14,.2f}")
    print(f"  TOTAL MARGIN    : {f['total']:>14,.2f}")
    if d["margin_benefit"] > 0:
        print(f"  MARGIN BENEFIT  : {d['margin_benefit']:>14,.2f}   "
              f"(saved vs holding each leg outright)")


# ----------------------------------------------------------------------
# 1. SINGLE LEG — one futures lot (quantity 65 = 1 lot)
show("1) Long 1 lot NIFTY future",
     [B("NIFTY26JUNFUT")])

# 2. SINGLE LEG — sell one option (naked)
show("2) Sell 1 lot NIFTY 23700 CE (naked short call)",
     [S("NIFTY26JUN23700CE")])

# 3. SHORT STRADDLE — sell ATM call + ATM put (same strike)
show("3) Short straddle — sell 23700 CE + 23700 PE",
     [S("NIFTY26JUN23700CE"), S("NIFTY26JUN23700PE")])

# 4. SHORT STRANGLE — sell OTM call + OTM put
show("4) Short strangle — sell 24500 CE + 23000 PE",
     [S("NIFTY26JUN24500CE"), S("NIFTY26JUN23000PE")])

# 5. COVERED CALL — long future + short call (futures + option combo)
show("5) Covered call — buy future + sell 24000 CE",
     [B("NIFTY26JUNFUT"), S("NIFTY26JUN24000CE")])

# 6. PROTECTIVE PUT — long future + long put (downside hedge)
show("6) Protective put — buy future + buy 23000 PE",
     [B("NIFTY26JUNFUT"), B("NIFTY26JUN23000PE")])

# 7. CALENDAR SPREAD — long near future + short far future
show("7) Calendar spread — buy JUN future + sell JUL future",
     [B("NIFTY26JUNFUT"), S("NIFTY26JULFUT")])

# 8. IRON CONDOR — sell a strangle, buy wings to cap risk (4 option legs)
show("8) Iron condor — sell 24000 CE / 23000 PE, buy 24500 CE / 22500 PE",
     [S("NIFTY26JUN24000CE"), B("NIFTY26JUN24500CE"),
      S("NIFTY26JUN23000PE"), B("NIFTY26JUN22500PE")])

# ----------------------------------------------------------------------
# Two ways to name a contract — both resolve to the same thing:
#   compact   : NIFTY26JUN23700CE     (monthly)   NIFTY2660223700CE (weekly)
#   full-date : NIFTY30JUN2623700CE
# And you can skip tradingsymbols entirely and pass fields directly:
show("9) Same short call, specified by explicit fields",
     [{"symbol": "NIFTY", "instrument": "CE", "expiry": "2026-06-30",
       "strike": 23700, "transaction_type": "SELL", "quantity": LOT}])

# 2 lots = quantity 130
show("10) Sell 2 lots NIFTY 23700 CE (quantity 130)",
     [S("NIFTY26JUN23700CE", qty=130)])
