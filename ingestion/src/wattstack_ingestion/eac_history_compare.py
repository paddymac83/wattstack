"""Run wattstack's EAC clearing on historical auctions and compare it with
what NESO published.

The comparison has four independent lenses, because "do the numbers
match?" alone cannot say WHY they do not:

1. RECONCILIATION  -- does the published data agree with itself?
   (executed = quantity x ratio, sell volume = buy volume = summary
   volume, order prices = summary prices, results-by-unit = order
   totals.) A failure here means a misread column, not a clearing issue.
2. ACTUAL-vs-RULES -- does the REAL outcome satisfy wattstack's rules
   (the independent checker `verify_clearing`, run on NESO's published
   acceptance ratios and prices)? A violation names the rule wattstack
   models differently from NESO, or an input it has wrong. Also shows
   how many paradoxically accepted buy orders NESO really allows per
   product-window, i.e. which overholding mode it behaves like.
3. WELFARE         -- the real outcome's welfare against the simulated
   optimum. If the real outcome is feasible under wattstack's rules and
   the simulated welfare is HIGHER, the real auction faced a constraint
   wattstack lacks (flexible groups, linked windows ...). Equal welfare
   with different orders is just a tie.
4. PRICES/VOLUMES  -- per product-window: cleared volume and price.

Order-level acceptance is reported too, but is the weakest lens: with
ties many acceptance sets share one welfare.
"""
from __future__ import annotations

import csv
import json
import math
import re
import time as _time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path

from wattstack_ingestion.eac_auction import (
    ClearingResult,
    OverholdingMode,
    clear_auction_by_component,
    verify_clearing,
)
from wattstack_ingestion.eac_history import (
    ALL_PRODUCTS,
    AuctionCase,
    HistoryData,
    Key,
    UnitRow,
    build_case,
    day_bounds_utc,
    tainted_keys,
)

_PARADOX_TOL = 5e-3


@dataclass(frozen=True)
class Tolerances:
    price: float = 0.011  # published prices are rounded to 0.01; sim price is rounded up before comparing
    volume_mw: float = 0.05
    ratio: float = 0.01
    rule_check: float = 5e-3  # tolerance for the independent checker on the published outcome


# ---------------------------------------------------------------------------
# Actual outcome
# ---------------------------------------------------------------------------


@dataclass
class ActualOutcome:
    sell_ratio: dict[str, float]
    buy_ratio: dict[str, float]
    price: dict[Key, float]
    volume: dict[Key, float]
    price_source: dict[Key, str]


def actual_outcome(case: AuctionCase) -> ActualOutcome:
    sell_ratio = {r.order_id: (0.0 if r.status == "EXCLUDED" else r.acceptance_ratio) for r in case.sell_rows}
    buy_ratio = {r.order_id: (0.0 if r.status == "EXCLUDED" else r.acceptance_ratio) for r in case.buy_rows}
    in_model_sells = case.sell_row_by_id
    price: dict[Key, float] = {}
    source: dict[Key, str] = {}
    volume: dict[Key, float] = defaultdict(float)
    for s in case.summary_rows:
        if s.key not in price:
            price[s.key] = s.clearing_price
            source[s.key] = "summary"
            volume[s.key] = s.cleared_volume
    executed_by_key: dict[Key, float] = defaultdict(float)
    order_price: dict[Key, float] = {}
    for r in in_model_sells.values():
        executed_by_key[r.key] += r.executed_quantity
        if r.executed_quantity > 0:
            order_price.setdefault(r.key, r.clearing_price)
    all_keys = set(executed_by_key)
    all_keys |= {r.key for r in case.buy_row_by_id.values()}
    for k in all_keys:
        if k not in price:
            price[k] = order_price.get(k, 0.0)
            source[k] = "orders" if k in order_price else "none (0.0)"
            volume[k] = executed_by_key.get(k, 0.0)
    return ActualOutcome(sell_ratio, buy_ratio, price, dict(volume), source)


def welfare_of(case: AuctionCase, sell_ratio: dict[str, float], buy_ratio: dict[str, float]) -> float:
    gain = sum(b.quantity_mw * b.price * buy_ratio.get(b.order_id, 0.0) for b in case.buy_inputs)
    cost = sum(s.quantity_mw * s.price_limit * sell_ratio.get(s.order_id, 0.0) for s in case.sell_inputs)
    return gain - cost


