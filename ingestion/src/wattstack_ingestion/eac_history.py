"""Historical EAC auction data: load, validate, and turn into inputs for
`eac_auction.clear_auction`, so wattstack's clearing can be compared with
what NESO actually published.

Source datasets (NESO data portal, "EAC auction results", new format from
6 May 2026) -- schemas read from their portal pages, November 2026:

  sell orders      13b511df-...  one row per sell order, WITH its outcome
                                 (acceptanceRatio, status, executedQuantity,
                                 clearingPrice, reasonRejected)
  buy orders       1cf68f59-...  same, for NESO's buy orders
  results summary  596f29ac-...  cleared volume + price per product/window
  results by unit  a63ab354-...  executed quantity per unit/product/window

The order datasets carry both the INPUT of the auction (quantity, price,
basket structure) and its OUTCOME. This module keeps them apart: only the
input columns ever reach the clearing model; outcome columns are held on
the side for comparison.

Nothing here has been run against the live API from the development
sandbox (it cannot reach api.neso.energy), so every parser is strict: a
missing required column or an unparseable value raises
`HistoryDataError` naming the dataset, row and column, rather than
guessing. Anything the data contains that `clear_auction` does NOT model
is counted in `AuctionCensus.unmodelled` instead of being ignored.
"""
from __future__ import annotations

import csv
import json
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Iterable
from zoneinfo import ZoneInfo

from wattstack_ingestion.eac_auction import (
    BuyOrderInput,
    SellOrderInput,
    _components,
)
from wattstack_ingestion.neso import KNOWN_RESOURCES, NesoClient

# Results are published in the format this module reads from thissql date; rows
# delivering earlier are in an older layout and are skipped before parsing.
NEW_FORMAT_START = date(2026, 5, 6)

RESPONSE_PRODUCTS = ("DRL", "DRH", "DML", "DMH", "DCL", "DCH")
RESERVE_PRODUCTS = ("NSR", "PSR", "NQR", "PQR", "NBR", "PBR")
ALL_PRODUCTS = RESPONSE_PRODUCTS + RESERVE_PRODUCTS
# Response is procured per EFA block (4 h), Reserve per half hour -- as
# stated by the user; windows of any other length are reported, not rejected.
EXPECTED_WINDOW_MINUTES = {**{p: 240 for p in RESPONSE_PRODUCTS}, **{p: 30 for p in RESERVE_PRODUCTS}}
SERVICE_TYPE = {
    **{p: "Response" for p in RESPONSE_PRODUCTS},
    "NQR": "Quick Reserve", "PQR": "Quick Reserve",
    "NSR": "Slow Reserve", "PSR": "Slow Reserve",
    "NBR": "Balancing Reserve", "PBR": "Balancing Reserve",
}

LONDON = ZoneInfo("Europe/London")
VALID_STATUSES = ("EXECUTED", "REJECTED", "EXCLUDED", "PARTIALLY_EXECUTED")

DATASET_FILES = {
    "sell_orders": "sell_orders.csv",
    "buy_orders": "buy_orders.csv",
    "results_summary": "results_summary.csv",
    "results_by_unit": "results_by_unit.csv",
}
RESOURCE_KEYS = {
    "sell_orders": "eac_sell_orders",
    "buy_orders": "eac_buy_orders",
    "results_summary": "response_reserve_results_summary",
    "results_by_unit": "response_reserve_results_by_unit",
}

