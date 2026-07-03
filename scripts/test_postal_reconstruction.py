#!/usr/bin/env python3
"""Focused tests for postal-source reconstruction helpers."""

from __future__ import annotations

from postal_source_utils import (
    classify_distance,
    haversine_km,
    normalize_postal_code,
    representative_point,
    source_classification,
    valid_bc_coordinate,
)
from google_maps_adjudicate_postal_codes import (
    check_monthly_cap,
    classify_google_adjudication,
    parse_source_coordinates,
    select_targets,
)
from build_golden_postal_geolocation import build_golden_rows, google_result_scope
from enrich_golden_health_authority import (
    assign_health_authority,
    point_in_polygon,
    prepared_features,
)


def test_normalize_postal_code() -> None:
    assert normalize_postal_code("v6b1a1") == "V6B 1A1"
    assert normalize_postal_code("V6B-1A1") == "V6B 1A1"
    assert normalize_postal_code("bad") == ""


def test_valid_bc_coordinate() -> None:
    assert valid_bc_coordinate("49.2827", "-123.1207")
    assert not valid_bc_coordinate("43.6532", "-79.3832")
    assert not valid_bc_coordinate("", "-123.1207")


def test_haversine_km() -> None:
    distance = haversine_km(49.2827, -123.1207, 48.4284, -123.3656)
    assert distance is not None
    assert 90 < distance < 110


def test_representative_point() -> None:
    lat, lon, spread, method = representative_point(
        [(49.0, -123.0), (49.001, -123.001), (49.002, -123.002)]
    )
    assert 49.0 <= lat <= 49.002
    assert -123.002 <= lon <= -123.0
    assert 0 < spread < 1
    assert method == "exact_medoid_address_point"


def test_classify_distance() -> None:
    assert classify_distance(None, 1) == "single_source"
    assert classify_distance(0.2, 2) == "agree"
    assert classify_distance(0.7, 2) == "minor"
    assert classify_distance(5, 2) == "major"
    assert classify_distance(11, 2) == "severe"


def test_source_classification() -> None:
    assert source_classification("direct_download") == "direct_download"
    try:
        source_classification("surprise")
    except ValueError:
        pass
    else:
        raise AssertionError("unsupported source access_status should fail")


def test_google_target_selection() -> None:
    rows = [
        {
            "postal_code": "V6B 1A1",
            "comparison_class": "agree",
            "disagreement_class": "agree",
            "max_disagreement_km": "0.100",
        },
        {
            "postal_code": "V6B 1A2",
            "comparison_class": "minor",
            "disagreement_class": "minor",
            "max_disagreement_km": "0.700",
        },
        {
            "postal_code": "V6B 1A3",
            "comparison_class": "major",
            "disagreement_class": "major",
            "max_disagreement_km": "3.000",
        },
        {
            "postal_code": "V6B 1A4",
            "comparison_class": "severe",
            "disagreement_class": "severe",
            "max_disagreement_km": "12.000",
        },
        {
            "postal_code": "V6B 1A5",
            "comparison_class": "missing_from_seed",
            "disagreement_class": "single_source",
            "max_disagreement_km": "",
        },
    ]
    targets = select_targets(rows, stable_qa_limit=1)
    assert [row["target_bucket"] for row in targets[:3]] == ["risky", "risky", "risky"]
    assert targets[0]["postal_code"] == "V6B 1A4"
    assert sum(row["target_bucket"] == "stable_qa" for row in targets) == 1


def test_google_ledger_cap() -> None:
    ledger = [
        {"calendar_month": "2026-07", "billable_event_count": "2"},
        {"calendar_month": "2026-07", "billable_event_count": "3"},
        {"calendar_month": "2026-06", "billable_event_count": "100"},
    ]
    ok, used, remaining = check_monthly_cap(ledger, "2026-07", hard_cap=9, planned_new_calls=4)
    assert ok
    assert used == 5
    assert remaining == 4
    ok, _, _ = check_monthly_cap(ledger, "2026-07", hard_cap=9, planned_new_calls=5)
    assert not ok


def test_google_source_coordinate_parse() -> None:
    parsed = parse_source_coordinates("osm:49.000000,-123.000000;geonames:50.000000,-124.000000")
    assert parsed == [("osm", 49.0, -123.0), ("geonames", 50.0, -124.0)]


def test_google_adjudication_classification() -> None:
    assert classify_google_adjudication("ZERO_RESULTS", "False", None, None) == "no_google_coordinate"
    assert classify_google_adjudication("OK", "False", None, None) == "google_outside_bc_bounds"
    assert classify_google_adjudication("OK", "True", 0.2, 0.2) == "google_confirms_selected"
    assert classify_google_adjudication("OK", "True", 0.8, 0.8) == "google_near_selected"
    assert classify_google_adjudication("OK", "True", 2.0, 0.2) == "google_supports_other_source"
    assert classify_google_adjudication("OK", "True", 2.0, 2.0) == "google_disagrees_with_free_sources"


