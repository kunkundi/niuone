#!/usr/bin/env python3
"""Contracts that keep optional US-rating enrichment off the first render."""

import importlib.util
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
COMPAT = APP / "compat"
sys.path[:0] = [str(COMPAT), str(APP)]

spec = importlib.util.spec_from_file_location(
    "dashboard_us_ratings_lazy_test",
    COMPAT / "niuone_dashboard.py",
)
dashboard = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(dashboard)


class FakeQuoteResponse:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return b'var hq_str_gb_aapl="Apple Inc,200.0,1.5,0,3.0"'


class UsRatingsLazyLoadingTests(unittest.TestCase):
    def test_live_quotes_do_not_wait_for_company_profiles(self):
        original_urlopen = dashboard.urllib.request.urlopen
        original_profiles = dashboard.fetch_us_company_profiles
        profile_calls = []
        try:
            dashboard.urllib.request.urlopen = lambda *_args, **_kwargs: FakeQuoteResponse()
            dashboard.fetch_us_company_profiles = lambda symbols: profile_calls.append(symbols) or {}

            payload = dashboard.fetch_us_quotes(["AAPL"])
        finally:
            dashboard.urllib.request.urlopen = original_urlopen
            dashboard.fetch_us_company_profiles = original_profiles

        self.assertEqual(profile_calls, [])
        self.assertEqual(payload["items"]["AAPL"]["price"], 200.0)
        self.assertNotIn("sector", payload["items"]["AAPL"])

    def test_company_profiles_have_an_independent_payload(self):
        original_profiles = dashboard.fetch_us_company_profiles
        try:
            dashboard.fetch_us_company_profiles = lambda symbols: {
                symbols[0]: {"sector": "科技", "industry": "软件"}
            }
            payload = dashboard.fetch_us_profiles(["AAPL"])
        finally:
            dashboard.fetch_us_company_profiles = original_profiles

        self.assertEqual(payload["symbols"], ["AAPL"])
        self.assertEqual(payload["items"]["AAPL"]["industry"], "软件")

    def test_frontend_loads_profiles_only_when_a_rating_row_expands(self):
        data_source = (
            ROOT / "web" / "src" / "composables" / "useUsRatingsData.js"
        ).read_text(encoding="utf-8")
        panel_source = (
            ROOT / "web" / "src" / "components" / "UsRatingsPanel.vue"
        ).read_text(encoding="utf-8")
        card_source = (
            ROOT / "web" / "src" / "components" / "us-ratings" / "UsRatingCard.vue"
        ).read_text(encoding="utf-8")

        self.assertIn("kind === 'quotes' ? '/api/us_quotes' : '/api/us_profiles'", data_source)
        self.assertIn("watch(selectedRecords, records => loadQuotesForRecords(records)", panel_source)
        self.assertIn("if (opening) props.loadProfile(row.ticker)", card_source)
        self.assertNotIn("loadProfile", panel_source.split("watch(selectedRecords", 1)[1].split("onMounted", 1)[0])


if __name__ == "__main__":
    unittest.main()
