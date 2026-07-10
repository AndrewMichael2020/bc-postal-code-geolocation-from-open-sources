#!/usr/bin/env python3
"""Build compact GitHub Pages assets for the Fraser Health OSRM demo."""

from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = ROOT / "outputs" / "fha_golden_distances_times.csv"
DEFAULT_OUTPUT = ROOT / "demo" / "data" / "fha-home-health-demo.json"

DEFAULT_LABOR_COST_PER_HOUR = 60.0
DEFAULT_GAS_PRICE_PER_LITRE = 1.70
DEFAULT_FUEL_CONSUMPTION_L_PER_100KM = 11.5
DEFAULT_MAINTENANCE_COST_PER_KM = 0.07
DEFAULT_TOP_CANDIDATES = 8

FACILITY_COLORS = [
    "#2563eb",
    "#dc2626",
    "#16a34a",
    "#7c3aed",
    "#0891b2",
    "#ea580c",
    "#0f766e",
    "#be123c",
    "#4f46e5",
    "#15803d",
    "#a16207",
    "#9333ea",
    "#0284c7",
    "#c2410c",
    "#0d9488",
    "#b91c1c",
    "#4338ca",
    "#65a30d",
    "#0369a1",
    "#db2777",
    "#7c2d12",
    "#047857",
    "#1d4ed8",
    "#a21caf",
    "#ca8a04",
    "#475569",
    "#14b8a6",
]

