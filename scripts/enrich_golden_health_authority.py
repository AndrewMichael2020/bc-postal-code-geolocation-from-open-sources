#!/usr/bin/env python3
"""Enrich the golden postal-code table with official BC Health Authority boundaries."""

from __future__ import annotations

import argparse
import json
import ssl
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from math import floor
from pathlib import Path
from typing import Any

import certifi

try:
    from shapely.geometry import Point, shape
except ImportError:  # pragma: no cover - exercised when optional GIS dependency is absent.
    Point = None
    shape = None

from postal_source_utils import (
    ROOT,
    default_run_id,
    ensure_dir,
    normalize_postal_code,
    read_csv,
    to_float,
    write_csv,
    write_json,
)


DEFAULT_GOLDEN = ROOT / "outputs/geolocation/postal_code_geolocation_golden.csv"
DEFAULT_OUTPUTS_DIR = ROOT / "outputs/geolocation"
DEFAULT_REPORTS_DIR = ROOT / "reports/geolocation"
DEFAULT_WORK_ROOT = ROOT / "work/postal_reconstruction"
BC_CATALOGUE_URL = "https://catalogue.data.gov.bc.ca/dataset/7bc6018f-bb4f-4e5d-845e-c529e3d1ac3b"
BC_WFS_URL = "https://openmaps.gov.bc.ca/geo/pub/WHSE_ADMIN_BOUNDARIES.BCHA_HEALTH_AUTHORITY_BNDRY_SP/ows"
BC_LAYER_NAME = "pub:WHSE_ADMIN_BOUNDARIES.BCHA_HEALTH_AUTHORITY_BNDRY_SP"
SOURCE_LICENSE = "Open Government Licence - British Columbia"
BOUNDARY_CONFIGURATION = "2022 boundary configuration"

ENRICHED_FIELDS = [
    "PostalCodeID",
    "postal_code",
    "latitude",
    "longitude",
    "health_authority",
    "health_authority_code",
    "health_authority_id",
    "health_authority_method",
    "health_authority_confidence",
    "health_authority_source",
    "health_authority_source_url",
    "health_authority_boundary_configuration",
    "health_authority_assignment_notes",
]
FSA_FIELDS = [
    "fsa",
    "postal_code_count",
    "health_authority_count",
    "dominant_health_authority",
    "dominant_count",
    "dominant_share",
    "classification",
    "fsa_prefix_rule_status",
    "fsa_prefix_rule_disposition",
    "fsa_prefix_rule_notes",
    "health_authority_counts",
]
SUMMARY_FIELDS = ["metric", "value"]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--golden", default=str(DEFAULT_GOLDEN))
    parser.add_argument("--outputs-dir", default=str(DEFAULT_OUTPUTS_DIR))
    parser.add_argument("--reports-dir", default=str(DEFAULT_REPORTS_DIR))
    parser.add_argument("--work-root", default=str(DEFAULT_WORK_ROOT))
    parser.add_argument("--run-id", default=default_run_id())
    parser.add_argument("--refresh-boundaries", action="store_true")
    return parser.parse_args()


def boundary_url() -> str:
    query = urllib.parse.urlencode(
        {
            "service": "WFS",
            "version": "2.0.0",
            "request": "GetFeature",
            "typeNames": BC_LAYER_NAME,
            "srsName": "EPSG:4326",
            "outputFormat": "application/json",
        }
    )
    return f"{BC_WFS_URL}?{query}"


def download_boundaries(path: Path) -> dict[str, Any]:
    ensure_dir(path.parent)
    context = ssl.create_default_context(cafile=certifi.where())
    with urllib.request.urlopen(boundary_url(), context=context, timeout=120) as response:
        payload = response.read()
    path.write_bytes(payload)
    return json.loads(payload)


def load_boundaries(path: Path, refresh: bool) -> dict[str, Any]:
    if refresh or not path.exists():
        return download_boundaries(path)
    return json.loads(path.read_text())


