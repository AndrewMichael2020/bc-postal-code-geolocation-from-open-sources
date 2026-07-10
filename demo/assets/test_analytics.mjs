import assert from "node:assert/strict";
import {
  buildPlan,
  buildPlanFromCompactSelections,
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
    [[0, 10, 10, []], [1, 12, 12, []], [2, 14, 14, []]],
    [[0, 10, 10, []], [1, 12, 12, []], [2, 14, 14, []]],
    [[0, 10, 10, []], [1, 12, 12, []], [2, 14, 14, [0]]],
  ],
};

const data = hydrateDemoData(asset);
const controls = mergeControls(data.defaults, {
  activeFacilityIds: new Set(["A", "B", "C"]),
});

assert.equal(vehicleCostPerKm(controls), 0.2);
assert.equal(routeCostPerVisit(data.postalCodes[0].candidates[0], controls), 12);

const plan = buildPlan(data, controls);
assert.equal(plan.mode, "travel_efficient");
assert.equal(plan.assignedPostalCodeCount, 3);
assert.equal(plan.availableFacilityCount, 3);
assert.equal(plan.activeFacilityCount, 1);
assert.equal(plan.coverageRate, 1);
assert.equal(plan.unassigned.length, 0);
assert.equal(plan.routeNoteCount, 0);
assert.equal(plan.summaries.find((summary) => summary.facility.id === "A").postalCodeCount, 3);
assert.equal(plan.weeklyTravelHours, 0.5);
assert.equal(plan.weeklyCareHours, 1.5);
assert.equal(plan.totalProviderHours, 2);
assert.equal(plan.averageProviderHours, 2);
assert.equal(plan.weeklyTravelCost, 36);
assert.equal(plan.weeklyDeliveryCost, 126);
assert.equal(plan.workloadCeilingHours, 2);
assert.equal(plan.summaries[0].workloadIndex, 1);
assert.equal(plan.summaries[0].workloadShare, 1);
assert(plan.summaries.every((summary) => summary.workloadIndex <= 1));

const siteControls = mergeControls(data.defaults, {
  activeFacilityIds: new Set(["A", "B", "C"]),
  siteOverrides: {
    A: { laborCostPerHour: 30, vehicleCostPerKm: 0.1, visitDurationMin: 45 },
  },
});
assert.equal(routeCostPerVisit(data.postalCodes[0].candidates[0], siteControls), 6);
const sitePlan = buildPlan(data, siteControls);
assert.equal(sitePlan.totalProviderHours, 2.75);
assert.equal(sitePlan.weeklyDeliveryCost, 85.5);

const selectedPlan = buildPlanFromCompactSelections(data, controls, [
  [1, 12, 12, []],
  [0, 10, 10, []],
  [0, 10, 10, []],
]);
assert.equal(selectedPlan.assignedPostalCodeCount, 3);
assert.equal(selectedPlan.summaries.find((summary) => summary.facility.id === "B").postalCodeCount, 1);
assert.equal(selectedPlan.activeFacilityCount, 2);
assert.equal(selectedPlan.availableFacilityCount, 3);
assert.equal(selectedPlan.coverageRate, 1);

console.log("OSRM analytics tests passed");