WARNING_CATALOG = [
    "snap warning",
    "long route",
    "very long route",
    "slow route",
    "very slow route",
    "high circuity",
    "forest/service road",
    "wilderness access",
    "terrain warning",
    "route detail warning",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--top-candidates", type=int, default=DEFAULT_TOP_CANDIDATES)
    parser.add_argument("--labor-cost-per-hour", type=float, default=DEFAULT_LABOR_COST_PER_HOUR)
    parser.add_argument("--gas-price-per-litre", type=float, default=DEFAULT_GAS_PRICE_PER_LITRE)
    parser.add_argument(
        "--fuel-consumption-l-per-100km",
        type=float,
        default=DEFAULT_FUEL_CONSUMPTION_L_PER_100KM,
    )
    parser.add_argument("--maintenance-cost-per-km", type=float, default=DEFAULT_MAINTENANCE_COST_PER_KM)
    return parser.parse_args()


def as_float(value: str | None) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except ValueError:
        return None


def route_cost(duration_min: float, distance_km: float, args: argparse.Namespace) -> float:
    labor_cost_per_minute = args.labor_cost_per_hour / 60.0
    vehicle_cost_per_km = (
        args.gas_price_per_litre * args.fuel_consumption_l_per_100km / 100.0
        + args.maintenance_cost_per_km
    )
    return duration_min * labor_cost_per_minute + distance_km * vehicle_cost_per_km


def warning_indexes(row: dict[str, str]) -> list[int]:
    warnings: set[str] = set()
    duration = as_float(row.get("duration_min"))
    origin_snap = as_float(row.get("origin_snap_m"))
    destination_snap = as_float(row.get("destination_snap_m"))
    avg_speed = as_float(row.get("avg_speed_kmh"))
    circuity = as_float(row.get("route_circuity_ratio"))
    profile = " ".join(
        row.get(field, "")
        for field in (
            "access_profile_enriched",
            "access_signals_enriched",
            "route_reasons",
            "route_flags",
            "road_names",
            "road_refs",
            "terrain_flags",
            "road_1km_terrain_flags",
            "road_2km_terrain_flags",
        )
    ).lower()

    if (origin_snap is not None and origin_snap > 500) or (
        destination_snap is not None and destination_snap > 500
    ):
        warnings.add("snap warning")
    if duration is not None and duration > 120:
        warnings.add("very long route")
    elif duration is not None and duration > 90:
        warnings.add("long route")
    if avg_speed is not None and avg_speed < 10:
        warnings.add("very slow route")
    elif avg_speed is not None and avg_speed < 20:
        warnings.add("slow route")
    if circuity is not None and circuity > 2:
        warnings.add("high circuity")
    if "fsr" in profile or "forest" in profile or "service road" in profile:
        warnings.add("forest/service road")
    if "wilderness" in profile or "remote access" in profile:
        warnings.add("wilderness access")
    if "steep" in profile or "mountain" in profile or "terrain" in profile:
        warnings.add("terrain warning")
    if row.get("route_detail_needed", "").lower() == "true":
        warnings.add("route detail warning")

    return [WARNING_CATALOG.index(item) for item in WARNING_CATALOG if item in warnings]


def compact_postal(row: dict[str, str]) -> list[Any]:
    return [
        row["PostalCodeID"],
        row["postal_code"],
        round(float(row["latitude"]), 6),
        round(float(row["longitude"]), 6),
    ]


def compact_facility(row: dict[str, str], color: str) -> list[Any]:
    return [
        row["facility_id"],
        row["facility_name"],
        row["facility_type"],
        row["facility_address"],
        round(float(row["facility_latitude"]), 6),
        round(float(row["facility_longitude"]), 6),
        color,
    ]


def build_asset(args: argparse.Namespace) -> dict[str, Any]:
    if args.top_candidates < 1:
        raise ValueError("--top-candidates must be at least 1")
    postal_rows: dict[str, list[Any]] = {}
    routes_by_postal: dict[str, list[tuple[float, str, float, float, list[int]]]] = defaultdict(list)
    facility_source_rows: dict[str, dict[str, str]] = {}
    source_rows = 0
    usable_rows = 0

    with args.source.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        required = {
            "PostalCodeID",
            "postal_code",
            "latitude",
            "longitude",
            "facility_id",
            "facility_name",
            "facility_type",
            "facility_address",
            "facility_latitude",
            "facility_longitude",
            "duration_min",
            "distance_km",
        }
        missing = sorted(required - set(reader.fieldnames or []))
        if missing:
            raise ValueError(f"Missing required columns: {', '.join(missing)}")

        for row in reader:
            source_rows += 1
            duration = as_float(row.get("duration_min"))
            distance = as_float(row.get("distance_km"))
            if duration is None or distance is None:
                continue
            usable_rows += 1
            postal_id = row["PostalCodeID"]
            facility_id = row["facility_id"]
            postal_rows.setdefault(postal_id, compact_postal(row))
            facility_source_rows.setdefault(facility_id, row)

            candidate = (
                route_cost(duration, distance, args),
                facility_id,
                round(duration, 2),
                round(distance, 2),
                warning_indexes(row),
            )
            candidates = routes_by_postal[postal_id]
            if len(candidates) < args.top_candidates:
                candidates.append(candidate)
            else:
                worst_index, worst = max(enumerate(candidates), key=lambda item: item[1][0])
                if candidate[0] < worst[0]:
                    candidates[worst_index] = candidate

    facility_ids = sorted(facility_source_rows)
    facility_index = {facility_id: index for index, facility_id in enumerate(facility_ids)}
    facilities = [
        compact_facility(facility_source_rows[facility_id], FACILITY_COLORS[index % len(FACILITY_COLORS)])
        for index, facility_id in enumerate(facility_ids)
    ]
    postal_ids = sorted(postal_rows, key=lambda item: postal_rows[item][1])
    postal_codes = [postal_rows[postal_id] for postal_id in postal_ids]
    route_candidates = []
    for postal_id in postal_ids:
        candidates = sorted(routes_by_postal[postal_id], key=lambda item: item[0])
        route_candidates.append(
            [
                [facility_index[facility_id], duration, distance, warnings]
                for _, facility_id, duration, distance, warnings in candidates
            ]
        )

    return {
        "schemaVersion": 1,
        "generatedAt": datetime.now(UTC).replace(microsecond=0).isoformat(),
        "source": {
            "path": "outputs/fha_golden_distances_times.csv",
            "sourceRows": source_rows,
            "usableRows": usable_rows,
            "topCandidatesPerPostalCode": args.top_candidates,
        },
        "defaults": {
            "laborCostPerHour": args.labor_cost_per_hour,
            "gasPricePerLitre": args.gas_price_per_litre,
            "fuelConsumptionLPer100Km": args.fuel_consumption_l_per_100km,
            "maintenanceCostPerKm": args.maintenance_cost_per_km,
            "visitsPerPostalCode": 0.05,
            "visitDurationMin": 45,
            "capacityHoursPerFacility": 110,
            "maxExtraTravelMin": 10,
            "maxExtraDistanceKm": 10,
            "maxRelativeCostPenalty": 0.25,
        },
        "warningCatalog": WARNING_CATALOG,
        "columns": {
            "postalCodes": ["PostalCodeID", "postal_code", "latitude", "longitude"],
            "facilities": [
                "facility_id",
                "facility_name",
                "facility_type",
                "facility_address",
                "latitude",
                "longitude",
                "color",
            ],
            "candidates": ["facility_index", "duration_min", "distance_km", "warning_indexes"],
        },
        "postalCodes": postal_codes,
        "facilities": facilities,
        "candidates": route_candidates,
    }


def main() -> None:
    args = parse_args()
    asset = build_asset(args)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(asset, separators=(",", ":"), ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"wrote {args.output.relative_to(ROOT)}")
    print(f"postal codes: {len(asset['postalCodes']):,}")
    print(f"facilities: {len(asset['facilities']):,}")
    print(f"route candidates: {sum(len(item) for item in asset['candidates']):,}")


if __name__ == "__main__":
    main()
