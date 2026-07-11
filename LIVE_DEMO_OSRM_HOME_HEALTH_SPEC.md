# Live Demo OSRM Home Health Territory Planner Spec

## 1. Purpose

The live demo is a leadership-facing planning workspace for recurring Fraser Health home health visits. It maps every Fraser Health postal-code area to a provider-base placeholder with OSRM road travel time and distance.

It answers:

- Which provider base has the lowest modeled travel cost for each area?
- What travel time, road distance, and provider time does the plan imply?
- How is modeled work distributed across provider bases?
- What changes when leadership proposes a different visit-share mix?
- Which routes carry access, terrain, snapping, slow-road, or wilderness notes?

It is not a staffing forecast, clinical triage system, emergency dispatch tool, appointment sequencer, or claim about current facility operations.

## 2. Source Data

The rich source is `outputs/fha_golden_distances_times.csv`: 41,176 Fraser Health postal codes, 27 facilities, and 1,111,752 postal-code-to-facility route pairs.

The browser uses:

- `demo/data/fha-home-health-demo.json`: compact initial asset with eight candidates per postal code.
- `demo/data/fha-home-health-advanced-candidates.json`: lazy asset with all 27 candidates per postal code.

Assignment and displayed travel measures must use OSRM `duration_min` and `distance_km`. The durations are static routing-profile estimates, not live or historical traffic observations, and must be described as modeled drive time. Haversine or other straight-line distance is prohibited in planning logic and travel KPIs.

## 3. Service Model

- Visits are recurring, non-urgent home health visits.
- Demand is a modeled weekly visit multiplier per postal code; no patient records or mock patients are present.
- Each facility is a replaceable provider-base placeholder.
- Every base is assumed able to absorb any assigned volume.
- Every postal code remains covered in every valid scenario.
- Real home-care stations can replace facility placeholders without changing the allocation contract.

No capacity limit, shortfall, overload, infeasible state, or staffing-availability claim may appear.

## 4. Planning Modes

### Travel-Efficient Plan

For each postal code, rank available facilities by one-way travel cost:

```text
travel_cost_per_visit =
  duration_min / 60 * provider_labour_cost_per_hour
  + distance_km * vehicle_cost_per_km
```

Select the lowest-cost route and use OSRM duration as the deterministic tie-breaker. This is the baseline, not an artificially constrained optimization.

### Target Visit-Share Scenario

Leadership may assign each provider base a target share of all modeled visits.

- `0%` means the base receives no modeled home-care visits.
- `100%` means the base is the sole provider base and receives every mapped visit.
- The expandable all-base editor shows current and proposed shares together before preview.
- Manually edited bases remain fixed while untouched bases proportionally absorb the remaining share, allowing several deliberate edits without earlier choices drifting.
- Proposed shares always total 100%; if manually fixed shares consume the total, untouched bases become 0%.
- Resetting target shares restores the travel-efficient plan mix.
- The planner assigns exact postal-code counts by largest-remainder rounding, uses a travel-cost-aware greedy allocation, and then applies bounded count-preserving pair swaps to remove obvious local cost inefficiencies. This is a responsive scenario heuristic, not a claim of globally optimal territory scheduling.
- Coverage must remain 100%.

A target scenario is a policy/workload preview, not an optimization claim. Always compare its travel cost, travel hours, and reassigned areas with the travel-efficient baseline. Disabling target shares or selecting **Return to travel-efficient plan** restores the baseline immediately.

## 5. Cost And Time Model

Default assumptions:

```text
provider_labour_cost_per_hour = 60 CAD
gas_price_per_litre = 1.70 CAD
fuel_consumption_l_per_100km = 11.5
maintenance_and_amortization_per_km = 0.07 CAD
weekly_visits_per_postal_code = 0.05
in_home_visit_duration_min = 30
```

Vehicle cost:

```text
vehicle_cost_per_km =
  gas_price_per_litre * fuel_consumption_l_per_100km / 100
  + maintenance_and_amortization_per_km
```

Analytics:

