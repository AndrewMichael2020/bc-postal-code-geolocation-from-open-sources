# Live Demo OSRM Home Health Territory Planner Spec

## 1. Purpose

Replace the current straight-line mobile care allocation demo with a management-grade home health territory planner that uses OSRM travel time and road distance from the Fraser Health access dataset.

The demo should help leaders answer:

- Which facility or team base should serve each patient postal-code area?
- What is the weekly travel-time and vehicle-cost burden of the current plan?
- Can we rebalance territories without creating geographically implausible assignments?
- Where is the plan infeasible because capacity is insufficient?
- Which reassignments are exceptions that require operational review?

This is a planning and scenario tool for longitudinal home health visits. It is not a real-time dispatch tool, emergency-routing tool, clinical triage tool, or appointment scheduler.

## 2. Problem With Current Demo

The current demo is visually unintuitive because it assigns FSA centroid points to fictional hubs using Haversine distance and a greedy workload-balancing score.

That creates several problems:

- Straight-line distance ignores bridges, rivers, mountains, road network shape, congestion proxies, and forest/service-road realities.
- FSA centroids are too coarse for credible operational travel planning.
- The optimizer can trade away geography to reduce workload imbalance.
- The UI calls the result a reallocation or recommendation even when the scenario worsens travel burden.
- The map displays colored dots, not defensible service territories.
- Leadership users can interpret the output as an operational recommendation even when it is only a toy scenario.

The new design must remove all straight-line travel measures from allocation logic and from displayed travel KPIs.

## 3. Source Data

Primary source:

`outputs/fha_golden_distances_times.csv`

This is the Git LFS dataset derived from the OSRM Fraser Health postal-code-to-facility access output.

Expected useful columns include:

- `PostalCodeID`
- `postal_code`
- `latitude`
- `longitude`
- `health_authority`
- `facility_id`
- `facility_type`
- `facility_name`
- `facility_address`
- `facility_latitude`
- `facility_longitude`
- `duration_min`
- `distance_km`
- access and route QA fields, where populated

The demo must treat `duration_min` and `distance_km` as the source of truth for travel cost. It must not compute Haversine distance for allocation, KPIs, ranking, or exception scoring.

## 4. Planning Assumptions

### Service Model

The modeled service is longitudinal home health visits.

Assumptions:

- Visits are recurring and mostly non-urgent.
- Travel cost matters more than immediate response time.
- Patient demand is a visit-volume multiplier, not an urgency priority.
- Capacity limits represent planned provider or team availability.
- The desired output is a stable service territory plan, not dynamic dispatch.

### Cost Model

Default labor cost:

`$60 per provider travel hour`

Equivalent:

`$1.00 per travel minute`

Vehicle cost should include fuel plus a modest distance-based allowance for wear, tires, maintenance, and old-truck operating overhead.

Default vehicle assumptions:

| Parameter | Default | Rationale |
| --- | ---: | --- |
| Regular gas price | `$1.70/L` | Plausible Lower Mainland planning assumption |
| Fuel consumption | `11.5 L/100 km` | Reasonable for an older small truck or compact pickup in mixed use |
| Fuel cost | `$0.1955/km` | `1.70 * 11.5 / 100` |
| Maintenance/amortization | `$0.0700/km` | Conservative old-vehicle wear allowance |
| Total vehicle cost | `$0.2655/km` | Fuel plus maintenance/amortization |

Displayed simplification:

`about $2.65 per 10 km`

The user previously suggested a fuel-only estimate around `$1.70 per 10 km`. That is plausible only if fuel consumption is closer to `10 L/100 km` at `$1.70/L`. For a 20-year-old small truck, the demo should default higher and allow users to tune the assumption.

### Route Cost Formula

For each postal-code-to-facility pair:

```text
route_cost_dollars =
  duration_min * labor_cost_per_minute
  + distance_km * vehicle_cost_per_km
```

Default:

```text
route_cost_dollars =
  duration_min * 1.00
  + distance_km * 0.2655
```

For weekly planning:

```text
weekly_route_cost_dollars =
  expected_visits_per_week
  * route_cost_dollars
```

Travel duration should dominate the default objective. Distance still matters for fuel and vehicle wear.

## 5. Demand Model

Demand is expected visit volume, not clinical priority.

Each postal code should have:

- `expected_visits_per_week`
- optional `visit_duration_min`
- optional `patient_count`
- optional demand segment for scenario modeling

Initial demo default:

```text
expected_visits_per_week = configurable base rate by postal code
```

If real visit volumes are unavailable, the demo may derive a proxy from postal-code counts or synthetic scenario multipliers, but it must label this clearly as modeled visit volume.

Demand must affect total weekly cost and capacity consumption:

```text
capacity_minutes_used =
  expected_visits_per_week
  * (duration_min + visit_duration_min)
```

