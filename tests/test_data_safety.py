import copy
import io
import json
import tempfile
import unittest
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from scripts.morning_pack import (
    DataError, anomalies, breadth_metrics, csv_bytes, latest_completed_session,
    generate, number, parse_index, parse_members, parse_stock, trade_date, wilder_rsi_series,
)
from scripts.validate_pack import audit


class DataSafetyTests(unittest.TestCase):
    def test_latest_session_dst_weekend_holiday_and_early_close(self):
        for now, expected in (
            ("2026-11-16T20:59:00+00:00", "2026-11-13"),
            ("2026-11-16T21:01:00+00:00", "2026-11-16"),
            ("2026-07-03T22:00:00+00:00", "2026-07-02"),
            ("2026-11-27T17:59:00+00:00", "2026-11-25"),
            ("2026-11-27T18:01:00+00:00", "2026-11-27"),
            ("2025-01-09T22:00:00+00:00", "2025-01-08"),
            ("2026-11-15T22:00:00+00:00", "2026-11-13"),
            ("2026-07-02T20:01:00+00:00", "2026-07-02"),
        ):
            with self.subTest(now=now):
                self.assertEqual(latest_completed_session(datetime.fromisoformat(now)), expected)

    def test_nasdaq_dates_keep_new_york_trade_day(self):
        dt = datetime(2026, 11, 16, 5, tzinfo=timezone.utc)
        self.assertEqual(trade_date(f"/Date({int(dt.timestamp() * 1000)})/"), "2026-11-16")
        self.assertEqual(trade_date("2026-11-16T00:00:00"), "2026-11-16")
        self.assertEqual(trade_date("2026-11-17T01:00:00Z"), "2026-11-16")
        with self.assertRaises(DataError):
            trade_date("not a trade date")

    def test_non_finite_and_missing_numbers_never_become_zero(self):
        for value in (None, "N/A", "--", "NaN", "inf", True):
            with self.subTest(value=value), self.assertRaises(DataError):
                number(value)
        self.assertEqual(number("$1,234.50"), 1234.5)

    def test_wilder_rsi_textbook_value_and_flat_series(self):
        prices = [44.34,44.09,44.15,43.61,44.33,44.83,45.10,45.42,45.84,46.08,45.89,46.03,45.61,46.28,46.28,46.00,46.03,46.41,46.22,45.64,46.21]
        self.assertAlmostEqual(wilder_rsi_series(prices)[14], 70.46413502109705)
        self.assertEqual(wilder_rsi_series([10] * 20)[-1], 50.0)
        self.assertIsNone(wilder_rsi_series([10] * 14)[-1])

    def index_payload(self):
        return {"iTotalRecords": 2, "aaData": [
            {"TradeDate":"2026-11-12T00:00:00", "Value": 102, "NetChange": 2, "High": 103, "Low": 99, "Constituents": 47},
            {"TradeDate":"2026-11-11T00:00:00", "Value": 100, "NetChange": 1, "High": 101, "Low": 99, "Constituents": 47},
        ]}

    def test_stale_truncated_duplicate_and_bad_arithmetic_index_are_rejected(self):
        payload = self.index_payload()
        self.assertEqual(parse_index(payload, "NDX", "2026-11-12", full=False)[-1]["Close"], 102)
        bad_cases = []
        bad = copy.deepcopy(payload); bad["iTotalRecords"] = 3; bad_cases.append(bad)
        bad = copy.deepcopy(payload); bad["aaData"][0]["NetChange"] = 3; bad_cases.append(bad)
        bad = copy.deepcopy(payload); bad["aaData"][0]["TradeDate"] = "2026-11-11T00:00:00"; bad_cases.append(bad)
        bad = copy.deepcopy(payload); bad["aaData"][0]["High"] = 90; bad_cases.append(bad)
        for bad in bad_cases:
            with self.subTest(bad=bad), self.assertRaises(DataError):
                parse_index(bad, "NDX", "2026-11-12", full=False)
        with self.assertRaises(DataError):
            parse_index(payload, "NDX", "2026-11-13", full=False)

    def test_membership_is_dynamic_but_duplicate_names_do_not_pass(self):
        payload = {"iTotalRecords":2, "aaData":[{"Symbol":"AAPL","Name":"Apple"},{"Symbol":"MSFT","Name":"Microsoft"}]}
        self.assertEqual(len(parse_members(payload, 2)), 2)
        with self.assertRaises(DataError):
            parse_members(payload, 47)
        payload["aaData"][1]["Symbol"] = "AAPL"
        with self.assertRaises(DataError):
            parse_members(payload, 2)

    def test_stock_symbol_pagination_staleness_and_ohlc(self):
        data = {"symbol":"AAPL", "totalRecords":2, "tradesTable":{"rows":[
            {"date":"11/16/2026","open":"100","high":"103","low":"99","close":"102","volume":"100"},
            {"date":"11/13/2026","open":"99","high":"101","low":"98","close":"100","volume":"100"},
        ]}}
        self.assertEqual(len(parse_stock({"data":data}, "AAPL", "2026-11-16")), 2)
        for changed in (
            {**data, "symbol":"MSFT"}, {**data, "totalRecords":3},
        ):
            with self.assertRaises(DataError):
                parse_stock({"data":changed}, "AAPL", "2026-11-16")
        with self.assertRaises(DataError):
            parse_stock({"data":data}, "AAPL", "2026-11-17")
        data["tradesTable"]["rows"][0]["low"] = "104"
        with self.assertRaises(DataError):
            parse_stock({"data":data}, "AAPL", "2026-11-16")

    def test_short_listed_stocks_use_valid_denominators(self):
        stocks = {"OLD":[{"Close":float(i+1)} for i in range(210)], "IPO":[{"Close":float(i+1)} for i in range(30)]}
        breadth = breadth_metrics(stocks)
        self.assertEqual(breadth["above_ma200_valid_count"], 1)
        self.assertEqual(breadth["above_ma200_count"], 1)
        self.assertEqual(breadth["above_ma200_pct"], 100)
        self.assertEqual(breadth["above_ma200_coverage_pct"], 50)
        self.assertEqual(breadth["above_ma20_valid_count"], 2)

    def test_new_extreme_is_flagged_and_prices_retained(self):
        stocks = {"TEST":[{"Date":"2026-11-13","Close":100},{"Date":"2026-11-16","Close":160}]}
        found = anomalies(stocks)
        self.assertEqual(len(found), 1)
        self.assertTrue(found[0]["Note"].startswith("UNREVIEWED"))
        self.assertEqual(stocks["TEST"][-1]["Close"], 160)

    def test_missing_manifest_file_fails_validation(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "bad.zip"
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("06_manifest.json", json.dumps({"as_of_date":"2026-11-16","component_count":47,"files":{}}))
            self.assertTrue(audit(path)["errors"])

    def test_stock_access_denial_stops_before_requesting_all_constituents(self):
        indices = [{"Date":"2026-11-13", "Close":100, "Constituents":47}, {"Date":"2026-11-16", "Close":101, "Constituents":47}]
        members = [{"Symbol":"AAPL", "Name":"Apple"}, {"Symbol":"MSFT", "Name":"Microsoft"}]
        with tempfile.TemporaryDirectory() as folder, \
             patch("scripts.morning_pack.latest_completed_session", return_value="2026-11-16"), \
             patch("scripts.morning_pack.NasdaqClient.index", return_value=indices), \
             patch("scripts.morning_pack.NasdaqClient.members", return_value=members), \
             patch("scripts.morning_pack.NasdaqClient.stock", side_effect=DataError("Nasdaq HTTP 403")) as fetch_stock:
            with self.assertRaisesRegex(DataError, "stopped before requesting"):
                generate(Path(folder))
            self.assertEqual(fetch_stock.call_count, 1)


if __name__ == "__main__":
    unittest.main()
