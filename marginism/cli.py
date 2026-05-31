"""Command-line interface: ``python -m marginism``.

Examples
--------
List symbols::

    python -m marginism nsccl.20260529.s/nsccl.20260529.s.spn --list

Show contracts for a symbol::

    python -m marginism <file.spn> --info NIFTY

Compute margin for positions (symbol:instrument:qty:expiry[:strike])::

    python -m marginism <file.spn> \
        --pos NIFTY:FUT:-65:20260630 \
        --pos NIFTY:CE:65:20260630:24000
"""

from __future__ import annotations

import argparse
import sys
from typing import List

from .calculator import SpanCalculator
from .portfolio import Position


def _parse_pos(spec: str) -> Position:
    parts = spec.split(":")
    if len(parts) < 3:
        raise SystemExit(f"bad --pos {spec!r}; need SYMBOL:INSTR:QTY[:EXPIRY[:STRIKE]]")
    symbol, instrument, qty = parts[0], parts[1], float(parts[2])
    expiry = parts[3] if len(parts) > 3 else None
    strike = float(parts[4]) if len(parts) > 4 else 0.0
    return Position(symbol, instrument, qty, expiry=expiry, strike=strike)


def main(argv: List[str] = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    ap = argparse.ArgumentParser(prog="marginism", description=__doc__)
    ap.add_argument("spn", help="path to the .spn SPAN file")
    ap.add_argument("--list", action="store_true", help="list combined commodities")
    ap.add_argument("--info", metavar="SYMBOL", help="show contracts for a symbol")
    ap.add_argument(
        "--pos",
        action="append",
        default=[],
        metavar="SYMBOL:INSTR:QTY[:EXPIRY[:STRIKE]]",
        help="a position; repeatable",
    )
    args = ap.parse_args(argv)

    symbols = None
    if args.info:
        symbols = [args.info]
    elif args.pos:
        symbols = sorted({p.split(":")[0] for p in args.pos})

    calc = SpanCalculator.from_file(args.spn, symbols=symbols)

    if args.list:
        for s in calc.span_file.symbols:
            print(s)
        return 0

    if args.info:
        c = calc.span_file.get(args.info)
        if c is None:
            print(f"{args.info} not found", file=sys.stderr)
            return 1
        print(f"{c.cc}  ({c.name})  currency={c.currency}  som_rate={c.som_rate}")
        print(f"futures ({len(c.futures)}):")
        for f in sorted(c.futures, key=lambda x: x.expiry):
            print(f"  {f.expiry}  price={f.price:>12,.2f}  scan(maxloss/unit)="
                  f"{max(f.risk_array.values):>10,.2f}")
        exps = sorted({o.expiry for o in c.options})
        print(f"option expiries ({len(exps)}): {', '.join(exps)}")
        return 0

    if args.pos:
        positions = [_parse_pos(p) for p in args.pos]
        result = calc.calculate(positions)
        print(result.summary())
        return 0

    ap.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