# column -> required?  (required = needed to run or to compare)
SELL_COLUMNS = {
    "auctionID": True, "registeredAuctionParticipant": False, "auctionUnit": True, "basketID": True,
    "serviceType": False, "deliveryStart": True, "deliveryEnd": True, "orderID": True, "orderType": True,
    "auctionProduct": True, "productID": False, "quantity": True, "priceLimit": True, "loopedBasketID": False,
    "orderEntryTime": False, "acceptanceRatio": True, "status": True, "executedQuantity": True,
    "clearingPrice": True, "reasonRejected": False, "flexibleGroupID": False, "minimumConsecutiveWindows": False,
}
BUY_COLUMNS = {
    "auctionID": True, "orderID": True, "serviceType": False, "auctionProduct": True, "deliveryStart": True,
    "deliveryEnd": True, "quantity": True, "price": True, "substitutabilityFamily": False,
    "paradoxicallyAcceptanceAllowed": True, "orderEntryTime": False, "acceptanceRatio": True, "status": True,
    "executedQuantity": True, "clearingPrice": True, "reasonRejected": False, "joinedFamily": False,
}
SUMMARY_COLUMNS = {
    "auctionID": True, "auctionProduct": True, "serviceType": False, "deliveryStart": True, "deliveryEnd": True,
    "clearedVolume": True, "clearingPrice": True, "linkedServiceWindowID": False,
}
UNIT_COLUMNS = {
    "registeredAuctionParticipant": False, "auctionUnit": True, "serviceType": False, "auctionProduct": True,
    "executedQuantity": True, "clearingPrice": True, "deliveryStart": True, "deliveryEnd": True,
    "technologyType": False, "postCode": False, "unitResultID": False,
}

Window = tuple[datetime, datetime]
Key = tuple[str, datetime, datetime]


class HistoryDataError(ValueError):
    """The downloaded data does not look like the documented schema."""


# ---------------------------------------------------------------------------
# Row types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SellRow:
    auction_id: int
    unit: str
    basket_id: str
    order_id: str
    order_type: str
    product: str
    start: datetime
    end: datetime
    quantity: float
    price_limit: float
    looped_basket_id: str | None
    acceptance_ratio: float
    status: str
    executed_quantity: float
    clearing_price: float
    reason_rejected: str | None
    flexible_group_id: str | None
    minimum_consecutive_windows: int | None
    participant: str | None = None

    @property
    def key(self) -> Key:
        return (self.product, self.start, self.end)


@dataclass(frozen=True)
class BuyRow:
    auction_id: int
    order_id: str
    product: str
    start: datetime
    end: datetime
    quantity: float
    price: float
    family: str | None
    paradox_allowed: bool
    acceptance_ratio: float
    status: str
    executed_quantity: float
    clearing_price: float
    reason_rejected: str | None
    joined_family: str | None

    @property
    def key(self) -> Key:
        return (self.product, self.start, self.end)


@dataclass(frozen=True)
class SummaryRow:
    auction_id: int
    product: str
    start: datetime
    end: datetime
    cleared_volume: float
    clearing_price: float
    linked_service_window_id: str | None

    @property
    def key(self) -> Key:
        return (self.product, self.start, self.end)


@dataclass(frozen=True)
class UnitRow:
    unit: str
    product: str
    start: datetime
    end: datetime
    executed_quantity: float
    clearing_price: float

    @property
    def key(self) -> Key:
        return (self.product, self.start, self.end)


# ---------------------------------------------------------------------------
# Value parsing
# ---------------------------------------------------------------------------

_NULLS = {"", "none", "null", "nan", "n/a"}


def _blank(value) -> bool:
    return value is None or (isinstance(value, str) and value.strip().lower() in _NULLS)


def _parse_dt(value) -> datetime:
    """UTC-naive datetime. NESO publishes deliveryStart/End in UTC (see
    neso.py); an explicit offset is converted, a naive value is taken as UTC."""
    if isinstance(value, datetime):
        dt = value
    else:
        text = str(value).strip().replace("Z", "+00:00")
        dt = datetime.fromisoformat(text)
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def _parse_float(value) -> float:
    return float(str(value).strip().replace(",", "")) if isinstance(value, str) else float(value)


def _parse_opt_id(value) -> str | None:
    """Optional identifiers arrive as '', None, 12, 12.0 or '12'; return a
    canonical string without a trailing '.0'."""
    if _blank(value):
        return None
    text = str(value).strip()  # str(12.0) == "12.0", handled by the ".0" rule below
    if text.endswith(".0") and text[:-2].lstrip("-").isdigit():
        return text[:-2]
    return text


def _parse_id(value) -> str:
    out = _parse_opt_id(value)
    if out is None:
        raise ValueError("missing identifier")
    return out


