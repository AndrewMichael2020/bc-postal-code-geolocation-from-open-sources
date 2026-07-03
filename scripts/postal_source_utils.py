#!/usr/bin/env python3
"""Shared helpers for BC postal-code source reconstruction."""

from __future__ import annotations

import csv
import datetime as dt
import hashlib
import json
import math
import re
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
POSTAL_RE = re.compile(r"^[ABCEGHJ-NPRSTVXY]\d[ABCEGHJ-NPRSTV-Z]\s?\d[ABCEGHJ-NPRSTV-Z]\d$")
BC_LAT_MIN = 47.0
BC_LAT_MAX = 61.2
BC_LON_MIN = -140.0
BC_LON_MAX = -113.0


def utc_now_iso() -> str:
    return dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def default_run_id() -> str:
    return dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%SZ")


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def normalize_postal_code(value: str | None) -> str:
    if value is None:
        return ""
    compact = re.sub(r"[^A-Za-z0-9]", "", value).upper()
    if len(compact) != 6:
        return ""
    formatted = f"{compact[:3]} {compact[3:]}"
    if not POSTAL_RE.match(formatted):
        return ""
    return formatted


def is_bc_postal_code(value: str | None) -> bool:
    code = normalize_postal_code(value)
    return bool(code and code.startswith("V"))


def to_float(value: Any) -> float | None:
    try:
        if value in ("", None):
            return None
        number = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(number) or math.isinf(number):
        return None
    return number


def valid_bc_coordinate(latitude: Any, longitude: Any) -> bool:
    lat = to_float(latitude)
    lon = to_float(longitude)
    if lat is None or lon is None:
        return False
    return BC_LAT_MIN <= lat <= BC_LAT_MAX and BC_LON_MIN <= lon <= BC_LON_MAX


def haversine_km(lat1: Any, lon1: Any, lat2: Any, lon2: Any) -> float | None:
    a_lat = to_float(lat1)
    a_lon = to_float(lon1)
    b_lat = to_float(lat2)
    b_lon = to_float(lon2)
    if None in {a_lat, a_lon, b_lat, b_lon}:
        return None
    radius_km = 6371.0088
    phi1 = math.radians(a_lat)
    phi2 = math.radians(b_lat)
    delta_phi = math.radians(b_lat - a_lat)
    delta_lambda = math.radians(b_lon - a_lon)
    a = (
        math.sin(delta_phi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2) ** 2
    )
    return radius_km * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def classify_distance(max_distance_km: float | None, source_count: int) -> str:
    if source_count <= 0:
        return "missing"
    if source_count == 1:
        return "single_source"
    if max_distance_km is None:
        return "single_source"
    if max_distance_km <= 0.25:
        return "agree"
    if max_distance_km <= 1.0:
        return "minor"
    if max_distance_km <= 10.0:
        return "major"
    return "severe"


def source_classification(access_status: str) -> str:
    allowed = {
        "direct_download",
        "free_registration",
        "restricted_account",
        "policy_excluded",
        "not_enough_evidence",
    }
    if access_status not in allowed:
        raise ValueError(f"Unsupported source access_status: {access_status}")
    return access_status


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    ensure_dir(path.parent)
    with path.open("w", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="") as file:
        return list(csv.DictReader(file))


def write_json(path: Path, payload: Any) -> None:
    ensure_dir(path.parent)
    with path.open("w") as file:
        json.dump(payload, file, indent=2, sort_keys=True)
        file.write("\n")


def representative_point(
    points: list[tuple[float, float]],
    exact_limit: int = 100,
) -> tuple[float, float, float, str]:
    """Return representative point, spread from it, and method label."""
    if not points:
        raise ValueError("representative_point requires at least one point")
    if len(points) == 1:
        lat, lon = points[0]
        return lat, lon, 0.0, "single_address_point"

    if len(points) <= exact_limit:
        best = None
        for candidate in points:
            total = 0.0
            for other in points:
                total += haversine_km(candidate[0], candidate[1], other[0], other[1]) or 0.0
            if best is None or total < best[0]:
                best = (total, candidate)
        assert best is not None
        chosen = best[1]
        method = "exact_medoid_address_point"
    else:
        mean_lat = sum(point[0] for point in points) / len(points)
        mean_lon = sum(point[1] for point in points) / len(points)
        chosen = min(
            points,
            key=lambda point: haversine_km(mean_lat, mean_lon, point[0], point[1]) or 0.0,
        )
        method = "centroid_nearest_address_point"

    spread = max(
        haversine_km(chosen[0], chosen[1], point[0], point[1]) or 0.0
        for point in points
    )
    return chosen[0], chosen[1], spread, method


def format_number(value: Any, places: int = 6) -> str:
    number = to_float(value)
    if number is None:
        return ""
    return f"{number:.{places}f}"
