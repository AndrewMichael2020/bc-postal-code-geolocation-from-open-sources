#!/usr/bin/env python3
"""Generate compact static assets for the Lower Mainland allocation demo."""

from __future__ import annotations

import csv
import json
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE_CSV = ROOT / "data" / "bc_postal_codes_geolocated.csv"
DEMO_DATA_DIR = ROOT / "demo" / "data"
POSTAL_OUTPUT = DEMO_DATA_DIR / "lower-mainland-postal-codes.json"
FSA_CLUSTER_OUTPUT = DEMO_DATA_DIR / "lower-mainland-fsa-clusters.json"
HUBS_OUTPUT = DEMO_DATA_DIR / "service-hubs.json"
SUMMARY_OUTPUT = DEMO_DATA_DIR / "demo-summary.json"

LOWER_MAINLAND_BOUNDS = {
    "min_lat": 49.0,
    "max_lat": 49.9,
    "min_lon": -123.55,
    "max_lon": -121.15,
}

SERVICE_HUBS = [
    {
        "id": "vancouver",
        "name": "Vancouver Central",
        "municipality": "Vancouver",
        "latitude": 49.2827,
        "longitude": -123.1207,
        "capacity": 9800,
        "color": "#2563eb",
    },
    {
        "id": "surrey",
        "name": "Surrey South Fraser",
        "municipality": "Surrey",
        "latitude": 49.1044,
        "longitude": -122.8011,
        "capacity": 10400,
        "color": "#dc2626",
    },
    {
        "id": "burnaby",
        "name": "Burnaby East Metro",
        "municipality": "Burnaby",
        "latitude": 49.2488,
        "longitude": -122.9805,
        "capacity": 8600,
        "color": "#7c3aed",
    },
    {
        "id": "abbotsford",
        "name": "Abbotsford Fraser Valley",
        "municipality": "Abbotsford",
        "latitude": 49.0504,
        "longitude": -122.3045,
        "capacity": 7600,
        "color": "#0891b2",
    },
    {
        "id": "richmond",
        "name": "Richmond Delta",
        "municipality": "Richmond",
        "latitude": 49.1666,
        "longitude": -123.1336,
        "capacity": 6800,
        "color": "#16a34a",
    },
    {
        "id": "north_vancouver",
        "name": "North Shore",
        "municipality": "North Vancouver",
        "latitude": 49.3200,
        "longitude": -123.0730,
        "capacity": 5200,
        "color": "#ea580c",
    },
    {
        "id": "chilliwack",
        "name": "Chilliwack East Valley",
        "municipality": "Chilliwack",
        "latitude": 49.1579,
        "longitude": -121.9515,
        "capacity": 6200,
        "color": "#0f766e",
    },
]


def in_bounds(latitude: float, longitude: float) -> bool:
    return (
        LOWER_MAINLAND_BOUNDS["min_lat"] <= latitude <= LOWER_MAINLAND_BOUNDS["max_lat"]
        and LOWER_MAINLAND_BOUNDS["min_lon"] <= longitude <= LOWER_MAINLAND_BOUNDS["max_lon"]
    )


def demand_segment(fsa: str, latitude: float, longitude: float) -> str:
    urban_prefixes = {
        "V5A",
        "V5B",
        "V5C",
        "V5G",
        "V5H",
        "V5J",
        "V5K",
        "V5L",
        "V5M",
        "V5N",
        "V5P",
        "V5R",
        "V5S",
        "V5T",
        "V5V",
        "V5W",
        "V5X",
        "V5Y",
        "V5Z",
        "V6A",
        "V6B",
        "V6C",
        "V6E",
        "V6G",
        "V6H",
        "V6J",
        "V6K",
        "V6L",
        "V6M",
        "V6N",
        "V6P",
        "V6R",
        "V6S",
        "V6T",
        "V6V",
        "V6W",
        "V6X",
        "V6Y",
        "V6Z",
        "V7A",
        "V7B",
        "V7C",
        "V7E",
        "V7G",
        "V7H",
        "V7J",
        "V7K",
        "V7L",
        "V7M",
        "V7N",
        "V7P",
        "V7R",
        "V7S",
        "V7T",
        "V7V",
        "V7W",
    }
    if fsa in urban_prefixes:
        return "urban"
    if latitude > 49.45 or longitude > -121.95 or fsa.startswith(("V0M", "V0N")):
        return "rural"
    return "suburban"