def _parse_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in ("true", "t", "1", "yes", "y"):
        return True
    if text in ("false", "f", "0", "no", "n"):
        return False
    raise ValueError(f"not a boolean: {value!r}")


def _parse_opt_int(value) -> int | None:
    if _blank(value):
        return None
    return int(float(str(value).strip()))


def _parse_order_type(value) -> str:
    text = "".join(ch for ch in str(value).upper() if ch.isalpha())
    if text not in ("PARENT", "CHILD", "SUBSTITUTABLECHILD"):
        raise ValueError(f"unknown orderType {value!r}")
    return text


def _parse_status(value) -> str:
    text = str(value).strip().upper()
    if text not in VALID_STATUSES:
        raise ValueError(f"unknown status {value!r} (expected one of {VALID_STATUSES})")
    return text


def _canonical(records: Iterable[dict], columns: dict[str, bool], dataset: str) -> list[dict]:
    records = list(records)
    if not records:
        return []
    lower = {c.lower(): c for c in columns}
    present = {str(k).strip().lstrip("﻿").lower() for k in records[0]}
    missing = [c for c, required in columns.items() if required and c.lower() not in present]
    if missing:
        raise HistoryDataError(
            f"{dataset}: required columns missing: {missing}. Columns found: {sorted(records[0])}. "
            "The portal schema may have changed -- compare with the dataset page."
        )
    out = []
    for rec in records:
        row = {}
        for k, v in rec.items():
            canonical = lower.get(str(k).strip().lstrip("﻿").lower())
            if canonical:
                row[canonical] = v
        out.append(row)
    return out


def _parse_rows(records, columns, dataset, build) -> list:
    """Applies `build(row)` to every record; collects the first few
    failures into one error so a systematic mismatch is visible at once."""
    rows, errors = [], []
    for i, row in enumerate(_canonical(records, columns, dataset)):
        try:
            rows.append(build(row))
        except (ValueError, TypeError, KeyError) as exc:
            errors.append(f"row {i + 1}: {exc}")
            if len(errors) >= 5:
                break
    if errors:
        raise HistoryDataError(f"{dataset}: could not parse rows -- " + "; ".join(errors))
    return rows


def parse_sell_rows(records: Iterable[dict]) -> list[SellRow]:
    def build(r):
        return SellRow(
            auction_id=int(_parse_id(r["auctionID"])), unit=_parse_id(r["auctionUnit"]),
            basket_id=_parse_id(r["basketID"]), order_id=_parse_id(r["orderID"]),
            order_type=_parse_order_type(r["orderType"]), product=str(r["auctionProduct"]).strip().upper(),
            start=_parse_dt(r["deliveryStart"]), end=_parse_dt(r["deliveryEnd"]),
            quantity=_parse_float(r["quantity"]), price_limit=_parse_float(r["priceLimit"]),
            looped_basket_id=_parse_opt_id(r.get("loopedBasketID")),
            acceptance_ratio=_parse_float(r["acceptanceRatio"]), status=_parse_status(r["status"]),
            executed_quantity=_parse_float(r["executedQuantity"]), clearing_price=_parse_float(r["clearingPrice"]),
            reason_rejected=None if _blank(r.get("reasonRejected")) else str(r["reasonRejected"]).strip(),
            flexible_group_id=_parse_opt_id(r.get("flexibleGroupID")),
            minimum_consecutive_windows=_parse_opt_int(r.get("minimumConsecutiveWindows")),
            participant=None if _blank(r.get("registeredAuctionParticipant")) else str(r["registeredAuctionParticipant"]),
        )

    return _parse_rows(records, SELL_COLUMNS, "sell orders", build)


