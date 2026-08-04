# wattstack

A transparent, open-source revenue-stacking model for GB battery
energy storage: what a battery can physically do (power, duration,
efficiency, ramp) co-optimised against what the GB wholesale,
Balancing Mechanism, and response/reserve markets will actually pay
for it -- with the SOC cost of holding response capacity modelled
explicitly, not hand-waved.

**This is a placeholder name and an early scaffold, not a finished
tool.** Prices are synthetic until real Elexon/NESO ingestion is
wired in. Renamed easily -- `wattstack` was chosen for this
conversation, not because it's confirmed available.

See [`ROADMAP.md`](ROADMAP.md) for what v1 actually includes and
what's deliberately deferred, and why -- kept up to date as real
scope decisions get made, not written once and left stale.

## Why this exists

Commercial GB battery optimizers (Tierra, Mosaic, Ascend's
SmartBidder) are black boxes; independent benchmarking (Modo Energy)
is a paid subscription. Nobody publishes an open, reproducible model
of the actual revenue-stacking optimization -- researchers, smaller
developers doing early feasibility work, and policy analysts have no
transparent tool to check numbers against. See `docs/adr/` for the
engineering decisions and their reasoning.

## Repo layout

| Folder   | Status | What it is |
|----------|--------|------------|
| `core/`  | **built** | Python: `BatterySpec`, GB market definitions, the `Scenario` config model, the LP dispatch optimizer (PuLP/CBC), the multi-day backtest + sweep engine, and a `wattstack` CLI. Zero UI dependencies -- pip-installable and usable standalone. |
| `web/`   | **built** | Django + HTMX local UI wrapping `core`. Sliders/checkboxes recompute a Plotly revenue-stack chart and a per-day dispatch chart (SOC, headroom/footroom, charge/discharge, reserve by market, prices) without a page reload. Runs from `manage.py runserver`, no separate frontend build. |
| `docs/adr/` | ongoing | Architecture decision records. |
| `ingestion/` | **built, Elexon shape unverified against live traffic** | Real Elexon (BMRS Insights) and NESO (CKAN data portal) clients, a local SQLite cache, generic exploratory plotting (price time series, distribution, by-hour, by-weekday, two-series overlay, grouped bar, stacked bar), a script -- `wattstack-explore` -- for reproducible batch reports, and `notebooks/`: `explore.py` (general interactive query-shaping) plus a growing family of `spar_*.py` notebooks reproducing individual charts from Elexon's System Prices Analysis Report (SPAR) -- `spar_frequency_of_system_prices.py` and `spar_accepted_offer_volume_by_fuel_type.py` so far. All are plain `.py` files, not `.ipynb` -- diffs like normal code. Separate from `core`/`web` on purpose: this is for *you*, to build intuition about real data before deciding what to build next, not a dependency of the app itself. **Before trusting it**: I could not reach elexon.co.uk or neso.energy from the environment this was written in, so both are researched-and-documented, not tested against live traffic -- run `wattstack-explore --verify-only` first, it fails loudly and specifically if either has drifted. NESO's live EAC results resource IDs (Nov 2023 onwards, DC/DM/DR + BR/QR/SR) are confirmed directly from NESO's own results page -- but the exact price/volume field names inside that resource still aren't confirmed; `verify_schema()` and the notebooks' schema-preview cells will show you the real ones. The offer-volume notebook is more approximate still: BOALF has no explicit Bid/Offer flag, so direction is inferred from level change (`offer_volume()`/`bid_volume()`), a relative-magnitude proxy, not a settlement-grade MWh figure. And confirmed against live data: Elexon's `fuelType` field doesn't reliably tag BESS at all -- both `explore.py` and `spar_accepted_offer_volume_by_fuel_type.py` now combine a fuel-type-label match with an ID-pattern match and display the actual matched units for you to check (a naive ID pattern demonstrably also catches real non-battery stations, see ADR 0007).

Each of `core/`, `web/`, and `ingestion/` carries its own
`.vscode/settings.json` (pins that package's `.venv` interpreter) and
`.vscode/launch.json` (debug configs) -- open `wattstack.code-workspace`
rather than the plain folder so all three are picked up correctly.

Not yet started, following the plan from the design conversation:
wiring a real `ElexonPriceProvider` into `core`'s optimizer (today
`ingestion/` is exploratory-only -- it doesn't feed the optimizer,
`SyntheticPriceProvider` still does; connecting them is the natural
next step once you've decided what real data the model should
actually consume), a Capacity Market de-rating module, a genuine
rolling-horizon (imperfect-foresight) backtest mode, and eventually a
hosted deployment (FastAPI-or-Django + Postgres + Celery on AWS,
per the earlier architecture discussion -- `web/` as built today is
intentionally local-only).

## Quickstart

```
./setup.sh
```

Creates all three packages' virtualenvs, installs `web`'s dependency on
`core` as an editable local path, runs migrations, and runs all three
test suites (20 core tests + 6 web tests + 88 ingestion tests). Safe to re-run.

### CLI, by hand

```
cd core
python3 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
pytest -v                                    # 20 tests

wattstack run --config scenarios/example.yaml
wattstack run --config scenarios/example.yaml --sweep
```

### Exploring real market data, by hand

```
cd ingestion
python3 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
pytest -v                                    # 88 tests, all mocked -- no network needed

wattstack-explore --verify-only              # confirm both APIs still match what's documented
wattstack-explore --days 14                  # writes charts to reports/

marimo edit notebooks/explore.py                                    # general interactive query-shaping
marimo edit notebooks/spar_frequency_of_system_prices.py            # SPAR: frequency by GBP20 bin, long/short
marimo edit notebooks/spar_accepted_offer_volume_by_fuel_type.py    # SPAR: daily offer volume, stacked by fuel type + BSAA
```

### Web UI, by hand

```
cd web
python3 -m venv .venv && . .venv/bin/activate
pip install -e ../core
pip install -e ".[dev]"
python manage.py migrate
python manage.py test scenarios              # 4 tests
python manage.py runserver
# -> http://127.0.0.1:8000/
```

## A note on the numbers

Revenue figures right now come from `SyntheticPriceProvider` --
a stylised, deterministic price shape (cheap overnight, evening peak),
not real GB market data. They're useful for checking the optimizer's
*behaviour* is sane (does more duration earn more revenue, does
adding response markets never make things worse, does SOC headroom
actually bind for a short-duration battery) but not for anything
resembling a real business case yet. Real prices are the next real
piece of work.

## License

MIT.
