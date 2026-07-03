#!/usr/bin/env python3
"""Import and catalog free-source BC postal-code geolocation evidence."""

from __future__ import annotations

import argparse
import csv
import gzip
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from postal_source_utils import (
    ROOT,
    default_run_id,
    ensure_dir,
    format_number,
    is_bc_postal_code,
    normalize_postal_code,
    representative_point,
    sha256_file,
    source_classification,
    utc_now_iso,
    valid_bc_coordinate,
    write_csv,
    write_json,
)


USER_AGENT = "BCPostalReconstruction/1.0 (+local audit; respectful bulk download)"
GEONAMES_URL = "https://download.geonames.org/export/zip/CA_full.csv.zip"
ODA_BC_URL = "https://www150.statcan.gc.ca/n1/en/pub/46-26-0001/2021001/ODA_BC_v1.zip"
OPENADDRESSES_TREE_URL = (
    "https://api.github.com/repos/openaddresses/openaddresses/git/trees/master?recursive=1"
)
OPENADDRESSES_COMMIT_URL = (
    "https://api.github.com/repos/openaddresses/openaddresses/commits/master"
)
GEOFABRIK_BC_PAGE = "https://download.geofabrik.de/north-america/canada/british-columbia.html"
GEOFABRIK_BC_PBF = (
    "https://download.geofabrik.de/north-america/canada/british-columbia-latest.osm.pbf"
)
OVERPASS_MAIN_ENDPOINT = "https://overpass-api.de/api/interpreter"
OVERPASS_PRIVATE_COFFEE_ENDPOINT = "https://overpass.private.coffee/api/interpreter"
OVERPASS_PRIVATE_COFFEE_URL = "https://overpass.private.coffee/"
BC_GEOCODER_PROBE = (
    "https://geocoder.api.gov.bc.ca/addresses.geojson?"
    "addressString=13450%20104%20ave%20surrey%20bc&maxResults=1&echo=true&outputSRS=4326"
)


OBSERVATION_FIELDS = [
    "source_id",
    "source_label",
    "postal_code",
    "latitude",
    "longitude",
    "methodology",
    "source_record_count",
    "spread_km",
    "raw_accuracy",
    "provider",
    "source_url",
    "license",
    "source_freshness",
    "downloaded_at",
    "notes",
]