def read_lower_mainland_rows() -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    with SOURCE_CSV.open(newline="", encoding="utf-8") as source:
        reader = csv.DictReader(source)
        for source_row in reader:
            postal_code = source_row["postal_code"].strip()
            latitude = float(source_row["latitude"])
            longitude = float(source_row["longitude"])
            if not in_bounds(latitude, longitude):
                continue
            fsa = postal_code[:3]
            rows.append(
                {
                    "id": source_row["PostalCodeID"],
                    "postal_code": postal_code,
                    "fsa": fsa,
                    "latitude": round(latitude, 6),
                    "longitude": round(longitude, 6),
                    "segment": demand_segment(fsa, latitude, longitude),
                }
            )
    return sorted(rows, key=lambda row: str(row["postal_code"]))


def build_fsa_clusters(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    clusters: dict[str, dict[str, object]] = {}
    for row in rows:
        fsa = str(row["fsa"])
        cluster = clusters.setdefault(
            fsa,
            {
                "fsa": fsa,
                "postalCodeCount": 0,
                "latitudeTotal": 0.0,
                "longitudeTotal": 0.0,
                "segments": {"urban": 0, "suburban": 0, "rural": 0},
            },
        )
        cluster["postalCodeCount"] = int(cluster["postalCodeCount"]) + 1
        cluster["latitudeTotal"] = float(cluster["latitudeTotal"]) + float(row["latitude"])
        cluster["longitudeTotal"] = float(cluster["longitudeTotal"]) + float(row["longitude"])
        segments = cluster["segments"]
        assert isinstance(segments, dict)
        segment = str(row["segment"])
        segments[segment] = int(segments[segment]) + 1

    output = []
    for cluster in clusters.values():
        count = int(cluster["postalCodeCount"])
        output.append(
            {
                "demand": 0,
                "fsa": cluster["fsa"],
                "latitude": round(float(cluster["latitudeTotal"]) / count, 6),
                "latitudeTotal": round(float(cluster["latitudeTotal"]), 6),
                "longitude": round(float(cluster["longitudeTotal"]) / count, 6),
                "longitudeTotal": round(float(cluster["longitudeTotal"]), 6),
                "postalCodeCount": count,
                "segments": cluster["segments"],
            }
        )
    return sorted(output, key=lambda row: str(row["fsa"]))


def write_json(path: Path, payload: object, *, compact: bool = False) -> None:
    if compact:
        serialized = json.dumps(payload, separators=(",", ":"), sort_keys=True)
    else:
        serialized = json.dumps(payload, indent=2, sort_keys=True)
    path.write_text(serialized + "\n", encoding="utf-8")


def main() -> None:
    DEMO_DATA_DIR.mkdir(parents=True, exist_ok=True)
    rows = read_lower_mainland_rows()
    fsa_clusters = build_fsa_clusters(rows)
    fsa_counts = Counter(str(row["fsa"]) for row in rows)
    segment_counts = Counter(str(row["segment"]) for row in rows)
    summary = {
        "source": "data/bc_postal_codes_geolocated.csv",
        "bounds": LOWER_MAINLAND_BOUNDS,
        "postal_code_count": len(rows),
        "fsa_count": len(fsa_counts),
        "segment_counts": dict(sorted(segment_counts.items())),
        "top_fsa_counts": dict(fsa_counts.most_common(20)),
        "hub_count": len(SERVICE_HUBS),
        "methodology": (
            "Static demo subset filtered from the public free/open BC postal-code dataset. "
            "Routing metrics are browser-side Haversine planning proxies, not road-network times."
        ),
    }
    write_json(POSTAL_OUTPUT, rows, compact=True)
    write_json(FSA_CLUSTER_OUTPUT, fsa_clusters, compact=True)
    write_json(HUBS_OUTPUT, SERVICE_HUBS)
    write_json(SUMMARY_OUTPUT, summary)
    print(f"wrote {POSTAL_OUTPUT.relative_to(ROOT)} ({len(rows):,} rows)")
    print(f"wrote {FSA_CLUSTER_OUTPUT.relative_to(ROOT)} ({len(fsa_clusters):,} clusters)")
    print(f"wrote {HUBS_OUTPUT.relative_to(ROOT)} ({len(SERVICE_HUBS):,} hubs)")
    print(f"wrote {SUMMARY_OUTPUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