def actual_as_result(case: AuctionCase, actual: ActualOutcome) -> ClearingResult:
    prices = list(actual.price.values()) + [s.price_limit for s in case.sell_inputs] + [b.price for b in case.buy_inputs]
    bounds = (min([0.0] + prices), max([0.0] + prices))
    return ClearingResult(
        acceptance_ratio_by_order_id={s.order_id: actual.sell_ratio.get(s.order_id, 0.0) for s in case.sell_inputs},
        buy_acceptance_ratio_by_order_id={b.order_id: actual.buy_ratio.get(b.order_id, 0.0) for b in case.buy_inputs},
        clearing_price_by_product_window=dict(actual.price), price_bounds=bounds,
        welfare=welfare_of(case, actual.sell_ratio, actual.buy_ratio),
    )


def _violation_category(message: str) -> str:
    m = re.match(r"rule (\d+)", message)
    if m:
        return f"rule {m.group(1)}"
    for prefix, label in (
        ("volume balance", "volume balance"), ("price ", "price outside bounds"),
        ("restricted overholding", "restricted overholding cap"), ("buy family", "buy family > 1"),
    ):
        if message.startswith(prefix):
            return label
    if "paradoxically accepted but the buyer forbids" in message:
        return "paradox flag violated"
    if "outside [0,1]" in message:
        return "ratio outside [0,1]"
    return message[:30]


@dataclass
class RuleCheck:
    violations: list[str]  # under PERMISSIVE (the loosest mode): hard rule breaks
    by_category: Counter
    cap_violations: list[str]  # extra breaks only under the RESTRICTED cap
    paradox_histogram: Counter  # number of paradoxically accepted buys per key -> keys
    paradox_by_key: dict[Key, list[str]]


def check_actual(case: AuctionCase, actual: ActualOutcome, tol: float) -> RuleCheck:
    result = actual_as_result(case, actual)
    perm = verify_clearing(case.sell_inputs, case.buy_inputs, result, OverholdingMode.PERMISSIVE, tolerance=tol)
    rest = verify_clearing(case.sell_inputs, case.buy_inputs, result, OverholdingMode.RESTRICTED, tolerance=tol)
    extra = [v for v in rest if v not in set(perm)]
    paradox: dict[Key, list[str]] = defaultdict(list)
    for b in case.buy_inputs:
        k = (b.auction_product, b.window_start, b.window_end)
        if actual.buy_ratio.get(b.order_id, 0.0) > tol and actual.price.get(k, 0.0) > b.price + tol:
            paradox[k].append(b.order_id)
    hist = Counter(min(len(ids), 3) for ids in paradox.values())
    keys_with_buys = {(b.auction_product, b.window_start, b.window_end) for b in case.buy_inputs}
    hist[0] = len(keys_with_buys) - len(paradox)
    return RuleCheck(perm, Counter(_violation_category(v) for v in perm), extra, hist, dict(paradox))


# ---------------------------------------------------------------------------
# Reconciliation of the dataset against itself
# ---------------------------------------------------------------------------


@dataclass
class Finding:
    kind: str
    where: str
    detail: str
    magnitude: float


