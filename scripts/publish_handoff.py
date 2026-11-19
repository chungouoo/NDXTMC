"""Publish one atomic, validated JSON input for the existing morning report."""
from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import io
import json
import math
import os
import re
import subprocess
import zipfile
from datetime import datetime
from pathlib import Path
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from .validate_pack import audit

HANDOFF_PATH = "data/latest_morning_input.json"
PACK_NAME = "LATEST_Morning_Report_Data_Pack.zip"
BEIJING = ZoneInfo("Asia/Shanghai")


def request_for_run(run: dict) -> dict:
    created = datetime.fromisoformat(run["created_at"])
    if created.tzinfo is None:
        raise ValueError("GitHub run timestamp is missing its timezone")
    requested = created.astimezone(BEIJING)
    day = requested.date().isoformat()
    event = run["event"]
    if event == "schedule":
        request_id = f"ndxtmc-daily-{day}"
    else:
        request_id = f"ndxtmc-{event}-{day}-{run['id']}"
    return {"schema_version": 1, "request_id": request_id, "requested_at": requested.isoformat(),
            "timezone": BEIJING.key, "reason": event}


def validate_inputs(summary: dict, manifest: dict, validation: dict) -> None:
    quality = summary.get("quality") or {}
    if summary.get("daily_ready") is not True or quality.get("daily_ready") is not True:
        raise ValueError("Handoff blocked: DAILY_READY must be true")
    if quality.get("critical") or not quality.get("checks") or not all(quality["checks"].values()):
        raise ValueError("Handoff blocked: failed or missing quality checks")
    if manifest.get("source_mode") != "LIVE" or manifest.get("quality_gate") != "PASS":
        raise ValueError("Handoff blocked: historical replay or failed manifest gate")
    target = summary.get("as_of_date")
    if not target or target != manifest.get("as_of_date") or target != validation.get("as_of_date"):
        raise ValueError("Handoff blocked: inconsistent trade dates")
    if validation.get("errors") != [] or validation.get("result") not in ("PASS", "PASS_WITH_SOURCE_CAVEAT"):
        raise ValueError("Handoff blocked: independent ZIP audit did not pass")
    count = manifest.get("component_count")
    if not isinstance(count, int) or count <= 0 or any(n != count for n in (
        manifest.get("target_ticker_count"), manifest.get("valid_ticker_count"),
        summary.get("breadth", {}).get("component_count"), validation.get("component_count"),
    )):
        raise ValueError("Handoff blocked: constituent coverage mismatch")
    if validation.get("verified_manifest_files") != len(manifest.get("files", {})) or len(manifest.get("files", {})) != 12:
        raise ValueError("Handoff blocked: manifest verification incomplete")
    if not summary.get("source_policy", {}).get("stocks"):
        raise ValueError("Handoff blocked: stock provider not disclosed")


def build_handoff(pack: Path, trigger: dict, context: dict, release: dict) -> dict:
    validation = audit(pack)
    with zipfile.ZipFile(pack) as archive:
        summary = json.loads(archive.read("07_morning_report_input.json"))
        manifest = json.loads(archive.read("06_manifest.json"))
        quality_report = archive.read("05_quality_report.md").decode("utf-8-sig")
        validate_inputs(summary, manifest, validation)
        history = {}
        for ticker in ("NDXTMC", "NDXT", "NDX"):
            rows = list(csv.DictReader(io.StringIO(archive.read(ticker + "_index_history.csv").decode("utf-8-sig"))))
            dates = [row["Date"] for row in rows]
            closes = [float(row["Close"]) for row in rows]
            if dates != sorted(set(dates)) or dates[-1] != summary["as_of_date"] or not all(math.isfinite(v) and v > 0 for v in closes):
                raise ValueError("Handoff blocked: invalid index history")
            history[ticker] = {"dates": dates, "closes": closes, "source": "Nasdaq HistoryData EOD"}
    if not isinstance(trigger.get("request_id"), str) or not trigger["request_id"]:
        raise ValueError("Handoff blocked: missing trigger request id")
    requested = datetime.fromisoformat(trigger["requested_at"])
    if requested.tzinfo is None or trigger.get("timezone") != "Asia/Shanghai":
        raise ValueError("Handoff blocked: trigger timezone missing")
    expected_tag = f"data-{summary['as_of_date']}-{context['run_id']}-{context['run_attempt']}"
    if release.get("tag_name") != expected_tag or release.get("draft") or not release.get("published_at"):
        raise ValueError("Handoff blocked: release does not match this workflow run")
    assets = {a["name"]: a for a in release.get("assets", [])}
    if PACK_NAME not in assets or PACK_NAME + ".sha256" not in assets:
        raise ValueError("Handoff blocked: release ZIP/checksum missing")
    if assets[PACK_NAME].get("size") != pack.stat().st_size:
        raise ValueError("Handoff blocked: release ZIP size mismatch")
    sha = hashlib.sha256(pack.read_bytes()).hexdigest()
    digest = assets[PACK_NAME].get("digest")
    if digest and digest != "sha256:" + sha:
        raise ValueError("Handoff blocked: release ZIP digest mismatch")
    return {
        "schema_version": 1, "status": "READY",
        "handoff_generated_at": datetime.now(BEIJING).isoformat(),
        "request_id": trigger["request_id"], "requested_at": trigger["requested_at"],
        "beijing_request_date": requested.astimezone(BEIJING).date().isoformat(),
        "pipeline": {
            "repository": context["repository"], "source_commit_sha": context["source_commit_sha"],
            "event": context["event"],
            "run_id": str(context["run_id"]), "run_attempt": str(context["run_attempt"]),
            "run_url": f"https://github.com/{context['repository']}/actions/runs/{context['run_id']}",
            "release_tag": expected_tag, "release_url": release["html_url"],
            "release_published_at": release["published_at"],
            "zip_url": assets[PACK_NAME]["browser_download_url"],
            "zip_sha256": sha,
        },
        "data": summary, "validation": validation, "manifest": manifest,
        "quality_report_markdown": quality_report,
        # Full official series let the report independently recalculate RSI/MAs
        # in its code tool without needing a desktop or binary ZIP download.
        "index_history": history,
    }


