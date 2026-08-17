"""Smoke test for notebooks/imbalance_price_probabilistic_forecast.py.
The important things this proves beyond structural soundness: the
train/test split is genuinely chronological (test days are always the
most recent, never touched during training), the probability table
correctly separates buckets that are cleanly distinguishable, and the
backtest achieves a real, checkable MAE improvement over the flat
baseline on data deliberately constructed to be perfectly separable --
proving the mechanism is capable of learning a real relationship when
one exists, not just that the cells execute.
"""
import runpy
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

NOTEBOOK_PATH = Path(__file__).parent.parent / "notebooks" / "imbalance_price_probabilistic_forecast.py"
CACHE_PATH = Path("wattstack_ingestion_cache.sqlite")


class _FakeValue:
    def __init__(self, v):
        self.value = v


@pytest.fixture(autouse=True)
def _isolated_cache():
    CACHE_PATH.unlink(missing_ok=True)
    yield
    CACHE_PATH.unlink(missing_ok=True)


def _load_app():
    mod = runpy.run_path(str(NOTEBOOK_PATH), run_name="not_main")
    return mod["app"]


def test_notebook_cell_graph_resolves_without_error():
    app = _load_app()
    _, namespace = app.run()
    assert namespace is not None


def test_fetch_button_defaults_unclicked_and_gated_cells_are_absent():
    app = _load_app()
    _, namespace = app.run()
    assert namespace["fetch"].value is False
    keys = set(namespace.keys())
    assert "labelled_df" not in keys
    assert "probability_table" not in keys


# A perfectly separable mock: period 1 is always Long at £40, period 2
# is always Short at £90, with genuinely distinct (non-coincidental)
# demand values so bin_counts_by_group()'s boundary-clamping behaviour
# (see test_analysis.py) doesn't merge them into one bucket.
def _fake_get(url, params=None, timeout=30):
    resp = MagicMock()
    resp.status_code = 200
    resp.raise_for_status = MagicMock()
    if "forecast/demand/day-ahead/history" in url:
        resp.json.return_value = [
            {"settlementPeriod": 1, "demand": 28150},
            {"settlementPeriod": 2, "demand": 30450},
        ]
    elif "system-prices" in url:
        day = url.rsplit("/", 1)[-1]
        resp.json.return_value = [
            {"settlementDate": day, "settlementPeriod": 1, "systemSellPrice": 40.0,
             "systemBuyPrice": 40.0, "netImbalanceVolume": -100.0},
            {"settlementDate": day, "settlementPeriod": 2, "systemSellPrice": 90.0,
             "systemBuyPrice": 90.0, "netImbalanceVolume": 200.0},
        ]
    else:
        resp.json.return_value = []
    return resp


def _run_happy_path(days_to_fetch=20, test_days_count=4, demand_bin_width=1000, shrinkage_strength=10.0):
    with patch("wattstack_ingestion.elexon.requests.get", side_effect=_fake_get):
        app = _load_app()
        _, namespace = app.run(defs={
            "fetch": _FakeValue(True), "start_date": _FakeValue(date(2026, 6, 1)),
            "days_to_fetch": _FakeValue(days_to_fetch),
            "forecast_period_field": _FakeValue("settlementPeriod"), "demand_field": _FakeValue("demand"),
            "price_field": _FakeValue("systemSellPrice"), "niv_field": _FakeValue("netImbalanceVolume"),
            "demand_bin_width": _FakeValue(demand_bin_width),
            "test_days_count": _FakeValue(test_days_count),
            "shrinkage_strength": _FakeValue(shrinkage_strength),
        })
        return namespace


def test_labelled_df_correctly_joins_demand_price_and_system_length():
    namespace = _run_happy_path(days_to_fetch=5, test_days_count=1)
    df = namespace["labelled_df"]
    assert len(df) == 10  # 5 days x 2 periods
    long_rows = df[df["system_length"] == "Long"]
    short_rows = df[df["system_length"] == "Short"]
    assert (long_rows["actual_price"] == 40.0).all()
    assert (short_rows["actual_price"] == 90.0).all()


def test_train_test_split_is_chronological_and_disjoint():
    namespace = _run_happy_path(days_to_fetch=20, test_days_count=4)
    train_days = set(namespace["train_df"]["day"])
    test_days = set(namespace["test_df"]["day"])
    assert train_days.isdisjoint(test_days)
    assert len(test_days) == 4
    # test days must be the most recent, not an arbitrary sample
    assert max(train_days) < min(test_days)


def test_probability_table_correctly_separates_perfectly_distinguishable_buckets():
    """shrinkage_strength=0.0 explicitly -- this tests the unshrunk
    separation property specifically; with the notebook's default
    shrinkage applied, even a perfectly separable bucket gets diluted
    toward the overall rate, which is correct default behaviour but
    not what this particular test is checking."""
    namespace = _run_happy_path(days_to_fetch=20, test_days_count=4, demand_bin_width=1000, shrinkage_strength=0.0)
    table = namespace["probability_table"]
    assert len(table) == 2  # two genuinely distinct demand buckets
    for bucket_probs in table.values():
        # each bucket should be a clean 100/0 split, given the deterministic mock
        assert set(bucket_probs.values()) == {0.0, 1.0}