The optimizer should not simply assign high-demand areas first in a way that breaks geography. Demand should scale the cost and capacity consequences of each assignment.

## 6. Optimization Objective

Primary objective:

Minimize total expected weekly operating cost while respecting credible service-territory guardrails.

The optimization should consider:

- travel labor cost
- vehicle cost
- weekly visit volume
- team/facility capacity
- geographic credibility
- route QA risk
- capacity shortfall, if constraints cannot be satisfied

### Base Assignment

For each postal code:

1. Rank candidate facilities by OSRM route cost.
2. Assign to the lowest-cost feasible facility.
3. Apply capacity constraints.
4. Rebalance only when needed.
5. Keep a full audit trail of any assignment away from the lowest-cost facility.

### Infeasibility

If all facilities are overloaded after guardrails are respected, do not produce absurd cross-region assignments.

Instead, mark the scenario as:

`Not feasible under current capacity`

and report:

- shortfall hours
- shortfall full-time-equivalent estimate
- affected facilities
- highest-cost unserved or over-capacity postal-code areas

## 7. Guardrails

The planner must prevent assignments that look efficient numerically but are operationally indefensible.

Default guardrails:

| Guardrail | Default |
| --- | ---: |
| Max extra travel time versus best facility | `10 min` |
| Max relative cost penalty versus best facility | `25%` |
| Max extra route distance versus best facility | `10 km` |
| Max one-way route time for normal assignment | `90 min` |
| Max origin snap warning for normal assignment | `500 m` |
| Max destination snap warning for normal assignment | `500 m` |

Rows that breach guardrails can still be shown, but only as exceptions.

Exception labels:

- `nearest feasible`
- `capacity-driven exception`
- `high travel penalty`
- `long route`
- `snap warning`
- `road/access warning`
- `terrain warning`
- `manual review needed`

The UI must never present a guardrail-breaking plan as a clean recommendation.

## 8. Route QA Signals

Use the route QA fields already available in the golden dataset.

Examples of signals to surface:

- FSR or forest-service-road evidence
- low average route speed
- route circuity
- long duration band
- snap warning
- terrain flags
- steep ascent or descent, where computed
- wilderness or access warning

Route QA should affect the plan in two ways:

1. It should appear as a warning in the exception table.
2. It may add an optional cost penalty in scenario mode.

Initial penalty design:

```text
risk_adjusted_route_cost =
  route_cost_dollars
  + qa_penalty_dollars
```

Default penalties can be conservative:

| Signal | Default penalty |
| --- | ---: |
| snap warning | `$5` per visit |
| route circuity high | `$3` per visit |
| very low route speed | `$5` per visit |
| FSR/access warning | `$10` per visit |
| steep/terrain warning | `$5` per visit |

The first version may display QA warnings without including penalties in optimization. If penalties are included, the UI must make that explicit.

## 9. Demo Asset Pipeline

The browser should not load the full 500+ MB golden CSV.

Create a build script that produces compact demo assets from the LFS dataset.

Proposed script:

`scripts/build_fha_home_health_demo_assets.py`

Inputs:

- `outputs/fha_golden_distances_times.csv`
- optional scenario config file

Outputs:

- `demo/data/fha_postal_codes.json`
- `demo/data/fha_facilities.json`
- `demo/data/fha_route_costs.json`
- `demo/data/fha_demo_summary.json`

### Compact Cost Matrix

`fha_route_costs.json` should not contain all rich columns. It should include the minimum fields needed for browser interaction.

Recommended row shape:

```json
{
  "postalCodeId": "SYN-PC-000001",
  "facilityId": "burnaby_hospital",
  "durationMin": 18.4,
  "distanceKm": 12.7,
  "routeCost": 21.77,
  "rankByCost": 1,
  "warnings": []
}
```

To keep the asset small:

- include top `N` facilities by route cost per postal code, default `N = 8`
- always include the current assigned facility if current assignment is modeled
- include all facilities only for small test/demo slices

The builder should preserve enough evidence for exception review without shipping the whole analytical dataset to the browser.

## 10. Frontend Behavior

### New App Framing

Recommended title:

`Fraser Health Home Health Territory Planner`

Subtitle:

`Plan longitudinal home health service territories using OSRM travel time, road distance, capacity, and route-risk signals.`

Avoid language that implies emergency dispatch or clinical prioritization.

### Scenario Controls

Controls should include:

- provider travel cost per hour
- gas price per litre
- fuel consumption in L/100 km
- maintenance/amortization per km
- expected visits per week multiplier
- visit duration minutes
- weekly capacity hours per team/facility
- max extra travel minutes
- max relative cost penalty
- max extra distance
- include route QA penalties toggle
- allow guardrail exceptions toggle

Remove or de-emphasize the current abstract controls:

