from django import forms

from wattstack_core.markets import Market

MARKET_CHOICES = [
    (m.value, m.name.replace("_", " ").title()) for m in Market if m != Market.CAPACITY_MARKET
]


class ScenarioForm(forms.Form):
    power_mw = forms.FloatField(min_value=0.1, initial=50)
    duration_hours = forms.FloatField(min_value=0.5, max_value=8, initial=2)
    round_trip_efficiency = forms.FloatField(min_value=0.5, max_value=1.0, initial=0.88)
    markets = forms.MultipleChoiceField(
        choices=MARKET_CHOICES,
        widget=forms.CheckboxSelectMultiple,
        initial=[Market.ENERGY.value, Market.DYNAMIC_CONTAINMENT.value],
    )
    days = forms.IntegerField(min_value=1, max_value=30, initial=7, label="Days to backtest")
    inspect_day = forms.IntegerField(
        min_value=1, initial=1, label="Day to inspect (dispatch chart)",
        help_text="Clamped to the last day if it's beyond the backtest length",
    )
