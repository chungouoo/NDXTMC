#!/usr/bin/env python3
"""Fetch public Nasdaq EOD data and build the existing 13-file morning pack.

No credentials, paid APIs, synthetic quotes, or fallback-to-stale data.
The current constituent list is fetched anew on every run.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import math
import os
import re
import statistics
import sys
import time
import zipfile
from http.cookiejar import CookieJar
from threading import Lock, local
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPCookieProcessor, Request, build_opener, urlopen
from zoneinfo import ZoneInfo

import exchange_calendars as xcals

NY = ZoneInfo("America/New_York")
BJ = ZoneInfo("Asia/Shanghai")
PERIODS = (20, 40, 50, 200)
EXTREME_THRESHOLD_PCT = 40.0
YAHOO_CLOSE_SOURCE = "Yahoo Finance chart Close; no local adjustment"
STOCK_HISTORY_FILE = "NDXTMC_47stocks_history.csv"
INDEX_NAMES = {
    "NDXTMC": "NASDAQ-100 Technology Sector Market-Cap Weighted Index",
    "NDXT": "NASDAQ-100 Technology Sector Index",
    "NDX": "NASDAQ-100 Index",
}
BASELINE_DATE = "2026-11-13"
BASELINE_CLOSES = {
    "NDXTMC": 2730.99319270507,
    "NDXT": 15358.67449828526,
    "NDX": 27943.73559001413,
}
# Values retained from the user's previously reviewed 2026-11-13 data pack.
# This is a reviewed-snapshot carryover, not a claim of new independent verification.
REVIEWED_EXTREMES = {
    ("CRWV", "2025-04-01"): (37.08, 52.57),
    ("NBIS", "2025-09-09"): (64.06, 95.72),
}


class DataError(ValueError):
    """Source is incomplete, stale, contradictory, or malformed."""


class TruncatedYahooHistory(DataError):
    def __init__(self, ticker: str, rows: list[dict], first_session: str):
        super().__init__(f"{ticker}: Yahoo history truncated before {rows[0]['Date']}")
        self.rows = rows
        self.first_session = first_session


def calendar(now: datetime | None = None):
    now = now or datetime.now(timezone.utc)
    return xcals.get_calendar("XNAS", start="2022-03-01", end=f"{now.year + 1}-12-31")


def latest_completed_session(now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise DataError("Current time must include a timezone")
    cal = calendar(now)
    schedule = cal.schedule
    closed = schedule.loc[schedule["close"] <= now.astimezone(timezone.utc)]
    if closed.empty:
        raise DataError("Calendar contains no completed session")
    return closed.index[-1].date().isoformat()


def expected_sessions(start: str, end: str) -> set[str]:
    return {d.date().isoformat() for d in calendar().sessions_in_range(start, end)}


def number(value) -> float:
    if isinstance(value, bool) or value is None:
        raise DataError(f"Invalid numeric value: {value!r}")
    try:
        result = float(str(value).replace("$", "").replace(",", "").strip())
    except (ValueError, TypeError) as exc:
        raise DataError(f"Invalid numeric value: {value!r}") from exc
    if not math.isfinite(result):
        raise DataError("Non-finite numeric value")
    return result


def trade_date(value: str) -> str:
    if not isinstance(value, str):
        raise DataError("Missing TradeDate")
    if value.startswith("/Date("):
        match = re.fullmatch(r"/Date\((-?\d+)(?:[+-]\d{4})?\)/", value)
        if not match:
            raise DataError("Invalid Nasdaq .NET date")
        return datetime.fromtimestamp(int(match[1]) / 1000, timezone.utc).astimezone(NY).date().isoformat()
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise DataError("Invalid Nasdaq trade date") from exc
    return (dt.astimezone(NY) if dt.tzinfo else dt).date().isoformat()


def wilder_rsi_series(closes: list[float], period: int = 14) -> list[float | None]:
    result: list[float | None] = [None] * len(closes)
    if len(closes) <= period:
        return result
    changes = [b - a for a, b in zip(closes, closes[1:])]
    gain = sum(max(d, 0) for d in changes[:period]) / period
    loss = sum(max(-d, 0) for d in changes[:period]) / period

    def current():
        return 100 - 100 / (1 + gain / loss) if loss else (100.0 if gain else 50.0)

    result[period] = current()
    for i, change in enumerate(changes[period:], period + 1):
        gain = (gain * (period - 1) + max(change, 0)) / period
        loss = (loss * (period - 1) + max(-change, 0)) / period
        result[i] = current()
    return result


def check_sessions(rows: list[dict], target: str, label: str) -> None:
    if not rows:
        raise DataError(f"{label}: empty history")
    dates = [r["Date"] for r in rows]
    if len(dates) != len(set(dates)):
        raise DataError(f"{label}: duplicate dates")
    if dates != sorted(dates):
        raise DataError(f"{label}: dates not sorted")
    if dates[-1] != target:
        raise DataError(f"{label}: stale source, last={dates[-1]}, required={target}")
    if len(dates) < 2:
        raise DataError(f"{label}: previous session missing")
    expected = expected_sessions(dates[0], target)
    if set(dates) != expected:
        missing = sorted(expected - set(dates))[:6]
        extra = sorted(set(dates) - expected)[:6]
        raise DataError(f"{label}: session mismatch; missing={missing}, extra={extra}")


def parse_index(payload: dict, ticker: str, target: str, *, full: bool = True) -> list[dict]:
    data = payload.get("aaData")
    if not isinstance(data, list) or not data:
        raise DataError(f"{ticker}: no official EOD rows")
    if int(payload.get("iTotalRecords", -1)) != len(data):
        raise DataError(f"{ticker}: incomplete index response")
    rows = []
    for raw in data:
        row = {
            "Open": "", "Date": trade_date(raw.get("TradeDate")), "Ticker": ticker,
            "IndexName": INDEX_NAMES[ticker], "Close": number(raw.get("Value")),
            "NetChange": number(raw.get("NetChange")),
            "High": number(raw.get("High")), "Low": number(raw.get("Low")),
            "Constituents": int(number(raw.get("Constituents"))),
            "Source": "Nasdaq HistoryData EOD", "ValidationStatus": "VALIDATED",
        }
        if row["Date"] > target or row["Close"] <= 0 or row["Constituents"] < 1:
            raise DataError(f"{ticker}: invalid EOD row")
        if row["Low"] <= 0 or row["High"] < max(row["Close"], row["Low"]) - 1e-6 or row["Low"] > row["Close"] + 1e-6:
            raise DataError(f"{ticker}: invalid index high/low")
        rows.append(row)
    rows.sort(key=lambda row: row["Date"])
    check_sessions(rows, target, ticker)
    if full and len(rows) < 252:
        raise DataError(f"{ticker}: insufficient history for 52-week metrics")
    if full:
        expected_start = "2022-03-21" if ticker == "NDXTMC" else "2025-01-02"
        if rows[0]["Date"] != expected_start:
            raise DataError(f"{ticker}: truncated start date {rows[0]['Date']}")
    for i, row in enumerate(rows):
        previous = rows[i - 1]["Close"] if i else None
        row["PreviousClose"] = previous
        row["PercentChange"] = (row["Close"] / previous - 1) * 100 if previous else None
        if previous and abs(row["Close"] - previous - row["NetChange"]) > 1e-5:
            raise DataError(f"{ticker}: NetChange/previous close mismatch on {row['Date']}")
        if row["Date"] == BASELINE_DATE and abs(row["Close"] - BASELINE_CLOSES[ticker]) > 1e-5:
            raise DataError(f"{ticker}: conflict with reviewed snapshot {BASELINE_DATE}")
    return rows


def parse_members(payload: dict, expected_count: int) -> list[dict]:
    rows = payload.get("aaData")
    if not isinstance(rows, list) or not rows:
        raise DataError("Official constituent list unavailable")
    symbols = []
    for row in rows:
        symbol = row.get("Symbol", "")
        name = row.get("Name", "")
        if not re.fullmatch(r"[A-Z][A-Z0-9.-]{0,11}", symbol) or not isinstance(name, str) or not name:
            raise DataError("Invalid constituent symbol/name")
        symbols.append(symbol)
    if len(symbols) != len(set(symbols)) or len(rows) != int(payload.get("iTotalRecords", -1)):
        raise DataError("Constituent list duplicate/truncated")
    if len(rows) != expected_count:
        raise DataError(f"Membership mismatch: list={len(rows)}, index={expected_count}")
    return sorted(rows, key=lambda row: row["Symbol"])


def parse_stock(payload: dict, ticker: str, target: str) -> list[dict]:
    data = payload.get("data")
    if not isinstance(data, dict) or data.get("symbol") != ticker:
        raise DataError(f"{ticker}: symbol/data mismatch")
    raw_rows = (data.get("tradesTable") or {}).get("rows")
    if not isinstance(raw_rows, list) or not raw_rows:
        raise DataError(f"{ticker}: no stock prices")
    if len(raw_rows) != int(data.get("totalRecords", -1)):
        raise DataError(f"{ticker}: incomplete stock response / pagination")
    rows = []
    for raw in raw_rows:
        row = {
            "Date": datetime.strptime(raw["date"], "%m/%d/%Y").date().isoformat(),
            "Ticker": ticker, **{k.title(): number(raw[k]) for k in ("open", "high", "low", "close", "volume")},
            "Source": "Nasdaq",
        }
        if row["Date"] < "2025-01-02" or row["Date"] > target:
            raise DataError(f"{ticker}: unexpected stock date {row['Date']}")
        if min(row[k] for k in ("Open", "High", "Low", "Close")) <= 0 or row["Volume"] < 0 or not row["Volume"].is_integer():
            raise DataError(f"{ticker}: invalid stock values")
        if row["High"] < max(row["Open"], row["Close"], row["Low"]) - 1e-6 or row["Low"] > min(row["Open"], row["Close"]) + 1e-6:
            raise DataError(f"{ticker}: OHLC inconsistency on {row['Date']}")
        row["Volume"] = int(row["Volume"])
        rows.append(row)
    rows.sort(key=lambda row: row["Date"])
    check_sessions(rows, target, ticker)
    # Shorter histories may be genuine IPOs; no pre-listing data is manufactured.
    return rows


def parse_yahoo_stock(payload: dict, ticker: str, target: str) -> list[dict]:
    """Read Yahoo provider Close, never substitute its dividend-adjusted close."""
    chart = payload.get("chart") or {}
    result = chart.get("result")
    if chart.get("error") or not isinstance(result, list) or len(result) != 1:
        raise DataError(f"{ticker}: Yahoo chart unavailable")
    data = result[0]
    meta = data.get("meta") or {}
    if meta.get("symbol") != ticker or meta.get("currency") != "USD":
        raise DataError(f"{ticker}: Yahoo symbol/currency mismatch")
    if meta.get("exchangeTimezoneName") not in ("America/New_York", "US/Eastern") or meta.get("dataGranularity") != "1d":
        raise DataError(f"{ticker}: Yahoo timezone or interval mismatch")
    timestamps = data.get("timestamp")
    quotes = (data.get("indicators") or {}).get("quote")
    if not isinstance(timestamps, list) or not timestamps or not isinstance(quotes, list) or len(quotes) != 1:
        raise DataError(f"{ticker}: Yahoo empty price arrays")
    quote = quotes[0]
    keys = ("open", "high", "low", "close", "volume")
    if not all(isinstance(quote.get(k), list) and len(quote[k]) == len(timestamps) for k in keys):
        raise DataError(f"{ticker}: Yahoo price-array length mismatch")
    provider_rows = []
    for i, timestamp in enumerate(timestamps):
        stamp = datetime.fromtimestamp(number(timestamp), timezone.utc).astimezone(NY)
        for key in keys:
            if quote[key][i] is None:
                raise DataError(f"{ticker}: Yahoo null {key} on {stamp.date().isoformat()} (target={target}); no substitute price")
        provider_rows.append({"date": stamp.strftime("%m/%d/%Y"), **{k: quote[k][i] for k in keys}})
    # Reuse the same OHLC, coverage, date and continuity validation for both vendors.
    rows = parse_stock({"data": {"symbol": ticker, "totalRecords": len(timestamps), "tradesTable": {"rows": provider_rows}}}, ticker, target)
    first_trade = datetime.fromtimestamp(number(meta.get("firstTradeDate")), timezone.utc).astimezone(NY).date().isoformat()
    earliest = max("2025-01-02", first_trade)
    sessions = sorted(expected_sessions(earliest, target))
    for row in rows:
        row["Source"] = YAHOO_CLOSE_SOURCE
    if not sessions or rows[0]["Date"] < sessions[0]:
        raise DataError(f"{ticker}: Yahoo rows precede the reported first trading session")
    if rows[0]["Date"] > sessions[0]:
        raise TruncatedYahooHistory(ticker, rows, sessions[0])
    return rows


def read_previous_yahoo_pack(handoff: Path) -> tuple[dict[str, list[dict]], dict]:
    snapshot = json.loads(handoff.read_text(encoding="utf-8"))
    summary, manifest, validation = (snapshot[key] for key in ("data", "manifest", "validation"))
    pipeline = snapshot["pipeline"]
    if (snapshot["status"] != "READY" or summary["daily_ready"] is not True
            or manifest["source_mode"] != "LIVE" or manifest["quality_gate"] != "PASS"
            or validation["result"] not in ("PASS", "PASS_WITH_SOURCE_CAVEAT") or validation["errors"] != []
            or summary["source_policy"]["stocks"] != YAHOO_CLOSE_SOURCE
            or summary["as_of_date"] != manifest["as_of_date"]
            or summary["as_of_date"] != validation["as_of_date"]):
        raise DataError("Previous pack is not an audited live Yahoo Finance pack")
    repo = os.environ.get("GITHUB_REPOSITORY", "chungouoo/NDXTMC")
    tag = f"data-{summary['as_of_date']}-{pipeline['run_id']}-{pipeline['run_attempt']}"
    url = f"https://github.com/{repo}/releases/download/{tag}/LATEST_Morning_Report_Data_Pack.zip"
    if pipeline["repository"] != repo or pipeline["zip_url"] != url or not re.fullmatch(r"data-\d{4}-\d{2}-\d{2}-\d+-\d+", tag):
        raise DataError("Previous pack does not match the repository release")
    with urlopen(url, timeout=25) as response:
        packed = response.read(16_000_001)
    digest = hashlib.sha256(packed).hexdigest()
    if len(packed) > 16_000_000 or digest != pipeline["zip_sha256"]:
        raise DataError("Previous pack ZIP checksum mismatch")
    with zipfile.ZipFile(io.BytesIO(packed)) as archive:
        if json.loads(archive.read("06_manifest.json")) != manifest:
            raise DataError("Previous pack manifest does not match its handoff")
        raw = archive.read(STOCK_HISTORY_FILE)
    if hashlib.sha256(raw).hexdigest() != manifest["files"][STOCK_HISTORY_FILE]["sha256"]:
        raise DataError("Previous pack stock history checksum mismatch")
    histories = defaultdict(list)
    for row in csv.DictReader(io.StringIO(raw.decode("utf-8-sig"))):
        if row["Source"] != YAHOO_CLOSE_SOURCE:
            raise DataError("Previous pack stock history contains a different stock data provider")
        for key in ("Open", "High", "Low", "Close"):
            row[key] = number(row[key])
        row["Volume"] = int(row["Volume"])
        histories[row["Ticker"]].append(row)
    return histories, {"as_of_date": summary["as_of_date"], "zip_url": url, "zip_sha256": digest}


def recover_yahoo_history(ticker: str, target: str, short: TruncatedYahooHistory, previous: list[dict]) -> tuple[list[dict], dict]:
    old_by_date = {row["Date"]: row for row in previous}
    overlaps = [row for row in short.rows if row["Date"] in old_by_date]
    if not overlaps:
        raise DataError(f"{ticker}: live Yahoo history does not overlap the previous verified pack")
    for row in overlaps:
        day = row["Date"]
        if any(not math.isclose(row[key], old_by_date[day][key], rel_tol=0, abs_tol=0.0051)
               for key in ("Open", "High", "Low", "Close")):
            raise DataError(f"{ticker}: Yahoo OHLC conflict with previous pack on {day}")
    first_live = short.rows[0]["Date"]
    prefix = [row for row in previous if row["Date"] < first_live]
    combined = prefix + short.rows
    if combined[0]["Date"] != short.first_session:
        raise DataError(f"{ticker}: recovery still missing Yahoo history before {combined[0]['Date']}")
    check_sessions(combined, target, ticker)
    return combined, {
        "ticker": ticker, "first_live_date": first_live, "carried_sessions": len(prefix),
        "overlap_dates": [row["Date"] for row in overlaps],
        "overlap_volume_revisions": sum(row["Volume"] != old_by_date[row["Date"]]["Volume"] for row in overlaps),
    }


class NasdaqClient:
    def __init__(self, raw_dir: Path, fixtures: Path | None = None):
        self.raw_dir = raw_dir
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        self.fixtures = fixtures
        self._http = local()

    def request(self, url: str, filename: str, params: dict | None = None) -> dict:
        if self.fixtures:
            payload = json.loads((self.fixtures / filename).read_text())
        else:
            yahoo = urlsplit(url).hostname == "query1.finance.yahoo.com"
            provider = "Yahoo Finance" if yahoo else "Nasdaq"
            origin = "https://finance.yahoo.com" if yahoo else "https://www.nasdaq.com"
            data = urlencode(params).encode() if params is not None else None
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/153.0.0.0 Safari/537.36",
                "Accept": "application/json, text/plain, */*",
                "Accept-Language": "en-US,en;q=0.9", "Origin": origin,
                "Referer": origin + "/",
            }
            if data is not None:
                headers["Content-Type"] = "application/x-www-form-urlencoded"
            request = Request(url, data=data, headers=headers)
            # A normal anonymous cookie session per worker; no copied credentials,
            # challenge solving, proxies, or fingerprint manipulation.
            if not hasattr(self._http, "opener"):
                self._http.opener = build_opener(HTTPCookieProcessor(CookieJar()))
            for attempt in range(3):
                try:
                    with self._http.opener.open(request, timeout=25) as response:
                        raw = response.read(20_000_001)
                    if len(raw) > 20_000_000:
                        raise DataError("Unexpectedly large source response")
                    payload = json.loads(raw)
                    break
                except HTTPError as exc:
                    if exc.code not in (500, 502, 503, 504) or attempt == 2:
                        raise DataError(f"{provider} HTTP {exc.code}; no permission/rate-limit bypass") from exc
                    time.sleep(attempt + 1)
                except (URLError, TimeoutError) as exc:
                    if attempt == 2:
                        raise DataError(f"{provider} network request failed after 3 attempts") from exc
                    time.sleep(attempt + 1)
        if not isinstance(payload, dict):
            raise DataError("Market-data response is not a JSON object")
        write_json(self.raw_dir / filename, payload)
        return payload

    def index(self, ticker: str, target: str, *, recheck: bool = False):
        start = (date.fromisoformat(target) - timedelta(days=10)).strftime("%m/%d/%Y") if recheck else ("03/21/2022" if ticker == "NDXTMC" else "01/02/2025")
        suffix = "_recheck" if recheck and not self.fixtures else ""
        payload = self.request("https://indexes.nasdaq.com/Index/HistoryData", f"raw_index_{ticker}{suffix}.json", {
            "id": ticker, "startDate": start,
            "endDate": date.fromisoformat(target).strftime("%m/%d/%Y"), "timeOfDay": "EOD",
        })
        return parse_index(payload, ticker, target, full=not recheck)

    def members(self, target: str, count: int):
        return parse_members(self.request("https://indexes.nasdaq.com/Index/WeightingData", f"membership_{target}.json", {
            "id": "NDXTMC", "tradeDate": date.fromisoformat(target).strftime("%m/%d/%Y"), "timeOfDay": "EOD",
        }), count)

    def stock(self, ticker: str, target: str):
        url = f"https://api.nasdaq.com/api/quote/{ticker}/historical?" + urlencode({
            "assetclass": "stocks", "fromdate": "2025-01-02", "todate": target, "limit": 5000,
        })
        return parse_stock(self.request(url, f"raw_stock_{ticker}.json"), ticker, target)


class YahooClient(NasdaqClient):
    def __init__(self, raw_dir: Path, *, previous_handoff: Path | None = None):
        super().__init__(raw_dir)
        self.previous_handoff = previous_handoff
        self.previous_pack = None
        self.previous_pack_lock = Lock()
        self.history_recoveries: dict[str, dict] = {}

    def stock(self, ticker: str, target: str):
        start = int(datetime(2025, 1, 2, tzinfo=NY).timestamp())
        end = int(datetime.combine(date.fromisoformat(target) + timedelta(days=1), datetime.min.time(), NY).timestamp())
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?" + urlencode({
            "period1": start, "period2": end, "interval": "1d", "events": "div,splits", "includeAdjustedClose": "true",
        })
        payload = self.request(url, f"raw_yahoo_stock_{ticker}.json")
        try:
            return parse_yahoo_stock(payload, ticker, target)
        except TruncatedYahooHistory as short:
            if self.previous_handoff is None or not self.previous_handoff.is_file():
                raise
            with self.previous_pack_lock:
                if self.previous_pack is None:
                    self.previous_pack = read_previous_yahoo_pack(self.previous_handoff)
                histories, provenance = self.previous_pack
            rows, evidence = recover_yahoo_history(ticker, target, short, histories.get(ticker, []))
            record = {**evidence, "previous_pack": provenance}
            write_json(self.raw_dir / f"yahoo_history_recovery_{ticker}.json", record)
            with self.previous_pack_lock:
                self.history_recoveries[ticker] = record
            print(f"{ticker}: recovered {evidence['carried_sessions']} earlier Yahoo sessions; overlap {', '.join(evidence['overlap_dates'])}; live through {target}", flush=True)
            return rows


def write_json(path: Path, value) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def csv_bytes(rows: list[dict], fields: list[str] | None = None) -> bytes:
    fields = fields or list(rows[0])
    out = io.StringIO(newline="")
    writer = csv.DictWriter(out, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    writer.writerows({k: row.get(k) for k in fields} for row in rows)
    return out.getvalue().encode("utf-8")


def fetch_stock_batch(client: NasdaqClient, members: list[dict], target: str, initial: dict | None = None) -> tuple[dict, list]:
    stocks, failures = dict(initial or {}), []
    if not stocks:
        first = members[0]["Symbol"]
        try:
            stocks[first] = client.stock(first, target)
            print(f"{first}: source connectivity OK, {len(stocks[first])} rows", flush=True)
        except Exception as exc:
            return {}, [{"Ticker": first, "Error": str(exc)}]
    with ThreadPoolExecutor(max_workers=4) as pool:
        tasks = {pool.submit(client.stock, m["Symbol"], target): m["Symbol"] for m in members if m["Symbol"] not in stocks}
        for task in as_completed(tasks):
            ticker = tasks[task]
            try:
                stocks[ticker] = task.result()
                print(f"{ticker}: {len(stocks[ticker])} rows through {stocks[ticker][-1]['Date']}", flush=True)
            except Exception as exc:
                failures.append({"Ticker": ticker, "Error": str(exc)})
    return stocks, failures


def indicator_rows(rows: list[dict]) -> list[dict]:
    closes = [row["Close"] for row in rows]
    rsis = wilder_rsi_series(closes)
    result, all_high, year_high, previous_year = [], 0.0, 0.0, ""
    for i, row in enumerate(rows):
        year = row["Date"][:4]
        if year != previous_year:
            year_high = 0.0
        previous_year = year
        all_high, year_high = max(all_high, closes[i]), max(year_high, closes[i])
        high_52 = max(closes[max(0, i - 251):i + 1])
        item = {"Date": row["Date"], "Ticker": row["Ticker"], "Close": closes[i], "PreviousClose": closes[i - 1] if i else None}
        item.update({f"return_{n}d_pct": (closes[i] / closes[i - n] - 1) * 100 if i >= n else None for n in (1, 5, 20)})
        item["rsi14_wilder"] = rsis[i]
        item.update({f"ma{n}": statistics.mean(closes[i - n + 1:i + 1]) if i >= n - 1 else None for n in PERIODS})
        item.update({"high_52w_closing": high_52, "high_ytd_closing": year_high, "high_all_time_closing": all_high})
        item.update({f"drawdown_from_{label}_closing_high_pct": (closes[i] / high - 1) * 100 for label, high in (("52w", high_52), ("ytd", year_high), ("all_time", all_high))})
        result.append(item)
    return result


def breadth_metrics(stocks: dict[str, list[dict]]) -> dict:
    count = len(stocks)
    out = {"component_count": count}
    closes = {t: [r["Close"] for r in rows] for t, rows in stocks.items()}
    for n in PERIODS:
        valid = [c for c in closes.values() if len(c) >= n]
        above = sum(c[-1] > statistics.mean(c[-n:]) for c in valid)
        out.update({f"above_ma{n}_count": above, f"above_ma{n}_valid_count": len(valid),
                    f"above_ma{n}_pct": 100 * above / len(valid) if valid else None,
                    f"above_ma{n}_coverage_pct": 100 * len(valid) / count})
    rsis = [v for c in closes.values() if (v := wilder_rsi_series(c)[-1]) is not None]
    out.update({"rsi14_valid_count": len(rsis), "rsi14_coverage_pct": 100 * len(rsis) / count,
                "median_rsi14": statistics.median(rsis) if rsis else None,
                "rsi_below_35": sum(v < 35 for v in rsis), "rsi_below_30": sum(v < 30 for v in rsis), "rsi_above_70": sum(v > 70 for v in rsis)})
    for key, comparison in (("advancing", lambda a, b: a > b), ("declining", lambda a, b: a < b), ("unchanged", lambda a, b: a == b)):
        out[key] = sum(comparison(c[-1], c[-2]) for c in closes.values())
        out[key + "_count"] = out[key]
    out.update({"daily_return_valid_count": count, "daily_return_coverage_pct": 100.0,
                "downloaded_component_count": count, "source_row_count": sum(len(c) for c in closes.values()),
                "valid_count": count, "ma40_valid_count": out["above_ma40_valid_count"],
                "below_or_equal_ma40_count": out["above_ma40_valid_count"] - out["above_ma40_count"],
                "breadth_scope": "NDXTMC constituent internal breadth; membership verified for as_of_date"})
    return out


def anomalies(stocks: dict[str, list[dict]]) -> list[dict]:
    result = []
    for ticker, rows in sorted(stocks.items()):
        for prior, row in zip(rows, rows[1:]):
            change = (row["Close"] / prior["Close"] - 1) * 100
            if abs(change) < EXTREME_THRESHOLD_PCT:
                continue
            reviewed = REVIEWED_EXTREMES.get((ticker, row["Date"]))
            # Yahoo chart values may be serialized from float32 (a few millionths).
            tolerance = 1e-4 if row.get("Source", "").startswith("Yahoo") else 1e-6
            matched = reviewed is not None and abs(reviewed[0] - prior["Close"]) < tolerance and abs(reviewed[1] - row["Close"]) < tolerance
            result.append({"Ticker": ticker, "Date": row["Date"], "PreviousClose": prior["Close"], "Close": row["Close"],
                           "ChangePct": change, "Note": "CARRIED_REVIEWED_SNAPSHOT; original source prices retained" if matched else "UNREVIEWED_EXTREME_RETURN; inspect corporate action/source"})
    return result


def build_pack(indices: dict[str, list[dict]], members: list[dict], stocks: dict[str, list[dict]], target: str, now: datetime, *,
               replay: bool = False, stock_source_note: str | None = None, stock_history_recovery: list[dict] | None = None) -> tuple[dict[str, bytes], dict]:
    count = len(members)
    if set(stocks) != {m["Symbol"] for m in members} or count != indices["NDXTMC"][-1]["Constituents"]:
        raise DataError("Constituent identity/count differs from downloaded stock universe")
    for ticker, rows in indices.items():
        check_sessions(rows, target, ticker)
    for ticker, rows in stocks.items():
        check_sessions(rows, target, ticker)
    providers = {row["Source"] for rr in stocks.values() for row in rr}
    if len(providers) != 1:
        raise DataError("Stock universe mixes providers; a single vendor is required for all constituents")
    stock_provider = next(iter(providers))
    indicators = {t: indicator_rows(rows) for t, rows in indices.items()}
    latest = [{k: v for k, v in indicators[t][-1].items() if k in ("Date", "Ticker", "Close", "PreviousClose", "return_1d_pct", "return_5d_pct", "return_20d_pct")} |
              {"Source": "Nasdaq HistoryData EOD", "IndexIdentityValidated": True} for t in INDEX_NAMES]
    relative = {"Date": target}
    for ticker in INDEX_NAMES:
        for n in (1, 5, 20):
            relative[f"{ticker}_return_{n}d_pct"] = indicators[ticker][-1][f"return_{n}d_pct"]
    for n in (1, 5, 20):
        difference = relative[f"NDXTMC_return_{n}d_pct"] - relative[f"NDXT_return_{n}d_pct"]
        relative[f"NDXTMC_minus_NDXT_{n}D"] = difference
        relative[f"NDXTMC_minus_NDXT_{n}d_pp"] = difference
    relative["NDXT_role"] = "Equal-weight diffusion proxy; not constituent MA breadth"
    breadth = breadth_metrics(stocks)
    anomaly_rows = anomalies(stocks)
    critical = [f"Unreviewed extreme: {r['Ticker']} {r['Date']}" for r in anomaly_rows if r["Note"].startswith("UNREVIEWED")]
    checks = {
        "LATEST_COMPLETED_SESSION": target == latest_completed_session(now) and not replay,
        "INDEX_SESSION_CONTINUITY": True, "CURRENT_CONSTITUENT_IDENTITIES": True,
        "CURRENT_STOCKS_LATEST": True, "STOCK_UNIVERSE_SINGLE_PROVIDER": True, "NO_DUPLICATE_TICKER_DATE": True,
        "STOCK_OHLC_VALID": True, "FAILED_TICKERS_CLEAR": True,
        "ANOMALY_CHECK_REVIEWED": not critical, "NDXTMC_RSI14": indicators["NDXTMC"][-1]["rsi14_wilder"] is not None,
        "NDXTMC_MA20_40_50_200": all(indicators["NDXTMC"][-1][f"ma{n}"] is not None for n in PERIODS),
        "BREADTH_COVERAGE": True, "RETURNS_1D_5D_20D": True, "ARITHMETIC_CONSISTENCY": True,
        "INDEX_IDENTITY_VALIDATION": True, "NO_CRITICAL_SOURCE_CONFLICT": True,
    }
    checks.update({f"{t}_EOD_VALID": True for t in INDEX_NAMES})
    warnings = ["SOURCE_ADJUSTMENT_UNKNOWN: source prices retained; not suitable for corporate-action-adjusted backtests",
                "SOURCE_RECHECK_IS_SAME_SOURCE: no independent supplier validation in this script",
                "HISTORICAL_POINT_IN_TIME_MEMBERSHIP_UNVERIFIED", "159509_WEIGHT_MAPPING_UNAVAILABLE"]
    if stock_source_note:
        warnings.append("STOCK_SOURCE_FALLBACK: " + stock_source_note)
        warnings.append("YAHOO_PROVIDER_CLOSE: chart quote.close, not adjclose; provider may revise split adjustments; no local adjustment applied")
    for item in stock_history_recovery or []:
        warnings.append(f"YAHOO_HISTORY_RECOVERED: {item['ticker']} used {item['carried_sessions']} earlier sessions from a checksum-verified Yahoo pack; matched live OHLC overlap and uses live rows from {item['first_live_date']}")
    warnings.extend(f"REVIEWED_SNAPSHOT_EXTREME: {r['Ticker']} {r['Date']} {r['ChangePct']:.2f}%" for r in anomaly_rows if not r["Note"].startswith("UNREVIEWED"))
    for n in PERIODS:
        if breadth[f"above_ma{n}_valid_count"] != count:
            warnings.append(f"PARTIAL_MA{n}_COVERAGE: {breadth[f'above_ma{n}_valid_count']}/{count}; denominator uses valid histories")
    if count != 47:
        warnings.append(f"MEMBERSHIP_COUNT_CHANGED: {count}; legacy 47stocks filename retained for compatibility")
    if replay:
        warnings.append("HISTORICAL_REPLAY: archived source responses; not a live fetch")
    if not checks["LATEST_COMPLETED_SESSION"] and not replay:
        critical.append("Target is not latest completed session")
    ready = not critical and all(checks.values())
    status = "DAILY_READY_WITH_WARNINGS" if ready else "HISTORICAL_REPLAY" if replay and not critical else "BLOCKED"
    quality = {"status": status, "daily_ready": ready, "backtest_ready": False, "weight_mapping_ready": False,
               "checks": checks, "critical": critical, "warnings": warnings}
    value = indicators["NDXTMC"][-1]
    run_id = now.astimezone(BJ).strftime("%Y%m%d_%H%M%S_%f")
    generated = now.astimezone(BJ).isoformat()
    summary = {"run_id": run_id, "generated_at": generated, "as_of_date": target, "daily_ready": ready,
               "backtest_ready": False, "weight_mapping_ready": False,
               "source_policy": {"indices": "Nasdaq HistoryData EOD", "membership": "Nasdaq WeightingData EOD", "stocks": stock_provider, "stock_fallback_used": bool(stock_source_note), "stock_fallback_reason": stock_source_note},
               "NDXTMC": {"close": value["Close"], "previous_close": value["PreviousClose"], "daily_return_pct": value["return_1d_pct"],
                           "rsi14_wilder": value["rsi14_wilder"], "ndxtmc_rsi14": value["rsi14_wilder"],
                           **{f"ma{n}": value[f"ma{n}"] for n in PERIODS},
                           **{k: v for k, v in value.items() if k.startswith("drawdown_")},
                           "drawdown_from_stage_high_pct": value["drawdown_from_52w_closing_high_pct"], "stage_high_closing": value["high_52w_closing"]},
               "breadth": breadth, "relative_performance": relative, "quality": quality,
               "membership": {"as_of_date": target, "component_count": count, "components": members, "weights_available": False}}
    if stock_history_recovery:
        summary["source_policy"]["stock_history_recovery"] = stock_history_recovery
    for ticker in ("NDXT", "NDX"):
        item = indicators[ticker][-1]
        summary[ticker] = {"close": item["Close"], **{f"return_{n}d_pct": item[f"return_{n}d_pct"] for n in (1, 5, 20)}}
    stock_rows = sorted((row for rr in stocks.values() for row in rr), key=lambda r: (r["Date"], r["Ticker"]))
    report = [f"# NDXTMC Core Quality Report — {target}", "", f"Generated at: {generated}", f"Status: {status}", f"DAILY_READY: {ready}",
              f"Index source: Nasdaq HistoryData EOD", f"Stock source: {stock_provider}", f"Stock fallback reason: {stock_source_note or 'None'}",
              f"Current constituents: {count}", f"Latest stock price distribution: {dict(Counter(rr[-1]['Date'] for rr in stocks.values()))}",
              "BACKTEST_READY: False", "WEIGHT_MAPPING_READY: False", "", "## Checks"]
    report += [f"- {key}: {'PASS' if ok else 'FAIL'}" for key, ok in checks.items()]
    report += ["", "## Critical"] + ([f"- {message}" for message in critical] or ["- None"])
    report += ["", "## Warnings"] + [f"- {message}" for message in warnings]
    report += ["", "Identity is validated by the requested symbol and the official Nasdaq endpoint, with last-two-session arithmetic and same-source rechecks. This is not independent market-data verification.",
               "Stock adjustment status is unknown. Source values are retained, with no local split adjustment. Breadth uses current constituents, not historical point-in-time members."]
    files = {
        "01_index_latest.csv": csv_bytes(latest), "02_ndxtmc_indicators.csv": csv_bytes(indicators["NDXTMC"]),
        "03_index_relative_performance.csv": csv_bytes([relative]), "04_breadth_latest.csv": csv_bytes([breadth]),
        "05_quality_report.md": ("\n".join(report) + "\n").encode(),
        "07_morning_report_input.json": (json.dumps(summary, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode(),
        STOCK_HISTORY_FILE: csv_bytes(stock_rows),
        "failed_tickers.csv": csv_bytes([], ["Ticker", "Error"]),
        "anomaly_check.csv": csv_bytes(anomaly_rows, ["Ticker", "Date", "PreviousClose", "Close", "ChangePct", "Note"]),
    }
    for ticker in INDEX_NAMES:
        files[f"{ticker}_index_history.csv"] = csv_bytes(indices[ticker])
    manifest = {"run_id": run_id, "generated_at": generated, "as_of_date": target,
                "source": f"Nasdaq official HistoryData EOD and WeightingData; stock history: {stock_provider}",
                "source_policy": summary["source_policy"],
                "source_mode": "HISTORICAL_REPLAY" if replay else "LIVE",
                "index_rows": {t: len(rr) for t, rr in indices.items()}, "component_count": count,
                "target_ticker_count": count, "valid_ticker_count": len(stocks), "stocks_latest_date": target,
                "quality_gate": "PASS" if ready else status,
                "latest_price_date_distribution": dict(Counter(rr[-1]["Date"] for rr in stocks.values())),
                "files": {name: {"size_bytes": len(content), "sha256": hashlib.sha256(content).hexdigest()} for name, content in sorted(files.items())}}
    manifest.update({f"{t.lower()}_latest_date": target for t in INDEX_NAMES})
    files["06_manifest.json"] = (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode()
    return files, summary


def generate(output: Path, fixtures: Path | None = None, as_of: str | None = None, *, previous_handoff: Path | None = None) -> dict:
    now = datetime.now(timezone.utc)
    latest = latest_completed_session(now)
    target = as_of or latest
    if not calendar(now).is_session(target):
        raise DataError(f"Not a Nasdaq trading session: {target}")
    if as_of and not fixtures:
        raise DataError("--as-of is restricted to archived-source replay; live mode always uses the latest completed session")
    raw_dir = output / "sources"
    client = NasdaqClient(raw_dir, fixtures)
    print(f"Target EOD: {target}; New York now: {now.astimezone(NY).isoformat()}", flush=True)
    indices = {t: client.index(t, target) for t in INDEX_NAMES}
    for ticker, rows in indices.items():
        recheck = client.index(ticker, target, recheck=True)
        if [(r["Date"], r["Close"]) for r in recheck[-2:]] != [(r["Date"], r["Close"]) for r in rows[-2:]]:
            raise DataError(f"{ticker}: contradictory repeat EOD response")
    members = client.members(target, indices["NDXTMC"][-1]["Constituents"])
    stocks, failures = {}, []
    stock_client = client
    stock_source_note = None
    # Validate the endpoint once before sending requests for the whole universe.
    first_ticker = members[0]["Symbol"]
    try:
        stocks[first_ticker] = client.stock(first_ticker, target)
        print(f"{first_ticker}: connectivity OK, {len(stocks[first_ticker])} rows", flush=True)
    except Exception as exc:
        if fixtures:
            raise DataError(f"Archived Nasdaq stock response unavailable: {exc}") from exc
        stock_source_note = f"Nasdaq stock endpoint unavailable ({exc}); all constituents use Yahoo Finance chart history"
        print(stock_source_note, flush=True)
        stock_client = YahooClient(raw_dir, previous_handoff=previous_handoff)
        try:
            stocks[first_ticker] = stock_client.stock(first_ticker, target)
            print(f"{first_ticker}: Yahoo connectivity OK, {len(stocks[first_ticker])} rows", flush=True)
        except Exception as fallback_error:
            raise DataError(f"Stock providers unavailable; stopped before requesting the other constituents. Nasdaq: {exc}; Yahoo: {fallback_error}") from fallback_error
    stocks, failures = fetch_stock_batch(stock_client, members, target, stocks)
    if failures and stock_client is client and not fixtures:
        write_json(output / "nasdaq_failed_tickers.json", failures)
        write_json(raw_dir / "nasdaq_provider_failures.json", failures)
        stock_source_note = f"Nasdaq failed for {len(failures)}/{len(members)} constituents; all constituents refetched using Yahoo Finance chart history"
        print(stock_source_note, flush=True)
        # Discard all primary stock rows; never combine vendors for breadth.
        stock_client = YahooClient(raw_dir, previous_handoff=previous_handoff)
        stocks, failures = fetch_stock_batch(stock_client, members, target)
    if failures:
        write_json(output / "failed_tickers.json", failures)
        raise DataError(f"{len(failures)}/{len(members)} stock sources failed; no current pack will be published: " + "; ".join(f"{row['Ticker']}: {row['Error']}" for row in failures[:8]))
    recovered = []
    if isinstance(stock_client, YahooClient):
        recovered = [stock_client.history_recoveries[ticker] for ticker in sorted(stock_client.history_recoveries)]
    files, summary = build_pack(indices, members, stocks, target, now, replay=bool(fixtures),
                                stock_source_note=stock_source_note, stock_history_recovery=recovered)
    if summary["quality"]["critical"]:
        write_json(output / "blocked_quality.json", summary["quality"])
        raise DataError("Quality gate blocked: " + "; ".join(summary["quality"]["critical"]))
    pack = output / ("HISTORICAL_REPLAY_Data_Pack.zip" if fixtures else "LATEST_Morning_Report_Data_Pack.zip")
    with zipfile.ZipFile(pack, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in sorted(files.items()):
            archive.writestr(name, content)
    # A second, independent reader validates calculations and the manifest.
    try:
        from .validate_pack import audit
    except ImportError:
        from validate_pack import audit
    validation = audit(pack, require_daily_ready=not fixtures)
    write_json(output / "validation.json", validation)
    if validation["errors"]:
        pack.unlink()
        raise DataError("Independent pack validation failed: " + "; ".join(validation["errors"]))
    with zipfile.ZipFile(output / "Nasdaq_Source_Audit.zip", "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for source in sorted(raw_dir.glob("*.json")):
            archive.write(source, "sources/" + source.name)
        archive.write(output / "validation.json", "validation.json")
    sha = hashlib.sha256(pack.read_bytes()).hexdigest()
    (output / (pack.name + ".sha256")).write_text(f"{sha}  {pack.name}\n")
    write_json(output / "summary.json", summary)
    release = [f"# 纳指晨报数据包 · {target}", "", f"北京时间生成：{now.astimezone(BJ).isoformat()}",
               f"美股交易日：**{target}**", f"状态：**{summary['quality']['status']}**", f"成分股：**{len(members)}/{len(members)}**", "",
               f"指数与名单来源：Nasdaq 官方；**个股来源：{summary['source_policy']['stocks']}**。", "",
               f"下载 **{pack.name}**，可直接上传给 ChatGPT 生成晨报。", "", "| 指数 | 收盘 | 当日涨跌 |", "|---|---:|---:|"]
    for ticker in INDEX_NAMES:
        change = summary[ticker].get("daily_return_pct", summary[ticker].get("return_1d_pct"))
        release.append(f"| {ticker} | {summary[ticker]['close']:.4f} | {change:+.4f}% |")
    release += ["", f"NDXTMC RSI14：{summary['NDXTMC']['rsi14_wilder']:.4f}",
                f"MA40 广度：{summary['breadth']['above_ma40_count']}/{summary['breadth']['above_ma40_valid_count']}", "", "注意：", *[f"- {w}" for w in summary["quality"]["warnings"]],
                "", "历史成分回测与 159509 权重映射未完成；源端复权状态未知。", "", f"ZIP SHA256：`{sha}`"]
    (output / "release_notes.md").write_text("\n".join(release) + "\n", encoding="utf-8")
    print(json.dumps({"pack": str(pack), "as_of_date": target, "daily_ready": summary["daily_ready"], "constituents": len(members), "sha256": sha}), flush=True)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("output"))
    parser.add_argument("--fixtures", type=Path, help="Replay archived official JSON responses; never labelled live")
    parser.add_argument("--as-of", help="Historical replay date only, YYYY-MM-DD")
    parser.add_argument("--previous-handoff", type=Path, default=Path("data/latest_morning_input.json"),
                        help="Previous validated handoff for recovering truncated same-provider Yahoo history")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    # Remove only outputs of this script, so a failed rerun cannot publish an old ZIP.
    for name in ("LATEST_Morning_Report_Data_Pack.zip", "HISTORICAL_REPLAY_Data_Pack.zip", "Nasdaq_Source_Audit.zip", "summary.json", "release_notes.md", "validation.json", "failure.json", "failed_tickers.json", "nasdaq_failed_tickers.json", "blocked_quality.json", "LATEST_Morning_Report_Data_Pack.zip.sha256"):
        (args.output / name).unlink(missing_ok=True)
    for old in (args.output / "sources").glob("*.json"):
        old.unlink()
    try:
        generate(args.output, args.fixtures, args.as_of, previous_handoff=args.previous_handoff)
        return 0
    except Exception as exc:
        failure = {"status": "BLOCKED", "daily_ready": False, "generated_at": datetime.now(BJ).isoformat(), "error": str(exc), "type": type(exc).__name__}
        write_json(args.output / "failure.json", failure)
        print(json.dumps(failure, ensure_ascii=False), file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