SOURCE_STATUS_FIELDS = [
    "source_id",
    "label",
    "access_status",
    "import_status",
    "imported_rows",
    "distinct_postal_codes",
    "rows_skipped",
    "source_url",
    "license",
    "attribution",
    "stated_freshness",
    "http_last_modified",
    "http_etag",
    "downloaded_at",
    "file_sha256",
    "raw_path",
    "api_version",
    "base_data_date",
    "quota_or_rate_limit",
    "access_requirements",
    "registration_steps",
    "methodology",
    "notes",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", default=default_run_id())
    parser.add_argument(
        "--work-root",
        default=str(ROOT / "work/postal_reconstruction"),
        help="Directory that receives run-specific raw/intermediate artifacts.",
    )
    parser.add_argument("--refresh", action="store_true", help="Redownload cached files.")
    parser.add_argument("--skip-geonames", action="store_true")
    parser.add_argument("--skip-oda", action="store_true")
    parser.add_argument("--skip-openaddresses", action="store_true")
    parser.add_argument("--skip-osm", action="store_true")
    parser.add_argument("--skip-overpass-probe", action="store_true")
    parser.add_argument("--skip-bc-geocoder-probe", action="store_true")
    parser.add_argument(
        "--attempt-geocoder-ca",
        action="store_true",
        help="Use GEOCODER_CA_AUTH for an explicit capped Geocoder.ca postal-code probe.",
    )
    parser.add_argument(
        "--geocoder-ca-limit",
        type=int,
        default=25,
        help="Maximum postal codes to query when --attempt-geocoder-ca is set.",
    )
    parser.add_argument(
        "--attempt-openaddresses-direct",
        action="store_true",
        help="Attempt direct OpenAddresses imports. This is now the default unless --skip-openaddresses-direct is set.",
    )
    parser.add_argument(
        "--skip-openaddresses-direct",
        action="store_true",
        help="Catalog OpenAddresses only, without attempting direct HTTP/ESRI layer imports.",
    )
    parser.add_argument(
        "--max-openaddresses-direct-sources",
        type=int,
        default=80,
        help="Safety cap for optional direct OpenAddresses imports.",
    )
    parser.add_argument(
        "--max-openaddresses-features-per-source",
        type=int,
        default=250000,
        help="Safety cap for ESRI/OpenAddresses features read from one source.",
    )
    parser.add_argument(
        "--osm-pbf",
        default="",
        help="Existing Geofabrik BC .osm.pbf path to inspect with osmium.",
    )
    parser.add_argument(
        "--download-osm-pbf",
        action="store_true",
        help="Download the large Geofabrik BC PBF before OSM extraction.",
    )
    parser.add_argument("--timeout", type=int, default=90)
    return parser.parse_args()


def source_registry() -> list[dict[str, Any]]:
    rows = [
        {
            "source_id": "geonames_ca_full",
            "label": "GeoNames CA full postal-code dump",
            "access_status": "direct_download",
            "source_url": GEONAMES_URL,
            "license": "Creative Commons Attribution 4.0",
            "attribution": "GeoNames",
            "stated_freshness": "Daily downloadable postal-code dump; use HTTP Last-Modified per run.",
            "quota_or_rate_limit": "Direct file download; be respectful.",
            "access_requirements": "None.",
            "registration_steps": "No registration required.",
            "methodology": "Postal-code latitude/longitude from GeoNames, with GeoNames accuracy code.",
            "notes": "High-coverage free seed; source readme says data is provided as-is.",
        },
        {
            "source_id": "statcan_oda_bc",
            "label": "Statistics Canada Open Database of Addresses - British Columbia",
            "access_status": "direct_download",
            "source_url": ODA_BC_URL,
            "license": "Open Government Licence - Canada",
            "attribution": "Statistics Canada and original local/provincial providers",
            "stated_freshness": "Version 1.0, gathered January-April 2021.",
            "quota_or_rate_limit": "Direct zipped CSV download.",
            "access_requirements": "None.",
            "registration_steps": "No registration required.",
            "methodology": "Open address points aggregated to postal-code representative coordinates.",
            "notes": "Incomplete postal-code coverage; useful for independent address-point checks.",
        },
        {
            "source_id": "openaddresses_bc",
            "label": "OpenAddresses BC source registry and simple direct imports",
            "access_status": "direct_download",
            "source_url": "https://github.com/openaddresses/openaddresses/tree/master/sources/ca/bc",
            "license": "Source-specific; OpenAddresses source JSON is CC0.",
            "attribution": "Varies by original source.",
            "stated_freshness": "GitHub source registry commit timestamp and source-specific portals.",
            "quota_or_rate_limit": "Varies by source.",
            "access_requirements": "None for source registry; raw providers vary.",
            "registration_steps": "No registration required for the OpenAddresses registry or public source layers.",
            "methodology": "Catalog source definitions; optionally import simple CSV/GeoJSON address points.",
            "notes": "ESRI REST and direct CSV/GeoJSON layers are imported when feasible; shapefiles are cataloged unless a stdlib path is available.",
        },
        {
            "source_id": "osm_geofabrik_bc",
            "label": "OpenStreetMap Geofabrik British Columbia extract",
            "access_status": "direct_download",
            "source_url": GEOFABRIK_BC_PAGE,
            "license": "Open Database License 1.0",
            "attribution": "OpenStreetMap contributors; Geofabrik processed extract",
            "stated_freshness": "Daily Geofabrik extract; capture page and file headers per run.",
            "quota_or_rate_limit": "Large direct file download.",
            "access_requirements": "None; project-local pyosmium or the osmium CLI is required for extraction.",
            "registration_steps": "No registration required.",
            "methodology": "Extract addr:postcode/postal_code tags from local PBF and aggregate features.",
            "notes": "Avoids Overpass/Nominatim bulk API usage.",
        },
        {
            "source_id": "overpass_main_osm",
            "label": "Main public Overpass OSM API slow hosted path",
            "access_status": "direct_download",
            "source_url": OVERPASS_MAIN_ENDPOINT,
            "license": "Open Database License 1.0; public Overpass instance usage constraints apply.",
            "attribution": "OpenStreetMap contributors; Overpass API operators",
            "stated_freshness": "Hosted Overpass database freshness varies by public instance.",
            "quota_or_rate_limit": "Main public instance guidance says less than 10,000 queries/day and less than 1 GB/day is usually safe; large extracts should use static OSM downloads.",
            "access_requirements": "None, but use a clear User-Agent, caching, small geographic tiles, and long backoff.",
            "registration_steps": "No registration required. For a multi-day run, prepare a tile list, cache every response, query only addr:postcode/postal_code tags, and stop/retry on HTTP 429/504.",
            "methodology": "Optional slow hosted OSM evidence path; not used by default because Geofabrik local PBF is more reproducible.",
            "notes": "Useful for incremental spot evidence if a local extract is not available; not a substitute for the Geofabrik PBF workflow.",
        },
        {
            "source_id": "overpass_private_coffee",
            "label": "Private.coffee Overpass OSM API hosted path",
            "access_status": "direct_download",
            "source_url": OVERPASS_PRIVATE_COFFEE_ENDPOINT,
            "license": "Open Database License 1.0; Private.coffee service terms apply.",
            "attribution": "OpenStreetMap contributors; Private.coffee Overpass operators",
            "stated_freshness": "Private.coffee says returned data may be slightly outdated versus OpenStreetMap.",
            "quota_or_rate_limit": "No fixed rate limit advertised; terms ask for optimized queries, limited simultaneous requests, POST, and advance notice for large operations over 10 requests/sec or more than 10 concurrent requests.",
            "access_requirements": "None.",
            "registration_steps": "No registration required. For a multi-day run, use POST, cache every tile response, keep concurrency below 10, and contact support@private.coffee before large operations.",
            "methodology": "Optional hosted Overpass addr:postcode/postal_code evidence path; small probe is run by default, full multi-day tile import is not.",
            "notes": "Good candidate for a polite multi-day hosted OSM ingestion fallback when local PBF is unavailable.",
        },
        {
            "source_id": "geoapify",
            "label": "Geoapify Geocoding API",
            "access_status": "free_registration",
            "source_url": "https://www.geoapify.com/geocoding-api/",
            "license": "Geoapify terms; returns may use OSM/OpenAddresses/GeoNames and other data.",
            "attribution": "Geoapify attribution required on free tier.",
            "stated_freshness": "Provider-maintained; not a static dataset.",
            "quota_or_rate_limit": "Free tier advertised as 3,000 credits/day, 5 req/s.",
            "access_requirements": "Free API key required.",
            "registration_steps": (
                "1. Go to https://www.geoapify.com/ and sign up. "
                "2. Create a project in My Projects. "
                "3. Copy the API key. "
                "4. Set GEOAPIFY_API_KEY in the shell or project environment. "
                "5. Rerun the importer when a Geoapify adjudication pass is desired."
            ),
            "methodology": "Optional batch/geocode adjudication when GEOAPIFY_API_KEY is provided.",
            "notes": "Catalog only by default; no key is requested or assumed.",
        },
        {
            "source_id": "geocoder_ca",
            "label": "Geocoder.ca API",
            "access_status": "free_registration",
            "source_url": "https://geocoder.ca/api",
            "license": "Geocoder.ca terms; free non-commercial API tier.",
            "attribution": "Geocoder.ca / Geolytica",
            "stated_freshness": "Provider-maintained; supports Canadian postal-code lookup.",
            "quota_or_rate_limit": "Free non-commercial tier states 2,500 calls/day; sign-up and whitelisting required.",
            "access_requirements": "Auth token and whitelisting required for the free tier.",
            "registration_steps": (
                "1. Open https://geocoder.ca/?register=1. "
                "2. Create an API account for non-commercial/free use. "
                "3. Complete any requested whitelisting step. "
                "4. Copy the auth token. "
                "5. Set GEOCODER_CA_AUTH in the shell or project environment. "
                "6. Rerun with --attempt-geocoder-ca --geocoder-ca-limit N to spend only an explicit capped number of lookups."
            ),
            "methodology": "Optional postal-code lookup/adjudication source when GEOCODER_CA_AUTH is provided.",
            "notes": "Catalog only by default; no token is requested or assumed.",
        },
        {
            "source_id": "opencage",
            "label": "OpenCage Geocoding API",
            "access_status": "free_registration",
            "source_url": "https://opencagedata.com/pricing",
            "license": "OpenCage terms; free trial is for testing.",
            "attribution": "OpenCage and underlying open data contributors",
            "stated_freshness": "Provider-maintained geocoding service.",
            "quota_or_rate_limit": "Free trial states 2,500 requests/day and 1 request/sec.",
            "access_requirements": "API key required; free trial, no credit card.",
            "registration_steps": (
                "1. Open https://opencagedata.com/users/sign_up. "
                "2. Create a free trial account. "
                "3. Copy the API key from the account dashboard. "
                "4. Set OPENCAGE_API_KEY in the shell or project environment. "
                "5. Rerun a small adjudication pass first; the free trial is for testing."
            ),
            "methodology": "Optional small adjudication/check source, not a full bulk import by default.",
            "notes": "Catalog only by default; free trial is not treated as a production bulk source.",
        },
        {
            "source_id": "locationiq",
            "label": "LocationIQ Geocoding API",
            "access_status": "free_registration",
            "source_url": "https://locationiq.com/pricing",
            "license": "LocationIQ terms; attribution required on free plan.",
            "attribution": "LocationIQ and underlying data contributors",
            "stated_freshness": "Provider-maintained geocoding service.",
            "quota_or_rate_limit": "Free plan states 5,000 requests/day and 2 requests/sec.",
            "access_requirements": "Access token required.",
            "registration_steps": (
                "1. Open https://my.locationiq.com/register. "
                "2. Create a free account. "
                "3. Copy the access token. "
                "4. Set LOCATIONIQ_TOKEN in the shell or project environment. "
                "5. Rerun a small adjudication pass first and confirm storage/attribution constraints."
            ),
            "methodology": "Optional geocoding adjudication/check source when LOCATIONIQ_TOKEN is provided.",
            "notes": "Catalog only by default; storage/attribution constraints should be reviewed before bulk use.",
        },
        {
            "source_id": "bc_address_geocoder",
            "label": "BC Address Geocoder API",
            "access_status": "free_registration",
            "source_url": "https://www2.gov.bc.ca/gov/content/data/geographic-data-services/location-services/geocoder",
            "license": "Province of British Columbia terms/copyright.",
            "attribution": "Province of British Columbia",
            "stated_freshness": "API returns baseDataDate in responses.",
            "quota_or_rate_limit": "Developer guide mentions API key with 1000 requests/minute.",
            "access_requirements": "API key/request flow for intended access; public probe only here.",
            "registration_steps": (
                "1. Open the BC Address Geocoder page. "
                "2. Use the API Services Portal or Data Systems and Services Request System link from that page. "
                "3. Request access/API key for the geocoder. "
                "4. Copy the API key if issued. "
                "5. Set BC_GEOCODER_API_KEY in the shell or project environment. "
                "6. Rerun targeted validation; postal-code-only queries are not enough for inventory."
            ),
            "methodology": "Address geocoder validation/probe, not bulk postal-code inventory.",
            "notes": "Postal-code-only queries may return coarse province matches.",
        },
        {
            "source_id": "bc_batch_geocoder",
            "label": "BC Batch Geocoder",
            "access_status": "restricted_account",
            "source_url": "https://www2.gov.bc.ca/gov/content/data/geographic-data-services/location-services/geocoder",
            "license": "Province of British Columbia terms/copyright.",
            "attribution": "Province of British Columbia",
            "stated_freshness": "Same geocoder base data when accessible.",
            "quota_or_rate_limit": "Batch service limits require account access.",
            "access_requirements": "IDIR or BCeID account required per BC page.",
            "registration_steps": (
                "1. Open the BC Address Geocoder page. "
                "2. Follow the Batch Geocoder restricted-access link. "
                "3. Sign in with IDIR or BCeID, or request access via the linked ticket flow. "
                "4. If an API endpoint/key or export becomes available, provide the token/export path. "
                "5. Rerun importer with that credential or file."
            ),
            "methodology": "Potential bulk address geocoding, not imported without account.",
            "notes": "Cataloged as a clear sign-in source.",
        },
        {
            "source_id": "nominatim_public",
            "label": "Public OSM Nominatim API",
            "access_status": "policy_excluded",
            "source_url": "https://operations.osmfoundation.org/policies/nominatim/",
            "license": "OSM ODbL; OSMF public service usage policy applies.",
            "attribution": "OpenStreetMap contributors",
            "stated_freshness": "Public service database freshness varies.",
            "quota_or_rate_limit": "Absolute max 1 request/s; scripts over a day restricted to 4/min.",
            "access_requirements": "Valid User-Agent/Referer; caching; policy compliance.",
            "registration_steps": "No registration path helps for this use; public policy excludes bulk postcode inventory queries.",
            "methodology": "Small spot checks only; bulk postcode reconstruction excluded.",
            "notes": "Policy forbids systematic complete-list postcode queries.",
        },
    ]
    for row in rows:
        source_classification(row["access_status"])
    return rows


def request_headers() -> dict[str, str]:
    return {"User-Agent": USER_AGENT, "Accept": "*/*"}


def parse_header_file(path: Path) -> dict[str, str]:
    text = path.read_text(errors="replace") if path.exists() else ""
    blocks = [block for block in text.replace("\r\n", "\n").split("\n\n") if block.strip()]
    if not blocks:
        return {}
    block = blocks[-1]
    headers: dict[str, str] = {}
    for line in block.splitlines()[1:]:
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        headers[key.strip()] = value.strip()
    return headers


def fetch_bytes(url: str, timeout: int) -> tuple[bytes, dict[str, str]]:
    with tempfile.NamedTemporaryFile() as header_file:
        command = [
            "curl",
            "--fail",
            "--location",
            "--silent",
            "--show-error",
            "--user-agent",
            USER_AGENT,
            "--dump-header",
            header_file.name,
            url,
        ]
        result = subprocess.run(command, check=True, timeout=timeout, capture_output=True)
        return result.stdout, parse_header_file(Path(header_file.name))


def fetch_json(url: str, timeout: int) -> tuple[Any, dict[str, str]]:
    data, headers = fetch_bytes(url, timeout)
    return json.loads(data.decode("utf-8")), headers


def fetch_text(url: str, timeout: int) -> tuple[str, dict[str, str]]:
    data, headers = fetch_bytes(url, timeout)
    return data.decode("utf-8", errors="replace"), headers


def post_form_json(url: str, form_key: str, form_value: str, timeout: int) -> tuple[Any, dict[str, str]]:
    with tempfile.NamedTemporaryFile() as header_file:
        command = [
            "curl",
            "--fail",
            "--location",
            "--silent",
            "--show-error",
            "--user-agent",
            USER_AGENT,
            "--dump-header",
            header_file.name,
            "--data-urlencode",
            f"{form_key}={form_value}",
            url,
        ]
        result = subprocess.run(command, check=False, timeout=timeout, capture_output=True)
        if result.returncode != 0:
            stderr = result.stderr.decode("utf-8", errors="replace").strip()
            raise RuntimeError(f"curl exited {result.returncode}: {stderr}")
        return json.loads(result.stdout.decode("utf-8")), parse_header_file(Path(header_file.name))


def head_url(url: str, timeout: int) -> dict[str, str]:
    with tempfile.NamedTemporaryFile() as header_file:
        command = [
            "curl",
            "--fail",
            "--location",
            "--silent",
            "--show-error",
            "--head",
            "--user-agent",
            USER_AGENT,
            "--dump-header",
            header_file.name,
            url,
        ]
        subprocess.run(command, check=True, timeout=timeout, capture_output=True)
        return parse_header_file(Path(header_file.name))


def download_file(url: str, path: Path, refresh: bool, timeout: int) -> dict[str, str]:
    ensure_dir(path.parent)
    headers_path = path.with_suffix(path.suffix + ".headers.json")
    if path.exists() and not refresh:
        headers = {}
        if headers_path.exists():
            headers = json.loads(headers_path.read_text())
        return headers
    part_path = path.with_suffix(path.suffix + ".part")
    with tempfile.NamedTemporaryFile() as header_file:
        command = [
            "curl",
            "--fail",
            "--location",
            "--silent",
            "--show-error",
            "--user-agent",
            USER_AGENT,
            "--dump-header",
            header_file.name,
            "--output",
            str(part_path),
        ]
        if part_path.exists():
            command.extend(["--continue-at", "-"])
        command.append(url)
        subprocess.run(command, check=True, timeout=timeout, capture_output=True)
        headers = parse_header_file(Path(header_file.name))
    part_path.replace(path)
    write_json(headers_path, headers)
    return headers


def init_status(registry: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    status = {}
    for source in registry:
        row = {field: "" for field in SOURCE_STATUS_FIELDS}
        row.update(source)
        row["import_status"] = "not_attempted"
        row["imported_rows"] = 0
        row["distinct_postal_codes"] = 0
        row["rows_skipped"] = 0
        status[source["source_id"]] = row
    return status


def source_lookup(registry: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {row["source_id"]: row for row in registry}


def update_download_status(
    status: dict[str, dict[str, Any]],
    source_id: str,
    headers: dict[str, str],
    path: Path | None,
) -> None:
    row = status[source_id]
    row["http_last_modified"] = headers.get("Last-Modified", "")
    row["http_etag"] = headers.get("ETag", "")
    row["downloaded_at"] = utc_now_iso()
    if path is not None:
        row["raw_path"] = str(path)
        if path.exists():
            row["file_sha256"] = sha256_file(path)


def import_geonames(
    run_dir: Path,
    registry: dict[str, dict[str, Any]],
    status: dict[str, dict[str, Any]],
    refresh: bool,
    timeout: int,
) -> list[dict[str, Any]]:
    source_id = "geonames_ca_full"
    raw_path = run_dir / "raw/geonames/CA_full.csv.zip"
    headers = download_file(GEONAMES_URL, raw_path, refresh, timeout)
    update_download_status(status, source_id, headers, raw_path)

    points_by_code: dict[str, list[tuple[float, float]]] = defaultdict(list)
    places_by_code: dict[str, Counter[str]] = defaultdict(Counter)
    accuracy_by_code: dict[str, Counter[str]] = defaultdict(Counter)
    skipped = 0
    accuracy_counts: Counter[str] = Counter()
    with zipfile.ZipFile(raw_path) as archive:
        name = next(name for name in archive.namelist() if name.lower().endswith(".txt"))
        with archive.open(name) as file:
            reader = csv.reader((line.decode("utf-8") for line in file), delimiter="\t")
            for row in reader:
                if len(row) < 12:
                    skipped += 1
                    continue
                postal_code = normalize_postal_code(row[1])
                latitude = row[9]
                longitude = row[10]
                if not postal_code.startswith("V") or not valid_bc_coordinate(latitude, longitude):
                    skipped += 1
                    continue
                accuracy = row[11]
                accuracy_counts[accuracy] += 1
                points_by_code[postal_code].append((float(latitude), float(longitude)))
                places_by_code[postal_code][row[2]] += 1
                accuracy_by_code[postal_code][accuracy] += 1
    observations: list[dict[str, Any]] = []
    for postal_code, points in sorted(points_by_code.items()):
        latitude, longitude, spread, method = representative_point(points)
        accuracy_summary = dict(sorted(accuracy_by_code[postal_code].items()))
        places = ";".join(name for name, _count in places_by_code[postal_code].most_common(3))
        methodology = "geonames_postal_code_coordinate"
        if len(points) > 1:
            methodology = f"geonames_duplicate_{method}"
        observations.append(
            {
                "source_id": source_id,
                "source_label": registry[source_id]["label"],
                "postal_code": postal_code,
                "latitude": format_number(latitude),
                "longitude": format_number(longitude),
                "methodology": methodology,
                "source_record_count": str(len(points)),
                "spread_km": f"{spread:.3f}",
                "raw_accuracy": json.dumps(accuracy_summary, sort_keys=True),
                "provider": "GeoNames",
                "source_url": GEONAMES_URL,
                "license": registry[source_id]["license"],
                "source_freshness": headers.get("Last-Modified", ""),
                "downloaded_at": status[source_id]["downloaded_at"],
                "notes": f"place_names={places}",
            }
        )
    status[source_id]["import_status"] = "imported"
    status[source_id]["imported_rows"] = len(observations)
    status[source_id]["distinct_postal_codes"] = len({row["postal_code"] for row in observations})
    status[source_id]["rows_skipped"] = skipped
    status[source_id]["notes"] = (
        f"GeoNames raw BC row accuracy counts before postal-code aggregation: {dict(sorted(accuracy_counts.items()))}"
    )
    return observations


def import_oda(
    run_dir: Path,
    registry: dict[str, dict[str, Any]],
    status: dict[str, dict[str, Any]],
    refresh: bool,
    timeout: int,
) -> list[dict[str, Any]]:
    source_id = "statcan_oda_bc"
    raw_path = run_dir / "raw/statcan_oda/ODA_BC_v1.zip"
    headers = download_file(ODA_BC_URL, raw_path, refresh, timeout)
    update_download_status(status, source_id, headers, raw_path)

    points_by_code: dict[str, list[tuple[float, float]]] = defaultdict(list)
    providers_by_code: dict[str, Counter[str]] = defaultdict(Counter)
    skipped = 0
    rows = 0
    with zipfile.ZipFile(raw_path) as archive:
        name = next(name for name in archive.namelist() if name.lower().endswith(".csv"))
        with archive.open(name) as file:
            reader = csv.DictReader((line.decode("utf-8-sig") for line in file))
            for row in reader:
                rows += 1
                postal_code = normalize_postal_code(row.get("postal_code", ""))
                latitude = row.get("latitude", "")
                longitude = row.get("longitude", "")
                if not postal_code.startswith("V") or not valid_bc_coordinate(latitude, longitude):
                    skipped += 1
                    continue
                points_by_code[postal_code].append((float(latitude), float(longitude)))
                provider = (row.get("provider") or "unknown").strip() or "unknown"
                providers_by_code[postal_code][provider] += 1

    observations: list[dict[str, Any]] = []
    for postal_code, points in sorted(points_by_code.items()):
        latitude, longitude, spread, method = representative_point(points)
        provider = ";".join(name for name, _count in providers_by_code[postal_code].most_common(3))
        observations.append(
            {
                "source_id": source_id,
                "source_label": registry[source_id]["label"],
                "postal_code": postal_code,
                "latitude": format_number(latitude),
                "longitude": format_number(longitude),
                "methodology": method,
                "source_record_count": str(len(points)),
                "spread_km": f"{spread:.3f}",
                "raw_accuracy": "",
                "provider": provider,
                "source_url": ODA_BC_URL,
                "license": registry[source_id]["license"],
                "source_freshness": "Version 1.0; gathered January-April 2021",
                "downloaded_at": status[source_id]["downloaded_at"],
                "notes": "Representative postal-code coordinate from ODA address points.",
            }
        )

    status[source_id]["import_status"] = "imported"
    status[source_id]["imported_rows"] = len(observations)
    status[source_id]["distinct_postal_codes"] = len(observations)
    status[source_id]["rows_skipped"] = skipped
    status[source_id]["notes"] = f"Read {rows} ODA rows; grouped address points by postal code."
    return observations


def simple_field(value: Any) -> str:
    return value if isinstance(value, str) else ""


def parse_openaddresses_sources(
    run_dir: Path,
    timeout: int,
) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, str]]:
    commit, commit_headers = fetch_json(OPENADDRESSES_COMMIT_URL, timeout)
    tree, tree_headers = fetch_json(OPENADDRESSES_TREE_URL, timeout)
    write_json(run_dir / "raw/openaddresses/master_commit.json", commit)
    write_json(run_dir / "raw/openaddresses/master_tree.json", tree)

    source_paths = sorted(
        item["path"]
        for item in tree.get("tree", [])
        if item.get("path", "").startswith("sources/ca/bc/")
        and item.get("path", "").lower().endswith((".json", ".geojson"))
    )
    source_rows: list[dict[str, Any]] = []
    for source_path in source_paths:
        raw_url = f"https://raw.githubusercontent.com/openaddresses/openaddresses/master/{source_path}"
        try:
            payload, _headers = fetch_json(raw_url, timeout)
        except (urllib.error.URLError, json.JSONDecodeError, TimeoutError) as error:
            source_rows.append(
                {
                    "source_path": source_path,
                    "raw_url": raw_url,
                    "provider": "",
                    "protocol": "",
                    "format": "",
                    "data_url": "",
                    "license": "",
                    "postcode_field": "",
                    "lat_field": "",
                    "lon_field": "",
                    "importable_by_stdlib": "false",
                    "notes": f"Could not fetch/parse source JSON: {error}",
                }
            )
            continue
        layers = payload.get("layers", {}) if isinstance(payload, dict) else {}
        address_layers = layers.get("addresses", []) if isinstance(layers, dict) else []
        if not isinstance(address_layers, list):
            address_layers = []
        if not address_layers:
            source_rows.append(
                {
                    "source_path": source_path,
                    "raw_url": raw_url,
                    "provider": "",
                    "protocol": "",
                    "format": "",
                    "data_url": "",
                    "license": json.dumps(payload.get("license", "")),
                    "postcode_field": "",
                    "lat_field": "",
                    "lon_field": "",
                    "importable_by_stdlib": "false",
                    "notes": "No address layers found.",
                }
            )
        for layer in address_layers:
            conform = layer.get("conform", {}) if isinstance(layer, dict) else {}
            fmt = conform.get("format", "")
            postcode = simple_field(conform.get("postcode", ""))
            lat_field = simple_field(conform.get("lat", ""))
            lon_field = simple_field(conform.get("lon", ""))
            protocol = layer.get("protocol", "")
            importable = bool(
                postcode
                and (
                (
                    protocol == "http"
                    and fmt in {"csv", "geojson"}
                    and (fmt == "geojson" or (lat_field and lon_field))
                )
                or (
                    protocol == "ESRI"
                    and fmt == "geojson"
                )
                )
            )
            source_rows.append(
                {
                    "source_path": source_path,
                    "raw_url": raw_url,
                    "provider": layer.get("name", ""),
                    "protocol": protocol,
                    "format": fmt,
                    "data_url": layer.get("data", ""),
                    "license": json.dumps(layer.get("license", payload.get("license", ""))),
                    "postcode_field": postcode,
                    "lat_field": lat_field,
                    "lon_field": lon_field,
                    "importable_by_stdlib": str(importable).lower(),
                    "notes": "",
                }
            )

    metadata = {
        "commit_sha": commit.get("sha", ""),
        "commit_date": (
            commit.get("commit", {}).get("committer", {}).get("date", "")
            if isinstance(commit, dict)
            else ""
        ),
        "source_count": len(source_rows),
        "path_count": len(source_paths),
    }
    headers = {
        "commit_last_modified": commit_headers.get("Last-Modified", ""),
        "tree_last_modified": tree_headers.get("Last-Modified", ""),
        "etag": tree_headers.get("ETag", ""),
    }
    return source_rows, metadata, headers


def open_text_from_download(path: Path) -> tuple[str, io.TextIOBase]:
    suffix = path.suffix.lower()
    if suffix == ".gz":
        return path.name, io.TextIOWrapper(gzip.open(path, "rb"), encoding="utf-8-sig")
    if suffix == ".zip":
        archive = zipfile.ZipFile(path)
        csv_name = next(
            (name for name in archive.namelist() if name.lower().endswith(".csv")),
            "",
        )
        if not csv_name:
            raise ValueError(f"No CSV found in {path}")
        return csv_name, io.TextIOWrapper(archive.open(csv_name), encoding="utf-8-sig")
    return path.name, path.open(encoding="utf-8-sig")


def download_openaddresses_source(
    run_dir: Path,
    row: dict[str, Any],
    index: int,
    refresh: bool,
    timeout: int,
) -> Path:
    data_url = row["data_url"]
    extension = ".dat"
    lowered = data_url.lower()
    for suffix in [".csv", ".csv.gz", ".geojson", ".json", ".zip", ".gz"]:
        if lowered.endswith(suffix):
            extension = suffix
            break
    target = run_dir / "raw/openaddresses/direct" / f"source_{index:03d}{extension}"
    download_file(data_url, target, refresh, timeout)
    return target


def esri_url(base_url: str, path: str = "", **params: Any) -> str:
    base = base_url.rstrip("/")
    if path:
        base = f"{base}/{path.lstrip('/')}"
    query = urllib.parse.urlencode({key: value for key, value in params.items() if value is not None})
    return f"{base}?{query}" if query else base


def esri_geometry_point(geometry: dict[str, Any]) -> tuple[float, float] | None:
    if not isinstance(geometry, dict):
        return None
    x_coord = geometry.get("x")
    y_coord = geometry.get("y")
    if x_coord is not None and y_coord is not None:
        try:
            return float(y_coord), float(x_coord)
        except (TypeError, ValueError):
            return None
    coordinate_groups = []
    for key in ("points", "paths", "rings"):
        value = geometry.get(key)
        if isinstance(value, list):
            coordinate_groups.append(value)
    flat_points: list[tuple[float, float]] = []
    for group in coordinate_groups:
        stack = list(group)
        while stack:
            item = stack.pop()
            if (
                isinstance(item, list)
                and len(item) >= 2
                and not isinstance(item[0], list)
            ):
                try:
                    flat_points.append((float(item[1]), float(item[0])))
                except (TypeError, ValueError):
                    continue
            elif isinstance(item, list):
                stack.extend(item)
    if not flat_points:
        return None
    latitude = sum(point[0] for point in flat_points) / len(flat_points)
    longitude = sum(point[1] for point in flat_points) / len(flat_points)
    return latitude, longitude


def esri_attribute_value(attributes: dict[str, Any], field_name: str) -> Any:
    if field_name in attributes:
        return attributes[field_name]
    lowered = field_name.lower()
    for key, value in attributes.items():
        if key.lower() == lowered:
            return value
    return ""


def import_openaddresses_esri_source(
    run_dir: Path,
    row: dict[str, Any],
    index: int,
    timeout: int,
    max_features: int,
) -> tuple[list[tuple[str, float, float, str]], int, str]:
    data_url = row.get("data_url", "")
    provider = row.get("provider") or "OpenAddresses ESRI"
    postcode_field = row.get("postcode_field", "")
    layer_meta, _headers = fetch_json(esri_url(data_url, f="json"), timeout)
    max_record_count = int(layer_meta.get("maxRecordCount") or 2000)
    page_size = max(1, min(max_record_count, 2000))
    source_dir = ensure_dir(run_dir / "raw/openaddresses/esri")
    write_json(source_dir / f"source_{index:03d}_metadata.json", layer_meta)

    imported: list[tuple[str, float, float, str]] = []
    skipped = 0
    offset = 0
    pages = 0
    while len(imported) + skipped < max_features:
        payload, _headers = fetch_json(
            esri_url(
                data_url,
                "query",
                f="json",
                where="1=1",
                outFields=postcode_field,
                returnGeometry="true",
                outSR="4326",
                resultOffset=offset,
                resultRecordCount=page_size,
            ),
            timeout,
        )
        if "error" in payload:
            raise RuntimeError(json.dumps(payload["error"], sort_keys=True))
        features = payload.get("features", [])
        if not features:
            break
        pages += 1
        if pages <= 3:
            write_json(source_dir / f"source_{index:03d}_page_{pages:03d}.json", payload)
        for feature in features:
            attributes = feature.get("attributes", {}) if isinstance(feature, dict) else {}
            postal_code = normalize_postal_code(esri_attribute_value(attributes, postcode_field))
            point = esri_geometry_point(feature.get("geometry", {}))
            if not postal_code.startswith("V") or point is None:
                skipped += 1
                continue
            latitude, longitude = point
            if not valid_bc_coordinate(latitude, longitude):
                skipped += 1
                continue
            imported.append((postal_code, latitude, longitude, provider))
            if len(imported) + skipped >= max_features:
                break
        if not payload.get("exceededTransferLimit") and len(features) < page_size:
            break
        offset += len(features)
    note = f"ESRI pages={pages}; layer_maxRecordCount={max_record_count}"
    return imported, skipped, note


def import_openaddresses_direct_rows(
    run_dir: Path,
    source_rows: list[dict[str, Any]],
    registry: dict[str, dict[str, Any]],
    status: dict[str, dict[str, Any]],
    refresh: bool,
    timeout: int,
    max_sources: int,
    max_features_per_source: int,
) -> list[dict[str, Any]]:
    points_by_code: dict[str, list[tuple[float, float]]] = defaultdict(list)
    providers_by_code: dict[str, Counter[str]] = defaultdict(Counter)
    attempted = 0
    skipped = 0
    errors: list[str] = []
    imported_points: list[dict[str, Any]] = []
    source_notes: list[str] = []

    for index, row in enumerate(source_rows, start=1):
        if row.get("importable_by_stdlib") != "true":
            continue
        if attempted >= max_sources:
            break
        attempted += 1
        try:
            fmt = row.get("format", "")
            postcode_field = row.get("postcode_field", "")
            if row.get("protocol") == "ESRI":
                rows, source_skipped, note = import_openaddresses_esri_source(
                    run_dir,
                    row,
                    index,
                    timeout,
                    max_features_per_source,
                )
                skipped += source_skipped
                source_notes.append(f"{row.get('source_path')}: {note}; imported_points={len(rows)}")
                for postal_code, latitude, longitude, provider in rows:
                    points_by_code[postal_code].append((latitude, longitude))
                    providers_by_code[postal_code][provider] += 1
                    imported_points.append(
                        {
                            "postal_code": postal_code,
                            "latitude": format_number(latitude),
                            "longitude": format_number(longitude),
                            "provider": provider,
                            "source_path": row.get("source_path", ""),
                            "source_url": row.get("data_url", ""),
                        }
                    )
                continue
            path = download_openaddresses_source(run_dir, row, index, refresh, timeout)
            if fmt == "csv":
                _name, file = open_text_from_download(path)
                with file:
                    reader = csv.DictReader(file)
                    for record in reader:
                        postal_code = normalize_postal_code(record.get(postcode_field, ""))
                        latitude = record.get(row.get("lat_field", ""), "")
                        longitude = record.get(row.get("lon_field", ""), "")
                        if not postal_code.startswith("V") or not valid_bc_coordinate(latitude, longitude):
                            skipped += 1
                            continue
                        provider = row.get("provider") or "OpenAddresses"
                        lat = float(latitude)
                        lon = float(longitude)
                        points_by_code[postal_code].append((lat, lon))
                        providers_by_code[postal_code][provider] += 1
                        imported_points.append(
                            {
                                "postal_code": postal_code,
                                "latitude": format_number(lat),
                                "longitude": format_number(lon),
                                "provider": provider,
                                "source_path": row.get("source_path", ""),
                                "source_url": row.get("data_url", ""),
                            }
                        )
            elif fmt == "geojson":
                payload = json.loads(path.read_text())
                for feature in payload.get("features", []):
                    props = feature.get("properties", {})
                    geom = feature.get("geometry", {})
                    coords = geom.get("coordinates", [])
                    if geom.get("type") != "Point" or len(coords) < 2:
                        skipped += 1
                        continue
                    postal_code = normalize_postal_code(props.get(postcode_field, ""))
                    longitude, latitude = coords[0], coords[1]
                    if not postal_code.startswith("V") or not valid_bc_coordinate(latitude, longitude):
                        skipped += 1
                        continue
                    provider = row.get("provider") or "OpenAddresses"
                    lat = float(latitude)
                    lon = float(longitude)
                    points_by_code[postal_code].append((lat, lon))
                    providers_by_code[postal_code][provider] += 1
                    imported_points.append(
                        {
                            "postal_code": postal_code,
                            "latitude": format_number(lat),
                            "longitude": format_number(lon),
                            "provider": provider,
                            "source_path": row.get("source_path", ""),
                            "source_url": row.get("data_url", ""),
                        }
                    )
        except Exception as error:  # noqa: BLE001 - report source-specific import failures.
            errors.append(f"{row.get('source_path')}: {error}")

    observations: list[dict[str, Any]] = []
    for postal_code, points in sorted(points_by_code.items()):
        latitude, longitude, spread, method = representative_point(points)
        provider = ";".join(name for name, _count in providers_by_code[postal_code].most_common(3))
        observations.append(
            {
                "source_id": "openaddresses_bc",
                "source_label": registry["openaddresses_bc"]["label"],
                "postal_code": postal_code,
                "latitude": format_number(latitude),
                "longitude": format_number(longitude),
                "methodology": method,
                "source_record_count": str(len(points)),
                "spread_km": f"{spread:.3f}",
                "raw_accuracy": "",
                "provider": provider,
                "source_url": registry["openaddresses_bc"]["source_url"],
                "license": "Source-specific OpenAddresses provider licenses",
                "source_freshness": status["openaddresses_bc"].get("stated_freshness", ""),
                "downloaded_at": utc_now_iso(),
                "notes": "Representative postal-code coordinate from simple OpenAddresses direct import.",
            }
        )
    status["openaddresses_bc"]["imported_rows"] = len(observations)
    status["openaddresses_bc"]["distinct_postal_codes"] = len(observations)
    status["openaddresses_bc"]["rows_skipped"] = skipped
    if observations:
        status["openaddresses_bc"]["import_status"] = "imported_partial"
    else:
        status["openaddresses_bc"]["import_status"] = "cataloged_only"
    status["openaddresses_bc"]["notes"] += (
        f" Optional direct imports attempted={attempted}; raw_points={len(imported_points)}; errors={len(errors)}."
    )
    if source_notes:
        write_json(run_dir / "raw/openaddresses/direct_import_source_notes.json", source_notes[:200])
    if imported_points:
        write_csv(
            run_dir / "raw/openaddresses/direct_import_points.csv",
            imported_points,
            ["postal_code", "latitude", "longitude", "provider", "source_path", "source_url"],
        )
    if errors:
        write_json(run_dir / "raw/openaddresses/direct_import_errors.json", errors[:100])
    return observations


def import_openaddresses(
    run_dir: Path,
    registry: dict[str, dict[str, Any]],
    status: dict[str, dict[str, Any]],
    refresh: bool,
    timeout: int,
    attempt_direct: bool,
    max_sources: int,
    max_features_per_source: int,
) -> list[dict[str, Any]]:
    source_id = "openaddresses_bc"
    source_rows, metadata, headers = parse_openaddresses_sources(run_dir, timeout)
    write_csv(
        run_dir / "raw/openaddresses/openaddresses_bc_sources.csv",
        source_rows,
        [
            "source_path",
            "raw_url",
            "provider",
            "protocol",
            "format",
            "data_url",
            "license",
            "postcode_field",
            "lat_field",
            "lon_field",
            "importable_by_stdlib",
            "notes",
        ],
    )
    row = status[source_id]
    row["import_status"] = "cataloged_only"
    row["imported_rows"] = 0
    row["distinct_postal_codes"] = 0
    row["rows_skipped"] = 0
    row["downloaded_at"] = utc_now_iso()
    row["http_last_modified"] = headers.get("tree_last_modified") or headers.get("commit_last_modified", "")
    row["http_etag"] = headers.get("etag", "")
    row["raw_path"] = str(run_dir / "raw/openaddresses/openaddresses_bc_sources.csv")
    row["file_sha256"] = sha256_file(Path(row["raw_path"]))
    row["stated_freshness"] = f"OpenAddresses master commit {metadata.get('commit_sha')} at {metadata.get('commit_date')}"
    importable_count = sum(1 for item in source_rows if item.get("importable_by_stdlib") == "true")
    row["notes"] = (
        f"Cataloged {metadata.get('path_count')} BC source files and {len(source_rows)} "
        f"address layers; {importable_count} simple stdlib-importable layers detected."
    )
    if attempt_direct:
        return import_openaddresses_direct_rows(
            run_dir,
            source_rows,
            registry,
            status,
            refresh,
            timeout,
            max_sources,
            max_features_per_source,
        )
    return []


def extract_osm_geojsonseq(
    pbf_path: Path,
    run_dir: Path,
    timeout: int,
) -> Path:
    osmium = shutil.which("osmium")
    if not osmium:
        raise RuntimeError("osmium command not found")
    filtered = run_dir / "raw/osm/osm_postcode_filtered.osm.pbf"
    geojsonseq = run_dir / "raw/osm/osm_postcode_filtered.geojsonseq"
    filter_cmd = [
        osmium,
        "tags-filter",
        str(pbf_path),
        "n/addr:postcode",
        "w/addr:postcode",
        "a/addr:postcode",
        "n/postal_code",
        "w/postal_code",
        "a/postal_code",
        "--overwrite",
        "-o",
        str(filtered),
    ]
    subprocess.run(filter_cmd, check=True, timeout=timeout)
    export_cmd = [
        osmium,
        "export",
        str(filtered),
        "--overwrite",
        "-f",
        "geojsonseq",
        "-o",
        str(geojsonseq),
    ]
    subprocess.run(export_cmd, check=True, timeout=timeout)
    return geojsonseq


def geometry_point(geometry: dict[str, Any]) -> tuple[float, float] | None:
    coords = geometry.get("coordinates")
    geom_type = geometry.get("type")
    if geom_type == "Point" and isinstance(coords, list) and len(coords) >= 2:
        return float(coords[1]), float(coords[0])
    if geom_type == "LineString" and isinstance(coords, list) and coords:
        lon = sum(point[0] for point in coords) / len(coords)
        lat = sum(point[1] for point in coords) / len(coords)
        return lat, lon
    if geom_type == "Polygon" and isinstance(coords, list) and coords and coords[0]:
        ring = coords[0]
        lon = sum(point[0] for point in ring) / len(ring)
        lat = sum(point[1] for point in ring) / len(ring)
        return lat, lon
    return None


def build_osm_observations(
    points_by_code: dict[str, list[tuple[float, float]]],
    registry: dict[str, dict[str, Any]],
    row: dict[str, Any],
    data_up_to: str,
) -> list[dict[str, Any]]:
    observations: list[dict[str, Any]] = []
    for postal_code, points in sorted(points_by_code.items()):
        latitude, longitude, spread, method = representative_point(points)
        observations.append(
            {
                "source_id": "osm_geofabrik_bc",
                "source_label": registry["osm_geofabrik_bc"]["label"],
                "postal_code": postal_code,
                "latitude": format_number(latitude),
                "longitude": format_number(longitude),
                "methodology": method,
                "source_record_count": str(len(points)),
                "spread_km": f"{spread:.3f}",
                "raw_accuracy": "",
                "provider": "OpenStreetMap contributors",
                "source_url": GEOFABRIK_BC_PAGE,
                "license": registry["osm_geofabrik_bc"]["license"],
                "source_freshness": row["stated_freshness"] or data_up_to,
                "downloaded_at": row["downloaded_at"],
                "notes": "Representative postal-code coordinate from OSM tagged features.",
            }
        )
    return observations


def import_osm_from_geojsonseq(
    geojsonseq: Path,
) -> tuple[dict[str, list[tuple[float, float]]], int, str]:
    points_by_code: dict[str, list[tuple[float, float]]] = defaultdict(list)
    skipped = 0
    with geojsonseq.open() as file:
        for line in file:
            if not line.strip():
                continue
            try:
                feature = json.loads(line)
            except json.JSONDecodeError:
                skipped += 1
                continue
            props = feature.get("properties", {})
            postal_code = normalize_postal_code(
                props.get("addr:postcode") or props.get("postal_code") or ""
            )
            point = geometry_point(feature.get("geometry", {}))
            if not postal_code.startswith("V") or point is None:
                skipped += 1
                continue
            latitude, longitude = point
            if not valid_bc_coordinate(latitude, longitude):
                skipped += 1
                continue
            points_by_code[postal_code].append((latitude, longitude))
    return points_by_code, skipped, "osmium_cli_geojsonseq"


def import_osm_with_pyosmium(
    pbf_path: Path,
) -> tuple[dict[str, list[tuple[float, float]]], int, str]:
    try:
        import osmium  # type: ignore[import-not-found]
    except ImportError as error:
        raise RuntimeError("Neither osmium CLI nor project-local pyosmium is available.") from error

    class PostalCodeHandler(osmium.SimpleHandler):  # type: ignore[name-defined]
        def __init__(self) -> None:
            super().__init__()
            self.points_by_code: dict[str, list[tuple[float, float]]] = defaultdict(list)
            self.skipped = 0
            self.tagged_features = 0

        def _postal_code(self, tags: Any) -> str:
            return normalize_postal_code(tags.get("addr:postcode") or tags.get("postal_code") or "")

        def _add_point(self, tags: Any, latitude: Any, longitude: Any) -> None:
            postal_code = self._postal_code(tags)
            if not postal_code.startswith("V") or not valid_bc_coordinate(latitude, longitude):
                self.skipped += 1
                return
            self.tagged_features += 1
            self.points_by_code[postal_code].append((float(latitude), float(longitude)))

        def node(self, node: Any) -> None:
            postal_code = self._postal_code(node.tags)
            if not postal_code:
                return
            if not node.location.valid():
                self.skipped += 1
                return
            self._add_point(node.tags, node.location.lat, node.location.lon)

        def way(self, way: Any) -> None:
            postal_code = self._postal_code(way.tags)
            if not postal_code:
                return
            points: list[tuple[float, float]] = []
            for node in way.nodes:
                try:
                    location = node.location
                    if location.valid():
                        points.append((float(location.lat), float(location.lon)))
                except Exception:  # noqa: BLE001 - pyosmium can omit node locations.
                    continue
            if not points:
                self.skipped += 1
                return
            latitude = sum(point[0] for point in points) / len(points)
            longitude = sum(point[1] for point in points) / len(points)
            self._add_point(way.tags, latitude, longitude)

        def area(self, area: Any) -> None:
            postal_code = self._postal_code(area.tags)
            if not postal_code:
                return
            points: list[tuple[float, float]] = []
            for ring in area.outer_rings():
                for node in ring:
                    try:
                        location = node.location
                        if location.valid():
                            points.append((float(location.lat), float(location.lon)))
                    except Exception:  # noqa: BLE001 - pyosmium can omit node locations.
                        continue
            if not points:
                self.skipped += 1
                return
            latitude = sum(point[0] for point in points) / len(points)
            longitude = sum(point[1] for point in points) / len(points)
            self._add_point(area.tags, latitude, longitude)

    handler = PostalCodeHandler()
    handler.apply_file(str(pbf_path), locations=True)
    note = f"pyosmium tagged_features={handler.tagged_features}"
    return handler.points_by_code, handler.skipped, note


def import_osm(
    run_dir: Path,
    registry: dict[str, dict[str, Any]],
    status: dict[str, dict[str, Any]],
    refresh: bool,
    timeout: int,
    osm_pbf: str,
    download_osm_pbf: bool,
) -> list[dict[str, Any]]:
    source_id = "osm_geofabrik_bc"
    page_text, page_headers = fetch_text(GEOFABRIK_BC_PAGE, timeout)
    page_path = run_dir / "raw/osm/geofabrik_bc_page.html"
    ensure_dir(page_path.parent)
    page_path.write_text(page_text)
    row = status[source_id]
    row["downloaded_at"] = utc_now_iso()
    row["http_last_modified"] = page_headers.get("Last-Modified", "")
    row["http_etag"] = page_headers.get("ETag", "")
    row["raw_path"] = str(page_path)
    row["file_sha256"] = sha256_file(page_path)
    data_up_to = ""
    match = re.search(r"contains all OSM data up to ([0-9T:Z-]+)", page_text)
    if match:
        data_up_to = match.group(1)
        row["stated_freshness"] = f"Geofabrik page states data up to {data_up_to}"

    pbf_path = Path(osm_pbf).expanduser() if osm_pbf else None
    if download_osm_pbf:
        pbf_path = run_dir / "raw/osm/british-columbia-latest.osm.pbf"
        headers = download_file(GEOFABRIK_BC_PBF, pbf_path, refresh, timeout)
        update_download_status(status, source_id, headers, pbf_path)
    if pbf_path is None or not pbf_path.exists():
        row["import_status"] = "cataloged_only"
        row["notes"] = "OSM extract metadata captured; no local PBF provided and download not requested."
        return []

    extraction_note = ""
    raw_output_path: Path = pbf_path
    try:
        if shutil.which("osmium"):
            geojsonseq = extract_osm_geojsonseq(pbf_path, run_dir, timeout)
            raw_output_path = geojsonseq
            points_by_code, skipped, extraction_note = import_osm_from_geojsonseq(geojsonseq)
        else:
            points_by_code, skipped, extraction_note = import_osm_with_pyosmium(pbf_path)
            aggregated_points = [
                {
                    "postal_code": postal_code,
                    "latitude": format_number(latitude),
                    "longitude": format_number(longitude),
                }
                for postal_code, points in sorted(points_by_code.items())
                for latitude, longitude in points
            ]
            raw_output_path = run_dir / "raw/osm/osm_postcode_points.csv"
            write_csv(raw_output_path, aggregated_points, ["postal_code", "latitude", "longitude"])
    except Exception as error:  # noqa: BLE001 - record extraction path failure.
        row["import_status"] = "skipped_missing_tool"
        row["notes"] = f"Local PBF exists at {pbf_path}, but OSM extraction failed: {error}"
        return []

    observations = build_osm_observations(points_by_code, registry, row, data_up_to)
    row["import_status"] = "imported"
    row["imported_rows"] = len(observations)
    row["distinct_postal_codes"] = len(observations)
    row["rows_skipped"] = skipped
    row["raw_path"] = str(raw_output_path)
    row["file_sha256"] = sha256_file(raw_output_path)
    row["notes"] = f"Extracted from {pbf_path}; {extraction_note}."
    return observations


def probe_overpass_private_coffee(
    run_dir: Path,
    registry: dict[str, dict[str, Any]],
    status: dict[str, dict[str, Any]],
    timeout: int,
) -> list[dict[str, Any]]:
    source_id = "overpass_private_coffee"
    query = """
[out:json][timeout:60];
(
  nwr["addr:postcode"](49.25,-123.16,49.30,-123.08);
  nwr["postal_code"](49.25,-123.16,49.30,-123.08);
);
out center tags 1000;
""".strip()
    row = status[source_id]
    raw_path = run_dir / "raw/overpass/private_coffee_vancouver_probe.json"
    try:
        payload, headers = post_form_json(
            OVERPASS_PRIVATE_COFFEE_ENDPOINT,
            "data",
            query,
            min(timeout, 45),
        )
    except Exception as error:  # noqa: BLE001 - source probe failure belongs in report.
        row["import_status"] = "probe_failed"
        row["notes"] = f"Private.coffee Overpass probe failed: {error}"
        return []

    ensure_dir(raw_path.parent)
    write_json(raw_path, payload)
    points_by_code: dict[str, list[tuple[float, float]]] = defaultdict(list)
    skipped = 0
    for element in payload.get("elements", []):
        tags = element.get("tags", {}) if isinstance(element, dict) else {}
        postal_code = normalize_postal_code(tags.get("addr:postcode") or tags.get("postal_code") or "")
        latitude = element.get("lat")
        longitude = element.get("lon")
        if latitude is None or longitude is None:
            center = element.get("center", {}) if isinstance(element, dict) else {}
            latitude = center.get("lat")
            longitude = center.get("lon")
        if not postal_code.startswith("V") or not valid_bc_coordinate(latitude, longitude):
            skipped += 1
            continue
        points_by_code[postal_code].append((float(latitude), float(longitude)))

    row["downloaded_at"] = utc_now_iso()
    observations = build_osm_observations(points_by_code, registry, row, "")
    for observation in observations:
        observation["source_id"] = source_id
        observation["source_label"] = registry[source_id]["label"]
        observation["source_url"] = OVERPASS_PRIVATE_COFFEE_ENDPOINT
        observation["license"] = registry[source_id]["license"]
        observation["notes"] = "Partial Vancouver-area hosted Overpass probe; not full BC coverage."
    osm_base = payload.get("osm3s", {}).get("timestamp_osm_base", "")
    row["import_status"] = "probed_partial_import"
    row["imported_rows"] = len(observations)
    row["distinct_postal_codes"] = len(observations)
    row["rows_skipped"] = skipped
    row["http_last_modified"] = headers.get("Last-Modified", "")
    row["http_etag"] = headers.get("ETag", "")
    row["raw_path"] = str(raw_path)
    row["file_sha256"] = sha256_file(raw_path)
    row["base_data_date"] = osm_base
    row["notes"] = (
        "Ran one bounded Vancouver-area POST probe for addr:postcode/postal_code tags; "
        f"osm_base={osm_base}; elements={len(payload.get('elements', []))}."
    )
    return observations


def probe_bc_geocoder(status: dict[str, dict[str, Any]], timeout: int) -> None:
    source_id = "bc_address_geocoder"
    row = status[source_id]
    try:
        payload, headers = fetch_json(BC_GEOCODER_PROBE, timeout)
        row["import_status"] = "probed_metadata_only"
        row["http_last_modified"] = headers.get("Last-Modified", "")
        row["http_etag"] = headers.get("ETag", "")
        row["downloaded_at"] = utc_now_iso()
        row["api_version"] = payload.get("version", "")
        row["base_data_date"] = payload.get("baseDataDate", "")
        row["imported_rows"] = 0
        row["distinct_postal_codes"] = 0
        row["notes"] = "Single address probe captured API version/baseDataDate; no bulk import run."
    except Exception as error:  # noqa: BLE001 - status report should capture probe errors.
        row["import_status"] = "probe_failed"
        row["notes"] = str(error)


def probe_geocoder_ca(
    observations: list[dict[str, Any]],
    registry: dict[str, dict[str, Any]],
    status: dict[str, dict[str, Any]],
    timeout: int,
    limit: int,
) -> list[dict[str, Any]]:
    source_id = "geocoder_ca"
    row = status[source_id]
    auth = os.environ.get("GEOCODER_CA_AUTH", "").strip()
    if not auth:
        row["import_status"] = "cataloged_credentials_required"
        row["notes"] = "No GEOCODER_CA_AUTH found; source cataloged only."
        return []

    seed_codes = sorted(
        {
            observation["postal_code"]
            for observation in observations
            if observation.get("source_id") == "geonames_ca_full"
        }
    )
    selected_codes = seed_codes[: max(0, limit)]
    geocoder_rows: list[dict[str, Any]] = []
    skipped = 0
    for postal_code in selected_codes:
        query = urllib.parse.urlencode(
            {
                "locate": postal_code,
                "json": "1",
                "auth": auth,
            }
        )
        try:
            payload, headers = fetch_json(f"https://geocoder.ca/?{query}", timeout)
        except Exception as error:  # noqa: BLE001 - keep sample probe resilient.
            skipped += 1
            if not row.get("notes"):
                row["notes"] = f"First Geocoder.ca probe error for {postal_code}: {error}"
            continue
        latitude = payload.get("latt")
        longitude = payload.get("longt")
        if not valid_bc_coordinate(latitude, longitude):
            skipped += 1
            continue
        geocoder_rows.append(
            {
                "source_id": source_id,
                "source_label": registry[source_id]["label"],
                "postal_code": postal_code,
                "latitude": format_number(latitude),
                "longitude": format_number(longitude),
                "methodology": "geocoder_ca_postal_code_lookup_sample",
                "source_record_count": "1",
                "spread_km": "0.000",
                "raw_accuracy": str(payload.get("confidence", "")),
                "provider": "Geocoder.ca / Geolytica",
                "source_url": "https://geocoder.ca/",
                "license": registry[source_id]["license"],
                "source_freshness": registry[source_id]["stated_freshness"],
                "downloaded_at": utc_now_iso(),
                "notes": "Explicit capped credentialed sample; one lookup per postal code.",
            }
        )
        row["http_last_modified"] = headers.get("Last-Modified", row.get("http_last_modified", ""))
        row["http_etag"] = headers.get("ETag", row.get("http_etag", ""))

    row["import_status"] = "imported_sample" if geocoder_rows else "credential_available_no_valid_rows"
    row["imported_rows"] = len(geocoder_rows)
    row["distinct_postal_codes"] = len({item["postal_code"] for item in geocoder_rows})
    row["rows_skipped"] = skipped
    row["downloaded_at"] = utc_now_iso()
    row["notes"] = row.get("notes") or (
        f"Explicit Geocoder.ca sample attempted {len(selected_codes)} postal codes; "
        f"valid_rows={len(geocoder_rows)}; skipped={skipped}."
    )
    return geocoder_rows


def catalog_credential_source(
    status: dict[str, dict[str, Any]],
    source_id: str,
    env_var: str,
) -> None:
    row = status[source_id]
    if row["import_status"] != "not_attempted":
        return
    if os.environ.get(env_var):
        row["import_status"] = "credential_available_not_used"
        row["notes"] = f"{env_var} is present; importer does not run free-tier batch calls by default."
    else:
        row["import_status"] = "cataloged_credentials_required"
        row["notes"] = f"No {env_var} found; source cataloged only."


def catalog_static_source(status: dict[str, dict[str, Any]], source_id: str, note: str) -> None:
    row = status[source_id]
    if row["import_status"] == "not_attempted":
        row["import_status"] = "cataloged_only"
        row["notes"] = note


def main() -> int:
    args = parse_args()
    run_dir = ensure_dir(Path(args.work_root) / args.run_id)
    registry_rows = source_registry()
    registry = source_lookup(registry_rows)
    status = init_status(registry_rows)
    observations: list[dict[str, Any]] = []

    ensure_dir(run_dir / "raw")
    write_json(run_dir / "source_registry.json", registry_rows)
    write_csv(run_dir / "source_registry.csv", registry_rows, SOURCE_STATUS_FIELDS)

    if not args.skip_geonames:
        observations.extend(import_geonames(run_dir, registry, status, args.refresh, args.timeout))
    else:
        status["geonames_ca_full"]["import_status"] = "skipped_by_user"

    if not args.skip_oda:
        observations.extend(import_oda(run_dir, registry, status, args.refresh, args.timeout))
    else:
        status["statcan_oda_bc"]["import_status"] = "skipped_by_user"

    if not args.skip_openaddresses:
        attempt_openaddresses_direct = (
            args.attempt_openaddresses_direct or not args.skip_openaddresses_direct
        )
        observations.extend(
            import_openaddresses(
                run_dir,
                registry,
                status,
                args.refresh,
                args.timeout,
                attempt_openaddresses_direct,
                args.max_openaddresses_direct_sources,
                args.max_openaddresses_features_per_source,
            )
        )
    else:
        status["openaddresses_bc"]["import_status"] = "skipped_by_user"

    if not args.skip_osm:
        try:
            observations.extend(
                import_osm(
                    run_dir,
                    registry,
                    status,
                    args.refresh,
                    args.timeout,
                    args.osm_pbf,
                    args.download_osm_pbf,
                )
            )
        except Exception as error:  # noqa: BLE001 - source failure belongs in report.
            status["osm_geofabrik_bc"]["import_status"] = "import_failed"
            status["osm_geofabrik_bc"]["notes"] = str(error)
    else:
        status["osm_geofabrik_bc"]["import_status"] = "skipped_by_user"

    if not args.skip_overpass_probe:
        observations.extend(probe_overpass_private_coffee(run_dir, registry, status, args.timeout))
    else:
        status["overpass_private_coffee"]["import_status"] = "skipped_by_user"

    if not args.skip_bc_geocoder_probe:
        probe_bc_geocoder(status, args.timeout)
    else:
        status["bc_address_geocoder"]["import_status"] = "skipped_by_user"

    if args.attempt_geocoder_ca:
        observations.extend(
            probe_geocoder_ca(
                observations,
                registry,
                status,
                args.timeout,
                args.geocoder_ca_limit,
            )
        )

    credential_sources = {
        "geoapify": "GEOAPIFY_API_KEY",
        "geocoder_ca": "GEOCODER_CA_AUTH",
        "opencage": "OPENCAGE_API_KEY",
        "locationiq": "LOCATIONIQ_TOKEN",
    }
    for source_id, env_var in credential_sources.items():
        catalog_credential_source(status, source_id, env_var)
    catalog_static_source(
        status,
        "overpass_main_osm",
        "Cataloged as an optional multi-day hosted OSM path; default import uses Geofabrik local PBF.",
    )
    catalog_static_source(
        status,
        "overpass_private_coffee",
        "Cataloged as an optional Private.coffee hosted OSM path; probe was skipped or unavailable.",
    )
    catalog_static_source(
        status,
        "bc_batch_geocoder",
        "Cataloged as a clear sign-in/restricted account source; no account used.",
    )
    catalog_static_source(
        status,
        "nominatim_public",
        "Cataloged as policy-excluded for bulk complete-list postcode queries.",
    )

    write_csv(run_dir / "source_observations.csv", observations, OBSERVATION_FIELDS)
    status_rows = [status[row["source_id"]] for row in registry_rows]
    write_csv(run_dir / "source_status.csv", status_rows, SOURCE_STATUS_FIELDS)
    write_json(
        run_dir / "run_metadata.json",
        {
            "run_id": args.run_id,
            "created_at": utc_now_iso(),
            "run_dir": str(run_dir),
            "observation_rows": len(observations),
            "distinct_postal_codes": len({row["postal_code"] for row in observations}),
            "sources": status_rows,
        },
    )
    print(run_dir)
    print(f"observations={len(observations)}")
    print(f"distinct_postal_codes={len({row['postal_code'] for row in observations})}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