```text
weekly_travel_hours = weekly_visits * one_way_duration_min / 60
weekly_care_hours = weekly_visits * in_home_visit_duration_min / 60
weekly_provider_hours = weekly_travel_hours + weekly_care_hours
weekly_travel_cost = weekly_visits * travel_cost_per_visit
weekly_delivery_cost = weekly_travel_cost
  + weekly_care_hours * provider_labour_cost_per_hour
```

The model charges one OSRM travel leg per visit. The UI must describe costs as estimates, not a complete operating budget.

## 6. Global And Per-Base Assumptions

Global controls set labour cost, gas price, fuel consumption, maintenance/amortization, weekly visits, and in-home visit duration.

For a selected provider base, leadership can override:

- provider labour cost per hour
- vehicle cost per kilometre
- in-home visit duration

Per-base labour and vehicle values affect route choice and cost. Per-base in-home duration affects provider-hours and delivery-cost analytics but not OSRM routing. **Use global values for this base** removes all overrides for the selected base.

## 7. Workload Analytics

For every participating base, report assigned areas, weekly visits, provider hours, visit/work share, P95 one-way time, and estimated delivery cost. Counts and averages include only bases with assigned visits; available zero-share bases remain selectable but are not described as participating. Hours are analytics only, not a capacity ceiling.

Facility marker size uses:

```text
workload_index = facility_provider_hours / maximum_facility_provider_hours
```

The busiest active base has index `1.0`. A base with no assigned visits is omitted from the allocation table and facility workload markers but remains selectable for a future target scenario.

## 8. Map Interaction

- Postal-code dots use the selected base color and retain a visual radius of `2.8` pixels.
- A 14-pixel map hit tolerance makes compact postal dots practical to click without enlarging them.
- A selected postal code receives a temporary outline only.
- Shared coordinates are grouped and expose a postal-code selector in the card.
- Postal cards show postal code and ID, provider base, OSRM time, road distance, travel cost, in-home duration, provider time, delivery cost, weekly visits, and route notes.
- Provider-base circles sit above postal dots and scale with workload index.

## 9. Route Notes

Supported notes include snap distance, long route, slow road, high circuity, forest/service road, wilderness access, terrain, and detailed-review signals.

The route-note checkbox and type menu are visual review tools only. Focus mode fades nonmatching postal dots and draws a bright halo around matching dots while preserving the base dot radius. It must not modify route ranking, assignments, costs, or target shares.

## 10. Language

Use calm, descriptive terms such as `Travel-efficient plan`, `Target workload scenario`, `Coverage mapped`, `Areas mapped`, `Provider hours`, `Workload distribution`, and `Route notes`.

Do not use `shortfall`, `overloaded`, `not feasible`, `failure`, `bad plan`, or `optimized plan` for a target scenario that may cost more than baseline.

## 11. Performance

- The initial view loads the compact asset and calculates in the main page.
- Advanced per-site routing and target-share work lazy-loads the full candidate asset in a Web Worker.
- Worker status is visible as loading/calculating text.
- Only compact selected rows return from the worker.
- Recalculation must not freeze map controls for the duration of advanced planning.

## 12. Acceptance Criteria

- All 41,176 postal codes are assigned and coverage displays as 100%.
- No straight-line distance is used.
- Default in-home care is 30 minutes and is distinct from travel time.
- A selected base can reach exactly 0% or 100% visit share while coverage remains 100%.
- Multiple manually edited base shares remain fixed while untouched shares remix to a 100% total.
- At 100%, the selected base is the only allocation row and receives all 41,176 areas.
- Target shares total 100% within rounding tolerance.
- Route-note filters do not alter travel cost or assignments.
- Postal cards open without increasing postal-dot visual radius.
- Shared-coordinate postal cards permit selecting each postal code at that coordinate.
- Site labour/vehicle overrides can alter route choice; site in-home duration updates analytics.
- Disabling target shares restores the travel-efficient plan.
- No capacity, shortfall, overload, infeasibility, or hostile recommendation language appears.
- Desktop and mobile layouts have no incoherent overlap or horizontal overflow.

## 13. Reproduction

```bash
python3 scripts/build_fha_home_health_demo_assets.py
make test
make compile
cd demo
python3 -m http.server 8000
```

Open `http://127.0.0.1:8000/`.