def parse_buy_rows(records: Iterable[dict]) -> list[BuyRow]:
    def build(r):
        return BuyRow(
            auction_id=int(_parse_id(r["auctionID"])), order_id=_parse_id(r["orderID"]),
            product=str(r["auctionProduct"]).strip().upper(), start=_parse_dt(r["deliveryStart"]),
            end=_parse_dt(r["deliveryEnd"]), quantity=_parse_float(r["quantity"]), price=_parse_float(r["price"]),
            family=_parse_opt_id(r.get("substitutabilityFamily")),
            paradox_allowed=_parse_bool(r["paradoxicallyAcceptanceAllowed"]),
            acceptance_ratio=_parse_float(r["acceptanceRatio"]), status=_parse_status(r["status"]),
            executed_quantity=_parse_float(r["executedQuantity"]), clearing_price=_parse_float(r["clearingPrice"]),
            reason_rejected=None if _blank(r.get("reasonRejected")) else str(r["reasonRejected"]).strip(),
            joined_family=_parse_opt_id(r.get("joinedFamily")),
        )

    return _parse_rows(records, BUY_COLUMNS, "buy orders", build)


def parse_summary_rows(records: Iterable[dict]) -> list[SummaryRow]:
    def build(r):
        return SummaryRow(
            auction_id=int(_parse_id(r["auctionID"])), product=str(r["auctionProduct"]).strip().upper(),
            start=_parse_dt(r["deliveryStart"]), end=_parse_dt(r["deliveryEnd"]),
            cleared_volume=_parse_float(r["clearedVolume"]), clearing_price=_parse_float(r["clearingPrice"]),
            linked_service_window_id=_parse_opt_id(r.get("linkedServiceWindowID")),
        )

    return _parse_rows(records, SUMMARY_COLUMNS, "results summary", build)


def parse_unit_rows(records: Iterable[dict]) -> list[UnitRow]:
    def build(r):
        return UnitRow(
            unit=_parse_id(r["auctionUnit"]), product=str(r["auctionProduct"]).strip().upper(),
            start=_parse_dt(r["deliveryStart"]), end=_parse_dt(r["deliveryEnd"]),
            executed_quantity=_parse_float(r["executedQuantity"]), clearing_price=_parse_float(r["clearingPrice"]),
        )

    return _parse_rows(records, UNIT_COLUMNS, "results by unit", build)


# ---------------------------------------------------------------------------
# Day selection
# ---------------------------------------------------------------------------


def day_bounds_utc(day: date, basis: str = "efa") -> Window:
    """[start, end) of a delivery day as UTC-naive datetimes.

    "efa": the Electricity Forward Agreement day -- 23:00 UK clock time on
           the previous evening to 23:00 on `day` (EFA block 1 is
           23:00-03:00). 23 or 25 hours long on clock-change days.
    "calendar": UK local midnight to midnight.
    """
    if basis not in ("efa", "calendar"):
        raise ValueError(f"basis must be 'efa' or 'calendar', got {basis!r}")
    if basis == "efa":
        local_start = datetime.combine(day - timedelta(days=1), time(23, 0), tzinfo=LONDON)
        local_end = datetime.combine(day, time(23, 0), tzinfo=LONDON)
    else:
        local_start = datetime.combine(day, time(0, 0), tzinfo=LONDON)
        local_end = datetime.combine(day + timedelta(days=1), time(0, 0), tzinfo=LONDON)
    to_utc = lambda dt: dt.astimezone(timezone.utc).replace(tzinfo=None)  # noqa: E731
    return to_utc(local_start), to_utc(local_end)


# ---------------------------------------------------------------------------
# Dataset container, file IO
# ---------------------------------------------------------------------------


@dataclass
class HistoryData:
    sells: list[SellRow]
    buys: list[BuyRow]
    summary: list[SummaryRow]
    units: list[UnitRow]

    def auction_ids_in(self, window: Window) -> list[int]:
        """Auctions with any published row delivering inside [start, end)."""
        start, end = window
        ids = set()
        for rows in (self.summary, self.sells, self.buys):
            ids.update(r.auction_id for r in rows if start <= r.start < end)
        return sorted(ids)