def gh_api(path: str, *, body: dict | None = None, allow_missing: bool = False) -> dict | list | None:
    command = ["gh", "api", path]
    if body is not None:
        command += ["--method", "PUT", "--input", "-"]
    result = subprocess.run(command, input=json.dumps(body) if body is not None else None, capture_output=True, text=True)
    if result.returncode:
        if allow_missing and "HTTP 404" in result.stderr:
            return None
        raise RuntimeError("GitHub handoff API failed: " + result.stderr.strip())
    return json.loads(result.stdout)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("output"))
    parser.add_argument("--trigger", type=Path, default=Path(".github/ndxtmc-trigger.json"))
    args = parser.parse_args()
    context = {"repository": os.environ["GH_REPO"], "source_commit_sha": os.environ["SOURCE_SHA"],
               "run_id": os.environ["RUN_ID"], "run_attempt": os.environ["RUN_ATTEMPT"],
               "event": os.environ["GITHUB_EVENT_NAME"]}
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", context["repository"]):
        raise ValueError("Invalid repository name")
    repo_path = f"repos/{context['repository']}"
    run = gh_api(f"{repo_path}/actions/runs/{context['run_id']}")
    if str(run["id"]) != context["run_id"] or run["head_sha"] != context["source_commit_sha"] or run["event"] != context["event"]:
        raise ValueError("GitHub run does not match this workflow")
    trigger = request_for_run(run)
    if context["event"] == "push" and args.trigger.is_file():
        query = urlencode({"path": args.trigger.as_posix(), "sha": context["source_commit_sha"], "per_page": 1})
        changes = gh_api(f"{repo_path}/commits?{query}")
        if changes and changes[0]["sha"] == context["source_commit_sha"]:
            trigger = json.loads(args.trigger.read_text(encoding="utf-8"))
    summary = json.loads((args.output / "summary.json").read_text())
    tag = f"data-{summary['as_of_date']}-{context['run_id']}-{context['run_attempt']}"
    release = gh_api(f"{repo_path}/releases/tags/{tag}")
    snapshot = build_handoff(args.output / PACK_NAME, trigger, context, release)
    encoded = base64.b64encode(json.dumps(snapshot, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()).decode()
    path = f"{repo_path}/contents/{HANDOFF_PATH}"
    previous = gh_api(path + "?ref=main", allow_missing=True)
    request = {"message": f"Publish validated morning input {snapshot['data']['as_of_date']} (run {context['run_id']})",
               "content": encoded, "branch": "main"}
    if previous:
        request["sha"] = previous["sha"]
    # One contents write atomically updates the ready flag, provenance, summary,
    # validation, and full index histories. This path cannot retrigger generation.
    result = gh_api(path, body=request)
    print(json.dumps({"status": snapshot["status"], "path": HANDOFF_PATH,
                      "trade_date": snapshot["data"]["as_of_date"], "run_id": context["run_id"],
                      "handoff_commit": result["commit"]["sha"]}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
