"""SYNTHETIC EAC day in the real NESO schema -- for smoke-testing the
history pipeline without network access, and for the unit tests.

It is NOT market data. A random market covering all 12 products is
generated, cleared with wattstack's own engine (RESTRICTED mode), and the
result is written out as if NESO had published it. Running the backtest
on this data should therefore reproduce the "actual" outcome exactly; any
mismatch is a pipeline bug (a misread column, a lost order), not a
market-design finding. It also shows the exact file layout and column
names that `wattstack-eac-history run` expects.
"""
from __future__ import annotations

import random
from datetime import date, datetime, timedelta
from pathlib import Path

from wattstack_ingestion.eac_auction import (
    BuyOrderInput,
    OverholdingMode,
    SellOrderInput,
    clear_auction_by_component,
)
from wattstack_ingestion.eac_history import (
    DATASET_FILES,
    RESERVE_PRODUCTS,
    RESPONSE_PRODUCTS,
    SELL_COLUMNS,
    BUY_COLUMNS,
    SERVICE_TYPE,
    SUMMARY_COLUMNS,
    UNIT_COLUMNS,
    _write_csv,
    day_bounds_utc,
)

PRODUCT_ID = {p: i + 1 for i, p in enumerate(RESPONSE_PRODUCTS + RESERVE_PRODUCTS)}