def health_name(properties: dict[str, Any]) -> str:
    raw = properties.get("HLTH_AUTHORITY_NAME", "")
    mapping = {
        "Interior": "Interior Health",
        "Fraser": "Fraser Health",
        "Vancouver Coastal": "Vancouver Coastal Health",
        "Vancouver Island": "Vancouver Island Health",
        "Northern": "Northern Health",
    }
    return mapping.get(raw, raw)


def iter_polygons(geometry: dict[str, Any]) -> list[list[list[float]]]:
    if geometry.get("type") == "Polygon":
        return [geometry.get("coordinates", [])]
    if geometry.get("type") == "MultiPolygon":
        return [rings for polygon in geometry.get("coordinates", []) for rings in [polygon]]
    return []


def ring_bbox(ring: list[list[float]]) -> tuple[float, float, float, float]:
    lons = [point[0] for point in ring]
    lats = [point[1] for point in ring]
    return min(lons), min(lats), max(lons), max(lats)


def bbox_contains(bbox: tuple[float, float, float, float], lon: float, lat: float) -> bool:
    min_lon, min_lat, max_lon, max_lat = bbox
    return min_lon <= lon <= max_lon and min_lat <= lat <= max_lat


def point_in_ring(lon: float, lat: float, ring: list[list[float]]) -> bool:
    inside = False
    if len(ring) < 4:
        return False
    previous_lon, previous_lat = ring[-1][0], ring[-1][1]
    for point in ring:
        current_lon, current_lat = point[0], point[1]
        crosses = (current_lat > lat) != (previous_lat > lat)
        if crosses:
            x_intersect = (previous_lon - current_lon) * (lat - current_lat) / (
                previous_lat - current_lat
            ) + current_lon
            if lon < x_intersect:
                inside = not inside
        previous_lon, previous_lat = current_lon, current_lat
    return inside


def point_in_polygon(lon: float, lat: float, rings: list[list[list[float]]]) -> bool:
    if not rings:
        return False
    if not bbox_contains(ring_bbox(rings[0]), lon, lat):
        return False
    if not point_in_ring(lon, lat, rings[0]):
        return False
    return not any(point_in_ring(lon, lat, hole) for hole in rings[1:])


def prepared_features(boundaries: dict[str, Any]) -> list[dict[str, Any]]:
    features = []
    for feature in boundaries.get("features", []):
        properties = feature.get("properties", {})
        polygons = []
        for rings in iter_polygons(feature.get("geometry", {})):
            if rings:
                polygons.append({"rings": rings, "bbox": ring_bbox(rings[0])})
        features.append(
            {
                "health_authority": health_name(properties),
                "code": str(properties.get("HLTH_AUTHORITY_CODE", "")),
                "authority_id": properties.get("HLTH_AUTHORITY_ID", ""),
                "polygons": polygons,
                "geometry": shape(feature.get("geometry", {})) if shape is not None else None,
            }
        )
    return features


def build_spatial_index(
    features: list[dict[str, Any]],
    cell_size: float = 0.5,
) -> dict[tuple[int, int], list[dict[str, Any]]]:
    index: dict[tuple[int, int], list[dict[str, Any]]] = defaultdict(list)
    for feature in features:
        for polygon in feature["polygons"]:
            min_lon, min_lat, max_lon, max_lat = polygon["bbox"]
            min_x = floor(min_lon / cell_size)
            max_x = floor(max_lon / cell_size)
            min_y = floor(min_lat / cell_size)
            max_y = floor(max_lat / cell_size)
            entry = {
                "health_authority": feature["health_authority"],
                "code": feature["code"],
                "authority_id": feature["authority_id"],
                "rings": polygon["rings"],
                "bbox": polygon["bbox"],
            }
            for x in range(min_x, max_x + 1):
                for y in range(min_y, max_y + 1):
                    index[(x, y)].append(entry)
    return index


