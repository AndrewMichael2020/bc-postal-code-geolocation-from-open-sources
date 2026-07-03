#!/usr/bin/env python3
"""Build a golden Google-adjudicated BC postal-code geolocation table.

The final four-column table can include Google Maps Geocoding coordinates for
rows already adjudicated by `google_maps_adjudicate_postal_codes.py`. Those rows
are license-restricted and are documented in the companion audit table. Google
province-only results, such as "British Columbia, Canada", are rejected as
non-postal legacy/inactive evidence and excluded from the golden table.
"""

from __future__ import annotations

import argparse
import re
from collections import Counter
from pathlib import Path
from typing import Any

from postal_source_utils import (
    ROOT,
    default_run_id,
    ensure_dir,
    format_number,
    read_csv,
    to_float,
    valid_bc_coordinate,
    write_csv,
    write_json,
)


DEFAULT_COMPARISON = ROOT / "outputs/geolocation/bc_postal_code_source_comparison.csv"
DEFAULT_GOOGLE_ADJUDICATION = ROOT / "outputs/geolocation/bc_postal_code_google_adjudication_license_restricted.csv"
DEFAULT_OUTPUTS_DIR = ROOT / "outputs/geolocation"
DEFAULT_REPORTS_DIR = ROOT / "reports/geolocation"

FINAL_FIELDS = ["PostalCodeID", "postal_code", "latitude", "longitude"]
AUDIT_FIELDS = [
    "PostalCodeID",
    "postal_code",
    "inclusion_status",
    "rejection_reason",
    "latitude",
    "longitude",
    "coordinate_source",
    "coordinate_methodology",
    "reliability_tier",
    "google_authority_action",
    "google_adjudication_class",
    "google_result_scope",
    "google_formatted_address",
    "google_types",
    "google_location_type",
    "google_restriction_status",
    "google_retention_mode",
    "google_cache_expires_at",
    "comparison_class",
    "selected_source_before",
    "selected_latitude_before",
    "selected_longitude_before",
    "nearest_free_source_to_google",
    "fsa",
    "source_count",
    "sources",
    "source_coordinates",
    "lineage_chain",
    "notes",
]
REJECTED_FIELDS = AUDIT_FIELDS
SUMMARY_FIELDS = ["metric", "value"]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--comparison", default=str(DEFAULT_COMPARISON))
    parser.add_argument("--google-adjudication", default=str(DEFAULT_GOOGLE_ADJUDICATION))
    parser.add_argument("--outputs-dir", default=str(DEFAULT_OUTPUTS_DIR))
    parser.add_argument("--reports-dir", default=str(DEFAULT_REPORTS_DIR))
    parser.add_argument("--run-id", default=default_run_id())
    parser.add_argument("--output-prefix", default="postal_code_geolocation_golden")
    return parser.parse_args()


def postal_compact(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9]", "", value or "").upper()


def fsa_for(postal_code: str) -> str:
    return postal_compact(postal_code)[:3]


def google_result_scope(google_row: dict[str, str] | None) -> str:
    if not google_row:
        return "not_requested"
    if google_row.get("google_status") != "OK":
        return "no_result"
    if google_row.get("google_valid_bc_coordinate") != "True":
        return "outside_bc"

    code = postal_compact(google_row.get("postal_code", ""))
    fsa = code[:3]
    formatted = postal_compact(google_row.get("google_formatted_address", ""))
    types = set(filter(None, (google_row.get("google_types") or "").split(";")))

    if code and code in formatted:
        return "full_postal_code"
    if "postal_code_prefix" in types:
        return "fsa_prefix"
    if fsa and fsa in formatted and code not in formatted:
        return "fsa_prefix"
    if "postal_code" in types:
        return "postal_code_unknown"
    if "administrative_area_level_1" in types and "political" in types:
        return "province_only"
    return "unknown"


def selected_coordinate(row: dict[str, str]) -> tuple[float | None, float | None]:
    return to_float(row.get("selected_latitude")), to_float(row.get("selected_longitude"))