def reconcile(case: AuctionCase, units: list[UnitRow], tol: Tolerances) -> list[Finding]:
    out: list[Finding] = []
    for r in case.sell_rows + case.buy_rows:  # executed = quantity x ratio
        is_sell = hasattr(r, "unit")
        if r.status == "EXCLUDED":
            if r.executed_quantity > tol.volume_mw:
                out.append(Finding("excluded_row_has_volume", f"{'sell' if is_sell else 'buy'} {r.order_id}",
                                   f"EXCLUDED but executedQuantity={r.executed_quantity}", r.executed_quantity))
            continue
        expected = r.quantity * r.acceptance_ratio
        if abs(r.executed_quantity - expected) > max(tol.volume_mw, 2e-3 * r.quantity):
            out.append(Finding(
                "executed_not_quantity_x_ratio", f"{'sell' if is_sell else 'buy'} {r.order_id}",
                f"executed {r.executed_quantity} vs quantity {r.quantity} x ratio {r.acceptance_ratio}",
                abs(r.executed_quantity - expected),
            ))
    for r in case.sell_rows:
        if r.order_type == "PARENT" and r.status != "EXCLUDED" and min(abs(r.acceptance_ratio), abs(1 - r.acceptance_ratio)) > 1e-3:
            out.append(Finding("parent_ratio_fractional", f"sell {r.order_id}", f"ratio {r.acceptance_ratio}", r.acceptance_ratio))

    sell_exec: dict[Key, float] = defaultdict(float)
    buy_exec: dict[Key, float] = defaultdict(float)
    for r in case.sell_row_by_id.values():
        sell_exec[r.key] += r.executed_quantity
    for r in case.buy_row_by_id.values():
        buy_exec[r.key] += r.executed_quantity
    summary = {s.key: s for s in case.summary_rows}
    for k in sorted(set(sell_exec) | set(buy_exec)):
        if abs(sell_exec.get(k, 0.0) - buy_exec.get(k, 0.0)) > max(tol.volume_mw, 1e-3 * sell_exec.get(k, 0.0)):
            out.append(Finding("sell_volume_not_buy_volume", _key_str(k),
                               f"sell {sell_exec.get(k, 0.0):.3f} vs buy {buy_exec.get(k, 0.0):.3f}",
                               abs(sell_exec.get(k, 0.0) - buy_exec.get(k, 0.0))))
        s = summary.get(k)
        if s is not None:
            if abs(s.cleared_volume - sell_exec.get(k, 0.0)) > max(tol.volume_mw, 1e-3 * s.cleared_volume):
                out.append(Finding("summary_volume_not_order_volume", _key_str(k),
                                   f"summary {s.cleared_volume:.3f} vs sell orders {sell_exec.get(k, 0.0):.3f}",
                                   abs(s.cleared_volume - sell_exec.get(k, 0.0))))
            prices = {round(r.clearing_price, 4) for r in case.sell_row_by_id.values() if r.key == k and r.executed_quantity > 0}
            for p in prices:
                if abs(p - s.clearing_price) > 0.0051:
                    out.append(Finding("order_price_not_summary_price", _key_str(k),
                                       f"executed order price {p} vs summary {s.clearing_price}", abs(p - s.clearing_price)))
        elif sell_exec.get(k, 0.0) > tol.volume_mw:
            out.append(Finding("window_missing_from_summary", _key_str(k),
                               f"{sell_exec[k]:.3f} MW executed but no summary row", sell_exec[k]))
    duplicate_summary = Counter(s.key for s in case.summary_rows)
    for k, n in duplicate_summary.items():
        if n > 1:
            out.append(Finding("duplicate_summary_rows", _key_str(k), f"{n} summary rows for one product-window", n))

    if units:  # results by unit vs order totals
        case_keys = set(sell_exec) | set(summary)
        by_unit: dict[tuple[str, Key], float] = defaultdict(float)
        for u in units:
            if u.key in case_keys:
                by_unit[(u.unit, u.key)] += u.executed_quantity
        orders_by_unit: dict[tuple[str, Key], float] = defaultdict(float)
        for r in case.sell_row_by_id.values():
            if r.executed_quantity > 0:
                orders_by_unit[(r.unit, r.key)] += r.executed_quantity
        for uk in sorted(set(by_unit) | set(orders_by_unit), key=str):
            a, b = by_unit.get(uk, 0.0), orders_by_unit.get(uk, 0.0)
            if abs(a - b) > max(tol.volume_mw, 1e-3 * max(a, b)):
                out.append(Finding("unit_result_not_order_volume", f"{uk[0]} {_key_str(uk[1])}",
                                   f"results-by-unit {a:.3f} vs sell orders {b:.3f}", abs(a - b)))
    return out


def _key_str(k: Key) -> str:
    return f"{k[0]} {k[1]:%Y-%m-%d %H:%M}-{k[2]:%H:%M}"


# ---------------------------------------------------------------------------
# Simulation + comparison of one auction
# ---------------------------------------------------------------------------


@dataclass
class SimRun:
    mode: OverholdingMode
    result: ClearingResult | None
    error: str | None
    wall_seconds: float


