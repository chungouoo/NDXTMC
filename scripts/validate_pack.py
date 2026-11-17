#!/usr/bin/env python3
"""Independent ZIP reader: verify hashes, prices, RSI, MAs and breadth.

This audit proves internal consistency, not independent quote authenticity.
It intentionally does not import any calculation from the pack generator.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import math
import statistics
import sys
import zipfile
from collections import defaultdict
from pathlib import Path


def rsi(closes):
    if len(closes) < 15:
        return None
    diffs = [b - a for a, b in zip(closes, closes[1:])]
    up = sum(d for d in diffs[:14] if d > 0) / 14
    down = -sum(d for d in diffs[:14] if d < 0) / 14
    for d in diffs[14:]:
        up = (13 * up + max(0, d)) / 14
        down = (13 * down + max(0, -d)) / 14
    return 100 - 100 / (1 + up / down) if down else 100.0 if up else 50.0


def audit(path: Path | str, require_daily_ready: bool = True) -> dict:
    errors = []

    def check(ok, message):
        if not ok:
            errors.append(message)

    def near(actual, expected, label):
        check(actual is not None and expected is not None and math.isfinite(float(actual)) and abs(float(actual) - expected) < 1e-6, label)

    report = {"result": "FAIL", "errors": errors}
    try:
        with zipfile.ZipFile(path) as z:
            check(len(z.namelist()) == 13 and len(set(z.namelist())) == 13, "Expected 13 distinct files")
            manifest = json.loads(z.read("06_manifest.json"))
            inp = json.loads(z.read("07_morning_report_input.json"))
            target = manifest["as_of_date"]
            count = int(manifest["component_count"])
            check(len(manifest["files"]) == 12, "Expected 12 manifest entries")
            check(set(manifest["files"]) == set(z.namelist()) - {"06_manifest.json"}, "Manifest membership mismatch")
            for name, info in manifest["files"].items():
                raw = z.read(name)
                check(len(raw) == info["size_bytes"] and hashlib.sha256(raw).hexdigest() == info["sha256"], "Hash/size mismatch: " + name)
            check(inp["as_of_date"] == target, "Summary date differs from manifest")
            check(inp["quality"]["daily_ready"] == inp["daily_ready"], "Readiness flags disagree")
            if require_daily_ready:
                check(inp["daily_ready"] is True, "DAILY_READY is not True")
                check(manifest["quality_gate"] == "PASS", "Quality gate not PASS")
                check(manifest.get("source_mode", "LIVE") == "LIVE", "Replay cannot be live")
                check(not inp["quality"]["critical"], "Critical quality errors")
                check(all(inp["quality"]["checks"].values()), "A quality check failed")

            def rows(name):
                return list(csv.DictReader(io.StringIO(z.read(name).decode("utf-8-sig"))))

            latest = {r["Ticker"]: r for r in rows("01_index_latest.csv")}
            relative = rows("03_index_relative_performance.csv")[0]
            report["indices"] = {}
            for ticker in ("NDXTMC", "NDXT", "NDX"):
                rr = rows(ticker + "_index_history.csv")
                dates = [r["Date"] for r in rr]
                check(dates == sorted(set(dates)) and dates[-1] == target, ticker + " dates invalid")
                closes = [float(r["Close"]) for r in rr]
                check(all(math.isfinite(c) and c > 0 for c in closes), ticker + " invalid price")
                check(len(rr) == manifest["index_rows"][ticker], ticker + " row count mismatch")
                near(inp[ticker]["close"], closes[-1], ticker + " summary close mismatch")
                near(latest[ticker]["Close"], closes[-1], ticker + " latest CSV close mismatch")
                for n in (1, 5, 20):
                    result = (closes[-1] / closes[-n - 1] - 1) * 100
                    near(latest[ticker][f"return_{n}d_pct"], result, ticker + " return mismatch")
                    near(relative[f"{ticker}_return_{n}d_pct"], result, ticker + " relative return mismatch")
                    if ticker != "NDXTMC":
                        near(inp[ticker][f"return_{n}d_pct"], result, ticker + " JSON return mismatch")
                if ticker == "NDXTMC":
                    near(inp[ticker]["daily_return_pct"], (closes[-1] / closes[-2] - 1) * 100, "NDXTMC daily return mismatch")
                    near(inp[ticker]["rsi14_wilder"], rsi(closes), "NDXTMC RSI14 mismatch")
                    for n in (20, 40, 50, 200):
                        near(inp[ticker][f"ma{n}"], statistics.mean(closes[-n:]), "NDXTMC MA mismatch")
                    near(inp[ticker]["drawdown_from_52w_closing_high_pct"], (closes[-1] / max(closes[-252:]) - 1) * 100, "NDXTMC drawdown mismatch")
                    end = rows("02_ndxtmc_indicators.csv")[-1]
                    near(end["rsi14_wilder"], rsi(closes), "Indicator CSV RSI mismatch")
                report["indices"][ticker] = {"rows": len(rr), "date": dates[-1], "close": closes[-1]}
            for n in (1, 5, 20):
                delta = float(relative[f"NDXTMC_return_{n}d_pct"]) - float(relative[f"NDXT_return_{n}d_pct"])
                near(relative[f"NDXTMC_minus_NDXT_{n}d_pp"], delta, "Relative spread mismatch")
            stock = rows("NDXTMC_47stocks_history.csv")
            check(len({(r["Ticker"], r["Date"]) for r in stock}) == len(stock), "Duplicate stock date/ticker")
            groups = defaultdict(list)
            for row in stock:
                groups[row["Ticker"]].append(row)
                o, h, l, c = [float(row[k]) for k in ("Open", "High", "Low", "Close")]
                check(all(math.isfinite(v) and v > 0 for v in (o, h, l, c)) and h >= max(o, l, c) - 1e-6 and l <= min(o, c) + 1e-6, "Bad stock OHLC")
            check(len(groups) == count and count > 0, "Stock coverage differs from constituent count")
            if "membership" in inp:
                check(set(groups) == {m["Symbol"] for m in inp["membership"]["components"]}, "Constituent identities differ")
            values = []
            for ticker, group in groups.items():
                group.sort(key=lambda r: r["Date"])
                check(group[-1]["Date"] == target, "Stale stock " + ticker)
                values.append([float(r["Close"]) for r in group])
            breadth = rows("04_breadth_latest.csv")[0]
            for n in (20, 40, 50, 200):
                valid = [c for c in values if len(c) >= n]
                above = sum(c[-1] > statistics.mean(c[-n:]) for c in valid)
                near(breadth[f"above_ma{n}_count"], above, "Breadth numerator mismatch")
                near(breadth[f"above_ma{n}_valid_count"], len(valid), "Breadth denominator mismatch")
                if valid:
                    near(breadth[f"above_ma{n}_pct"], 100 * above / len(valid), "Breadth percent mismatch")
                near(breadth[f"above_ma{n}_coverage_pct"], 100 * len(valid) / count, "Breadth coverage mismatch")
                near(inp["breadth"][f"above_ma{n}_count"], above, "JSON breadth mismatch")
            rsis = [value for c in values if (value := rsi(c)) is not None]
            if rsis:
                near(breadth["median_rsi14"], statistics.median(rsis), "Median stock RSI mismatch")
            near(breadth["advancing"], sum(c[-1] > c[-2] for c in values), "Advancing count mismatch")
            near(breadth["declining"], sum(c[-1] < c[-2] for c in values), "Declining count mismatch")
            near(breadth["unchanged"], sum(c[-1] == c[-2] for c in values), "Unchanged count mismatch")
            check(not rows("failed_tickers.csv"), "Failed tickers not empty")
            report.update({"as_of_date": target, "component_count": count, "stock_rows": len(stock), "verified_manifest_files": len(manifest["files"])})
    except Exception as exc:
        errors.append(f"Malformed pack: {type(exc).__name__}: {exc}")
    report["result"] = "PASS_WITH_SOURCE_CAVEAT" if not errors else "FAIL"
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("zipfile", type=Path)
    parser.add_argument("--allow-replay", action="store_true")
    args = parser.parse_args()
    result = audit(args.zipfile, not args.allow_replay)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    sys.exit(1 if result["errors"] else 0)