def choose_golden_coordinate(
    row: dict[str, str],
    google_row: dict[str, str] | None,
) -> dict[str, Any]:
    selected_lat, selected_lon = selected_coordinate(row)
    result = {
        "inclusion_status": "included",
        "rejection_reason": "",
        "latitude": selected_lat,
        "longitude": selected_lon,
        "coordinate_source": row.get("selected_source", ""),
        "coordinate_methodology": row.get("selected_methodology", ""),
        "reliability_tier": "free_open_selected_not_google_checked",
        "google_authority_action": "free_selected_no_google_evidence",
        "lineage_chain": (
            f"comparison:{row.get('comparison_class', '')}"
            f" -> selected:{row.get('selected_source', '')}"
            " -> google:not_requested"
        ),
        "notes": "No Google adjudication row was available for this postal code.",
    }
    if not google_row:
        return result

    adjudication_class = google_row.get("adjudication_class", "")
    scope = google_result_scope(google_row)
    google_lat = to_float(google_row.get("google_latitude"))
    google_lon = to_float(google_row.get("google_longitude"))
    if google_row.get("google_status") == "OK" and valid_bc_coordinate(google_lat, google_lon):
        if scope not in {"full_postal_code", "fsa_prefix", "postal_code_unknown"}:
            result.update(
                {
                    "inclusion_status": "rejected",
                    "rejection_reason": f"google_{scope}_non_postal_result",
                    "latitude": google_lat,
                    "longitude": google_lon,
                    "coordinate_source": "google_maps_geocoding",
                    "coordinate_methodology": f"google_maps_geocoding_{scope}_rejected",
                    "reliability_tier": "rejected_google_non_postal_scope",
                    "google_authority_action": "google_non_postal_scope_discarded_as_legacy_inactive",
                    "lineage_chain": (
                        f"comparison:{row.get('comparison_class', '')}"
                        f" -> selected:{row.get('selected_source', '')}"
                        f" -> google:{scope}"
                        " -> reject:non_postal_scope"
                    ),
                    "notes": (
                        "Google returned a non-postal administrative/geographic result rather than the "
                        "six-character postal code or FSA. This is treated as legacy/inactive/trash and "
                        "excluded from the golden table."
                    ),
                }
            )
            return result

        methodology = "google_maps_geocoding_approximate"
        reliability_tier = "google_postal_code_authoritative"
        action = "google_authoritative_coordinate_persisted_restricted"
        if scope == "fsa_prefix":
            methodology = "google_maps_geocoding_fsa_prefix_approximate"
            reliability_tier = "google_fsa_prefix_authoritative_likely_legacy_or_changed"
            action = "google_fsa_prefix_coordinate_persisted_restricted"
        elif scope == "full_postal_code":
            methodology = "google_maps_geocoding_full_postal_code_approximate"
        elif scope == "postal_code_unknown":
            methodology = "google_maps_geocoding_postal_code_approximate"
        result.update(
            {
                "latitude": google_lat,
                "longitude": google_lon,
                "coordinate_source": "google_maps_geocoding",
                "coordinate_methodology": methodology,
                "reliability_tier": reliability_tier,
                "google_authority_action": action,
                "lineage_chain": (
                    f"comparison:{row.get('comparison_class', '')}"
                    f" -> selected:{row.get('selected_source', '')}"
                    f" -> google:{scope}"
                    " -> include:google_authoritative"
                ),
                "notes": (
                    "Google Maps Geocoding is treated as the authoritative coordinate for this row. "
                    "The coordinate is persisted for local experimentation with Google restriction metadata."
                ),
            }
        )
        return result

    result["google_authority_action"] = "google_not_usable_keep_free_selected"
    result["reliability_tier"] = "free_open_selected_after_unusable_google"
    result["lineage_chain"] = (
        f"comparison:{row.get('comparison_class', '')}"
        f" -> selected:{row.get('selected_source', '')}"
        f" -> google:{scope}"
        " -> include:free_open_selected"
    )
    result["notes"] = (
        f"Google adjudication class `{adjudication_class}` did not provide a valid BC coordinate; "
        "the selected free/open coordinate was retained."
    )
    return result