def simulate(
    case: AuctionCase,
    mode: OverholdingMode,
    *,
    price_bounds: tuple[float, float] | None = None,
    time_limit_s: float | None = None,
    mip_gap: float | None = None,
) -> SimRun:
    started = _time.perf_counter()
    try:
        result = clear_auction_by_component(
            case.sell_inputs, case.buy_inputs, overholding_mode=mode, price_bounds=price_bounds,
            time_limit_s=time_limit_s, mip_gap=mip_gap,
        )
        return SimRun(mode, result, None, _time.perf_counter() - started)
    except (RuntimeError, ValueError) as exc:  # includes PricingInfeasibleError
        return SimRun(mode, None, f"{type(exc).__name__}: {exc}"[:600], _time.perf_counter() - started)


def _ceil_cent(value: float) -> float:
    return math.ceil(round(value * 100, 6)) / 100


@dataclass
class AuctionComparison:
    case: AuctionCase
    sim: SimRun
    actual: ActualOutcome
    rules: RuleCheck
    findings: list[Finding]
    welfare_actual: float
    welfare_sim: float | None
    window_rows: list[dict]
    sell_rows: list[dict]
    buy_rows: list[dict]
    stats: dict


def compare_auction(
    case: AuctionCase, units: list[UnitRow], sim: SimRun, tol: Tolerances = Tolerances()
) -> AuctionComparison:
    actual = actual_outcome(case)
    rules = check_actual(case, actual, tol.rule_check)
    findings = reconcile(case, units, tol)
    welfare_actual = welfare_of(case, actual.sell_ratio, actual.buy_ratio)
    res = sim.result
    tainted = tainted_keys(case)

    sell_by_key: dict[Key, list] = defaultdict(list)
    for s in case.sell_inputs:
        sell_by_key[(s.auction_product, s.window_start, s.window_end)].append(s)

    window_rows = []
    keys = sorted(set(actual.price) | set(sell_by_key) | {(b.auction_product, b.window_start, b.window_end) for b in case.buy_inputs})
    for k in keys:
        a_vol, a_price = actual.volume.get(k, 0.0), actual.price.get(k, 0.0)
        row = {
            "auction_id": case.auction_id, "product": k[0], "start": k[1].isoformat(), "end": k[2].isoformat(),
            "actual_volume_mw": round(a_vol, 4), "actual_price": a_price, "price_source": actual.price_source.get(k, ""),
            "affected_by_unmodelled_feature": k in tainted, "linked_service_window": k in case.flagged_keys,
        }
        if res is not None:
            s_vol = sum(s.quantity_mw * res.acceptance_ratio_by_order_id.get(s.order_id, 0.0) for s in sell_by_key.get(k, []))
            s_price = _ceil_cent(res.clearing_price_by_product_window.get(k, 0.0))
            both_idle = a_vol <= tol.volume_mw and s_vol <= tol.volume_mw
            row.update({
                "sim_volume_mw": round(s_vol, 4), "sim_price": s_price,
                "volume_diff_mw": round(s_vol - a_vol, 4),
                "price_diff": None if both_idle else round(s_price - a_price, 4),
                "volume_match": abs(s_vol - a_vol) <= tol.volume_mw,
                "price_match": True if both_idle else abs(s_price - a_price) <= tol.price,
                "both_idle": both_idle,
            })
        window_rows.append(row)

    sell_rows, buy_rows = [], []
    if res is not None:
        for s in case.sell_inputs:
            r = case.sell_row_by_id[s.order_id]
            a, m = actual.sell_ratio.get(s.order_id, 0.0), res.acceptance_ratio_by_order_id.get(s.order_id, 0.0)
            sell_rows.append({
                "auction_id": case.auction_id, "order_id": s.order_id, "unit": s.auction_unit, "basket_id": s.basket_id,
                "order_type": s.order_type, "product": s.auction_product, "start": s.window_start.isoformat(),
                "quantity_mw": s.quantity_mw, "price_limit": s.price_limit, "actual_status": r.status,
                "actual_ratio": a, "sim_ratio": m, "ratio_diff": round(m - a, 4), "match": abs(m - a) <= tol.ratio,
                "actual_reason": r.reason_rejected or "", "flagged_unmodelled": s.order_id in case.flagged_sell_ids,
            })
        for b in case.buy_inputs:
            r = case.buy_row_by_id[b.order_id]
            a, m = actual.buy_ratio.get(b.order_id, 0.0), res.buy_acceptance_ratio_by_order_id.get(b.order_id, 0.0)
            buy_rows.append({
                "auction_id": case.auction_id, "order_id": b.order_id, "product": b.auction_product,
                "start": b.window_start.isoformat(), "quantity_mw": b.quantity_mw, "price": b.price,
                "family": b.substitutability_family or "", "paradox_allowed": b.paradoxical_acceptance_allowed,
                "actual_status": r.status, "actual_ratio": a, "sim_ratio": m, "ratio_diff": round(m - a, 4),
                "match": abs(m - a) <= tol.ratio, "actual_reason": r.reason_rejected or "",
            })

    stats = _stats(case, res, window_rows, sell_rows, welfare_actual, rules, tol)
    return AuctionComparison(
        case, sim, actual, rules, findings, welfare_actual, stats.get("welfare_sim"), window_rows, sell_rows, buy_rows, stats
    )


