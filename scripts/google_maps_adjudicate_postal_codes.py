#!/usr/bin/env python3
"""Google Maps Geocoding adjudication and QA for BC postal-code reconstruction.

This workflow is intentionally separate from the free/open reconstruction outputs.
Google-derived coordinates are license-restricted QA evidence and must not be
mixed into `bc_postal_code_reconstructed_free.csv`.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import json
import os
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import certifi

from postal_source_utils import (
    ROOT,
    ensure_dir,
    format_number,
    haversine_km,
    read_csv,
    to_float,
    utc_now_iso,
    valid_bc_coordinate,
    write_csv,
    write_json,
)


GOOGLE_GEOCODE_URL = "https://maps.googleapis.com/maps/api/geocode/json"
GOOGLE_GEOCODE_V4_URL = "https://geocode.googleapis.com/v4/geocode/address"
DEFAULT_BILLING_ACCOUNT = "billingAccounts/012A43-7018A5-22E217"
DEFAULT_LEDGER = ROOT / "work/google_maps_geocoding/google_maps_geocoding_ledger.csv"
DEFAULT_COMPARISON = ROOT / "outputs/geolocation/bc_postal_code_source_comparison.csv"
DEFAULT_OUTPUTS_DIR = ROOT / "outputs/geolocation"
DEFAULT_REPORTS_DIR = ROOT / "reports/geolocation"
DEFAULT_WORK_ROOT = ROOT / "work/google_maps_geocoding"
DEFAULT_TIMEZONE = "America/Vancouver"

RISKY_CLASSES = {"missing_from_seed", "major", "severe"}
STABLE_QA_CLASSES = {"agree", "minor"}
CLASS_PRIORITY = {"severe": 0, "major": 1, "missing_from_seed": 2, "minor": 3, "agree": 4}

TARGET_FIELDS = [
    "target_order",
    "target_bucket",
    "postal_code",
    "comparison_class",
    "disagreement_class",
    "max_disagreement_km",
    "selected_source",
    "selected_latitude",
    "selected_longitude",
    "sources",
    "source_coordinates",
    "sample_hash",
    "request_address",
]

ADJUDICATION_FIELDS = [
    "target_order",
    "target_bucket",
    "postal_code",
    "comparison_class",
    "selected_source",
    "selected_latitude",
    "selected_longitude",
    "google_status",
    "google_result_count",
    "google_latitude",
    "google_longitude",
    "google_location_type",
    "google_formatted_address",
    "google_types",
    "google_partial_match",
    "google_valid_bc_coordinate",
    "distance_to_selected_km",
    "nearest_source",
    "distance_to_nearest_source_km",
    "adjudication_class",
    "license_status",
    "cache_expires_at",
    "raw_response_path",
    "ledger_request_id",
    "error_message",
]

SUMMARY_FIELDS = [
    "metric",
    "value",
]

LEDGER_FIELDS = [
    "event_at",
    "calendar_month",
    "run_id",
    "request_id",
    "postal_code",
    "target_bucket",
    "request_address",
    "status",
    "cache_hit",
    "billable_event_count",
    "http_status",
    "google_status",
    "raw_response_path",
    "error_message",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--comparison", default=str(DEFAULT_COMPARISON))
    parser.add_argument("--outputs-dir", default=str(DEFAULT_OUTPUTS_DIR))
    parser.add_argument("--reports-dir", default=str(DEFAULT_REPORTS_DIR))
    parser.add_argument("--work-root", default=str(DEFAULT_WORK_ROOT))
    parser.add_argument("--ledger", default=str(DEFAULT_LEDGER))
    parser.add_argument("--run-id", default="")
    parser.add_argument("--timezone", default=DEFAULT_TIMEZONE)
    parser.add_argument("--billing-account", default=DEFAULT_BILLING_ACCOUNT)
    parser.add_argument("--gcp-project", default="")
    parser.add_argument(
        "--gcp-preflight-json",
        default="",
        help="Read-only GCP/MCP preflight evidence JSON. If provided, it is used before local gcloud checks.",
    )
    parser.add_argument("--api-key-env", default="GOOGLE_MAPS_API_KEY")
    parser.add_argument("--fallback-api-key-env", default="GOOGLE_API_KEY")
    parser.add_argument(
        "--auth-method",
        choices=["api_key_v3", "oauth_v4"],
        default="api_key_v3",
        help="Google Geocoding auth/API path. oauth_v4 uses GOOGLE_OAUTH_ACCESS_TOKEN and --gcp-project as quota project.",
    )
    parser.add_argument("--oauth-token-env", default="GOOGLE_OAUTH_ACCESS_TOKEN")
    parser.add_argument("--hard-monthly-cap", type=int, default=9000)
    parser.add_argument("--stable-qa-limit", type=int, default=1000)
    parser.add_argument("--max-requests", type=int, default=0)
    parser.add_argument("--delay-seconds", type=float, default=0.05)
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument("--execute", action="store_true", help="Make Google Maps Geocoding calls.")
    parser.add_argument(
        "--rerun-ledger-successes",
        action="store_true",
        help="Spend calls again for request IDs already logged as executed/http_error this month.",
    )
    parser.add_argument(
        "--allow-unverified-gcp-preflight",
        action="store_true",
        help="Allow execution when local gcloud service preflight cannot verify Geocoding API enablement.",
    )
    parser.add_argument(
        "--preflight-note",
        default="",
        help="Human-readable read-only GCP/MCP preflight note to include in the report.",
    )
    return parser.parse_args()


def default_run_id() -> str:
    return dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%SZ")


def local_month(timezone: str) -> str:
    return dt.datetime.now(ZoneInfo(timezone)).strftime("%Y-%m")


def sample_hash(postal_code: str, seed: str = "bc-google-stable-qa-v1") -> str:
    return hashlib.sha256(f"{seed}|{postal_code}".encode("utf-8")).hexdigest()


def request_id(postal_code: str, request_address: str) -> str:
    digest = hashlib.sha256(f"google-geocode-v1|{postal_code}|{request_address}".encode("utf-8"))
    return digest.hexdigest()[:24]


def request_address(postal_code: str) -> str:
    return f"{postal_code}, British Columbia, Canada"


def distance_sort_value(row: dict[str, str]) -> float:
    value = to_float(row.get("max_disagreement_km"))
    return value if value is not None else -1.0


def select_targets(
    comparison_rows: list[dict[str, str]],
    stable_qa_limit: int = 1000,
) -> list[dict[str, str]]:
    risky = [
        dict(row, target_bucket="risky", sample_hash="")
        for row in comparison_rows
        if row.get("comparison_class") in RISKY_CLASSES
    ]
    risky.sort(
        key=lambda row: (
            CLASS_PRIORITY.get(row.get("comparison_class", ""), 99),
            -distance_sort_value(row),
            row.get("postal_code", ""),
        )
    )

    stable = [
        dict(row, target_bucket="stable_qa", sample_hash=sample_hash(row.get("postal_code", "")))
        for row in comparison_rows
        if row.get("comparison_class") in STABLE_QA_CLASSES
    ]
    stable.sort(key=lambda row: (row["sample_hash"], row.get("postal_code", "")))
    selected = risky + stable[: max(stable_qa_limit, 0)]
    for index, row in enumerate(selected, start=1):
        row["target_order"] = str(index)
        row["request_address"] = request_address(row.get("postal_code", ""))
    return selected


def read_ledger(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    return read_csv(path)


def monthly_billable_count(ledger_rows: list[dict[str, str]], month: str) -> int:
    total = 0
    for row in ledger_rows:
        if row.get("calendar_month") != month:
            continue
        try:
            total += int(float(row.get("billable_event_count") or 0))
        except ValueError:
            continue
    return total


def completed_request_ids(ledger_rows: list[dict[str, str]], month: str) -> set[str]:
    return {
        row.get("request_id", "")
        for row in ledger_rows
        if row.get("calendar_month") == month
        and row.get("request_id")
        and row.get("status") == "executed"
        and row.get("google_status") in {"OK", "ZERO_RESULTS"}
    }


def completed_ledger_by_request_id(
    ledger_rows: list[dict[str, str]],
    month: str,
) -> dict[str, dict[str, str]]:
    completed: dict[str, dict[str, str]] = {}
    for row in ledger_rows:
        request_id_value = row.get("request_id", "")
        if (
            row.get("calendar_month") == month
            and request_id_value
            and row.get("status") == "executed"
            and row.get("google_status") in {"OK", "ZERO_RESULTS"}
        ):
            completed[request_id_value] = row
    return completed


def append_ledger(path: Path, row: dict[str, Any]) -> None:
    ensure_dir(path.parent)
    exists = path.exists()
    with path.open("a", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=LEDGER_FIELDS, extrasaction="ignore")
        if not exists:
            writer.writeheader()
        writer.writerow(row)


def check_monthly_cap(
    ledger_rows: list[dict[str, str]],
    month: str,
    hard_cap: int,
    planned_new_calls: int,
) -> tuple[bool, int, int]:
    used = monthly_billable_count(ledger_rows, month)
    remaining = max(hard_cap - used, 0)
    return planned_new_calls <= remaining, used, remaining


def enabled_geocoding_services(project: str) -> tuple[bool, str]:
    if not project:
        return False, "No --gcp-project provided for local service preflight."
    service_names = {
        "geocoding-backend.googleapis.com",
        "geocoding.googleapis.com",
        "maps-backend.googleapis.com",
    }
    cmd = [
        "gcloud",
        "services",
        "list",
        "--enabled",
        f"--project={project}",
        "--format=json(config.name)",
    ]
    try:
        completed = subprocess.run(cmd, check=False, capture_output=True, text=True, timeout=60)
    except FileNotFoundError:
        return False, "gcloud is not installed or not on PATH."
    except subprocess.TimeoutExpired:
        return False, "gcloud service preflight timed out."
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip().replace("\n", " ")
        return False, f"gcloud service preflight failed: {detail}"
    try:
        services = json.loads(completed.stdout or "[]")
    except json.JSONDecodeError:
        return False, "gcloud service preflight returned non-JSON output."
    enabled = sorted(
        item.get("config", {}).get("name", "")
        for item in services
        if item.get("config", {}).get("name", "") in service_names
    )
    if enabled:
        return True, f"Enabled Geocoding/Maps service(s): {', '.join(enabled)}."
    return False, "No Geocoding/Maps API service was enabled in local gcloud service preflight."


def preflight_from_json(path: Path, billing_account: str, project: str) -> tuple[bool, str]:
    try:
        payload = json.loads(path.read_text())
    except FileNotFoundError:
        return False, f"GCP preflight JSON not found: {path}"
    except json.JSONDecodeError as exc:
        return False, f"GCP preflight JSON is invalid: {exc}"

    expected_billing = payload.get("billing_account")
    if expected_billing and expected_billing != billing_account:
        return False, f"GCP preflight billing account mismatch: {expected_billing} != {billing_account}"
    expected_project = payload.get("execution_project") or payload.get("gcp_project")
    if expected_project and project and expected_project != project:
        return False, f"GCP preflight project mismatch: {expected_project} != {project}"

    enabled = bool(payload.get("geocoding_api_enabled"))
    message = payload.get("message") or payload.get("summary") or "GCP preflight JSON loaded."
    return enabled, message


def resolve_gcp_preflight(args: argparse.Namespace) -> tuple[bool, str]:
    if args.gcp_preflight_json:
        return preflight_from_json(Path(args.gcp_preflight_json), args.billing_account, args.gcp_project)
    return enabled_geocoding_services(args.gcp_project)


def google_geocode(
    args: argparse.Namespace,
    api_key: str,
    oauth_token: str,
    address: str,
    timeout: int,
) -> tuple[int, dict[str, Any]]:
    if args.auth_method == "oauth_v4":
        if not oauth_token:
            raise RuntimeError(f"${args.oauth_token_env} is required for --auth-method oauth_v4")
        if not args.gcp_project:
            raise RuntimeError("--gcp-project is required for --auth-method oauth_v4")
        query = urllib.parse.urlencode(
            {
                "addressQuery": address,
                "regionCode": "CA",
                "languageCode": "en",
            }
        )
        request = urllib.request.Request(
            f"{GOOGLE_GEOCODE_V4_URL}?{query}",
            headers={
                "Authorization": f"Bearer {oauth_token}",
                "User-Agent": "bc-postal-code-google-qa/1.0",
                "X-Goog-FieldMask": "results.formattedAddress,results.location,results.granularity,results.types",
                "X-Goog-User-Project": args.gcp_project,
            },
        )
    else:
        query = urllib.parse.urlencode(
            {
                "address": address,
                "components": "country:CA",
                "region": "ca",
                "key": api_key,
            }
        )
        request = urllib.request.Request(
            f"{GOOGLE_GEOCODE_URL}?{query}",
            headers={"User-Agent": "bc-postal-code-google-qa/1.0"},
        )
    context = ssl.create_default_context(cafile=certifi.where())
    with urllib.request.urlopen(request, timeout=timeout, context=context) as response:
        payload = json.loads(response.read().decode("utf-8"))
        return int(response.status), payload


def first_google_result(payload: dict[str, Any]) -> dict[str, Any]:
    results = payload.get("results") or []
    if not results:
        return {}
    return results[0] if isinstance(results[0], dict) else {}


def parse_google_result(payload: dict[str, Any]) -> dict[str, Any]:
    result = first_google_result(payload)
    geometry = result.get("geometry") or {}
    location = geometry.get("location") or result.get("location") or {}
    lat = to_float(location.get("lat") if "lat" in location else location.get("latitude"))
    lon = to_float(location.get("lng") if "lng" in location else location.get("longitude"))
    result_count = len(payload.get("results") or [])
    status = payload.get("status", "")
    if not status:
        status = "OK" if result_count else "ZERO_RESULTS"
    return {
        "google_status": status,
        "google_result_count": str(result_count),
        "google_latitude": format_number(lat),
        "google_longitude": format_number(lon),
        "google_location_type": geometry.get("location_type", "") or result.get("granularity", ""),
        "google_formatted_address": result.get("formatted_address", "") or result.get("formattedAddress", ""),
        "google_types": ";".join(result.get("types") or []),
        "google_partial_match": str(bool(result.get("partial_match", False))),
        "google_valid_bc_coordinate": str(valid_bc_coordinate(lat, lon)),
    }


def parse_source_coordinates(value: str) -> list[tuple[str, float, float]]:
    parsed = []
    for item in (value or "").split(";"):
        if not item or ":" not in item or "," not in item:
            continue
        source, rest = item.split(":", 1)
        lat_text, lon_text = rest.split(",", 1)
        lat = to_float(lat_text)
        lon = to_float(lon_text)
        if lat is None or lon is None:
            continue
        parsed.append((source, lat, lon))
    return parsed


def nearest_source_coordinate(
    google_lat: Any,
    google_lon: Any,
    source_coordinates: str,
) -> tuple[str, float | None]:
    lat = to_float(google_lat)
    lon = to_float(google_lon)
    if lat is None or lon is None:
        return "", None
    best_source = ""
    best_distance = None
    for source, source_lat, source_lon in parse_source_coordinates(source_coordinates):
        distance = haversine_km(lat, lon, source_lat, source_lon)
        if distance is None:
            continue
        if best_distance is None or distance < best_distance:
            best_source = source
            best_distance = distance
    return best_source, best_distance


def classify_google_adjudication(
    google_status: str,
    google_valid_bc: str,
    distance_to_selected: float | None,
    distance_to_nearest_source: float | None,
) -> str:
    if google_status != "OK":
        return "no_google_coordinate"
    if google_valid_bc != "True":
        return "google_outside_bc_bounds"
    if distance_to_selected is not None and distance_to_selected <= 0.25:
        return "google_confirms_selected"
    if distance_to_selected is not None and distance_to_selected <= 1.0:
        return "google_near_selected"
    if distance_to_nearest_source is not None and distance_to_nearest_source <= 0.25:
        return "google_supports_other_source"
    if distance_to_nearest_source is not None and distance_to_nearest_source <= 1.0:
        return "google_near_other_source"
    return "google_disagrees_with_free_sources"


def build_adjudication_row(
    target: dict[str, str],
    parsed: dict[str, Any],
    raw_path: Path,
    request_id_value: str,
    cache_expires_at: str,
    error_message: str = "",
) -> dict[str, Any]:
    google_lat = to_float(parsed.get("google_latitude"))
    google_lon = to_float(parsed.get("google_longitude"))
    selected_lat = to_float(target.get("selected_latitude"))
    selected_lon = to_float(target.get("selected_longitude"))
    distance_to_selected = haversine_km(google_lat, google_lon, selected_lat, selected_lon)
    nearest_source, distance_to_nearest = nearest_source_coordinate(
        google_lat,
        google_lon,
        target.get("source_coordinates", ""),
    )
    adjudication_class = classify_google_adjudication(
        str(parsed.get("google_status", "")),
        str(parsed.get("google_valid_bc_coordinate", "")),
        distance_to_selected,
        distance_to_nearest,
    )
    return {
        "target_order": target.get("target_order", ""),
        "target_bucket": target.get("target_bucket", ""),
        "postal_code": target.get("postal_code", ""),
        "comparison_class": target.get("comparison_class", ""),
        "selected_source": target.get("selected_source", ""),
        "selected_latitude": target.get("selected_latitude", ""),
        "selected_longitude": target.get("selected_longitude", ""),
        "google_status": parsed.get("google_status", ""),
        "google_result_count": parsed.get("google_result_count", ""),
        "google_latitude": parsed.get("google_latitude", ""),
        "google_longitude": parsed.get("google_longitude", ""),
        "google_location_type": parsed.get("google_location_type", ""),
        "google_formatted_address": parsed.get("google_formatted_address", ""),
        "google_types": parsed.get("google_types", ""),
        "google_partial_match": parsed.get("google_partial_match", ""),
        "google_valid_bc_coordinate": parsed.get("google_valid_bc_coordinate", ""),
        "distance_to_selected_km": "" if distance_to_selected is None else f"{distance_to_selected:.3f}",
        "nearest_source": nearest_source,
        "distance_to_nearest_source_km": "" if distance_to_nearest is None else f"{distance_to_nearest:.3f}",
        "adjudication_class": adjudication_class,
        "license_status": "google_license_restricted_temporary_cache",
        "cache_expires_at": cache_expires_at,
        "raw_response_path": str(raw_path) if str(raw_path) else "",
        "ledger_request_id": request_id_value,
        "error_message": error_message,
    }


def cache_expiry_iso(timezone: str) -> str:
    now = dt.datetime.now(ZoneInfo(timezone)).replace(microsecond=0)
    return (now + dt.timedelta(days=30)).isoformat()


def write_target_plan(path: Path, targets: list[dict[str, str]]) -> None:
    rows = []
    for target in targets:
        rows.append({field: target.get(field, "") for field in TARGET_FIELDS})
    write_csv(path, rows, TARGET_FIELDS)


def build_summary_rows(
    targets: list[dict[str, str]],
    adjudication_rows: list[dict[str, Any]],
    ledger_used: int,
    ledger_remaining_before: int,
    hard_cap: int,
    execute: bool,
    gcp_preflight_ok: bool,
    gcp_preflight_message: str,
    execute_pending_targets: int = 0,
    ledger_skipped_targets: int = 0,
) -> list[dict[str, Any]]:
    target_counts = Counter(row["target_bucket"] for row in targets)
    comparison_counts = Counter(row["comparison_class"] for row in targets)
    adjudication_counts = Counter(row.get("adjudication_class", "") for row in adjudication_rows)
    rows = [
        {"metric": "execute", "value": str(execute)},
        {"metric": "target_total", "value": str(len(targets))},
        {"metric": "target_risky", "value": str(target_counts.get("risky", 0))},
        {"metric": "target_stable_qa", "value": str(target_counts.get("stable_qa", 0))},
        {"metric": "target_severe", "value": str(comparison_counts.get("severe", 0))},
        {"metric": "target_major", "value": str(comparison_counts.get("major", 0))},
        {"metric": "target_missing_from_seed", "value": str(comparison_counts.get("missing_from_seed", 0))},
        {"metric": "target_agree", "value": str(comparison_counts.get("agree", 0))},
        {"metric": "target_minor", "value": str(comparison_counts.get("minor", 0))},
        {"metric": "adjudicated_rows", "value": str(len(adjudication_rows))},
        {"metric": "execute_pending_targets", "value": str(execute_pending_targets)},
        {"metric": "ledger_skipped_targets", "value": str(ledger_skipped_targets)},
        {"metric": "ledger_used_before_run", "value": str(ledger_used)},
        {"metric": "ledger_remaining_before_run", "value": str(ledger_remaining_before)},
        {"metric": "hard_monthly_cap", "value": str(hard_cap)},
        {"metric": "gcp_preflight_ok", "value": str(gcp_preflight_ok)},
        {"metric": "gcp_preflight_message", "value": gcp_preflight_message},
    ]
    for name, count in sorted(adjudication_counts.items()):
        rows.append({"metric": f"adjudication_class_{name}", "value": str(count)})
    return rows


def markdown_report(
    run_id: str,
    args: argparse.Namespace,
    target_path: Path,
    adjudication_path: Path,
    summary_path: Path,
    targets: list[dict[str, str]],
    adjudication_rows: list[dict[str, Any]],
    summary_rows: list[dict[str, Any]],
    month: str,
) -> str:
    target_counts = Counter(row["target_bucket"] for row in targets)
    comparison_counts = Counter(row["comparison_class"] for row in targets)
    adjudication_counts = Counter(row.get("adjudication_class", "") for row in adjudication_rows)
    top_risky = [
        row
        for row in targets
        if row.get("target_bucket") == "risky"
    ][:25]
    top_adjudicated = sorted(
        [
            row for row in adjudication_rows
            if row.get("distance_to_selected_km")
        ],
        key=lambda row: float(row["distance_to_selected_km"]),
        reverse=True,
    )[:25]

    lines = [
        "# Google Maps BC Postal-Code Adjudication Report",
        "",
        f"Run ID: `{run_id}`",
        f"Generated at: {utc_now_iso()}",
        f"Calendar month ledger bucket: `{month}`",
        "",
        "## Guardrails",
        "",
        f"- Hard Google Maps Geocoding cap: {args.hard_monthly_cap:,} billable requests per calendar month.",
        f"- Stable QA sample cap: {args.stable_qa_limit:,}.",
        "- Risky rows are selected before stable QA rows.",
        "- This workflow does not modify `outputs/geolocation/bc_postal_code_reconstructed_free.csv`.",
        "- Google-derived coordinates are license-restricted QA evidence, not free/open reconstruction data.",
        "- Google Maps service terms include cache/use restrictions for Geocoding coordinates; this report marks raw/output evidence with a 30-day cache expiry.",
        "",
        "## GCP Preflight",
        "",
        f"- Billing account: `{args.billing_account}`",
        f"- Project: `{args.gcp_project or '(not provided)'}`",
        f"- Auth method: `{args.auth_method}`",
        f"- Preflight JSON: `{args.gcp_preflight_json or '(not supplied)'}`",
        f"- Execute mode: `{args.execute}`",
        f"- Note: {args.preflight_note or '(none supplied)'}",
        "",
        "## Outputs",
        "",
        f"- Target plan: `{target_path}`",
        f"- Google adjudication CSV: `{adjudication_path}`",
        f"- Summary CSV: `{summary_path}`",
        "",
        "## Target Counts",
        "",
        f"- Total targets: {len(targets):,}",
        f"- Risky targets: {target_counts.get('risky', 0):,}",
        f"- Stable QA targets: {target_counts.get('stable_qa', 0):,}",
    ]
    for name in ["severe", "major", "missing_from_seed", "agree", "minor"]:
        lines.append(f"- {name}: {comparison_counts.get(name, 0):,}")

    lines.extend(["", "## Adjudication Counts", ""])
    if not adjudication_counts:
        lines.append("- No Google calls were executed in this run.")
    for name, count in sorted(adjudication_counts.items()):
        lines.append(f"- {name}: {count:,}")

    lines.extend(
        [
            "",
            "## Top Risky Targets",
            "",
            "| Postal code | Class | Max km | Selected | Sources |",
            "| --- | --- | ---: | --- | --- |",
        ]
    )
    for row in top_risky:
        lines.append(
            f"| {row.get('postal_code')} | {row.get('comparison_class')} | "
            f"{row.get('max_disagreement_km')} | {row.get('selected_source')} | "
            f"{row.get('sources')} |"
        )

    lines.extend(
        [
            "",
            "## Top Google-vs-Selected Distances",
            "",
            "| Postal code | Bucket | Class | Google status | Distance km | Adjudication |",
            "| --- | --- | --- | --- | ---: | --- |",
        ]
    )
    if not top_adjudicated:
        lines.append("| (none) |  |  |  |  |  |")
    for row in top_adjudicated:
        lines.append(
            f"| {row.get('postal_code')} | {row.get('target_bucket')} | "
            f"{row.get('comparison_class')} | {row.get('google_status')} | "
            f"{row.get('distance_to_selected_km')} | {row.get('adjudication_class')} |"
        )

    lines.extend(
        [
            "",
            "## Reproducibility",
            "",
            "```bash",
            "python3 scripts/google_maps_adjudicate_postal_codes.py --stable-qa-limit 1000",
            "python3 scripts/google_maps_adjudicate_postal_codes.py --execute --stable-qa-limit 1000 --gcp-project <project-with-geocoding-enabled>",
            "```",
        ]
    )
    return "\n".join(lines) + "\n"


def execute_targets(
    targets: list[dict[str, str]],
    args: argparse.Namespace,
    run_dir: Path,
    ledger_path: Path,
    month: str,
    completed_ledger: dict[str, dict[str, str]] | None = None,
) -> list[dict[str, Any]]:
    api_key = os.environ.get(args.api_key_env) or os.environ.get(args.fallback_api_key_env)
    oauth_token = os.environ.get(args.oauth_token_env, "")
    if args.auth_method == "api_key_v3" and not api_key:
        raise SystemExit(
            f"Execution requested but neither ${args.api_key_env} nor "
            f"${args.fallback_api_key_env} is set."
        )
    if args.auth_method == "oauth_v4" and not oauth_token:
        raise SystemExit(f"Execution requested with oauth_v4 but ${args.oauth_token_env} is not set.")

    raw_dir = ensure_dir(run_dir / "raw")
    results: list[dict[str, Any]] = []
    for target in targets:
        rid = request_id(target["postal_code"], target["request_address"])
        if completed_ledger and rid in completed_ledger and not args.rerun_ledger_successes:
            cached = adjudication_from_ledger(target, completed_ledger[rid], args.timezone)
            if cached is not None:
                results.append(cached)
                continue
        raw_path = raw_dir / f"{target['postal_code'].replace(' ', '')}_{rid}.json"
        expires_at = cache_expiry_iso(args.timezone)
        try:
            http_status, payload = google_geocode(args, api_key, oauth_token, target["request_address"], args.timeout)
            wrapped_payload = {
                "metadata": {
                    "auth_method": args.auth_method,
                    "request_id": rid,
                    "postal_code": target["postal_code"],
                    "request_address": target["request_address"],
                    "retrieved_at": utc_now_iso(),
                    "cache_expires_at": expires_at,
                    "license_status": "google_license_restricted_temporary_cache",
                },
                "response": payload,
            }
            write_json(raw_path, wrapped_payload)
            parsed = parse_google_result(payload)
            error_message = payload.get("error_message", "")
            append_ledger(
                ledger_path,
                {
                    "event_at": utc_now_iso(),
                    "calendar_month": month,
                    "run_id": args.run_id,
                    "request_id": rid,
                    "postal_code": target["postal_code"],
                    "target_bucket": target["target_bucket"],
                    "request_address": target["request_address"],
                    "status": "executed",
                    "cache_hit": "False",
                    "billable_event_count": "1",
                    "http_status": str(http_status),
                    "google_status": parsed.get("google_status", ""),
                    "raw_response_path": str(raw_path),
                    "error_message": error_message,
                },
            )
            results.append(
                build_adjudication_row(target, parsed, raw_path, rid, expires_at, error_message)
            )
        except urllib.error.HTTPError as exc:
            error_message = exc.read().decode("utf-8", errors="replace")
            append_ledger(
                ledger_path,
                {
                    "event_at": utc_now_iso(),
                    "calendar_month": month,
                    "run_id": args.run_id,
                    "request_id": rid,
                    "postal_code": target["postal_code"],
                    "target_bucket": target["target_bucket"],
                    "request_address": target["request_address"],
                    "status": "http_error",
                    "cache_hit": "False",
                    "billable_event_count": "1",
                    "http_status": str(exc.code),
                    "google_status": "",
                    "raw_response_path": "",
                    "error_message": error_message[:500],
                },
            )
            parsed = {
                "google_status": "",
                "google_result_count": "",
                "google_valid_bc_coordinate": "False",
            }
            results.append(build_adjudication_row(target, parsed, Path(), rid, expires_at, error_message[:500]))
        except Exception as exc:  # noqa: BLE001 - durable run ledger needs the failure.
            append_ledger(
                ledger_path,
                {
                    "event_at": utc_now_iso(),
                    "calendar_month": month,
                    "run_id": args.run_id,
                    "request_id": rid,
                    "postal_code": target["postal_code"],
                    "target_bucket": target["target_bucket"],
                    "request_address": target["request_address"],
                    "status": "local_error",
                    "cache_hit": "False",
                    "billable_event_count": "0",
                    "http_status": "",
                    "google_status": "",
                    "raw_response_path": "",
                    "error_message": str(exc)[:500],
                },
            )
            parsed = {
                "google_status": "",
                "google_result_count": "",
                "google_valid_bc_coordinate": "False",
            }
            results.append(build_adjudication_row(target, parsed, Path(), rid, expires_at, str(exc)[:500]))
        time.sleep(max(args.delay_seconds, 0.0))
    return results


def adjudication_from_ledger(
    target: dict[str, str],
    ledger_row: dict[str, str],
    timezone: str,
) -> dict[str, Any] | None:
    rid = ledger_row.get("request_id", "")
    raw_path_text = ledger_row.get("raw_response_path", "")
    expires_at = cache_expiry_iso(timezone)
    if ledger_row.get("status") == "executed" and raw_path_text:
        raw_path = Path(raw_path_text)
        try:
            payload = json.loads(raw_path.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            return None
        metadata = payload.get("metadata") or {}
        response = payload.get("response") or payload
        parsed = parse_google_result(response)
        return build_adjudication_row(
            target,
            parsed,
            raw_path,
            rid,
            metadata.get("cache_expires_at") or expires_at,
            response.get("error_message", ""),
        )
    if ledger_row.get("status") == "http_error":
        parsed = {
            "google_status": ledger_row.get("google_status", ""),
            "google_result_count": "",
            "google_valid_bc_coordinate": "False",
        }
        return build_adjudication_row(
            target,
            parsed,
            Path(),
            rid,
            expires_at,
            ledger_row.get("error_message", ""),
        )
    return None


def main() -> int:
    args = parse_args()
    if not args.run_id:
        args.run_id = default_run_id()

    comparison_path = Path(args.comparison)
    if not comparison_path.exists():
        raise SystemExit(f"Comparison CSV not found: {comparison_path}")

    comparison_rows = read_csv(comparison_path)
    targets = select_targets(comparison_rows, stable_qa_limit=args.stable_qa_limit)
    if args.max_requests > 0:
        targets = targets[: args.max_requests]
        for index, row in enumerate(targets, start=1):
            row["target_order"] = str(index)

    month = local_month(args.timezone)
    ledger_path = Path(args.ledger)
    ledger_rows = read_ledger(ledger_path)
    already_done = completed_request_ids(ledger_rows, month)
    completed_ledger = completed_ledger_by_request_id(ledger_rows, month)
    if args.execute and not args.rerun_ledger_successes:
        planned_calls = sum(
            1
            for target in targets
            if request_id(target["postal_code"], target["request_address"]) not in already_done
        )
    else:
        planned_calls = len(targets) if args.execute else 0
    ledger_skipped_targets = (len(targets) - planned_calls) if args.execute else 0
    cap_ok, ledger_used, ledger_remaining = check_monthly_cap(
        ledger_rows,
        month,
        args.hard_monthly_cap,
        planned_calls,
    )
    if args.execute and not cap_ok:
        raise SystemExit(
            f"Google Geocoding monthly cap would be exceeded: used={ledger_used}, "
            f"remaining={ledger_remaining}, planned={planned_calls}, cap={args.hard_monthly_cap}."
        )

    gcp_preflight_ok, gcp_preflight_message = resolve_gcp_preflight(args)
    if args.execute and not gcp_preflight_ok and not args.allow_unverified_gcp_preflight:
        raise SystemExit(
            "Execution requested but read-only GCP preflight did not verify Geocoding API "
            f"enablement. {gcp_preflight_message}"
        )

    work_root = ensure_dir(Path(args.work_root))
    run_dir = ensure_dir(work_root / args.run_id)
    outputs_dir = ensure_dir(Path(args.outputs_dir))
    reports_dir = ensure_dir(Path(args.reports_dir))

    target_path = outputs_dir / "bc_postal_code_google_geocoding_targets.csv"
    adjudication_path = outputs_dir / "bc_postal_code_google_adjudication_license_restricted.csv"
    summary_path = outputs_dir / "bc_postal_code_google_adjudication_summary.csv"
    report_md_path = reports_dir / f"google_maps_adjudication_{args.run_id}.md"
    report_json_path = reports_dir / f"google_maps_adjudication_{args.run_id}.json"

    write_target_plan(target_path, targets)

    adjudication_rows: list[dict[str, Any]] = []
    if args.execute:
        adjudication_rows = execute_targets(targets, args, run_dir, ledger_path, month, completed_ledger)
    write_csv(adjudication_path, adjudication_rows, ADJUDICATION_FIELDS)

    summary_rows = build_summary_rows(
        targets,
        adjudication_rows,
        ledger_used,
        ledger_remaining,
        args.hard_monthly_cap,
        args.execute,
        gcp_preflight_ok,
        gcp_preflight_message,
        planned_calls,
        ledger_skipped_targets,
    )
    write_csv(summary_path, summary_rows, SUMMARY_FIELDS)

    report_md_path.write_text(
        markdown_report(
            args.run_id,
            args,
            target_path,
            adjudication_path,
            summary_path,
            targets,
            adjudication_rows,
            summary_rows,
            month,
        )
    )
    write_json(
        report_json_path,
        {
            "run_id": args.run_id,
            "generated_at": utc_now_iso(),
            "comparison_path": str(comparison_path),
            "execute": args.execute,
            "auth_method": args.auth_method,
            "billing_account": args.billing_account,
            "gcp_project": args.gcp_project,
            "gcp_preflight_json": args.gcp_preflight_json,
            "calendar_month": month,
            "hard_monthly_cap": args.hard_monthly_cap,
            "stable_qa_limit": args.stable_qa_limit,
            "target_count": len(targets),
            "execute_pending_targets": planned_calls,
            "ledger_skipped_targets": ledger_skipped_targets,
            "target_bucket_counts": dict(Counter(row["target_bucket"] for row in targets)),
            "target_class_counts": dict(Counter(row["comparison_class"] for row in targets)),
            "adjudication_class_counts": dict(Counter(row.get("adjudication_class", "") for row in adjudication_rows)),
            "ledger_used_before_run": ledger_used,
            "ledger_remaining_before_run": ledger_remaining,
            "gcp_preflight_ok": gcp_preflight_ok,
            "gcp_preflight_message": gcp_preflight_message,
            "preflight_note": args.preflight_note,
            "outputs": {
                "targets": str(target_path),
                "adjudication": str(adjudication_path),
                "summary": str(summary_path),
                "report_md": str(report_md_path),
                "report_json": str(report_json_path),
            },
        },
    )

    print(target_path)
    print(adjudication_path)
    print(summary_path)
    print(report_md_path)
    print(report_json_path)
    print(f"targets={len(targets)} execute={args.execute}")
    print(f"ledger_used_before_run={ledger_used} ledger_remaining_before_run={ledger_remaining}")
    print(f"gcp_preflight_ok={gcp_preflight_ok} {gcp_preflight_message}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
