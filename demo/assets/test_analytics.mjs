import assert from "node:assert/strict";
import {
  buildPlan,
  classifyVerdict,
  comparePlans,
  guardrailBreaches,
  hydrateDemoData,
  mergeControls,
  routeCostPerVisit,
  vehicleCostPerKm,
} from "./analytics.js";

const asset = {
  schemaVersion: 1,
  defaults: {
    laborCostPerHour: 60,
    gasPricePerLitre: 2,
    fuelConsumptionLPer100Km: 10,
    maintenanceCostPerKm: 0,
    visitsPerPostalCode: 1,
    visitDurationMin: 30,
    capacityHoursPerFacility: 0.9,
    maxExtraTravelMin: 5,
    maxExtraDistanceKm: 10,
    maxRelativeCostPenalty: 0.5,
  },
  warningCatalog: ["long route", "snap warning"],
  facilities: [
    ["A", "Alpha", "hospital", "1 Alpha Way", 49.1, -122.1, "#2563eb"],
    ["B", "Bravo", "upcc", "2 Bravo Way", 49.2, -122.2, "#16a34a"],
    ["C", "Charlie", "upcc", "3 Charlie Way", 49.3, -122.3, "#dc2626"],
  ],
  postalCodes: [
    ["P1", "V1A 1A1", 49.11, -122.11],
    ["P2", "V1A 1A2", 49.12, -122.12],
    ["P3", "V1A 1A3", 49.13, -122.13],
  ],
  candidates: [
    [
      [0, 10, 10, []],
      [1, 12, 12, []],
      [2, 14, 14, []],
    ],
    [
      [0, 10, 10, []],
      [1, 12, 12, []],
      [2, 14, 14, []],
    ],
    [
      [0, 10, 10, []],
      [1, 12, 12, []],
      [2, 14, 14, [0]],
    ],
  ],
};

const data = hydrateDemoData(asset);
const controls = mergeControls(data.defaults, {
  activeFacilityIds: new Set(["A", "B", "C"]),
});

assert.equal(vehicleCostPerKm(controls), 0.2);
assert.equal(routeCostPerVisit(data.postalCodes[0].candidates[0], controls), 12);

const baseline = buildPlan(data, controls, "lowest_cost");
assert.equal(baseline.assignedPostalCodeCount, 3);
assert.equal(baseline.exceptionCount, 0);
assert(baseline.shortfallHours > 1);
assert.equal(baseline.summaries.find((summary) => summary.facility.id === "A").postalCodeCount, 3);

const optimized = buildPlan(data, controls, "optimized");
assert.equal(optimized.shortfallHours, 0);
assert.equal(optimized.exceptionCount, 2);
assert.equal(optimized.summaries.filter((summary) => summary.postalCodeCount > 0).length, 3);
assert(comparePlans(baseline, optimized).shortfallHoursDelta < 0);
assert.equal(classifyVerdict(baseline, optimized).label, "Trade-off");

const tightControls = mergeControls(data.defaults, {
  activeFacilityIds: new Set(["A", "B", "C"]),
  maxExtraTravelMin: 1,
});
const tightOptimized = buildPlan(data, tightControls, "optimized");
assert(tightOptimized.shortfallHours > 1);
assert.equal(classifyVerdict(baseline, tightOptimized).label, "Not feasible");

const ranked = data.postalCodes[0].candidates.map((candidate) => ({
  ...candidate,
  routeCost: routeCostPerVisit(candidate, controls),
}));
assert.deepEqual(guardrailBreaches(ranked[2], ranked[0], tightControls), ["extra travel time"]);

const qaControls = mergeControls(data.defaults, {
  activeFacilityIds: new Set(["A", "B", "C"]),
  includeQaPenalties: true,
});
assert(routeCostPerVisit(data.postalCodes[2].candidates[2], qaControls) > 16.8);

console.log("OSRM analytics tests passed");
