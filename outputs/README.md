# Outputs

## fha_golden_distances_times.csv

Git LFS dataset for Fraser Health postal-code-to-facility driving access.

- One row per Fraser Health postal code and healthcare facility pair.
- Includes OSRM driving distance and duration fields.
- Includes route/access QA signals; detailed route and terrain fields are populated where route-detail evidence was computed.
- Source scope: Fraser Health postal codes and available Fraser Health hospitals/UPCC facilities.

The CSV is stored with Git LFS because it is larger than GitHub regular-file limits.
