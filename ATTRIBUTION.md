# Attribution And Publishing Notes

This repository publishes `data/bc_postal_codes_geolocated.csv`, a free/open-source reconstruction of BC postal-code geolocations. It does not publish Google Maps Geocoding-derived coordinates.

This is a practical engineering note, not legal advice.

## Publishable Sources Used

| Source | Upstream licence / terms | Repository use |
| --- | --- | --- |
| GeoNames postal-code dump | Creative Commons Attribution 4.0; GeoNames asks for credit and states the data is provided as-is. | High-coverage postal-code seed and selected coordinate source. |
| Statistics Canada Open Database of Addresses BC | Statistics Canada Open Licence permits use, reproduction, publishing, distribution, sale, and value-added products with required acknowledgement and no endorsement. | Address-point evidence aggregated to postal-code medoids. |
| OpenStreetMap / Geofabrik BC extract | OSM data is licensed under ODbL; attribution and licence notice are required; adapted databases may trigger share-alike obligations. | Local PBF extraction of postcode-tagged features. |
| OpenAddresses BC registry and public layers | OpenAddresses source JSON is CC0; actual data layers remain source-specific. | Public layers imported only where the source was directly readable and cataloged as open/public. |

Attribution:

- Contains information from GeoNames, available under Creative Commons Attribution 4.0.
- Contains information from OpenStreetMap contributors, available under the Open Database License.
- Adapted from Statistics Canada Open Database of Addresses, reference date 2021. This does not constitute an endorsement by Statistics Canada of this product.
- Contains information cataloged by OpenAddresses; individual public address sources may have their own licence notices.

## Google Maps Note

Google Maps Geocoding is supported only as an optional local QA/adjudication workflow. Google’s Geocoding API policy states that content pre-fetching, caching, or storage is generally restricted, with place IDs called out as an exception. Therefore Google-derived latitude/longitude outputs are not published in this repository.

Local-only Google-adjudicated files should remain under ignored paths such as:

```text
local_private/
work/google_maps_geocoding/
outputs/geolocation/*google*
```

## Operational Rule

For public releases, commit only:

```text
data/bc_postal_codes_geolocated.csv
```

Do not commit raw downloads, API responses, local ledgers, or Google-derived adjudicated coordinate files.