- generic distance penalty
- urban demand multiplier
- rural demand multiplier

These can remain only as advanced synthetic-demand controls.

### Action Buttons

Replace:

`Reallocate now`

with:

`Optimize territories`

Secondary actions:

- `Show current plan`
- `Show lowest-cost plan`
- `Show capacity-balanced plan`
- `Show exceptions`

### Verdict System

Every optimized scenario must produce one verdict:

- `Recommended`
- `Trade-off`
- `Not recommended`
- `Not feasible`

Verdict rules:

`Recommended` if:

- total weekly cost decreases or capacity feasibility improves
- no severe guardrail exceptions
- no increase in high-risk route assignments

`Trade-off` if:

- cost improves but exceptions increase
- capacity improves but travel burden worsens
- a manager must decide whether the trade-off is acceptable

`Not recommended` if:

- travel cost increases without solving capacity
- P95 travel time worsens materially
- too many postal codes are assigned away from nearest feasible facility

`Not feasible` if:

- capacity constraints cannot be satisfied without breaking hard guardrails

## 11. KPI Design

Replace the current KPI set with management-facing operations metrics.

Primary KPIs:

- weekly travel cost
- weekly travel hours
- weekly vehicle cost
- average travel cost per visit
- P50 one-way travel time
- P95 one-way travel time
- capacity utilization
- capacity shortfall hours
- reassigned postal codes
- guardrail exceptions

Comparison KPIs:

- cost delta versus current plan
- travel-hour delta
- vehicle-cost delta
- P95 time delta
- exception delta
- capacity-shortfall delta

Do not show straight-line kilometres.

Distance may be shown only as OSRM road distance.

## 12. Map Design

The map should make service territories legible.

Minimum map layers:

- facility/team base markers
- postal-code points or aggregated cells colored by assigned facility
- current plan layer
- optimized plan layer
- exception overlay
- route warning overlay

Preferred territory display:

- grouped postal-code points by assigned facility
- optional convex/concave hull around assigned points
- clear legend for facilities
- click-to-inspect postal code assignment

Popup content for a postal code:

- postal code
- assigned facility
- lowest-cost facility
- duration to assigned facility
- distance to assigned facility
- cost per visit
- expected weekly visits
- weekly travel cost
- extra minutes versus best facility
- extra cost versus best facility
- warnings

The map should visually punish bad reallocations. If North Shore and Vancouver Central territories cross, the UI should make clear whether those are accepted exceptions, not silently color them as normal.

## 13. Exception Table

Add an exception table as a first-class panel.

Columns:

- postal code
- assigned facility
- lowest-cost facility
- assigned duration
- best duration
- extra minutes
- assigned distance
- best distance
- extra km
- extra cost per visit
- expected visits per week
- weekly extra cost
- reason
- QA warnings

Default sort:

`weekly extra cost desc`

This table is the leadership trust builder. It explains why a map looks strange or proves that the plan should be rejected.

## 14. Algorithm Options

### Version 1: Guarded Greedy Assignment

This is the recommended first implementation.

Steps:

1. Calculate route cost for every available postal-code-to-facility pair.
2. Mark each postal code's best facility.
3. Assign each postal code to its best facility.
4. Check facility capacity.
5. For overloaded facilities, move the lowest-harm postal codes to alternative facilities.
6. Only allow moves that pass guardrails unless the user enables exception mode.
7. Stop when capacity is feasible or no acceptable moves remain.
8. If no acceptable moves remain, mark remaining overload as capacity shortfall.

Benefits:

- easy to explain
- deterministic
- fast in browser for compact data
- produces clear exception evidence

### Version 2: Min-Cost Flow

Use min-cost flow if Version 1 is not strong enough.

Nodes:

- postal-code demand nodes
- facility capacity nodes
- sink node

Edges:

- postal code to candidate facility
- facility to sink

Edge cost:

`weekly_route_cost_dollars`

Capacity:

- postal-code demand
- facility weekly capacity

Guardrails:

- remove edges that breach hard guardrails
- include soft-penalty edges only in exception mode

Benefits:

- mathematically cleaner
- handles capacity globally
- avoids greedy ordering artifacts

Risk:

- more code
- harder to explain in the UI
- may need a small JS optimization library or precomputed server/build-step results

### Recommended Path

Implement Version 1 first. Keep data structures compatible with Version 2.

## 15. Acceptance Criteria

Functional acceptance:

- No Haversine distance is used in assignment, scoring, ranking, KPIs, or exception metrics.
- All travel time and distance comes from the OSRM dataset.
- The demo loads compact assets, not the full LFS CSV.
- The optimizer can produce a feasible plan, a trade-off plan, or an infeasible verdict.
- Capacity shortfall is displayed instead of forcing absurd assignments.
- Every assignment away from the lowest-cost facility is explainable.
- The UI distinguishes normal assignments from exceptions.

