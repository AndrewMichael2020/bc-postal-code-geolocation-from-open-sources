#!/usr/bin/env python3
"""Tests for generated GitHub Pages OSRM demo assets."""

from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ASSET = ROOT / "demo" / "data" / "fha-home-health-demo.json"


def test_osrm_demo_asset_shape() -> None:
    asset = json.loads(ASSET.read_text(encoding="utf-8"))
    assert asset["schemaVersion"] == 1
    assert asset["source"]["sourceRows"] == 1_111_752
    assert asset["source"]["usableRows"] == 1_111_752
    assert asset["source"]["topCandidatesPerPostalCode"] == 8
    assert len(asset["postalCodes"]) == 41_176
    assert len(asset["facilities"]) == 27
    assert len(asset["candidates"]) == len(asset["postalCodes"])
    assert set(asset["defaults"]) >= {
        "laborCostPerHour",
        "gasPricePerLitre",
        "fuelConsumptionLPer100Km",
        "maintenanceCostPerKm",
        "visitsPerPostalCode",
        "visitDurationMin",
        "capacityHoursPerFacility",
        "maxExtraTravelMin",
        "maxExtraDistanceKm",
        "maxRelativeCostPenalty",
    }


def test_osrm_demo_asset_references_are_valid() -> None:
    asset = json.loads(ASSET.read_text(encoding="utf-8"))
    postal_ids = [row[0] for row in asset["postalCodes"]]
    postal_codes = [row[1] for row in asset["postalCodes"]]
    assert len(postal_ids) == len(set(postal_ids))
    assert len(postal_codes) == len(set(postal_codes))
    assert postal_codes == sorted(postal_codes)
    facility_count = len(asset["facilities"])
    warning_count = len(asset["warningCatalog"])
    for row in asset["postalCodes"]:
        assert len(row) == 4
        assert row[1].startswith("V")
        assert 48.0 <= row[2] <= 50.5
        assert -123.5 <= row[3] <= -121.0
    for row in asset["facilities"]:
        assert len(row) == 7
        assert row[0]
        assert row[1]
        assert row[2] in {"hospital", "upcc", "upcc_after_hours"}
    for candidate_list in asset["candidates"]:
        assert 1 <= len(candidate_list) <= 8
        previous_cost = -1.0
        for candidate in candidate_list:
            assert len(candidate) == 4
            facility_index, duration_min, distance_km, warning_indexes = candidate
            assert 0 <= facility_index < facility_count
            assert duration_min >= 0
            assert distance_km >= 0
            for warning_index in warning_indexes:
                assert 0 <= warning_index < warning_count
            default_cost = duration_min + distance_km * 0.2655
            assert default_cost >= previous_cost
            previous_cost = default_cost


def test_no_legacy_demo_assets_required() -> None:
    legacy_assets = [
        ROOT / "demo" / "data" / "lower-mainland-fsa-clusters.json",
        ROOT / "demo" / "data" / "lower-mainland-postal-codes.json",
        ROOT / "demo" / "data" / "service-hubs.json",
    ]
    assert not any(path.exists() for path in legacy_assets)


if __name__ == "__main__":
    test_osrm_demo_asset_shape()
    test_osrm_demo_asset_references_are_valid()
    test_no_legacy_demo_assets_required()
    print("demo asset tests passed")
