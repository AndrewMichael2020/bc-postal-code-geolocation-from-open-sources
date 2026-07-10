# Live Demo OSRM Home Health Territory Planner Spec

## 1. Purpose

The live demo is a management-facing planning view for longitudinal Fraser Health home health visits. It maps every Fraser Health postal code to a provider-base placeholder using OSRM road travel time and distance.

The demo answers:

- Which provider base offers the lowest modeled travel cost for each postal-code area?
- What travel time, vehicle distance, and comparative travel cost does the resulting territory plan imply?
- How is modeled weekly work distributed across provider bases?
- Which routes carry access, terrain, snapping, slow-road, or wilderness considerations?

It is not a staffing forecast, clinical triage system, emergency dispatch tool, or appointment sequencer.

## 2. Source Data

Primary source:

`outputs/fha_golden_distances_times.csv`

The source contains 41,176 Fraser Health postal codes, 27 healthcare facilities, and 1,111,752 postal-code-to-facility route pairs. The browser receives a compact top-candidate asset:

`demo/data/fha-home-health-demo.json`

Assignment, ranking, and displayed travel measures must use OSRM `duration_min` and `distance_km`. Haversine or other straight-line distances are not permitted in planning logic or travel KPIs.

## 3. Service Model

- Visits are recurring longitudinal home health visits.
- Demand is a modeled weekly visit multiplier per postal code.
- Visits are not ranked by clinical urgency.
- All 27 facilities are provider-base placeholders.
- All provider bases are assumed able to absorb assigned work.
- Every postal code remains covered.
- Real home care stations can later replace the placeholder facilities without changing the assignment logic.

No facility capacity limit, capacity shortfall, overload state, infeasible state, or staffing availability claim may appear in the demo.

## 4. Assignment Rule

For each postal code:

1. Read its available OSRM facility candidates.
2. Calculate comparative route cost for every candidate.
3. Select the candidate with the lowest route cost.
4. Use OSRM duration as the deterministic tie-breaker.
5. Preserve route considerations for display and optional ranking penalties.

The selected plan is already the least-cost plan under this objective. The demo must not offer a second plan that increases total travel cost or travel time under the label "optimized."

## 5. Cost Model

Default assumptions:

```text
provider_travel_cost_per_hour = 60 CAD
gas_price_per_litre = 1.70 CAD
fuel_consumption_l_per_100km = 11.5
maintenance_and_amortization_per_km = 0.07 CAD
weekly_visits_per_postal_code = 0.05
visit_duration_min = 45
```

Vehicle cost:

```text
vehicle_cost_per_km =
  gas_price_per_litre * fuel_consumption_l_per_100km / 100
  + maintenance_and_amortization_per_km
```

Comparative route cost per modeled visit:

```text
route_cost =
  duration_min * provider_travel_cost_per_minute
  + distance_km * vehicle_cost_per_km
  + optional_route_consideration_penalty
```

The current model charges one OSRM leg per modeled visit. The UI must call the result an **estimated weekly travel cost** and disclose that it is a comparative estimate rather than a complete operating budget.

## 6. Workload Analytics

Service hours are descriptive analytics only:

```text
weekly_service_hours =
  weekly_visits * (visit_duration_min + one_way_duration_min) / 60
```

For every facility, report:

- assigned postal-code areas
- modeled weekly visits
- modeled weekly service hours
- share of total modeled service hours
- P95 one-way travel time
- estimated weekly travel cost

The workload map uses a dynamic display scale:

```text
workload_scale_ceiling = maximum facility weekly_service_hours
facility_workload_index = facility weekly_service_hours / workload_scale_ceiling
```

The busiest facility therefore has an index of `1.0`, every other facility is between `0.0` and `1.0`, and marker size reflects relative workload. This ceiling is visual only and must never be described as capacity or availability.

## 7. Map

- Postal-code dots use the color of their selected provider base.
- Provider-base circles are drawn above postal-code dots.
- Provider-base circle size reflects the dynamic workload index.
- Facility popups show modeled weekly service hours and workload share.
- Postal-code popups show provider base, OSRM travel time, road distance, comparative cost per visit, weekly visits, and route considerations.
- Route considerations may receive a restrained outline but must not use hostile or alarming language.

## 8. Leadership-Facing Language

Use:

- `Travel-efficient plan`
- `Coverage mapped`
- `Areas mapped`
- `Estimated weekly travel cost`
- `Modeled service hours`
- `Areas with route notes`
- `Workload distribution`
- `Route considerations`

Do not use:

- `shortfall`
- `overloaded`
- `not feasible`
- `failure`
- `exception`
- `bad plan`
- `optimized plan` when no strictly better plan exists

## 9. Controls

Retain controls for:

- provider travel cost per hour
- gas price per litre
- fuel consumption
- maintenance and amortization per kilometre
- weekly visits per postal code
- visit duration
- optional inclusion of route considerations in facility ranking

Do not expose capacity, overload, infeasibility, extra-distance guardrails, or cost-penalty guardrails in this version.

## 10. Route Considerations

The compact asset may include:

- snap warning
- long route
- very long route
- slow route
- very slow route
- high circuity
- forest/service road
- wilderness access
- terrain warning
- route detail warning

Present these as operational notes. They remain visible even when they do not affect ranking.

## 11. Acceptance Criteria

- All 41,176 postal codes are assigned when the complete demo asset is loaded.
- Coverage is displayed as 100%.
- No straight-line distance is used.
- No capacity, shortfall, overload, infeasible, or negative recommendation language appears.
- No alternative plan can replace the lowest-cost plan with a more expensive result.
- Facility workload shares sum to 100% within floating-point tolerance.
- Facility workload indexes are between 0 and 1.
- At least one active facility has workload index 1 when assignments exist.
- Changing visit assumptions updates service hours and workload marker sizes.
- Changing cost assumptions can change lowest-cost assignments when candidate rankings change.
- Route considerations remain available for review.
- Desktop and mobile layouts remain usable without overlapping controls or text.

## 12. Reproduction

Regenerate the compact asset:

```bash
python3 scripts/build_fha_home_health_demo_assets.py
```

Run tests:

```bash
make test
make compile
```

Run locally:

```bash
cd demo
python3 -m http.server 8000
```

Open `http://127.0.0.1:8000/`.