def test_golden_geolocation_google_authoritative() -> None:
    comparison_rows = [
        {
            "postal_code": "V1A 1A1",
            "selected_source": "geonames_ca_full",
            "selected_methodology": "geonames_postal_code_coordinate",
            "selected_latitude": "49.000000",
            "selected_longitude": "-123.000000",
            "comparison_class": "major",
            "source_count": "1",
            "sources": "geonames_ca_full",
            "source_coordinates": "geonames_ca_full:49.000000,-123.000000",
        },
        {
            "postal_code": "V0A 1W0",
            "selected_source": "osm_geofabrik_bc",
            "selected_methodology": "exact_medoid_address_point",
            "selected_latitude": "59.925451",
            "selected_longitude": "-128.489487",
            "comparison_class": "missing_from_seed",
            "source_count": "1",
            "sources": "osm_geofabrik_bc",
            "source_coordinates": "osm_geofabrik_bc:59.925451,-128.489487",
        },
        {
            "postal_code": "V9Z 9Z9",
            "selected_source": "geonames_ca_full",
            "selected_methodology": "geonames_postal_code_coordinate",
            "selected_latitude": "48.400000",
            "selected_longitude": "-123.400000",
            "comparison_class": "single_source",
            "source_count": "1",
            "sources": "geonames_ca_full",
            "source_coordinates": "geonames_ca_full:48.400000,-123.400000",
        },
        {
            "postal_code": "V3Z 0C1",
            "selected_source": "osm_geofabrik_bc",
            "selected_methodology": "single_address_point",
            "selected_latitude": "49.045507",
            "selected_longitude": "-122.769524",
            "comparison_class": "missing_from_seed",
            "source_count": "1",
            "sources": "osm_geofabrik_bc",
            "source_coordinates": "osm_geofabrik_bc:49.045507,-122.769524",
        },
    ]
    google_rows = [
        {
            "postal_code": "V1A 1A1",
            "google_status": "OK",
            "google_latitude": "49.100000",
            "google_longitude": "-123.100000",
            "google_valid_bc_coordinate": "True",
            "google_formatted_address": "Kimberley, BC V1A 1A1, Canada",
            "google_types": "postal_code",
            "adjudication_class": "google_disagrees_with_free_sources",
            "cache_expires_at": "2026-08-02T00:00:00-07:00",
        },
        {
            "postal_code": "V0A 1W0",
            "google_status": "OK",
            "google_latitude": "51.930820",
            "google_longitude": "-117.920483",
            "google_valid_bc_coordinate": "True",
            "google_formatted_address": "British Columbia V0A, Canada",
            "google_types": "postal_code;postal_code_prefix",
            "adjudication_class": "google_disagrees_with_free_sources",
            "cache_expires_at": "2026-08-02T00:00:00-07:00",
        },
        {
            "postal_code": "V3Z 0C1",
            "google_status": "OK",
            "google_latitude": "53.726668",
            "google_longitude": "-127.647621",
            "google_valid_bc_coordinate": "True",
            "google_formatted_address": "British Columbia, Canada",
            "google_types": "administrative_area_level_1;political",
            "adjudication_class": "google_disagrees_with_free_sources",
            "cache_expires_at": "2026-08-02T00:00:00-07:00",
        },
    ]
    assert google_result_scope(google_rows[0]) == "full_postal_code"
    assert google_result_scope(google_rows[1]) == "fsa_prefix"
    assert google_result_scope(google_rows[2]) == "province_only"
    final_rows, audit_rows, rejected_rows, summary = build_golden_rows(comparison_rows, google_rows)
    by_code = {row["postal_code"]: row for row in final_rows}
    audit_by_code = {row["postal_code"]: row for row in audit_rows}
    assert by_code["V1A 1A1"]["latitude"] == "49.100000"
    assert by_code["V0A 1W0"]["longitude"] == "-117.920483"
    assert by_code["V9Z 9Z9"]["latitude"] == "48.400000"
    assert "V3Z 0C1" not in by_code
    assert audit_by_code["V0A 1W0"]["coordinate_methodology"] == (
        "google_maps_geocoding_fsa_prefix_approximate"
    )
    assert audit_by_code["V0A 1W0"]["google_retention_mode"] == (
        "persisted_no_auto_delete_per_user_request"
    )
    assert audit_by_code["V3Z 0C1"]["inclusion_status"] == "rejected"
    assert audit_by_code["V3Z 0C1"]["rejection_reason"] == "google_province_only_non_postal_result"
    assert summary["google_coordinates_persisted"] == 2
    assert summary["google_fsa_prefix_coordinates_persisted"] == 1
    assert summary["rejected_row_count"] == 1
    assert rejected_rows[0]["postal_code"] == "V3Z 0C1"


def test_health_authority_point_in_polygon_assignment() -> None:
    polygon = [
        [[-124.0, 48.0], [-122.0, 48.0], [-122.0, 50.0], [-124.0, 50.0], [-124.0, 48.0]]
    ]
    assert point_in_polygon(-123.0, 49.0, polygon)
    assert not point_in_polygon(-125.0, 49.0, polygon)
    features = prepared_features(
        {
            "features": [
                {
                    "type": "Feature",
                    "properties": {
                        "HLTH_AUTHORITY_NAME": "Fraser",
                        "HLTH_AUTHORITY_CODE": "2",
                        "HLTH_AUTHORITY_ID": "2 FHA",
                    },
                    "geometry": {"type": "Polygon", "coordinates": polygon},
                }
            ]
        }
    )
    assigned = assign_health_authority("49.0", "-123.0", features)
    assert assigned["health_authority"] == "Fraser Health"
    assert assigned["health_authority_confidence"] == "official_boundary_match"


def main() -> int:
    test_normalize_postal_code()
    test_valid_bc_coordinate()
    test_haversine_km()
    test_representative_point()
    test_classify_distance()
    test_source_classification()
    test_google_target_selection()
    test_google_ledger_cap()
    test_google_source_coordinate_parse()
    test_google_adjudication_classification()
    test_golden_geolocation_google_authoritative()
    test_health_authority_point_in_polygon_assignment()
    print("postal reconstruction helper tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