def _rate(rows: list[dict], field_name: str) -> float | None:
    live = [r for r in rows if not r.get("both_idle")]
    return (sum(1 for r in live if r[field_name]) / len(live)) if live else None


def _stats(case, res, window_rows, sell_rows, welfare_actual, rules, tol) -> dict:
    stats: dict = {
        "auction_id": case.auction_id, "sell_rows": case.census.n_sell_rows, "buy_rows": case.census.n_buy_rows,
        "windows": len(window_rows), "components": case.census.n_components,
        "largest_component_orders": case.census.largest_component_orders,
        "actual_rule_violations": len(rules.violations), "actual_cap_violations": len(rules.cap_violations),
        "welfare_actual": round(welfare_actual, 2),
    }
    if res is None:
        return stats
    clean = [r for r in window_rows if not r["affected_by_unmodelled_feature"]]
    flagged = [r for r in window_rows if r["affected_by_unmodelled_feature"]]
    # Like with like: both welfares are computed from ratio vectors rounded the
    # same way (the published data is rounded; the engine's result is rounded
    # to 4 decimals), so rounding alone can never show up as a welfare gap.
    welfare_sim = welfare_of(case, res.acceptance_ratio_by_order_id, res.buy_acceptance_ratio_by_order_id)
    stats.update({
        "welfare_sim": round(welfare_sim, 2), "welfare_gap": round(welfare_sim - welfare_actual, 2) + 0.0,
        "welfare_gap_pct": (round(100 * (welfare_sim - welfare_actual) / abs(welfare_actual), 3) + 0.0 if abs(welfare_actual) > 1e-9 else None),
        "optimality_proven": res.optimality_proven, "subproblems": res.n_subproblems,
        "solve_seconds": round(res.solve_seconds, 2),
        "price_match_rate": _rate(window_rows, "price_match"), "volume_match_rate": _rate(window_rows, "volume_match"),
        "price_match_rate_clean": _rate(clean, "price_match"), "volume_match_rate_clean": _rate(clean, "volume_match"),
        "price_match_rate_flagged": _rate(flagged, "price_match"), "volume_match_rate_flagged": _rate(flagged, "volume_match"),
        "windows_clean": len(clean), "windows_flagged": len(flagged),
    })
    moved = sum(abs(r["ratio_diff"]) * r["quantity_mw"] for r in sell_rows)
    accepted = sum(r["actual_ratio"] * r["quantity_mw"] for r in sell_rows)
    stats["sell_mw_moved_vs_actual"] = round(moved, 2)
    stats["sell_mw_accepted_actual"] = round(accepted, 2)
    stats["sell_order_agreement"] = (sum(1 for r in sell_rows if r["match"]) / len(sell_rows)) if sell_rows else None
    return stats


# ---------------------------------------------------------------------------
# Day runner
# ---------------------------------------------------------------------------


@dataclass
class DayRun:
    day: date
    basis: str
    modes: list[OverholdingMode]
    excluded: str
    product_subset: tuple[str, ...] | None
    time_limit_s: float | None
    auction_ids: list[int]
    comparisons: list[AuctionComparison]
    cases: dict[int, AuctionCase]
    tolerances: Tolerances