def spatial_candidates(
    features: list[dict[str, Any]],
    lon: float,
    lat: float,
    spatial_index: dict[tuple[int, int], list[dict[str, Any]]] | None = None,
    cell_size: float = 0.5,
) -> list[dict[str, Any]]:
    if spatial_index is None:
        candidates = []
        for feature in features:
            for polygon in feature["polygons"]:
                candidates.append(
                    {
                        "health_authority": feature["health_authority"],
                        "code": feature["code"],
                        "authority_id": feature["authority_id"],
                        "rings": polygon["rings"],
                        "bbox": polygon["bbox"],
                    }
                )
        return candidates
    return spatial_index.get((floor(lon / cell_size), floor(lat / cell_size)), [])


def assign_health_authority(
    latitude: str,
    longitude: str,
    features: list[dict[str, Any]],
    spatial_index: dict[tuple[int, int], list[dict[str, Any]]] | None = None,
) -> dict[str, str]:
    lat = to_float(latitude)
    lon = to_float(longitude)
    if lat is None or lon is None:
        return {
            "health_authority": "",
            "health_authority_code": "",
            "health_authority_id": "",
            "health_authority_method": "official_boundary_not_assigned",
            "health_authority_confidence": "unassigned_invalid_coordinate",
            "health_authority_assignment_notes": "Coordinate was missing or invalid.",
        }

    if Point is not None and features and features[0].get("geometry") is not None:
        point = Point(lon, lat)
        matches = [feature for feature in features if feature["geometry"].covers(point)]
        if len(matches) == 1:
            match = matches[0]
            return {
                "health_authority": match["health_authority"],
                "health_authority_code": match["code"],
                "health_authority_id": match["authority_id"],
                "health_authority_method": "official_bc_ha_boundary_point_in_polygon",
                "health_authority_confidence": "official_boundary_match",
                "health_authority_assignment_notes": (
                    "Assigned by placing the postal-code coordinate inside the official BC Health "
                    "Authority boundary polygon."
                ),
            }
        if not matches:
            return {
                "health_authority": "",
                "health_authority_code": "",
                "health_authority_id": "",
                "health_authority_method": "official_boundary_not_assigned",
                "health_authority_confidence": "unassigned_outside_boundary",
                "health_authority_assignment_notes": (
                    "Coordinate did not fall inside any official BC Health Authority boundary polygon."
                ),
            }
        return {
            "health_authority": ";".join(match["health_authority"] for match in matches),
            "health_authority_code": ";".join(match["code"] for match in matches),
            "health_authority_id": ";".join(match["authority_id"] for match in matches),
            "health_authority_method": "official_boundary_multiple_matches",
            "health_authority_confidence": "ambiguous_boundary_overlap",
            "health_authority_assignment_notes": (
                "Coordinate matched more than one official boundary polygon and needs manual review."
            ),
        }

    matches = []
    seen_authorities = set()
    for candidate in spatial_candidates(features, lon, lat, spatial_index):
        if not bbox_contains(candidate["bbox"], lon, lat):
            continue
        if point_in_polygon(lon, lat, candidate["rings"]):
            key = (candidate["health_authority"], candidate["code"], candidate["authority_id"])
            if key not in seen_authorities:
                matches.append(candidate)
                seen_authorities.add(key)

    if len(matches) == 1:
        match = matches[0]
        return {
            "health_authority": match["health_authority"],
            "health_authority_code": match["code"],
            "health_authority_id": match["authority_id"],
            "health_authority_method": "official_bc_ha_boundary_point_in_polygon",
            "health_authority_confidence": "official_boundary_match",
            "health_authority_assignment_notes": (
                "Assigned by placing the postal-code coordinate inside the official BC Health "
                "Authority boundary polygon."
            ),
        }
    if not matches:
        return {
            "health_authority": "",
            "health_authority_code": "",
            "health_authority_id": "",
            "health_authority_method": "official_boundary_not_assigned",
            "health_authority_confidence": "unassigned_outside_boundary",
            "health_authority_assignment_notes": (
                "Coordinate did not fall inside any official BC Health Authority boundary polygon."
            ),
        }
    return {
        "health_authority": ";".join(match["health_authority"] for match in matches),
        "health_authority_code": ";".join(match["code"] for match in matches),
        "health_authority_id": ";".join(match["authority_id"] for match in matches),
        "health_authority_method": "official_boundary_multiple_matches",
        "health_authority_confidence": "ambiguous_boundary_overlap",
        "health_authority_assignment_notes": (
            "Coordinate matched more than one official boundary polygon and needs manual review."
        ),
    }


