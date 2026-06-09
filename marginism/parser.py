"""Streaming parser for NSCCL / CME SPAN ``.spn`` files (XML, fileFormat 4.00).

The settlement file is ~50 MB with ~2 million risk-array values, so we parse
with ``iterparse`` and clear each top-level record as soon as it is consumed.
Pass ``symbols=`` to keep only the combined commodities you care about — this
makes parsing both fast and light when you only need a handful of underlyings.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import Iterable, Optional, Set

from .model import (
    CalendarSpread,
    CombinedCommodity,
    Contract,
    FuturesContract,
    OptionContract,
    RiskArray,
    SpanFile,
    SpreadLeg,
)


def _text(el: Optional[ET.Element], default: str = "") -> str:
    if el is None or el.text is None:
        return default
    return el.text.strip()


def _f(el: Optional[ET.Element], default: float = 0.0) -> float:
    t = _text(el)
    try:
        return float(t) if t else default
    except ValueError:
        return default


def _i(el: Optional[ET.Element], default: int = 0) -> int:
    t = _text(el)
    try:
        return int(t) if t else default
    except ValueError:
        return default


def _risk_array(ra_el: ET.Element) -> RiskArray:
    values = [float(a.text) for a in ra_el.findall("a")]
    comp_delta = _f(ra_el.find("d"))
    return RiskArray(values=values, composite_delta=comp_delta)


def _parse_futpf(el: ET.Element) -> list:
    cc = _text(el.find("pfCode"))
    pf_id = _i(el.find("pfId"))
    cvf_pf = _f(el.find("cvf"), 1.0)
    out = []
    for fut in el.findall("fut"):
        ra_el = fut.find("ra")
        if ra_el is None:
            continue
        out.append(
            FuturesContract(
                cc=cc,
                pf_id=pf_id,
                pf_type="FUT",
                contract_id=_i(fut.find("cId")),
                expiry=_text(fut.find("pe")),
                price=_f(fut.find("p")),
                delta=_f(fut.find("d"), 1.0),
                volatility=_f(fut.find("v")),
                cvf=_f(fut.find("cvf"), cvf_pf),
                risk_array=_risk_array(ra_el),
            )
        )
    return out


def _parse_ooppf(el: ET.Element) -> list:
    cc = _text(el.find("pfCode"))
    pf_id = _i(el.find("pfId"))
    cvf_pf = _f(el.find("cvf"), 1.0)
    out = []
    for series in el.findall("series"):
        expiry = _text(series.find("pe"))
        cvf_s = _f(series.find("cvf"), cvf_pf)
        for opt in series.findall("opt"):
            ra_el = opt.find("ra")
            if ra_el is None:
                continue
            out.append(
                OptionContract(
                    cc=cc,
                    pf_id=pf_id,
                    pf_type="OOP",
                    contract_id=_i(opt.find("cId")),
                    expiry=expiry,
                    price=_f(opt.find("p")),
                    delta=_f(opt.find("d")),
                    volatility=_f(opt.find("v")),
                    cvf=_f(opt.find("cvf"), cvf_s),
                    risk_array=_risk_array(ra_el),
                    option_type=_text(opt.find("o")),
                    strike=_f(opt.find("k")),
                )
            )
    return out


def _parse_phypf(el: ET.Element) -> list:
    cc = _text(el.find("pfCode"))
    pf_id = _i(el.find("pfId"))
    cvf_pf = _f(el.find("cvf"), 1.0)
    out = []
    for phy in el.findall("phy"):
        ra_el = phy.find("ra")
        if ra_el is None:
            # physical legs sometimes carry only a scanRate, not a full array
            continue
        out.append(
            Contract(
                cc=cc,
                pf_id=pf_id,
                pf_type="PHY",
                contract_id=_i(phy.find("cId")),
                expiry=_text(phy.find("pe")),
                price=_f(phy.find("p")),
                delta=_f(phy.find("d")),
                volatility=_f(phy.find("v")),
                cvf=_f(phy.find("cvf"), cvf_pf),
                risk_array=_risk_array(ra_el),
            )
        )
    return out


def _parse_ccdef(el: ET.Element) -> CombinedCommodity:
    cc = _text(el.find("cc"))
    cmty = CombinedCommodity(
        cc=cc,
        name=_text(el.find("name")),
        currency=_text(el.find("currency"), "INR"),
        som_method=_text(el.find("somMeth"), "GROSS"),
        spot_method=_text(el.find("spotMeth"), "NORMAL"),
        risk_exponent=_f(el.find("riskExponent")),
    )

    # Short Option Minimum: first non-zero rate across the SOM tiers.
    som_rate = 0.0
    som_tiers = el.find("somTiers")
    if som_tiers is not None:
        for tier in som_tiers.findall("tier"):
            for rate in tier.findall("rate"):
                v = _f(rate.find("val"))
                if v:
                    som_rate = v
    cmty.som_rate = som_rate

    for ds in el.findall("dSpread"):
        legs = [
            SpreadLeg(
                cc=_text(leg.find("cc")),
                expiry=_text(leg.find("pe")),
                side=_text(leg.find("rs")),
                ratio=_f(leg.find("i"), 1.0),
            )
            for leg in ds.findall("pLeg")
        ]
        rate_el = ds.find("rate")
        cmty.spreads.append(
            CalendarSpread(
                priority=_i(ds.find("spread")),
                charge_method=_text(ds.find("chargeMeth"), "F"),
                rate=_f(rate_el.find("val")) if rate_el is not None else 0.0,
                legs=legs,
            )
        )
    cmty.spreads.sort(key=lambda s: s.priority)
    return cmty


def parse_spn(
    path: str,
    symbols: Optional[Iterable[str]] = None,
) -> SpanFile:
    """Parse a SPAN ``.spn`` file into a :class:`SpanFile`.

    Parameters
    ----------
    path:
        Path to the ``.spn`` (XML) file.
    symbols:
        Optional iterable of combined-commodity codes (NSE trading symbols) to
        retain.  Everything else is skipped and discarded while streaming,
        which dramatically reduces time and memory.  ``None`` keeps everything.
    """
    wanted: Optional[Set[str]] = (
        {s.strip().upper() for s in symbols} if symbols is not None else None
    )

    sf = SpanFile()
    # Collect raw records keyed by cc/pfCode, then stitch together at the end.
    futures: dict = {}
    options: dict = {}
    physicals: dict = {}
    spot_price: dict = {}
    ccdefs: dict = {}

    def keep(code: str) -> bool:
        return wanted is None or code.upper() in wanted

    context = ET.iterparse(path, events=("end",))
    for _event, el in context:
        tag = el.tag
        if tag == "fileFormat":
            sf.file_format = _text(el)
        elif tag == "created":
            sf.created = _text(el)
        elif tag == "date":
            if not sf.business_date:
                sf.business_date = _text(el)
        elif tag == "isSetl":
            sf.is_settlement = _text(el) in ("1", "true", "True")
        elif tag == "ec":
            if not sf.clearing_org:
                sf.clearing_org = _text(el)
        elif tag == "futPf":
            code = _text(el.find("pfCode"))
            if keep(code):
                futures.setdefault(code, []).extend(_parse_futpf(el))
            el.clear()
        elif tag in ("oopPf", "oofPf"):
            # oopPf — options on physical/index (NSE/BSE)
            # oofPf — options on futures (MCX RPF format)
            # Both have the same series → opt → ra structure.
            code = _text(el.find("pfCode"))
            if keep(code):
                options.setdefault(code, []).extend(_parse_ooppf(el))
            el.clear()
        elif tag == "phyPf":
            code = _text(el.find("pfCode"))
            if keep(code):
                physicals.setdefault(code, []).extend(_parse_phypf(el))
                phy = el.find("phy")
                if phy is not None:
                    spot_price[code] = _f(phy.find("p"))
            el.clear()
        elif tag == "ccDef":
            code = _text(el.find("cc"))
            if keep(code):
                ccdefs[code] = _parse_ccdef(el)
            el.clear()

    # Stitch: a combined commodity exists for any cc that has a ccDef or any
    # parsed portfolio.  pfCode == cc on NSE, so we can merge by code.
    codes = set(ccdefs) | set(futures) | set(options) | set(physicals)
    for code in codes:
        cmty = ccdefs.get(code) or CombinedCommodity(cc=code, name=code)
        cmty.futures = futures.get(code, [])
        cmty.options = options.get(code, [])
        cmty.physicals = physicals.get(code, [])
        cmty.underlying_price = spot_price.get(code, 0.0)
        sf.commodities[code.upper()] = cmty

    return sf