Management acceptance:

- A leader can see the dollar cost of travel.
- A leader can see which plan is cheaper and by how much.
- A leader can see where capacity is insufficient.
- A leader can see why any area moved away from its natural facility.
- A leader can reject a scenario based on exceptions without reading code.

Data acceptance:

- Facility count matches the source facility asset.
- Postal-code count matches the filtered Fraser Health planning population.
- Each postal code has at least one route candidate or is flagged as unroutable.
- Null duration or distance rows are excluded from normal assignment and listed as data gaps.
- Route QA warning counts are summarized.

## 16. Test Plan

### Unit Tests

Add tests for:

- route cost formula
- fuel and vehicle cost conversion
- candidate ranking by OSRM cost
- guardrail pass/fail
- capacity accounting
- infeasible capacity detection
- exception reason generation
- verdict classification

### Asset Tests

Add tests for:

- generated JSON files exist
- no Haversine fields are present
- all route candidates have `durationMin` and `distanceKm`
- no postal code exceeds top `N` candidates unless required by current assignment
- all IDs referenced by route costs exist in postal-code and facility assets

### UI Tests

Add browser tests for:

- default plan renders
- optimize button changes verdict/KPIs
- exception table appears when guardrails are breached
- capacity shortfall appears when capacity is too low
- no straight-line distance labels appear
- map popups show OSRM duration, distance, and cost

### Regression Tests

Specific scenario that must never return as normal:

- A Vancouver postal code assigned to Chilliwack when Vancouver is materially cheaper and no exception mode is enabled.
- A North Shore postal code assigned deep into the Fraser Valley when North Shore is materially cheaper and capacity shortfall should be reported instead.

## 17. Implementation Plan

### Phase 1: Data Builder

- Add `scripts/build_fha_home_health_demo_assets.py`.
- Read the LFS CSV locally.
- Build compact postal-code, facility, and cost-matrix JSON.
- Add data validation and summary output.
- Add tests for generated assets.

### Phase 2: Cost Engine

- Replace Haversine allocation helpers with OSRM-cost helpers.
- Add route-cost calculation.
- Add guardrail evaluation.
- Add capacity accounting.
- Add verdict classification.

### Phase 3: UI Reframe

- Rename the app to home health territory planning.
- Replace abstract sliders with cost/capacity controls.
- Replace scenario narrative with verdict-based copy.
- Add exception table.
- Update KPIs.

### Phase 4: Map Improvements

- Keep current point layer initially, but color by assigned facility using OSRM assignments.
- Add facility markers with stronger labels.
- Add exception overlay.
- Add popup details.
- Add optional hull/territory layer after the assignment engine is stable.

### Phase 5: Publish

- Rebuild demo assets.
- Run unit and browser tests.
- Push code and compact demo assets.
- Keep the full golden CSV in Git LFS.

## 18. Non-Goals

This project will not initially:

- schedule individual provider daily routes
- solve vehicle routing with time windows
- optimize appointment sequencing
- model live traffic
- handle emergency response
- use clinical acuity as a priority score
- expose patient-identifiable information
- replace operational review for difficult road/access areas

## 19. Open Questions

- What should the default expected visits per postal code be?
- Should capacity be modeled by facility, team, or provider FTE?
- Should visit duration be uniform or vary by service type?
- Should route QA warnings affect optimization cost or only display as warnings?
- What is the acceptable management default for extra travel time: `10 min`, `15 min`, or percentage-based only?
- Should the first public demo show all Fraser Health postal codes or a smaller management-friendly slice?
- Do we want a current-plan assignment source, or should the baseline be lowest-cost facility assignment?

## 20. Recommended Defaults For First Build

Use these defaults unless better business inputs are provided:

```text
labor_cost_per_hour = 60
gas_price_per_litre = 1.70
fuel_consumption_l_per_100km = 11.5
maintenance_cost_per_km = 0.07
vehicle_cost_per_km = 0.2655
expected_visits_per_week = 1.0 per postal code
visit_duration_min = 45
max_extra_travel_min = 10
max_relative_cost_penalty = 0.25
max_extra_distance_km = 10
max_one_way_duration_min = 90
candidate_facility_count = 8
```

The first management demo should optimize for:

```text
minimize weekly travel labor cost
+ weekly vehicle cost
+ optional route QA penalty
```

subject to:

```text
capacity constraints
+ guardrail constraints
+ explicit infeasibility reporting
```

## 21. Executive Summary For Stakeholders

The revised demo will turn the current map from a visual allocation toy into a defensible home health planning tool. It will use actual OSRM road travel time and distance, convert travel into dollars, treat demand as recurring visit volume, and prevent the optimizer from hiding capacity problems behind implausible cross-region assignments.

The main product principle is:

`Show the cheapest credible territory plan, and explain every exception.`