def read_csv_records(path: Path, since: date | None = None) -> list[dict]:
    """All rows of a CSV as dicts. With `since`, rows whose deliveryStart is
    before that UTC date are dropped WITHOUT being parsed further -- so a
    whole-dataset download that still contains rows in an older layout does
    not trip the strict parsers. A row whose deliveryStart cannot be read is
    kept (and will be reported by the parser, not silently lost)."""
    cutoff = datetime.combine(since, time(0, 0)) if since else None
    out = []
    with open(path, newline="", encoding="utf-8-sig") as fh:
        for rec in csv.DictReader(fh):
            if cutoff is not None:
                raw = next((v for k, v in rec.items() if str(k).strip().lstrip("\ufeff").lower() == "deliverystart"), None)
                try:
                    if raw is not None and _parse_dt(raw) < cutoff:
                        continue
                except ValueError:
                    pass
            out.append(rec)
    return out


def load_history_dir(
    data_dir: Path, *, optional: tuple[str, ...] = ("results_by_unit",), since: date | None = NEW_FORMAT_START
) -> HistoryData:
    """Reads the four standard CSVs from `data_dir` (names in
    DATASET_FILES -- what `download` writes, and what a manual portal
    download should be renamed to). `results_by_unit` is optional.
    `since` (default: 6 May 2026, when the new format starts) skips older
    rows; pass None to read everything."""
    data_dir = Path(data_dir)
    parsed = {}
    for name, filename in DATASET_FILES.items():
        path = data_dir / filename
        if not path.exists():
            if name in optional:
                parsed[name] = []
                continue
            raise HistoryDataError(f"{path} not found. Expected files: {sorted(DATASET_FILES.values())}")
        parsed[name] = read_csv_records(path, since)
    return HistoryData(
        sells=parse_sell_rows(parsed["sell_orders"]), buys=parse_buy_rows(parsed["buy_orders"]),
        summary=parse_summary_rows(parsed["results_summary"]), units=parse_unit_rows(parsed["results_by_unit"]),
    )


def _write_csv(path: Path, records: list[dict], columns: dict[str, bool]) -> None:
    extras = sorted({k for r in records for k in r} - set(columns))
    fieldnames = list(columns) + extras
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for r in records:
            writer.writerow({k: ("" if r.get(k) is None else r.get(k)) for k in fieldnames})


# ---------------------------------------------------------------------------
# Download (CKAN SQL API)
# ---------------------------------------------------------------------------


def _sql_page(client: NesoClient, resource_id: str, where: str, page_size: int) -> list[dict]:
    out: list[dict] = []
    offset = 0
    while True:
        sql = (
            f'SELECT * FROM "{resource_id}" WHERE {where} ORDER BY "_id" '
            f"LIMIT {int(page_size)} OFFSET {int(offset)}"
        )
        page = client.datastore_search_sql(sql)
        out.extend(page)
        if len(page) < page_size:
            return out
        offset += page_size


