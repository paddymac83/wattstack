from django.test import Client, TestCase


class ScenarioViewTests(TestCase):
    def test_index_loads(self):
        response = Client().get("/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "wattstack")

    def test_recompute_returns_a_chart(self):
        response = Client().post(
            "/recompute/",
            {
                "power_mw": 5,
                "duration_hours": 2,
                "round_trip_efficiency": 0.88,
                "markets": ["wholesale", "dynamic_containment_low"],
                "days": 2,
                "inspect_day": 1,
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"MW/yr", response.content)

    def test_recompute_returns_a_dispatch_chart_with_headroom_and_footroom(self):
        response = Client().post(
            "/recompute/",
            {
                "power_mw": 5,
                "duration_hours": 2,
                "round_trip_efficiency": 0.88,
                "markets": ["wholesale", "dynamic_containment_low"],
                "days": 2,
                "inspect_day": 1,
            },
        )
        content = response.content.decode()
        self.assertIn("Dispatch", content)
        self.assertIn("Headroom", content)
        self.assertIn("Footroom", content)

    def test_inspect_day_beyond_backtest_length_is_clamped_not_a_500(self):
        response = Client().post(
            "/recompute/",
            {
                "power_mw": 5,
                "duration_hours": 2,
                "round_trip_efficiency": 0.88,
                "markets": ["wholesale"],
                "days": 2,
                "inspect_day": 99,
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"day 2", response.content)

    def test_recompute_persists_a_run(self):
        from .models import BacktestRunRecord

        Client().post(
            "/recompute/",
            {
                "power_mw": 5,
                "duration_hours": 2,
                "round_trip_efficiency": 0.88,
                "markets": ["wholesale"],
                "days": 1,
                "inspect_day": 1,
            },
        )
        self.assertEqual(BacktestRunRecord.objects.count(), 1)

    def test_invalid_form_does_not_500(self):
        response = Client().post("/recompute/", {"power_mw": -5})
        self.assertEqual(response.status_code, 200)