def test_conditional_stats_correctly_reflect_the_real_price_split():
    namespace = _run_happy_path(days_to_fetch=20, test_days_count=4)
    stats = namespace["conditional_stats"]
    assert stats["Short"]["mean"] == 90.0
    assert stats["Long"]["mean"] == 40.0


def test_mixture_model_beats_flat_baseline_on_perfectly_separable_data():
    """The actual point of this notebook: on data where a genuine
    relationship exists, the mixture model must measurably outperform
    the flat baseline -- proving the mechanism works, not just that
    it runs. Extracted directly from the backtest cell's rendered
    output, the same number a person reading the notebook would see.

    shrinkage_strength=0.0 explicitly here: with the notebook's
    default (10.0), shrinkage would dilute even this perfectly
    separable case's confident probabilities, which would test
    something different (how much shrinkage costs on clean data, not
    whether the underlying mechanism is capable of learning a real
    relationship at all)."""
    with patch("wattstack_ingestion.elexon.requests.get", side_effect=_fake_get):
        app = _load_app()
        outputs, _ = app.run(defs={
            "fetch": _FakeValue(True), "start_date": _FakeValue(date(2026, 6, 1)),
            "days_to_fetch": _FakeValue(20),
            "forecast_period_field": _FakeValue("settlementPeriod"), "demand_field": _FakeValue("demand"),
            "price_field": _FakeValue("systemSellPrice"), "niv_field": _FakeValue("netImbalanceVolume"),
            "demand_bin_width": _FakeValue(1000),
            "test_days_count": _FakeValue(4),
            "shrinkage_strength": _FakeValue(0.0),
        })
    backtest_text = outputs[-1].text
    assert "£0.00/MWh" in backtest_text  # mixture MAE -- perfect on perfectly separable data
    assert "100.0%" in backtest_text     # improvement over the flat baseline


def test_labelled_df_still_populates_even_when_the_train_split_ends_up_empty():
    """A degenerate edge case (very little data fetched) must degrade
    gracefully -- labelled_df doesn't depend on the train/test split
    at all, so it should still be present even when downstream
    training cells correctly gate themselves off via mo.stop()."""
    namespace = _run_happy_path(days_to_fetch=1, test_days_count=1)
    assert "labelled_df" in namespace
    assert not namespace["labelled_df"].empty


def test_probability_table_uses_shrinkage_by_default_not_raw_frequency():
    """The real fix from a real bad result: the default training path
    must go through shrink_probability_by_bin(), not the raw
    probability_by_bin() that caused the original -291.9% failure."""
    # 2 training days, one thin bucket per period -- exactly the noisy-small-sample shape that failed before
    namespace = _run_happy_path(days_to_fetch=6, test_days_count=1, demand_bin_width=1000, shrinkage_strength=10.0)
    table = namespace["probability_table"]
    for bucket_probs in table.values():
        # with real shrinkage applied and only a few observations per bucket,
        # no bucket should show an exact, unshrunk 0.0/1.0 split
        assert not (0.0 in bucket_probs.values() and 1.0 in bucket_probs.values())


def test_sweep_covers_the_expected_range_of_shrinkage_strengths():
    namespace = _run_happy_path(days_to_fetch=20, test_days_count=4, demand_bin_width=1000)
    sweep_df = namespace["sweep_df"]
    assert list(sweep_df["shrinkage_strength"]) == [0.0, 1.0, 2.0, 5.0, 10.0, 20.0, 50.0, 100.0]


def test_sweep_at_zero_strength_matches_the_original_unshrunk_backtest_exactly():
    """shrinkage_strength=0 in the sweep must reproduce exactly what
    the raw, unshrunk estimator would give -- the same number that
    produced the original real-data failure, now visible as one row
    in the sweep table rather than requiring a separate run."""
    namespace = _run_happy_path(days_to_fetch=20, test_days_count=4, demand_bin_width=1000)
    sweep_df = namespace["sweep_df"]
    zero_strength_row = sweep_df[sweep_df["shrinkage_strength"] == 0.0].iloc[0]
    # on this perfectly-separable mock, unshrunk should still achieve perfect prediction
    assert zero_strength_row["mixture_mae"] == 0.0
    assert zero_strength_row["vs_flat_baseline_pct"] == 100.0


def test_sweep_produces_a_valid_finite_mae_for_every_candidate_strength():
    """Structural correctness of the sweep cell itself -- that it
    correctly wires shrink_probability_by_bin() into the backtest
    loop for every candidate value. The real mathematical guarantee
    of shrinkage (convergence to the unconditional rate as strength
    grows) is tested directly against the function itself in
    test_analysis.py -- NOT asserted here as monotonic improvement,
    which isn't actually guaranteed on any single, possibly-small
    backtest: a small test set can make the raw, unshrunk estimator
    look good purely by chance, the same way it can look bad."""
    namespace = _run_happy_path(days_to_fetch=20, test_days_count=4, demand_bin_width=1000)
    sweep_df = namespace["sweep_df"]
    assert len(sweep_df) == 8
    assert sweep_df["mixture_mae"].notna().all()
    assert (sweep_df["mixture_mae"] >= 0).all()