def fsa_for(postal_code: str) -> str:
    normalized = normalize_postal_code(postal_code)
    return normalized.replace(" ", "")[:3] if normalized else ""


def build_fsa_diagnostics(enriched_rows: list[dict[str, str]]) -> list[dict[str, Any]]:
    counts: dict[str, Counter[str]] = defaultdict(Counter)
    for row in enriched_rows:
        fsa = fsa_for(row["postal_code"])
        if fsa and row.get("health_authority"):
            counts[fsa][row["health_authority"]] += 1

    diagnostics = []
    for fsa in sorted(counts):
        total = sum(counts[fsa].values())
        dominant, dominant_count = counts[fsa].most_common(1)[0]
        share = dominant_count / total if total else 0.0
        if len(counts[fsa]) == 1:
            classification = "unambiguous_fsa_in_current_golden_coordinates"
            rule_status = "diagnostic_only_unambiguous_in_current_coordinates"
            disposition = "do_not_treat_as_authoritative_without_boundary_assignment"
            notes = (
                "The current coordinates all land in one HA, but this workflow still assigns HA by "
                "official boundary overlay rather than by FSA prefix."
            )
        elif share >= 0.95:
            classification = "mostly_single_ha_fsa_but_mixed"
            rule_status = "discard_as_trash_mixed_fsa"
            disposition = "do_not_use_for_health_authority_assignment"
            notes = (
                "This FSA contains postal-code coordinates in more than one HA. Any prefix-only HA "
                "mapping for this FSA is unsafe and must be discarded."
            )
        else:
            classification = "mixed_ha_fsa_do_not_use_prefix_rule"
            rule_status = "discard_as_trash_mixed_fsa"
            disposition = "do_not_use_for_health_authority_assignment"
            notes = (
                "This FSA materially spans multiple HAs. Any prefix-only HA mapping for this FSA is "
                "trash for this workflow."
            )
        diagnostics.append(
            {
                "fsa": fsa,
                "postal_code_count": total,
                "health_authority_count": len(counts[fsa]),
                "dominant_health_authority": dominant,
                "dominant_count": dominant_count,
                "dominant_share": f"{share:.6f}",
                "classification": classification,
                "fsa_prefix_rule_status": rule_status,
                "fsa_prefix_rule_disposition": disposition,
                "fsa_prefix_rule_notes": notes,
                "health_authority_counts": json.dumps(dict(counts[fsa]), sort_keys=True),
            }
        )
    return diagnostics


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
    enriched_path: Path,
    fsa_path: Path,
    fsa_trash_path: Path,
    summary_path: Path,
    boundary_path: Path,
    summary: dict[str, Any],
    fsa_rows: list[dict[str, Any]],
    unmatched_rows: list[dict[str, str]],
) -> str:
    mixed = [row for row in fsa_rows if row["classification"] == "mixed_ha_fsa_do_not_use_prefix_rule"]
    mostly = [row for row in fsa_rows if row["classification"] == "mostly_single_ha_fsa_but_mixed"]
    lines = [
        "# Golden Postal-Code Health Authority Enrichment",
        "",
        f"Run ID: `{run_id}`",
        "",
        "## Source Rule",
        "",
        (
            "- Health authority is assigned by official BC Health Authority boundary point-in-polygon, "
            "not by parsing postal-code prefixes."
        ),
        f"- Boundary source: {BC_CATALOGUE_URL}",
        f"- WFS source: {BC_WFS_URL}",
        f"- Licence: {SOURCE_LICENSE}",
        f"- Boundary configuration: {BOUNDARY_CONFIGURATION}",
        "",
        "## Outputs",
        "",
        f"- Enriched golden table: `{enriched_path}`",
        f"- FSA ambiguity diagnostics: `{fsa_path}`",
        f"- FSA prefix-rule trash list: `{fsa_trash_path}`",
        f"- Summary table: `{summary_path}`",
        f"- Raw boundary GeoJSON snapshot: `{boundary_path}`",
        "",
        "## Assignment Summary",
        "",
        f"- Golden rows processed: {summary['golden_rows']:,}",
        f"- Official boundary matches: {summary['assignment_counts'].get('official_boundary_match', 0):,}",
        f"- Unassigned/outside boundary: {summary['assignment_counts'].get('unassigned_outside_boundary', 0):,}",
        f"- Ambiguous boundary overlaps: {summary['assignment_counts'].get('ambiguous_boundary_overlap', 0):,}",
        f"- FSA prefix rules discarded as trash: {summary['discarded_fsa_prefix_rule_count']:,}",
        "",
        "## Health Authority Counts",
        "",
    ]
    for health, count in sorted(summary["health_authority_counts"].items()):
        lines.append(f"- {health or '(unassigned)'}: {count:,}")

    lines.extend(
        [
            "",
            "## Why Postal-Code Prefix Rules Are Unsafe",
            "",
            (
                "FSAs are useful for triage, but they are not an authoritative HA key. In the current "
                "coordinate-based golden set, these FSAs span multiple Health Authorities and their "
                "prefix-only HA rules are discarded as trash:"
            ),
            "",
            *markdown_table(
                mixed[:25],
                [
                    ("FSA", "fsa"),
                    ("Dominant HA", "dominant_health_authority"),
                    ("Share", "dominant_share"),
                    ("Counts", "health_authority_counts"),
                ],
            ),
            "",
            "Mostly-single but still mixed FSAs:",
            "",
            *markdown_table(
                mostly[:25],
                [
                    ("FSA", "fsa"),
                    ("Dominant HA", "dominant_health_authority"),
                    ("Share", "dominant_share"),
                    ("Counts", "health_authority_counts"),
                ],
            ),
            "",
            "## Unmatched Rows: First 25",
            "",
            *markdown_table(
                unmatched_rows[:25],
                [
                    ("Postal code", "postal_code"),
                    ("Latitude", "latitude"),
                    ("Longitude", "longitude"),
                    ("Confidence", "health_authority_confidence"),
                ],
            ),
            "",
            "## Reproducibility",
            "",
            "```bash",
            "python3 scripts/enrich_golden_health_authority.py --refresh-boundaries",
            "```",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> int:
    args = parse_args()
    run_id = args.run_id
    outputs_dir = ensure_dir(Path(args.outputs_dir))
    reports_dir = ensure_dir(Path(args.reports_dir))
    run_work_dir = ensure_dir(Path(args.work_root) / run_id)
    boundary_path = run_work_dir / "bc_health_authority_boundaries_2022.geojson"
    boundaries = load_boundaries(boundary_path, args.refresh_boundaries)
    features = prepared_features(boundaries)
    spatial_index = build_spatial_index(features)

    golden_rows = read_csv(Path(args.golden))
    enriched_rows: list[dict[str, str]] = []
    for row in golden_rows:
        assignment = assign_health_authority(
            row.get("latitude", ""),
            row.get("longitude", ""),
            features,
            spatial_index,
        )
        enriched_rows.append(
            {
                **{field: row.get(field, "") for field in ["PostalCodeID", "postal_code", "latitude", "longitude"]},
                **assignment,
                "health_authority_source": "BC Data Catalogue / DataBC WFS Health Authority Boundaries",
                "health_authority_source_url": BC_CATALOGUE_URL,
                "health_authority_boundary_configuration": BOUNDARY_CONFIGURATION,
            }
        )

    fsa_rows = build_fsa_diagnostics(enriched_rows)
    assignment_counts = Counter(row["health_authority_confidence"] for row in enriched_rows)
    health_counts = Counter(row["health_authority"] for row in enriched_rows)
    summary = {
        "golden_rows": len(golden_rows),
        "enriched_rows": len(enriched_rows),
        "boundary_feature_count": len(features),
        "fsa_count": len(fsa_rows),
        "mixed_fsa_count": sum(
            1 for row in fsa_rows if row["classification"] == "mixed_ha_fsa_do_not_use_prefix_rule"
        ),
        "mostly_single_but_mixed_fsa_count": sum(
            1 for row in fsa_rows if row["classification"] == "mostly_single_ha_fsa_but_mixed"
        ),
        "discarded_fsa_prefix_rule_count": sum(
            1 for row in fsa_rows if row["fsa_prefix_rule_status"] == "discard_as_trash_mixed_fsa"
        ),
        "assignment_counts": dict(assignment_counts),
        "health_authority_counts": dict(health_counts),
        "source": {
            "catalogue_url": BC_CATALOGUE_URL,
            "wfs_url": BC_WFS_URL,
            "license": SOURCE_LICENSE,
            "boundary_configuration": BOUNDARY_CONFIGURATION,
        },
    }

    enriched_path = outputs_dir / "postal_code_geolocation_golden_with_health_authority.csv"
    fsa_path = outputs_dir / "postal_code_geolocation_health_authority_fsa_diagnostics.csv"
    fsa_trash_path = outputs_dir / "postal_code_geolocation_health_authority_fsa_trash.csv"
    summary_path = outputs_dir / "postal_code_geolocation_health_authority_summary.csv"
    report_md_path = reports_dir / f"postal_code_geolocation_health_authority_{run_id}.md"
    report_json_path = reports_dir / f"postal_code_geolocation_health_authority_{run_id}.json"

    write_csv(enriched_path, enriched_rows, ENRICHED_FIELDS)
    write_csv(fsa_path, fsa_rows, FSA_FIELDS)
    fsa_trash_rows = [
        row for row in fsa_rows if row["fsa_prefix_rule_status"] == "discard_as_trash_mixed_fsa"
    ]
    write_csv(fsa_trash_path, fsa_trash_rows, FSA_FIELDS)
    write_csv(summary_path, flatten_summary(summary), SUMMARY_FIELDS)
    write_json(
        report_json_path,
        {
            "run_id": run_id,
            "inputs": {"golden": str(Path(args.golden)), "boundary_snapshot": str(boundary_path)},
            "outputs": {
                "enriched": str(enriched_path),
                "fsa_diagnostics": str(fsa_path),
                "fsa_trash": str(fsa_trash_path),
                "summary": str(summary_path),
            },
            "summary": summary,
        },
    )
    unmatched = [
        row for row in enriched_rows if row["health_authority_confidence"] != "official_boundary_match"
    ]
    report_md_path.write_text(
        markdown_report(
            run_id,
            enriched_path,
            fsa_path,
            fsa_trash_path,
            summary_path,
            boundary_path,
            summary,
            fsa_rows,
            unmatched,
        )
    )

    print(f"wrote {enriched_path}")
    print(f"wrote {fsa_path}")
    print(f"wrote {fsa_trash_path}")
    print(f"wrote {summary_path}")
    print(f"wrote {report_md_path}")
    print(f"wrote {report_json_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