def download_range(
    client: NesoClient, start_day: date, end_day: date, out_dir: Path, *, basis: str = "efa", page_size: int = 5000,
    log=lambda msg: None,
) -> dict:
    """Pulls every auction that delivers in [start_day, end_day] (inclusive,
    delivery-day basis) and writes the four CSVs into `out_dir`.

    Order: results summary first (it names the auctionIDs), then ALL sell
    and buy order rows of those auctions -- whole auctions, because a
    looped basket or substitutability family can reach outside the day --
    and finally results by unit. Date bounds are widened by a day in SQL
    (so a text-vs-timestamp format difference cannot clip the edges) and
    trimmed exactly in Python.
    """
    if end_day < start_day:
        raise ValueError("end_day precedes start_day")
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    lo = day_bounds_utc(start_day, basis)[0]
    hi = day_bounds_utc(end_day, basis)[1]
    wide_lo, wide_hi = (lo - timedelta(days=1)).isoformat(), (hi + timedelta(days=1)).isoformat()
    window_where = f"\"deliveryStart\" >= '{wide_lo}' AND \"deliveryStart\" < '{wide_hi}'"

    def resource(name):
        return KNOWN_RESOURCES[RESOURCE_KEYS[name]]

    log("results summary ...")
    summary = [r for r in _sql_page(client, resource("results_summary"), window_where, page_size)]
    summary_rows = parse_summary_rows(summary)
    keep = {r.auction_id for r in summary_rows if lo <= r.start < hi}
    summary = [raw for raw, parsed in zip(summary, summary_rows) if parsed.auction_id in keep]
    if not keep:
        raise HistoryDataError(
            f"the results summary has no rows delivering between {lo} and {hi} (UTC). Results in the new format "
            "start on 6 May 2026; check the dates."
        )
    ids = ", ".join(str(i) for i in sorted(keep))
    _write_csv(out_dir / DATASET_FILES["results_summary"], summary, SUMMARY_COLUMNS)

    counts = {"results_summary": len(summary)}
    for name, columns in (("sell_orders", SELL_COLUMNS), ("buy_orders", BUY_COLUMNS)):
        log(f"{name} for {len(keep)} auctions ...")
        rows = _sql_page(client, resource(name), f'"auctionID" IN ({ids})', page_size)
        _write_csv(out_dir / DATASET_FILES[name], rows, columns)
        counts[name] = len(rows)

    log("results by unit ...")
    unit_raw = _sql_page(client, resource("results_by_unit"), window_where, page_size)
    unit_rows = parse_unit_rows(unit_raw)
    unit_raw = [raw for raw, parsed in zip(unit_raw, unit_rows) if lo <= parsed.start < hi]
    _write_csv(out_dir / DATASET_FILES["results_by_unit"], unit_raw, UNIT_COLUMNS)
    counts["results_by_unit"] = len(unit_raw)

    manifest = {
        "fetched_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "start_day": start_day.isoformat(), "end_day": end_day.isoformat(), "day_basis": basis,
        "delivery_window_utc": [lo.isoformat(), hi.isoformat()],
        "auction_ids": sorted(keep), "rows": counts,
        "resources": {n: resource(n) for n in DATASET_FILES},
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))
    return manifest


# ---------------------------------------------------------------------------
# One auction as a case
# ---------------------------------------------------------------------------


@dataclass
class DataIssue:
    kind: str
    detail: str


@dataclass
class AuctionCensus:
    n_sell_rows: int = 0
    n_buy_rows: int = 0
    sell_status: Counter = field(default_factory=Counter)
    buy_status: Counter = field(default_factory=Counter)
    products: dict[str, int] = field(default_factory=dict)  # product -> number of windows
    n_keys: int = 0
    reason_codes: Counter = field(default_factory=Counter)
    order_types: Counter = field(default_factory=Counter)
    n_looped_baskets: int = 0
    n_dangling_loop_links: int = 0
    n_buy_families: int = 0
    n_components: int = 0
    largest_component_orders: int = 0
    largest_component_keys: int = 0
    window_length_anomalies: list[str] = field(default_factory=list)
    unknown_products: list[str] = field(default_factory=list)
    price_range: tuple[float, float] | None = None
    # name -> number of rows carrying it. Each is data the auction used
    # that clear_auction does not model; results touching them are not
    # expected to match.
    unmodelled: dict[str, int] = field(default_factory=dict)


@dataclass
class AuctionCase:
    auction_id: int
    sell_rows: list[SellRow]
    buy_rows: list[BuyRow]
    summary_rows: list[SummaryRow]
    sell_inputs: list[SellOrderInput]
    buy_inputs: list[BuyOrderInput]
    sell_row_by_id: dict[str, SellRow]
    buy_row_by_id: dict[str, BuyRow]
    issues: list[DataIssue]
    census: AuctionCensus
    flagged_sell_ids: set[str]  # rows touching an unmodelled feature
    flagged_buy_ids: set[str]
    flagged_keys: set[Key]  # keys with a linked service window
    product_subset: tuple[str, ...] | None = None