def build_golden_rows(
    comparison_rows: list[dict[str, str]],
    google_rows: list[dict[str, str]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    google_by_code = {row.get("postal_code", ""): row for row in google_rows}
    final_rows: list[dict[str, Any]] = []
    audit_rows: list[dict[str, Any]] = []
    rejected_rows: list[dict[str, Any]] = []
    sorted_rows = sorted(comparison_rows, key=lambda row: row.get("postal_code", ""))
    final_index = 0

    for row in sorted_rows:
        postal_code = row.get("postal_code", "")
        fsa = fsa_for(postal_code)
        google_row = google_by_code.get(postal_code)
        chosen = choose_golden_coordinate(row, google_row)
        lat = chosen.get("latitude")
        lon = chosen.get("longitude")
        included = chosen.get("inclusion_status") == "included"
        if included:
            final_index += 1
        postal_id = f"SYN-PC-{final_index:06d}" if included else ""
        google_restricted = chosen.get("coordinate_source") == "google_maps_geocoding"

        if included:
            final_rows.append(
                {
                    "PostalCodeID": postal_id,
                    "postal_code": postal_code,
                    "latitude": format_number(lat),
                    "longitude": format_number(lon),
                }
            )
        audit_row = {
            "PostalCodeID": postal_id,
            "postal_code": postal_code,
            "inclusion_status": chosen.get("inclusion_status", ""),
            "rejection_reason": chosen.get("rejection_reason", ""),
            "latitude": format_number(lat),
            "longitude": format_number(lon),
            "coordinate_source": chosen.get("coordinate_source", ""),
            "coordinate_methodology": chosen.get("coordinate_methodology", ""),
            "reliability_tier": chosen.get("reliability_tier", ""),
            "google_authority_action": chosen.get("google_authority_action", ""),
            "google_adjudication_class": google_row.get("adjudication_class", "") if google_row else "",
            "google_result_scope": google_result_scope(google_row),
            "google_formatted_address": google_row.get("google_formatted_address", "") if google_row else "",
            "google_types": google_row.get("google_types", "") if google_row else "",
            "google_location_type": google_row.get("google_location_type", "") if google_row else "",
            "google_restriction_status": (
                "google_maps_content_license_restricted" if google_restricted else ""
            ),
            "google_retention_mode": (
                "persisted_no_auto_delete_per_user_request" if google_restricted and included else ""
            ),
            "google_cache_expires_at": google_row.get("cache_expires_at", "") if google_row else "",
            "comparison_class": row.get("comparison_class", ""),
            "selected_source_before": row.get("selected_source", ""),
            "selected_latitude_before": row.get("selected_latitude", ""),
            "selected_longitude_before": row.get("selected_longitude", ""),
            "nearest_free_source_to_google": google_row.get("nearest_source", "") if google_row else "",
            "fsa": fsa,
            "source_count": row.get("source_count", ""),
            "sources": row.get("sources", ""),
            "source_coordinates": row.get("source_coordinates", ""),
            "lineage_chain": chosen.get("lineage_chain", ""),
            "notes": chosen.get("notes", ""),
        }
        audit_rows.append(audit_row)
        if not included:
            rejected_rows.append(audit_row)

    summary = {
        "final_row_count": len(final_rows),
        "audit_row_count": len(audit_rows),
        "rejected_row_count": len(rejected_rows),
        "google_rows_available": len(google_rows),
        "google_coordinates_persisted": sum(
            1
            for row in audit_rows
            if row["coordinate_source"] == "google_maps_geocoding"
            and row["inclusion_status"] == "included"
        ),
        "google_fsa_prefix_coordinates_persisted": sum(
            1
            for row in audit_rows
            if row["coordinate_source"] == "google_maps_geocoding"
            and row["google_result_scope"] == "fsa_prefix"
            and row["inclusion_status"] == "included"
        ),
        "coordinate_source_counts": dict(
            Counter(row["coordinate_source"] for row in audit_rows if row["inclusion_status"] == "included")
        ),
        "inclusion_status_counts": dict(Counter(row["inclusion_status"] for row in audit_rows)),
        "rejection_reason_counts": dict(Counter(row["rejection_reason"] for row in rejected_rows)),
        "reliability_tier_counts": dict(Counter(row["reliability_tier"] for row in audit_rows)),
        "google_authority_action_counts": dict(
            Counter(row["google_authority_action"] for row in audit_rows)
        ),
        "google_adjudication_class_counts": dict(
            Counter(row["google_adjudication_class"] or "not_requested" for row in audit_rows)
        ),
        "google_result_scope_counts": dict(Counter(row["google_result_scope"] for row in audit_rows)),
    }
    return final_rows, audit_rows, rejected_rows, summary


def summary_rows(summary: dict[str, Any]) -> list[dict[str, Any]]:
    rows = [
        {"metric": "final_row_count", "value": summary["final_row_count"]},
        {"metric": "audit_row_count", "value": summary["audit_row_count"]},
        {"metric": "rejected_row_count", "value": summary["rejected_row_count"]},
        {"metric": "google_rows_available", "value": summary["google_rows_available"]},
        {"metric": "google_coordinates_persisted", "value": summary["google_coordinates_persisted"]},
        {
            "metric": "google_fsa_prefix_coordinates_persisted",
            "value": summary["google_fsa_prefix_coordinates_persisted"],
        },
    ]
    for group in [
        "coordinate_source_counts",
        "inclusion_status_counts",
        "rejection_reason_counts",
        "reliability_tier_counts",
        "google_authority_action_counts",
        "google_adjudication_class_counts",
        "google_result_scope_counts",
    ]:
        for key, value in sorted(summary[group].items()):
            rows.append({"metric": f"{group}.{key}", "value": value})
    return rows


def validate_final_rows(final_rows: list[dict[str, Any]]) -> None:
    seen: set[str] = set()
    previous = ""
    for row in final_rows:
        if set(row.keys()) != set(FINAL_FIELDS):
            raise ValueError("Final rows must contain exactly PostalCodeID, postal_code, latitude, longitude.")
        postal_code = row["postal_code"]
        if postal_code in seen:
            raise ValueError(f"Duplicate postal code in final output: {postal_code}")
        if previous and postal_code < previous:
            raise ValueError("Final output is not sorted by postal_code.")
        if not valid_bc_coordinate(row["latitude"], row["longitude"]):
            raise ValueError(f"Invalid BC coordinate in final output: {postal_code}")
        seen.add(postal_code)
        previous = postal_code


def markdown_report(
    run_id: str,
    final_path: Path,
    audit_path: Path,
    rejected_path: Path,
    summary_path: Path,
    summary: dict[str, Any],
    audit_rows: list[dict[str, Any]],
    rejected_rows: list[dict[str, Any]],
) -> str:
    actions = Counter(row["google_authority_action"] for row in audit_rows)
    source_counts = Counter(
        row["coordinate_source"] for row in audit_rows if row["inclusion_status"] == "included"
    )
    google_persisted = [
        row
        for row in audit_rows
        if row["coordinate_source"] == "google_maps_geocoding"
        and row["inclusion_status"] == "included"
    ]
    google_prefix = [row for row in google_persisted if row["google_result_scope"] == "fsa_prefix"][:25]
    google_exact = [row for row in google_persisted if row["google_result_scope"] != "fsa_prefix"][:25]
    retained = [
        row
        for row in audit_rows
        if row["google_authority_action"] == "google_not_usable_keep_free_selected"
    ][:25]

    lines = [
        "# Postal Code Geolocation Golden Build Report",
        "",
        f"Run ID: `{run_id}`",
        "",
        "## Rule Set",
        "",
        "- Google Maps Geocoding is treated as the authoritative coordinate source for adjudicated rows.",
        "- No FSA centroid is calculated internally; FSA/prefix coordinates are taken only from Google Maps Geocoding results.",
        "- Google province-only/non-postal results are discarded as legacy/inactive trash and excluded from the golden table.",
        "- Google-derived rows are marked in the audit as `google_maps_content_license_restricted`.",
        "- The workflow does not auto-delete Google-derived rows; retention is explicitly marked as a local user-requested persistence mode.",
        "- Rows without a valid Google BC coordinate keep the selected free/open coordinate.",
        "",
        "## Outputs",
        "",
        f"- Updated geolocation table: `{final_path}`",
        f"- Audit table: `{audit_path}`",
        f"- Rejected/trash table: `{rejected_path}`",
        f"- Summary table: `{summary_path}`",
        "",
        "## Coverage",
        "",
        f"- Final rows: {summary['final_row_count']:,}",
        f"- Audit rows: {summary['audit_row_count']:,}",
        f"- Rejected rows: {summary['rejected_row_count']:,}",
        f"- Google adjudication rows available: {summary['google_rows_available']:,}",
        f"- Google coordinates persisted: {summary['google_coordinates_persisted']:,}",
        f"- Google FSA/prefix coordinates persisted: {summary['google_fsa_prefix_coordinates_persisted']:,}",
        "",
        "## Coordinate Source Counts",
        "",
    ]
    for source, count in sorted(source_counts.items()):
        lines.append(f"- {source}: {count:,}")
    lines.extend(["", "## Google Authority Actions", ""])
    for action, count in sorted(actions.items()):
        lines.append(f"- {action}: {count:,}")

    lines.extend(["", "## Rejected / Trash Rows", ""])
    if rejected_rows:
        lines.extend(
            [
                "| Postal code | Scope | Google address | Google types | Reason |",
                "| --- | --- | --- | --- | --- |",
            ]
        )
        for row in rejected_rows[:50]:
            lines.append(
                f"| {row['postal_code']} | {row['google_result_scope']} | "
                f"{row['google_formatted_address']} | {row['google_types']} | "
                f"{row['rejection_reason']} |"
            )
    else:
        lines.append("- None.")

    lines.extend(
        [
            "",
            "## Google Full/Postal-Code Coordinates Persisted",
            "",
            "| Postal code | Scope | Before source | Google class |",
            "| --- | --- | --- | --- |",
        ]
    )
    for row in google_exact:
        lines.append(
            f"| {row['postal_code']} | {row['google_result_scope']} | "
            f"{row['selected_source_before']} | {row['google_adjudication_class']} |"
        )
    if not google_exact:
        lines.append("| (none) |  |  |  |")

    lines.extend(
        [
            "",
            "## Google FSA/Prefix Coordinates Persisted",
            "",
            "| Postal code | FSA | Before source | Google class | Retention mode |",
            "| --- | --- | --- | --- | --- |",
        ]
    )
    for row in google_prefix:
        lines.append(
            f"| {row['postal_code']} | {row['fsa']} | {row['selected_source_before']} | "
            f"{row['google_adjudication_class']} | {row['google_retention_mode']} |"
        )
    if not google_prefix:
        lines.append("| (none) |  |  |  |  |")

    lines.extend(
        [
            "",
            "## Google Rows Retained From Free/Open Because Google Was Not Usable",
            "",
            "| Postal code | Selected source retained | Scope | Note |",
            "| --- | --- | --- | --- |",
        ]
    )
    for row in retained:
        lines.append(
            f"| {row['postal_code']} | {row['selected_source_before']} | "
            f"{row['google_result_scope']} | {row['notes']} |"
        )
    if not retained:
        lines.append("| (none) |  |  |  |")

    lines.extend(
        [
            "",
            "## Reproducibility",
            "",
            "```bash",
            "python3 scripts/build_golden_postal_geolocation.py",
            "```",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> int:
    args = parse_args()
    comparison_rows = read_csv(Path(args.comparison))
    google_path = Path(args.google_adjudication)
    google_rows = read_csv(google_path) if google_path.exists() else []
    final_rows, audit_rows, rejected_rows, summary = build_golden_rows(comparison_rows, google_rows)
    validate_final_rows(final_rows)

    outputs_dir = ensure_dir(Path(args.outputs_dir))
    reports_dir = ensure_dir(Path(args.reports_dir))
    final_path = outputs_dir / f"{args.output_prefix}.csv"
    audit_path = outputs_dir / f"{args.output_prefix}_audit.csv"
    rejected_path = outputs_dir / f"{args.output_prefix}_rejected.csv"
    summary_path = outputs_dir / f"{args.output_prefix}_summary.csv"
    report_md_path = reports_dir / f"{args.output_prefix}_{args.run_id}.md"
    report_json_path = reports_dir / f"{args.output_prefix}_{args.run_id}.json"

    write_csv(final_path, final_rows, FINAL_FIELDS)
    write_csv(audit_path, audit_rows, AUDIT_FIELDS)
    write_csv(rejected_path, rejected_rows, REJECTED_FIELDS)
    write_csv(summary_path, summary_rows(summary), SUMMARY_FIELDS)
    write_json(
        report_json_path,
        {
            "run_id": args.run_id,
            "inputs": {
                "comparison": str(Path(args.comparison)),
                "google_adjudication": str(google_path),
            },
            "outputs": {
                "final": str(final_path),
                "audit": str(audit_path),
                "rejected": str(rejected_path),
                "summary": str(summary_path),
            },
            "summary": summary,
            "rule_set": {
                "google_lat_lng_persisted_in_final": True,
                "google_role": "authoritative_coordinate_source_for_adjudicated_rows",
                "fsa_centroid_source": "google_maps_geocoding_prefix_results_only",
                "google_province_only_results_in_final": False,
                "auto_delete_google_rows": False,
                "retention_note": (
                    "Google-derived rows are persisted locally per user request and marked with "
                    "restriction/cache metadata in the audit table."
                ),
            },
        },
    )
    report_md_path.write_text(
        markdown_report(
            args.run_id,
            final_path,
            audit_path,
            rejected_path,
            summary_path,
            summary,
            audit_rows,
            rejected_rows,
        )
    )

    print(f"wrote {final_path}")
    print(f"wrote {audit_path}")
    print(f"wrote {rejected_path}")
    print(f"wrote {summary_path}")
    print(f"wrote {report_md_path}")
    print(f"wrote {report_json_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
