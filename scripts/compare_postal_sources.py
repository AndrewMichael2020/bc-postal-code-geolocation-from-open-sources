#!/usr/bin/env python3
"""Compare imported BC postal-code geolocation sources and build outputs."""

from __future__ import annotations

import argparse
import csv
import itertools
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from postal_source_utils import (
    ROOT,
    classify_distance,
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


FINAL_FIELDS = ["PostalCodeID", "postal_code", "latitude", "longitude"]
COMPARISON_FIELDS = [
    "postal_code",
    "source_count",
    "sources",
    "selected_source",
    "selected_methodology",
    "selected_latitude",
    "selected_longitude",
    "comparison_class",
    "disagreement_class",
    "max_disagreement_km",
    "missing_from_geonames",
    "source_coordinates",
    "selection_notes",
]
SUMMARY_FIELDS = [
    "source_id",
    "label",
    "access_status",
    "import_status",
    "imported_rows",
    "distinct_postal_codes",
    "rows_skipped",
    "stated_freshness",
    "http_last_modified",
    "http_etag",
    "downloaded_at",
    "file_sha256",
    "raw_path",
    "source_url",
    "api_version",
    "base_data_date",
    "quota_or_rate_limit",
    "license",
    "access_requirements",
    "registration_steps",
    "methodology",
    "notes",
]


SOURCE_PRIORITY = {
    "osm_geofabrik_bc": 10,
    "overpass_private_coffee": 12,
    "openaddresses_bc": 20,
    "statcan_oda_bc": 30,
    "geonames_ca_full": 40,
    "bc_address_geocoder": 60,
    "geoapify": 70,
    "geocoder_ca": 75,
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--run-dir",
        default="",
        help="Import run directory. Defaults to latest work/postal_reconstruction run.",
    )
    parser.add_argument("--outputs-dir", default=str(ROOT / "outputs/geolocation"))
    parser.add_argument("--reports-dir", default=str(ROOT / "reports/geolocation"))
    return parser.parse_args()


def latest_run_dir() -> Path:
    root = ROOT / "work/postal_reconstruction"
    candidates = [path for path in root.iterdir() if path.is_dir()] if root.exists() else []
    if not candidates:
        raise SystemExit("No import runs found. Run scripts/import_postal_sources.py first.")
    return sorted(candidates)[-1]


def parse_observations(path: Path) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in read_csv(path):
        latitude = to_float(row.get("latitude"))
        longitude = to_float(row.get("longitude"))
        postal_code = row.get("postal_code", "")
        if not postal_code.startswith("V") or not valid_bc_coordinate(latitude, longitude):
            continue
        row["latitude_number"] = latitude
        row["longitude_number"] = longitude
        row["record_count_number"] = int(float(row.get("source_record_count") or 0))
        row["spread_number"] = to_float(row.get("spread_km")) or 0.0
        grouped[postal_code].append(row)
    return grouped


def max_pairwise_distance(rows: list[dict[str, Any]]) -> float | None:
    max_distance = 0.0
    found = False
    for left, right in itertools.combinations(rows, 2):
        distance = haversine_km(
            left["latitude_number"],
            left["longitude_number"],
            right["latitude_number"],
            right["longitude_number"],
        )
        if distance is None:
            continue
        found = True
        max_distance = max(max_distance, distance)
    return max_distance if found else None


def is_strong_address_evidence(row: dict[str, Any]) -> bool:
    if row["source_id"] not in {
        "statcan_oda_bc",
        "openaddresses_bc",
        "osm_geofabrik_bc",
        "overpass_private_coffee",
    }:
        return False
    if row["record_count_number"] < 2:
        return False
    return row["spread_number"] <= 1.0


def select_final_coordinate(rows: list[dict[str, Any]]) -> tuple[dict[str, Any], str]:
    strong = [row for row in rows if is_strong_address_evidence(row)]
    if strong:
        selected = sorted(
            strong,
            key=lambda row: (
                row["spread_number"],
                -row["record_count_number"],
                SOURCE_PRIORITY.get(row["source_id"], 99),
            ),
        )[0]
        return selected, "strong multi-address medoid evidence"

    geonames = [row for row in rows if row["source_id"] == "geonames_ca_full"]
    if geonames:
        return geonames[0], "GeoNames high-coverage seed"

    selected = sorted(
        rows,
        key=lambda row: (
            SOURCE_PRIORITY.get(row["source_id"], 99),
            row["spread_number"],
            -row["record_count_number"],
        ),
    )[0]
    return selected, "best available non-GeoNames free-source coordinate"


def source_coordinates(rows: list[dict[str, Any]]) -> str:
    parts = []
    for row in sorted(rows, key=lambda item: SOURCE_PRIORITY.get(item["source_id"], 99)):
        parts.append(
            f"{row['source_id']}:{format_number(row['latitude_number'])},"
            f"{format_number(row['longitude_number'])}"
        )
    return ";".join(parts)


def build_comparison(
    grouped: dict[str, list[dict[str, Any]]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    comparison_rows: list[dict[str, Any]] = []
    final_rows: list[dict[str, Any]] = []
    for index, postal_code in enumerate(sorted(grouped), start=1):
        rows = grouped[postal_code]
        selected, reason = select_final_coordinate(rows)
        max_distance = max_pairwise_distance(rows)
        source_count = len({row["source_id"] for row in rows})
        disagreement_class = classify_distance(max_distance, source_count)
        missing_from_geonames = not any(row["source_id"] == "geonames_ca_full" for row in rows)
        comparison_class = "missing_from_seed" if missing_from_geonames else disagreement_class
        comparison_rows.append(
            {
                "postal_code": postal_code,
                "source_count": source_count,
                "sources": ";".join(sorted({row["source_id"] for row in rows})),
                "selected_source": selected["source_id"],
                "selected_methodology": selected.get("methodology", ""),
                "selected_latitude": format_number(selected["latitude_number"]),
                "selected_longitude": format_number(selected["longitude_number"]),
                "comparison_class": comparison_class,
                "disagreement_class": disagreement_class,
                "max_disagreement_km": "" if max_distance is None else f"{max_distance:.3f}",
                "missing_from_geonames": str(missing_from_geonames),
                "source_coordinates": source_coordinates(rows),
                "selection_notes": reason,
            }
        )
        final_rows.append(
            {
                "PostalCodeID": f"SYN-PC-{index:06d}",
                "postal_code": postal_code,
                "latitude": format_number(selected["latitude_number"]),
                "longitude": format_number(selected["longitude_number"]),
            }
        )
    return final_rows, comparison_rows


def summarize_sources(status_rows: list[dict[str, str]], grouped: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    observed_counts: Counter[str] = Counter()
    for rows in grouped.values():
        for row in rows:
            observed_counts[row["source_id"]] += 1
    summary = []
    for row in status_rows:
        summary.append(
            {
                field: row.get(field, "")
                for field in SUMMARY_FIELDS
            }
        )
        if observed_counts[row.get("source_id", "")]:
            summary[-1]["distinct_postal_codes"] = str(observed_counts[row["source_id"]])
    return summary


def report_markdown(
    run_dir: Path,
    final_rows: list[dict[str, Any]],
    comparison_rows: list[dict[str, Any]],
    summary_rows: list[dict[str, Any]],
) -> str:
    class_counts = Counter(row["comparison_class"] for row in comparison_rows)
    selected_counts = Counter(row["selected_source"] for row in comparison_rows)
    imported_sources = [
        row for row in summary_rows if row.get("import_status") not in {"not_attempted", ""}
    ]
    registration_sources = [
        row
        for row in summary_rows
        if row.get("access_status") in {"free_registration", "restricted_account"}
    ]
    top_disagreements = sorted(
        [
            row
            for row in comparison_rows
            if row["max_disagreement_km"]
        ],
        key=lambda row: float(row["max_disagreement_km"]),
        reverse=True,
    )[:25]

    lines = [
        "# BC Postal-Code Free-Source Reconstruction Report",
        "",
        f"Run directory: `{run_dir}`",
        f"Generated at: {utc_now_iso()}",
        "",
        "## Coverage",
        "",
        f"- Final reconstructed postal codes: {len(final_rows):,}",
        "",
        "## Source Freshness And Access",
        "",
        "| Source | Access | Import status | Distinct codes | Freshness | Hash/API |",
        "| --- | --- | --- | ---: | --- | --- |",
    ]
    for row in imported_sources:
        freshness = (
            row.get("base_data_date")
            or row.get("http_last_modified")
            or row.get("stated_freshness")
        )
        hash_or_api = row.get("file_sha256") or row.get("api_version")
        lines.append(
            f"| {row.get('source_id')} | {row.get('access_status')} | "
            f"{row.get('import_status')} | {row.get('distinct_postal_codes') or 0} | "
            f"{freshness} | {hash_or_api} |"
        )

    lines.extend(
        [
            "",
            "## Methodology",
            "",
        "- GeoNames is treated as the high-coverage seed when no stronger current address-point evidence exists.",
        "- ODA, OpenAddresses, and OSM tagged features are address-point sources; multiple points per postal code are represented by a medoid-style coordinate and spread metric.",
        "- Public Overpass can be used as a slow hosted OSM evidence path if it is split into small cached tiles over multiple days; the local Geofabrik PBF path remains preferred for province-scale reproducibility.",
        "- Nominatim is documented but excluded from bulk use because its public policy forbids systematic complete-list postcode queries.",
        "- Sources that are not practically usable for a free local workflow are intentionally excluded from the runnable import.",
        "",
        "## Registration And Sign-In Sources",
        "",
    ]
    )
    if not registration_sources:
        lines.append("- None cataloged.")
    for row in registration_sources:
        lines.extend(
            [
                f"### {row.get('source_id')}",
                "",
                f"- URL: {row.get('source_url')}",
                f"- Access: {row.get('access_status')} / {row.get('import_status')}",
                f"- Requirements: {row.get('access_requirements')}",
                f"- Quota or rate limit: {row.get('quota_or_rate_limit')}",
                f"- Steps: {row.get('registration_steps')}",
                "",
            ]
        )
    lines.extend(
        [
            "## Comparison Classes",
            "",
        ]
    )
    for name, count in sorted(class_counts.items()):
        lines.append(f"- {name}: {count:,}")
    lines.extend(["", "## Selected Source Counts", ""])
    for name, count in sorted(selected_counts.items()):
        lines.append(f"- {name}: {count:,}")

    lines.extend(
        [
            "",
            "## Largest Coordinate Disagreements",
            "",
            "| Postal code | Class | Max km | Sources | Selected | Coordinates |",
            "| --- | --- | ---: | --- | --- | --- |",
        ]
    )
    for row in top_disagreements:
        lines.append(
            f"| {row['postal_code']} | {row['comparison_class']} | "
            f"{row['max_disagreement_km']} | {row['sources']} | "
            f"{row['selected_source']} | {row['source_coordinates']} |"
        )

    lines.extend(
        [
            "",
            "## Reproducibility",
            "",
            "```bash",
            "python3 scripts/import_postal_sources.py",
            f"python3 scripts/compare_postal_sources.py --run-dir {run_dir}",
            "```",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> int:
    args = parse_args()
    run_dir = Path(args.run_dir) if args.run_dir else latest_run_dir()
    observations_path = run_dir / "source_observations.csv"
    status_path = run_dir / "source_status.csv"
    if not observations_path.exists() or not status_path.exists():
        raise SystemExit(f"Import run is missing source_observations.csv/source_status.csv: {run_dir}")

    grouped = parse_observations(observations_path)
    final_rows, comparison_rows = build_comparison(grouped)
    status_rows = read_csv(status_path)
    summary_rows = summarize_sources(status_rows, grouped)

    outputs_dir = ensure_dir(Path(args.outputs_dir))
    reports_dir = ensure_dir(Path(args.reports_dir))
    run_id = run_dir.name

    final_path = outputs_dir / "bc_postal_code_reconstructed_free.csv"
    comparison_path = outputs_dir / "bc_postal_code_source_comparison.csv"
    summary_path = outputs_dir / "bc_postal_code_source_summary.csv"
    report_md_path = reports_dir / f"postal_reconstruction_{run_id}.md"
    report_json_path = reports_dir / f"postal_reconstruction_{run_id}.json"

    write_csv(final_path, final_rows, FINAL_FIELDS)
    write_csv(comparison_path, comparison_rows, COMPARISON_FIELDS)
    write_csv(summary_path, summary_rows, SUMMARY_FIELDS)
    report_md_path.write_text(
        report_markdown(run_dir, final_rows, comparison_rows, summary_rows)
    )
    write_json(
        report_json_path,
        {
            "run_id": run_id,
            "run_dir": str(run_dir),
            "generated_at": utc_now_iso(),
            "final_row_count": len(final_rows),
            "comparison_class_counts": dict(Counter(row["comparison_class"] for row in comparison_rows)),
            "selected_source_counts": dict(Counter(row["selected_source"] for row in comparison_rows)),
            "sources": summary_rows,
            "largest_disagreements": sorted(
                [row for row in comparison_rows if row["max_disagreement_km"]],
                key=lambda row: float(row["max_disagreement_km"]),
                reverse=True,
            )[:100],
        },
    )

    print(final_path)
    print(comparison_path)
    print(summary_path)
    print(report_md_path)
    print(report_json_path)
    print(f"final_rows={len(final_rows)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