def build_case(
    auction_id: int,
    data: HistoryData,
    *,
    products: Iterable[str] | None = None,
    excluded: str = "drop",
    strict: bool = False,
) -> AuctionCase:
    """Everything needed to clear one auction and compare.

    `excluded`: "drop" (default) leaves EXCLUDED orders out of the model --
    the working assumption is that EXCLUDED means "never entered the
    optimisation"; the portal's reason codes are listed in the census so
    that can be checked. "keep" puts them in as ordinary orders, to test
    the opposite hypothesis.

    `products`: restrict to these product codes. This DISTORTS the market
    (cross-product baskets and units lose their partners): use it only to
    smoke-test the pipeline on a small slice, never to judge accuracy.
    """
    if excluded not in ("drop", "keep"):
        raise ValueError("excluded must be 'drop' or 'keep'")
    subset = tuple(p.upper() for p in products) if products else None
    sell_rows = [r for r in data.sells if r.auction_id == auction_id]
    buy_rows = [r for r in data.buys if r.auction_id == auction_id]
    summary_rows = [r for r in data.summary if r.auction_id == auction_id]
    issues: list[DataIssue] = []

    if subset:
        sell_rows = [r for r in sell_rows if r.product in subset]
        buy_rows = [r for r in buy_rows if r.product in subset]
        summary_rows = [r for r in summary_rows if r.product in subset]

    census = AuctionCensus(n_sell_rows=len(sell_rows), n_buy_rows=len(buy_rows))
    census.sell_status = Counter(r.status for r in sell_rows)
    census.buy_status = Counter(r.status for r in buy_rows)
    census.order_types = Counter(r.order_type for r in sell_rows)
    for r in sell_rows:
        if r.reason_rejected:
            census.reason_codes[f"sell {r.status}: {r.reason_rejected}"] += 1
    for r in buy_rows:
        if r.reason_rejected:
            census.reason_codes[f"buy {r.status}: {r.reason_rejected}"] += 1

    # --- duplicate IDs ---
    for label, rows, key_fn in (("sell", sell_rows, lambda r: r.order_id), ("buy", buy_rows, lambda r: r.order_id)):
        counts = Counter(key_fn(r) for r in rows)
        dups = sorted(k for k, n in counts.items() if n > 1)
        if dups:
            detail = f"{len(dups)} duplicate {label} orderIDs, e.g. {dups[:3]}"
            if strict:
                raise HistoryDataError(f"auction {auction_id}: {detail}")
            issues.append(DataIssue("duplicate_order_id", detail + " -- all copies dropped"))
            dup_set = set(dups)
            if label == "sell":
                sell_rows = [r for r in sell_rows if r.order_id not in dup_set]
            else:
                buy_rows = [r for r in buy_rows if r.order_id not in dup_set]

    # --- which rows enter the model ---
    in_sells = [r for r in sell_rows if excluded == "keep" or r.status != "EXCLUDED"]
    in_buys = [r for r in buy_rows if excluded == "keep" or r.status != "EXCLUDED"]

    # --- baskets need exactly one parent ---
    by_basket: dict[str, list[SellRow]] = defaultdict(list)
    for r in in_sells:
        by_basket[r.basket_id].append(r)
    bad_baskets = {}
    for basket_id, rows in by_basket.items():
        n_parents = sum(1 for r in rows if r.order_type == "PARENT")
        if n_parents != 1:
            bad_baskets[basket_id] = n_parents
    if bad_baskets:
        detail = (
            f"{len(bad_baskets)} baskets without exactly one PARENT after exclusions "
            f"(basketID: parents) e.g. {dict(list(bad_baskets.items())[:3])} -- whole baskets dropped"
        )
        if strict:
            raise HistoryDataError(f"auction {auction_id}: {detail}")
        issues.append(DataIssue("basket_without_single_parent", detail))
        in_sells = [r for r in in_sells if r.basket_id not in bad_baskets]

    # --- window length sanity ---
    seen_lengths: set[tuple[str, int]] = set()
    for r in list(in_sells) + list(in_buys):
        minutes = int((r.end - r.start).total_seconds() // 60)
        expected = EXPECTED_WINDOW_MINUTES.get(r.product)
        if expected is not None and minutes != expected and (r.product, minutes) not in seen_lengths:
            seen_lengths.add((r.product, minutes))
            census.window_length_anomalies.append(f"{r.product}: {minutes} min window (expected {expected})")
    census.unknown_products = sorted({r.product for r in list(in_sells) + list(in_buys)} - set(ALL_PRODUCTS))

    # --- model inputs: ONLY input columns ---
    sell_inputs = [
        SellOrderInput(
            order_id=r.order_id, auction_unit=r.unit, basket_id=r.basket_id, order_type=r.order_type,
            auction_product=r.product, quantity_mw=r.quantity, price_limit=r.price_limit, window_start=r.start,
            window_end=r.end, looped_basket_id=r.looped_basket_id,
        )
        for r in in_sells
    ]
    buy_inputs = [
        BuyOrderInput(
            order_id=r.order_id, auction_product=r.product, quantity_mw=r.quantity, price=r.price,
            window_start=r.start, window_end=r.end, substitutability_family=r.family,
            paradoxical_acceptance_allowed=r.paradox_allowed,
        )
        for r in in_buys
    ]

    keys = {r.key for r in in_sells} | {r.key for r in in_buys}
    per_product: dict[str, set] = defaultdict(set)
    for k in keys:
        per_product[k[0]].add((k[1], k[2]))
    census.products = {p: len(w) for p, w in sorted(per_product.items())}
    census.n_keys = len(keys)
    all_prices = [r.price_limit for r in in_sells] + [r.price for r in in_buys]
    census.price_range = (min(all_prices), max(all_prices)) if all_prices else None

    baskets_present = {r.basket_id for r in in_sells}
    parents = [r for r in in_sells if r.order_type == "PARENT"]
    census.n_looped_baskets = sum(1 for r in parents if r.looped_basket_id)
    census.n_dangling_loop_links = sum(
        1 for r in parents if r.looped_basket_id and r.looped_basket_id not in baskets_present
    )
    census.n_buy_families = len({r.family for r in in_buys if r.family})

    comps = _components(sell_inputs, buy_inputs)
    census.n_components = len(comps)
    if comps:
        largest = max(comps, key=lambda c: len(c[0]) + len(c[1]))
        census.largest_component_orders = len(largest[0]) + len(largest[1])
        census.largest_component_keys = len({_k for _k in (
            [(o.auction_product, o.window_start, o.window_end) for o in largest[0]]
            + [(o.auction_product, o.window_start, o.window_end) for o in largest[1]]
        )})

    # --- features the engine does not model ---
    flagged_sell = {r.order_id for r in in_sells if r.flexible_group_id is not None}
    flagged_sell |= {r.order_id for r in in_sells if r.minimum_consecutive_windows is not None}
    flagged_buy = {r.order_id for r in in_buys if r.joined_family is not None}
    flagged_keys = {r.key for r in summary_rows if r.linked_service_window_id is not None}
    if flagged_sell:
        census.unmodelled["flexible groups / minimumConsecutiveWindows (sell rows)"] = len(flagged_sell)
    if flagged_buy:
        census.unmodelled["joinedFamily (buy rows; meaning not documented)"] = len(flagged_buy)
    if flagged_keys:
        census.unmodelled["linked service windows (product-windows)"] = len(flagged_keys)

    return AuctionCase(
        auction_id=auction_id, sell_rows=sell_rows, buy_rows=buy_rows, summary_rows=summary_rows,
        sell_inputs=sell_inputs, buy_inputs=buy_inputs,
        sell_row_by_id={r.order_id: r for r in in_sells}, buy_row_by_id={r.order_id: r for r in in_buys},
        issues=issues, census=census, flagged_sell_ids=flagged_sell, flagged_buy_ids=flagged_buy,
        flagged_keys=flagged_keys, product_subset=subset,
    )


def tainted_keys(case: AuctionCase) -> set[Key]:
    """Every (product, window) that shares an independent sub-market with
    something the engine does not model -- the effect of an unmodelled
    constraint travels through the same couplings as any other."""
    tainted: set[Key] = set()
    for sells, buys in _components(case.sell_inputs, case.buy_inputs):
        keys = {(o.auction_product, o.window_start, o.window_end) for o in sells}
        keys |= {(o.auction_product, o.window_start, o.window_end) for o in buys}
        flagged = (
            any(o.order_id in case.flagged_sell_ids for o in sells)
            or any(o.order_id in case.flagged_buy_ids for o in buys)
            or bool(keys & case.flagged_keys)
        )
        if flagged:
            tainted |= keys
    return tainted | case.flagged_keys