def run_day(
    data: HistoryData,
    day: date,
    *,
    basis: str = "efa",
    modes: tuple[OverholdingMode, ...] = (OverholdingMode.RESTRICTED,),
    auction_ids: list[int] | None = None,
    products: list[str] | None = None,
    excluded: str = "drop",
    price_bounds: tuple[float, float] | None = None,
    time_limit_s: float | None = None,
    mip_gap: float | None = None,
    strict: bool = False,
    tolerances: Tolerances = Tolerances(),
    log=lambda msg: None,
) -> DayRun:
    window = day_bounds_utc(day, basis)
    ids = auction_ids if auction_ids else data.auction_ids_in(window)
    if not ids:
        raise ValueError(f"no auctions found delivering in {window[0]} .. {window[1]} (UTC)")
    comparisons: list[AuctionComparison] = []
    cases: dict[int, AuctionCase] = {}
    for auction_id in ids:
        case = build_case(auction_id, data, products=products, excluded=excluded, strict=strict)
        cases[auction_id] = case
        log(
            f"auction {auction_id}: {case.census.n_sell_rows} sell / {case.census.n_buy_rows} buy rows, "
            f"{case.census.n_keys} product-windows, {case.census.n_components} independent sub-markets "
            f"(largest {case.census.largest_component_orders} orders)"
        )
        for mode in modes:
            sim = simulate(case, mode, price_bounds=price_bounds, time_limit_s=time_limit_s, mip_gap=mip_gap)
            log(f"  {mode.value}: " + (f"solved in {sim.wall_seconds:.1f}s" if sim.error is None else f"FAILED {sim.error[:120]}"))
            comparisons.append(compare_auction(case, data.units, sim, tolerances))
    return DayRun(day, basis, list(modes), excluded, tuple(products) if products else None, time_limit_s,
                  list(ids), comparisons, cases, tolerances)


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------


def _write_rows(path: Path, rows: list[dict]) -> None:
    if not rows:
        path.write_text("")
        return
    fields = list(rows[0])
    for r in rows[1:]:
        for k in r:
            if k not in fields:
                fields.append(k)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _pct(x) -> str:
    return "n/a" if x is None else f"{100 * x:.1f}%"


def _md(headers: list[str], rows: list[list]) -> str:
    out = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    out += ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
    return "\n".join(out)


def write_outputs(run: DayRun, out_dir: Path) -> Path:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    auctions, windows, sells, buys, findings = [], [], [], [], []
    for c in run.comparisons:
        mode = c.sim.mode.value
        auctions.append({"mode": mode, "sim_error": c.sim.error or "", **c.stats})
        windows += [{"mode": mode, **r} for r in c.window_rows]
        sells += [{"mode": mode, **r} for r in c.sell_rows]
        buys += [{"mode": mode, **r} for r in c.buy_rows]
        if c.sim.mode is run.modes[0]:
            findings += [{"auction_id": c.case.auction_id, "kind": f.kind, "where": f.where, "detail": f.detail,
                          "magnitude": round(f.magnitude, 4)} for f in c.findings]
    _write_rows(out_dir / "auctions.csv", auctions)
    _write_rows(out_dir / "windows.csv", windows)
    _write_rows(out_dir / "sell_orders.csv", sells)
    _write_rows(out_dir / "buy_orders.csv", buys)
    _write_rows(out_dir / "reconciliation_findings.csv", findings)
    report = render_report(run)
    (out_dir / "report.md").write_text(report)
    (out_dir / "run.json").write_text(json.dumps({
        "day": run.day.isoformat(), "basis": run.basis, "modes": [m.value for m in run.modes], "excluded": run.excluded,
        "product_subset": run.product_subset, "time_limit_s": run.time_limit_s, "auction_ids": run.auction_ids,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "tolerances": run.tolerances.__dict__,
    }, indent=2))
    return out_dir / "report.md"