def synth_day(
    day: date,
    seed: int = 0,
    *,
    n_units: int = 14,
    response_windows: int = 3,
    reserve_windows: int = 4,
    auction_id: int | None = None,
    with_unmodelled_features: bool = False,
    baskets_per_unit: tuple[int, int] = (1, 3),
) -> dict[str, list[dict]]:
    """Returns {"sell_orders", "buy_orders", "results_summary",
    "results_by_unit"} -> list of NESO-schema records (typed values)."""
    rng = random.Random(seed)
    auction_id = auction_id if auction_id is not None else int(day.strftime("%Y%m%d"))
    start, _ = day_bounds_utc(day)
    resp_windows = [(start + timedelta(hours=4 * i), start + timedelta(hours=4 * (i + 1))) for i in range(response_windows)]
    resv_windows = [(start + timedelta(minutes=30 * i), start + timedelta(minutes=30 * (i + 1))) for i in range(reserve_windows)]

    sells: list[SellOrderInput] = []
    meta: dict[str, dict] = {}
    basket_no = 0
    order_no = 1000
    chain: dict[str, str] = {}  # unit -> last response basket id, to loop consecutive windows
    for u in range(n_units):
        unit = f"SYNTH_U{u:02d}"
        for _ in range(rng.randint(*baskets_per_unit)):
            reserve = rng.random() < 0.4
            windows, products = (resv_windows, RESERVE_PRODUCTS) if reserve else (resp_windows, RESPONSE_PRODUCTS)
            window = rng.choice(windows)
            product = rng.choice(products)
            basket_no += 1
            basket = f"{900000 + basket_no}"
            looped = None
            if not reserve and unit in chain and rng.random() < 0.5:
                looped = chain[unit]
            if not reserve:
                chain[unit] = basket
            order_no += 1
            sells.append(SellOrderInput(
                f"{order_no}", unit, basket, "PARENT", product, float(rng.choice([5, 10, 20, 50])),
                float(rng.randint(-2, 18)), window[0], window[1], looped,
            ))
            for _c in range(rng.randint(0, 2)):
                order_no += 1
                sells.append(SellOrderInput(
                    f"{order_no}", unit, basket, rng.choice(["CHILD", "SUBSTITUTABLECHILD"]),
                    rng.choice(products), float(rng.choice([5, 10])), float(rng.randint(0, 25)), window[0], window[1],
                ))
    # a unit's baskets that overlap in time exclude each other: keep the data realistic by
    # leaving it to the clearing rules (rule 6), not pre-filtering here.

    keys = sorted({(s.auction_product, s.window_start, s.window_end) for s in sells})
    buys: list[BuyOrderInput] = []
    buy_no = 5000
    for k in keys:
        price = float(rng.randint(25, 45))
        for _step in range(rng.randint(2, 4)):
            buy_no += 1
            buys.append(BuyOrderInput(
                f"{buy_no}", k[0], float(rng.choice([10, 20, 30, 40])), price, k[1], k[2], None,
                paradoxical_acceptance_allowed=rng.random() < 0.85,
            ))
            price = max(1.0, price - rng.randint(4, 12))
    # a zero-priced overholding order per response window, as the design report describes
    for p, w in {(s.auction_product, (s.window_start, s.window_end)) for s in sells if s.auction_product in RESPONSE_PRODUCTS}:
        buy_no += 1
        buys.append(BuyOrderInput(f"{buy_no}", p, 5.0, 0.0, w[0], w[1], None, True))
    families = 0
    by_window: dict[tuple, list[BuyOrderInput]] = {}
    for b in buys:
        by_window.setdefault((b.window_start, b.window_end), []).append(b)
    for ws, group in by_window.items():
        dcl = [b for b in group if b.auction_product == "DCL" and b.price > 0]
        dch = [b for b in group if b.auction_product == "DCH" and b.price > 0]
        if dcl and dch:
            families += 1
            dcl[0].substitutability_family = dch[0].substitutability_family = f"{700 + families}"

    result = clear_auction_by_component(sells, buys, overholding_mode=OverholdingMode.RESTRICTED)
    price_of = result.clearing_price_by_product_window
    ts = (start - timedelta(hours=20)).isoformat()

    sell_records = []
    for i, s in enumerate(sells):
        ratio = result.acceptance_ratio_by_order_id[s.order_id]
        k = (s.auction_product, s.window_start, s.window_end)
        sell_records.append({
            "auctionID": auction_id, "registeredAuctionParticipant": "SYNTHETIC", "auctionUnit": s.auction_unit,
            "basketID": s.basket_id, "serviceType": SERVICE_TYPE[s.auction_product],
            "deliveryStart": s.window_start.isoformat(), "deliveryEnd": s.window_end.isoformat(),
            "orderID": s.order_id, "orderType": s.order_type, "auctionProduct": s.auction_product,
            "productID": PRODUCT_ID[s.auction_product], "quantity": s.quantity_mw, "priceLimit": s.price_limit,
            "loopedBasketID": s.looped_basket_id, "orderEntryTime": ts, "acceptanceRatio": ratio,
            "status": "EXECUTED" if ratio > 0 else "REJECTED", "executedQuantity": round(s.quantity_mw * ratio, 4),
            "clearingPrice": price_of.get(k, 0.0) if ratio > 0 else 0.0,
            "reasonRejected": "" if ratio > 0 else "SYNTH_NOT_SELECTED",
            "flexibleGroupID": (f"{300 + i}" if with_unmodelled_features and s.auction_product in ("NSR", "PSR") else None),
            "minimumConsecutiveWindows": (4 if with_unmodelled_features and s.auction_product in ("NSR", "PSR") else None),
        })
    # EXCLUDED orders never enter the optimisation: published with zero outcome
    for j in range(2):
        s = rng.choice(sells)
        order_no += 1
        sell_records.append({
            "auctionID": auction_id, "registeredAuctionParticipant": "SYNTHETIC", "auctionUnit": f"SYNTH_X{j}",
            "basketID": f"{990000 + j}", "serviceType": SERVICE_TYPE[s.auction_product],
            "deliveryStart": s.window_start.isoformat(), "deliveryEnd": s.window_end.isoformat(),
            "orderID": f"{order_no}", "orderType": "PARENT", "auctionProduct": s.auction_product,
            "productID": PRODUCT_ID[s.auction_product], "quantity": 10.0, "priceLimit": 1.0, "loopedBasketID": None,
            "orderEntryTime": ts, "acceptanceRatio": 0.0, "status": "EXCLUDED", "executedQuantity": 0.0,
            "clearingPrice": 0.0, "reasonRejected": "SYNTH_INVALID", "flexibleGroupID": None, "minimumConsecutiveWindows": None,
        })

    buy_records = []
    for i, b in enumerate(buys):
        ratio = result.buy_acceptance_ratio_by_order_id[b.order_id]
        k = (b.auction_product, b.window_start, b.window_end)
        buy_records.append({
            "auctionID": auction_id, "orderID": b.order_id, "serviceType": SERVICE_TYPE[b.auction_product],
            "auctionProduct": b.auction_product, "deliveryStart": b.window_start.isoformat(),
            "deliveryEnd": b.window_end.isoformat(), "quantity": b.quantity_mw, "price": b.price,
            "substitutabilityFamily": b.substitutability_family,
            "paradoxicallyAcceptanceAllowed": "TRUE" if b.paradoxical_acceptance_allowed else "FALSE",
            "orderEntryTime": ts, "acceptanceRatio": ratio, "status": "EXECUTED" if ratio > 0 else "REJECTED",
            "executedQuantity": round(b.quantity_mw * ratio, 4), "clearingPrice": price_of.get(k, 0.0) if ratio > 0 else 0.0,
            "reasonRejected": "" if ratio > 0 else "SYNTH_NOT_SELECTED",
            "joinedFamily": (f"{800 + i}" if with_unmodelled_features and i == 0 else None),
        })

    summary_records = []
    for k in keys:
        volume = sum(s.quantity_mw * result.acceptance_ratio_by_order_id[s.order_id] for s in sells
                     if (s.auction_product, s.window_start, s.window_end) == k)
        summary_records.append({
            "auctionID": auction_id, "auctionProduct": k[0], "serviceType": SERVICE_TYPE[k[0]],
            "deliveryStart": k[1].isoformat(), "deliveryEnd": k[2].isoformat(), "clearedVolume": round(volume, 4),
            "clearingPrice": price_of.get(k, 0.0),
            "linkedServiceWindowID": (1 if with_unmodelled_features and k == keys[0] else None),
        })

    unit_totals: dict[tuple, float] = {}
    for s in sells:
        k = (s.auction_unit, s.auction_product, s.window_start, s.window_end)
        unit_totals[k] = unit_totals.get(k, 0.0) + s.quantity_mw * result.acceptance_ratio_by_order_id[s.order_id]
    unit_records = []
    for (unit, product, ws, we), mw in sorted(unit_totals.items()):
        if mw > 0:
            unit_records.append({
                "registeredAuctionParticipant": "SYNTHETIC", "auctionUnit": unit, "serviceType": SERVICE_TYPE[product],
                "auctionProduct": product, "executedQuantity": round(mw, 4),
                "clearingPrice": price_of.get((product, ws, we), 0.0), "deliveryStart": ws.isoformat(),
                "deliveryEnd": we.isoformat(), "technologyType": "Battery", "postCode": "", "unitResultID": f"{unit}-{product}-{ws:%H%M}",
            })
    return {"sell_orders": sell_records, "buy_orders": buy_records, "results_summary": summary_records,
            "results_by_unit": unit_records}


def write_synth(out_dir: Path, day: date, seed: int = 0, **kwargs) -> dict[str, int]:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    data = synth_day(day, seed, **kwargs)
    columns = {"sell_orders": SELL_COLUMNS, "buy_orders": BUY_COLUMNS, "results_summary": SUMMARY_COLUMNS,
               "results_by_unit": UNIT_COLUMNS}
    for name, records in data.items():
        _write_csv(out_dir / DATASET_FILES[name], records, columns[name])
    (out_dir / "README.txt").write_text(
        "SYNTHETIC DATA -- not NESO data. Generated by `wattstack-eac-history synth`.\n"
        f"day={day} seed={seed}. The 'published outcome' is wattstack's own RESTRICTED clearing, so a backtest on\n"
        "this folder should match it exactly. Use it to check the pipeline, never to judge the market model.\n"
    )
    return {n: len(r) for n, r in data.items()}
