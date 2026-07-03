#!/usr/bin/env python3
"""Greenfield postal-code coordinate sanity rules.

This audit uses only reconstructed source comparison, GeoNames lineage, Google
scope, and official BC Health Authority boundaries.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from enrich_golden_health_authority import (
    BC_CATALOGUE_URL,
    DEFAULT_WORK_ROOT,
    assign_health_authority,
    build_spatial_index,
    load_boundaries,
    prepared_features,
)
from postal_source_utils import ROOT, default_run_id, ensure_dir, read_csv, to_float, write_csv, write_json


DEFAULT_COMPARISON = ROOT / "outputs/geolocation/bc_postal_code_source_comparison.csv"
DEFAULT_AUDIT = ROOT / "outputs/geolocation/postal_code_geolocation_golden_audit.csv"
DEFAULT_SOURCE_OBSERVATIONS = ""
DEFAULT_OUTPUTS_DIR = ROOT / "outputs/geolocation"
DEFAULT_REPORTS_DIR = ROOT / "reports/geolocation"

RULE_FIELDS = [
    "priority",
    "disposition",
    "rule_id",
    "postal_code",
    "fsa",
    "selected_source",
    "source_count",
    "sources",
    "comparison_class",
    "max_disagreement_km",
    "google_result_scope",
    "google_adjudication_class",
    "geonames_latitude",
    "geonames_longitude",
    "geonames_place_name",
    "geonames_boundary_health_authority",
    "place_dominant_health_authority",
    "place_dominant_share",
    "place_health_authority_counts",
    "place_coordinate_cluster_size",
    "exact_coordinate_cluster_size",
    "fsa_geonames_health_authority_counts",
    "greenfield_reason",
    "recommended_action",
]
SUMMARY_FIELDS = ["metric", "value"]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--comparison", default=str(DEFAULT_COMPARISON))
    parser.add_argument("--audit", default=str(DEFAULT_AUDIT))
    parser.add_argument("--source-observations", default=str(DEFAULT_SOURCE_OBSERVATIONS))
    parser.add_argument("--outputs-dir", default=str(DEFAULT_OUTPUTS_DIR))
    parser.add_argument("--reports-dir", default=str(DEFAULT_REPORTS_DIR))
    parser.add_argument("--work-root", default=str(DEFAULT_WORK_ROOT))
    parser.add_argument("--run-id", default=default_run_id())
    parser.add_argument("--refresh-boundaries", action="store_true")
    return parser.parse_args()


def latest_source_observations(work_root: Path) -> Path:
    candidates = sorted(
        path
        for path in work_root.iterdir()
        if path.is_dir() and (path / "source_observations.csv").exists()
    )
    if not candidates:
        raise FileNotFoundError(
            f"No source_observations.csv found under {work_root}. "
            "Run scripts/import_postal_sources.py first or pass --source-observations."
        )
    return candidates[-1] / "source_observations.csv"


def fsa_for(postal_code: str) -> str:
    return (postal_code or "").replace(" ", "")[:3]


def place_from_notes(notes: str) -> str:
    if notes.startswith("place_names="):
        return notes.removeprefix("place_names=")
    return notes


def coordinate_key(latitude: Any, longitude: Any) -> str:
    lat = to_float(latitude)
    lon = to_float(longitude)
    if lat is None or lon is None:
        return ""
    return f"{lat:.6f},{lon:.6f}"


def load_geonames_observations(
    path: Path,
    features: list[dict[str, Any]],
    spatial_index: dict[tuple[int, int], list[dict[str, Any]]],
) -> dict[str, dict[str, str]]:
    observations = {}
    for row in read_csv(path):
        if row.get("source_id") != "geonames_ca_full":
            continue
        assignment = assign_health_authority(
            row.get("latitude", ""),
            row.get("longitude", ""),
            features,
            spatial_index,
        )
        observations[row["postal_code"]] = {
            "postal_code": row["postal_code"],
            "latitude": row.get("latitude", ""),
            "longitude": row.get("longitude", ""),
            "place_name": place_from_notes(row.get("notes", "")),
            "raw_accuracy": row.get("raw_accuracy", ""),
            "boundary_health_authority": assignment.get("health_authority", ""),
            "boundary_confidence": assignment.get("health_authority_confidence", ""),
        }
    return observations


def stats_by_place(
    observations: dict[str, dict[str, str]],
) -> tuple[dict[str, Counter[str]], Counter[tuple[str, str]], Counter[str], dict[str, Counter[str]]]:
    place_ha_counts: dict[str, Counter[str]] = defaultdict(Counter)
    place_coord_counts: Counter[tuple[str, str]] = Counter()
    exact_coord_counts: Counter[str] = Counter()
    fsa_ha_counts: dict[str, Counter[str]] = defaultdict(Counter)
    for code, row in observations.items():
        place = row["place_name"]
        health = row["boundary_health_authority"] or "unassigned"
        coord = coordinate_key(row["latitude"], row["longitude"])
        if place:
            place_ha_counts[place][health] += 1
            place_coord_counts[(place, coord)] += 1
        exact_coord_counts[coord] += 1
        fsa_ha_counts[fsa_for(code)][health] += 1
    return place_ha_counts, place_coord_counts, exact_coord_counts, fsa_ha_counts


def dominant(counter: Counter[str]) -> tuple[str, int, float]:
    if not counter:
        return "", 0, 0.0
    key, count = counter.most_common(1)[0]
    total = sum(counter.values())
    return key, count, count / total if total else 0.0


def make_rule_row(
    priority: str,
    disposition: str,
    rule_id: str,
    comparison: dict[str, str],
    audit: dict[str, str],
    geonames: dict[str, str] | None,
    place_ha_counts: dict[str, Counter[str]],
    place_coord_counts: Counter[tuple[str, str]],
    exact_coord_counts: Counter[str],
    fsa_ha_counts: dict[str, Counter[str]],
    reason: str,
    action: str,
) -> dict[str, str]:
    code = comparison.get("postal_code") or audit.get("postal_code", "")
    geonames = geonames or {}
    place = geonames.get("place_name", "")
    coord = coordinate_key(geonames.get("latitude"), geonames.get("longitude"))
    place_dom, _, place_share = dominant(place_ha_counts.get(place, Counter()))
    return {
        "priority": priority,
        "disposition": disposition,
        "rule_id": rule_id,
        "postal_code": code,
        "fsa": fsa_for(code),
        "selected_source": comparison.get("selected_source", audit.get("selected_source_before", "")),
        "source_count": comparison.get("source_count", audit.get("source_count", "")),
        "sources": comparison.get("sources", audit.get("sources", "")),
        "comparison_class": comparison.get("comparison_class", audit.get("comparison_class", "")),
        "max_disagreement_km": comparison.get("max_disagreement_km", ""),
        "google_result_scope": audit.get("google_result_scope", ""),
        "google_adjudication_class": audit.get("google_adjudication_class", ""),
        "geonames_latitude": geonames.get("latitude", ""),
        "geonames_longitude": geonames.get("longitude", ""),
        "geonames_place_name": place,
        "geonames_boundary_health_authority": geonames.get("boundary_health_authority", ""),
        "place_dominant_health_authority": place_dom,
        "place_dominant_share": f"{place_share:.6f}" if place else "",
        "place_health_authority_counts": json.dumps(dict(place_ha_counts.get(place, Counter())), sort_keys=True),
        "place_coordinate_cluster_size": str(place_coord_counts[(place, coord)]) if place and coord else "",
        "exact_coordinate_cluster_size": str(exact_coord_counts[coord]) if coord else "",
        "fsa_geonames_health_authority_counts": json.dumps(
            dict(fsa_ha_counts.get(fsa_for(code), Counter())), sort_keys=True
        ),
        "greenfield_reason": reason,
        "recommended_action": action,
    }


def build_rules(
    comparison_rows: list[dict[str, str]],
    audit_rows: list[dict[str, str]],
    observations: dict[str, dict[str, str]],
) -> list[dict[str, str]]:
    comparison_by_code = {row["postal_code"]: row for row in comparison_rows}
    audit_by_code = {row["postal_code"]: row for row in audit_rows}
    place_ha_counts, place_coord_counts, exact_coord_counts, fsa_ha_counts = stats_by_place(observations)
    rows = []

    for code in sorted(set(comparison_by_code) | set(audit_by_code)):
        comparison = comparison_by_code.get(code, {})
        audit = audit_by_code.get(code, {})
        geonames = observations.get(code)

        if audit.get("inclusion_status") == "rejected" and audit.get("google_result_scope") not in {
            "full_postal_code",
            "fsa_prefix",
            "postal_code_unknown",
        }:
            rows.append(
                make_rule_row(
                    "P0",
                    "trash",
                    "google_non_postal_scope",
                    comparison,
                    audit,
                    geonames,
                    place_ha_counts,
                    place_coord_counts,
                    exact_coord_counts,
                    fsa_ha_counts,
                    "Google returned a non-postal/province-level result. This is greenfield trash evidence.",
                    "Exclude from golden coordinate output unless a later full postal-code source exists.",
                )
            )
            continue

        max_disagreement = to_float(comparison.get("max_disagreement_km"))
        sources = set(filter(None, (comparison.get("sources") or "").split(";")))
        has_address_source = bool(sources & {"osm_geofabrik_bc", "openaddresses_bc", "statcan_oda_bc"})
        if (
            geonames
            and has_address_source
            and max_disagreement is not None
            and max_disagreement > 10
        ):
            disposition = (
                "trash_geonames_coordinate"
                if comparison.get("selected_source") == "geonames_ca_full"
                else "geonames_conflict_evidence_not_selected"
            )
            rows.append(
                make_rule_row(
                    "P1",
                    disposition,
                    "severe_free_source_disagreement_with_address_source",
                    comparison,
                    audit,
                    geonames,
                    place_ha_counts,
                    place_coord_counts,
                    exact_coord_counts,
                    fsa_ha_counts,
                    (
                        "GeoNames disagrees by >10 km with at least one address/OSM source. "
                        "This contradiction is visible from independent greenfield sources."
                    ),
                    "Prefer Google full postal-code adjudication or address-point medoid over GeoNames.",
                )
            )

        if geonames:
            place = geonames["place_name"]
            coord = coordinate_key(geonames["latitude"], geonames["longitude"])
            place_dom, _, place_share = dominant(place_ha_counts.get(place, Counter()))
            place_cluster = place_coord_counts[(place, coord)]
            geonames_ha = geonames.get("boundary_health_authority") or "unassigned"
            if (
                place
                and sum(place_ha_counts[place].values()) >= 20
                and place_share >= 0.70
                and geonames_ha != place_dom
                and place_cluster >= 3
            ):
                rows.append(
                    make_rule_row(
                        "P1",
                        "trash_geonames_coordinate",
                        "geonames_place_name_official_ha_outlier_cluster",
                        comparison,
                        audit,
                        geonames,
                        place_ha_counts,
                        place_coord_counts,
                        exact_coord_counts,
                        fsa_ha_counts,
                        (
                            "GeoNames repeats the same coordinate for a place name, but that coordinate "
                            "falls in a different official HA than the place-name dominant HA."
                        ),
                        "Discard GeoNames coordinate and adjudicate with Google or address-point evidence.",
                    )
                )

        if (
            geonames
            and comparison.get("selected_source") == "geonames_ca_full"
            and comparison.get("comparison_class") == "single_source"
        ):
            rows.append(
                make_rule_row(
                    "P3",
                    "watch_unconfirmed_single_source",
                    "single_source_geonames_no_independent_evidence",
                    comparison,
                    audit,
                    geonames,
                    place_ha_counts,
                    place_coord_counts,
                    exact_coord_counts,
                    fsa_ha_counts,
                    "Only GeoNames supports this coordinate. This is weak greenfield evidence, not proof of trash.",
                    "Keep only as provisional; prioritize if clustered with another greenfield trash rule.",
                )
            )

    priority_order = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}
    disposition_order = {
        "trash": 0,
        "trash_geonames_coordinate": 1,
        "geonames_conflict_evidence_not_selected": 2,
        "watch_unconfirmed_single_source": 3,
    }
    return sorted(
        rows,
        key=lambda row: (
            priority_order.get(row["priority"], 9),
            disposition_order.get(row["disposition"], 9),
            row["postal_code"],
            row["rule_id"],
        ),
    )


def flatten_summary(summary: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for key, value in summary.items():
        if isinstance(value, dict):
            for sub_key, sub_value in sorted(value.items()):
                rows.append({"metric": f"{key}.{sub_key}", "value": sub_value})
        else:
            rows.append({"metric": key, "value": value})
    return rows


def markdown_table(rows: list[dict[str, Any]], columns: list[tuple[str, str]]) -> list[str]:
    lines = [
        "| " + " | ".join(label for label, _ in columns) + " |",
        "| " + " | ".join("---" for _ in columns) + " |",
    ]
    for row in rows:
        lines.append("| " + " | ".join(str(row.get(key, "")) for _, key in columns) + " |")
    if not rows:
        lines.append("| (none) |" + " |" * (len(columns) - 1))
    return lines


def markdown_report(
    run_id: str,
    rules_path: Path,
    summary_path: Path,
    summary: dict[str, Any],
    rows: list[dict[str, str]],
) -> str:
    lines = [
        "# Greenfield Coordinate Rules Audit",
        "",
        f"Run ID: `{run_id}`",
        "",
        "## Method",
        "",
        "This report uses reconstructed source evidence, Google scope, GeoNames lineage, and official BC Health Authority boundaries.",
        "",
        "Rules used:",
        "",
        "- trash Google results that are non-postal/province-only scope;",
        "- trash GeoNames coordinates that severely disagree with address/OSM evidence;",
        "- trash repeated GeoNames place-name coordinate clusters that fall in the wrong official HA;",
        "- mark single-source GeoNames rows as provisional watch items, not automatic trash.",
        "",
        "Mixed FSA prefix mappings are not used as row evidence; they are discarded separately by the HA enrichment diagnostics.",
        "",
        "## Outputs",
        "",
        f"- Rule rows: `{rules_path}`",
        f"- Summary: `{summary_path}`",
        f"- Official boundary source: {BC_CATALOGUE_URL}",
        "",
        "## Summary",
        "",
        f"- Rule rows: {summary['rule_rows']:,}",
        f"- Trash rows/rules: {summary['disposition_counts'].get('trash', 0) + summary['disposition_counts'].get('trash_geonames_coordinate', 0):,}",
        f"- Watch-only single-source GeoNames rules: {summary['disposition_counts'].get('watch_unconfirmed_single_source', 0):,}",
        "",
        "## Disposition Counts",
        "",
    ]
    for key, value in sorted(summary["disposition_counts"].items()):
        lines.append(f"- {key}: {value:,}")
    lines.extend(
        [
            "",
            "## Rule Counts",
            "",
        ]
    )
    for key, value in sorted(summary["rule_counts"].items()):
        lines.append(f"- {key}: {value:,}")
    trash_rows = [row for row in rows if row["disposition"] in {"trash", "trash_geonames_coordinate"}]
    lines.extend(
        [
            "",
            "## Top Trash Rules",
            "",
            *markdown_table(
                trash_rows[:40],
                [
                    ("Priority", "priority"),
                    ("Code", "postal_code"),
                    ("Disposition", "disposition"),
                    ("Rule", "rule_id"),
                    ("Place", "geonames_place_name"),
                    ("GeoNames HA", "geonames_boundary_health_authority"),
                    ("Place dominant HA", "place_dominant_health_authority"),
                ],
            ),
            "",
            "## Reproducibility",
            "",
            "```bash",
            "python3 scripts/audit_greenfield_coordinate_rules.py --refresh-boundaries",
            "```",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> int:
    args = parse_args()
    run_id = args.run_id
    work_dir = ensure_dir(Path(args.work_root) / run_id)
    boundary_path = work_dir / "bc_health_authority_boundaries_2022.geojson"
    boundaries = load_boundaries(boundary_path, args.refresh_boundaries)
    features = prepared_features(boundaries)
    spatial_index = build_spatial_index(features)

    comparison_rows = read_csv(Path(args.comparison))
    audit_rows = read_csv(Path(args.audit))
    source_observations = (
        Path(args.source_observations)
        if args.source_observations
        else latest_source_observations(Path(args.work_root))
    )
    observations = load_geonames_observations(source_observations, features, spatial_index)
    rows = build_rules(comparison_rows, audit_rows, observations)
    summary = {
        "rule_rows": len(rows),
        "disposition_counts": dict(Counter(row["disposition"] for row in rows)),
        "rule_counts": dict(Counter(row["rule_id"] for row in rows)),
        "priority_counts": dict(Counter(row["priority"] for row in rows)),
        "inputs": {
            "comparison": str(Path(args.comparison)),
            "audit": str(Path(args.audit)),
            "source_observations": str(source_observations),
            "greenfield_only": True,
        },
    }

    outputs_dir = ensure_dir(Path(args.outputs_dir))
    reports_dir = ensure_dir(Path(args.reports_dir))
    rules_path = outputs_dir / "postal_code_geolocation_greenfield_coordinate_rules.csv"
    summary_path = outputs_dir / "postal_code_geolocation_greenfield_coordinate_rules_summary.csv"
    report_md_path = reports_dir / f"postal_code_geolocation_greenfield_coordinate_rules_{run_id}.md"
    report_json_path = reports_dir / f"postal_code_geolocation_greenfield_coordinate_rules_{run_id}.json"

    write_csv(rules_path, rows, RULE_FIELDS)
    write_csv(summary_path, flatten_summary(summary), SUMMARY_FIELDS)
    write_json(
        report_json_path,
        {
            "run_id": run_id,
            "outputs": {"rules": str(rules_path), "summary": str(summary_path)},
            "boundary_snapshot": str(boundary_path),
            "summary": summary,
        },
    )
    report_md_path.write_text(markdown_report(run_id, rules_path, summary_path, summary, rows))

    print(f"wrote {rules_path}")
    print(f"wrote {summary_path}")
    print(f"wrote {report_md_path}")
    print(f"wrote {report_json_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