def render_report(run: DayRun) -> str:
    L: list[str] = []
    L.append(f"# EAC clearing backtest -- delivery day {run.day} ({run.basis} day)\n")
    L.append(
        f"Modes: {', '.join(m.value for m in run.modes)} | EXCLUDED orders: {run.excluded} | "
        f"auctions: {', '.join(map(str, run.auction_ids))} | solver time limit: {run.time_limit_s or 'none'}\n"
    )
    if run.product_subset:
        L.append(f"> **PRODUCT SUBSET {run.product_subset}: smoke test only.** Cross-product baskets and units lost their "
                 "partners, so these results are NOT comparable with the published ones.\n")
    errs = [c for c in run.comparisons if c.sim.error]
    for c in errs:
        L.append(f"> **Auction {c.case.auction_id} ({c.sim.mode.value}) FAILED to clear:** `{c.sim.error}`\n")
    unproven = [c for c in run.comparisons if c.sim.result is not None and not c.sim.result.optimality_proven]
    for c in unproven:
        L.append(f"> Auction {c.case.auction_id} ({c.sim.mode.value}): solver time limit hit -- welfare is a LOWER bound, not proven optimal.\n")
    for c in run.comparisons[:: len(run.modes)]:
        for issue in c.case.issues:
            L.append(f"> Data issue, auction {c.case.auction_id}: **{issue.kind}** -- {issue.detail}\n")

    L.append("## 1. Headline\n")
    rows = []
    for c in run.comparisons:
        s = c.stats
        if c.sim.result is None:
            rows.append([s["auction_id"], c.sim.mode.value, "FAILED", "", "", "", "", ""])
            continue
        rows.append([
            s["auction_id"], c.sim.mode.value, f"{s['welfare_actual']:,.0f}", f"{s['welfare_sim']:,.0f}",
            f"{s['welfare_gap']:+,.0f}" + (f" ({s['welfare_gap_pct']:+.2f}%)" if s["welfare_gap_pct"] is not None else ""),
            f"{_pct(s['price_match_rate'])} / {_pct(s['volume_match_rate'])}",
            f"{_pct(s['price_match_rate_clean'])} / {_pct(s['volume_match_rate_clean'])}",
            f"{s['solve_seconds']}s ({s['subproblems']} sub-markets)",
        ])
    L.append(_md(["auction", "mode", "welfare actual", "welfare wattstack", "gap", "price / volume match (all windows)",
                  "price / volume match (windows free of unmodelled features)", "solve"], rows))
    L.append("\nWelfare = sum(buy qty x price x ratio) - sum(sell qty x limit x ratio), in GBP/h. Gap = wattstack - actual.\n")

    L.append("## 2. What the real outcome says about wattstack's rules\n")
    L.append("`verify_clearing` run on NESO's own published ratios and prices.\n")
    for c in run.comparisons[:: len(run.modes)]:
        r = c.rules
        L.append(f"**Auction {c.case.auction_id}:** " + (
            "the real outcome satisfies every wattstack rule (checked under the loosest overholding mode)."
            if not r.violations else f"{len(r.violations)} violations -- {dict(r.by_category)}"))
        hist = r.paradox_histogram
        L.append(f"Paradoxically accepted buy orders per product-window in the real outcome: 0 -> {hist.get(0, 0)} windows, "
                 f"1 -> {hist.get(1, 0)}, 2 -> {hist.get(2, 0)}, 3+ -> {hist.get(3, 0)}. "
                 + ("Consistent with RESTRICTED overholding." if not r.cap_violations else
                    "More than one in some windows: NESO behaves like PERMISSIVE overholding."))
        for v in r.violations[:8]:
            L.append(f"- {v}")
        if len(r.violations) > 8:
            L.append(f"- ... {len(r.violations) - 8} more (see windows.csv / rerun with the same data)")
        L.append("")

    L.append("## 3. By product\n")
    for mode in run.modes:
        per: dict[str, list[dict]] = defaultdict(list)
        for c in run.comparisons:
            if c.sim.mode is mode:
                for r in c.window_rows:
                    if "price_match" in r:
                        per[r["product"]].append(r)
        rows = []
        for p in sorted(per, key=lambda x: ALL_PRODUCTS.index(x) if x in ALL_PRODUCTS else 99):
            rs = per[p]
            live = [r for r in rs if not r["both_idle"]]
            diffs = [abs(r["price_diff"]) for r in live if r["price_diff"] is not None]
            rows.append([
                p, len(rs), len(live), _pct(_rate(rs, "price_match")), _pct(_rate(rs, "volume_match")),
                f"{sum(diffs) / len(diffs):.3f}" if diffs else "n/a",
                sum(1 for r in rs if r["affected_by_unmodelled_feature"]),
            ])
        L.append(f"**{mode.value}**\n")
        L.append(_md(["product", "windows", "with volume", "price match", "volume match", "mean abs price diff",
                      "windows with unmodelled features"], rows))
        L.append("")

    L.append("## 4. Largest disagreements (by volume)\n")
    for mode in run.modes:
        worst = sorted(
            (r for c in run.comparisons if c.sim.mode is mode for r in c.window_rows if "volume_diff_mw" in r and not r["volume_match"]),
            key=lambda r: -abs(r["volume_diff_mw"]),
        )[:10]
        L.append(f"**{mode.value}** -- {len(worst)} shown\n")
        if worst:
            L.append(_md(["auction", "product", "window start (UTC)", "actual MW", "wattstack MW", "actual price", "wattstack price", "unmodelled?"],
                         [[r["auction_id"], r["product"], r["start"], r["actual_volume_mw"], r["sim_volume_mw"], r["actual_price"],
                           r["sim_price"], "yes" if r["affected_by_unmodelled_feature"] else "no"] for r in worst]))
        L.append("")

    L.append("## 5. Does the published data agree with itself? (reconciliation)\n")
    for c in run.comparisons[:: len(run.modes)]:
        kinds = Counter(f.kind for f in c.findings)
        L.append(f"**Auction {c.case.auction_id}:** " + ("all checks passed." if not kinds else str(dict(kinds))))
        for f in c.findings[:5]:
            L.append(f"- {f.kind}: {f.where} -- {f.detail}")
        L.append("")
    L.append("A failure here means a column is not what this code assumes (fix the reading, not the clearing).\n")

    L.append("## 6. What is in the data\n")
    for c in run.comparisons[:: len(run.modes)]:
        ce = c.case.census
        L.append(f"**Auction {c.case.auction_id}**\n")
        L.append(f"- Sell rows {ce.n_sell_rows} {dict(ce.sell_status)}; buy rows {ce.n_buy_rows} {dict(ce.buy_status)}; order types {dict(ce.order_types)}")
        L.append(f"- Products and number of windows: {ce.products}")
        L.append(f"- Looped baskets {ce.n_looped_baskets} (dangling links {ce.n_dangling_loop_links}); buy families {ce.n_buy_families}")
        L.append(f"- Independent sub-markets {ce.n_components}; largest has {ce.largest_component_orders} orders over {ce.largest_component_keys} product-windows")
        if ce.price_range:
            L.append(f"- Order price range {ce.price_range[0]} .. {ce.price_range[1]}; observed clearing prices "
                     f"{min(c.actual.price.values(), default=0)} .. {max(c.actual.price.values(), default=0)}")
        if ce.reason_codes:
            L.append(f"- Reason codes: {dict(ce.reason_codes.most_common(12))}")
        if ce.unmodelled:
            L.append(f"- **Not modelled by clear_auction, present in this auction:** {ce.unmodelled}")
        if ce.window_length_anomalies:
            L.append(f"- Window lengths differing from the expected 4 h (Response) / 30 min (Reserve): {ce.window_length_anomalies}")
        if ce.unknown_products:
            L.append(f"- Unknown product codes: {ce.unknown_products}")
        L.append("")

    L.append("## 7. How to read this\n")
    L.append(
        "- **Gap 0 and every window matching** is the goal. A small price difference with identical volumes is usually "
        "Section 7.2 price indeterminacy or rounding, not an error.\n"
        "- **Real outcome violates a wattstack rule** -> look at section 2 first: that rule (or the input behind it) is "
        "modelled differently from NESO.\n"
        "- **Real outcome feasible, wattstack welfare higher** -> the real auction had a constraint wattstack lacks. "
        "Compare the clean-window match rate with the all-window rate: a gap between them points at the unmodelled "
        "features listed in section 6.\n"
        "- **Wattstack welfare lower than the real outcome's** (and proven optimal) -> wattstack is over-constrained.\n"
        "- Order-level differences with equal welfare are ties, not errors.\n"
    )
    return "\n".join(L)
