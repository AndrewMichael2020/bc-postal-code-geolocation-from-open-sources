#!/usr/bin/env python3
"""Tests for generated GitHub Pages demo assets."""

from __future__ import annotations

import json
from pathlib import Path

from generate_demo_assets import LOWER_MAINLAND_BOUNDS, read_lower_mainland_rows


ROOT = Path(__file__).resolve().parents[1]
POSTAL_ASSET = ROOT / "demo" / "data" / "lower-mainland-postal-codes.json"
HUB_ASSET = ROOT / "demo" / "data" / "service-hubs.json"
SUMMARY_ASSET = ROOT / "demo" / "data" / "demo-summary.json"


def test_demo_asset_is_current() -> None:
    expected = read_lower_mainland_rows()
    actual = json.loads(POSTAL_ASSET.read_text(encoding="utf-8"))
    assert actual == expected


def test_demo_postal_asset_shape() -> None:
    rows = json.loads(POSTAL_ASSET.read_text(encoding="utf-8"))
    assert len(rows) > 50_000
    assert rows == sorted(rows, key=lambda row: row["postal_code"])
    postal_codes = [row["postal_code"] for row in rows]
    assert len(postal_codes) == len(set(postal_codes))
    for row in rows:
        assert set(row) == {"id", "postal_code", "fsa", "latitude", "longitude", "segment"}
        assert row["postal_code"].startswith("V")
        assert row["fsa"] == row["postal_code"][:3]
        assert LOWER_MAINLAND_BOUNDS["min_lat"] <= row["latitude"] <= LOWER_MAINLAND_BOUNDS["max_lat"]
        assert LOWER_MAINLAND_BOUNDS["min_lon"] <= row["longitude"] <= LOWER_MAINLAND_BOUNDS["max_lon"]
        assert row["segment"] in {"urban", "suburban", "rural"}


def test_demo_hubs_and_summary() -> None:
    hubs = json.loads(HUB_ASSET.read_text(encoding="utf-8"))
    summary = json.loads(SUMMARY_ASSET.read_text(encoding="utf-8"))
    assert len(hubs) == 7
    assert summary["hub_count"] == len(hubs)
    assert summary["postal_code_count"] > 50_000
    assert summary["fsa_count"] > 100
    for hub in hubs:
        assert {"id", "name", "latitude", "longitude", "capacity", "color"} <= set(hub)
        assert LOWER_MAINLAND_BOUNDS["min_lat"] <= hub["latitude"] <= LOWER_MAINLAND_BOUNDS["max_lat"]
        assert LOWER_MAINLAND_BOUNDS["min_lon"] <= hub["longitude"] <= LOWER_MAINLAND_BOUNDS["max_lon"]
