import assert from "node:assert/strict";
import {
  allocatePostalCodes,
  assignClusters,
  buildFsaClusters,
  comparePlans,
  haversineKm,
  nearestHub,
  percentile,
} from "./analytics.js";

const vancouverToVictoria = haversineKm(49.2827, -123.1207, 48.4284, -123.3656);
assert(vancouverToVictoria > 90 && vancouverToVictoria < 110);

const hubs = [
  {
    id: "west",
    name: "West Hub",
    latitude: 49.28,
    longitude: -123.12,
    capacity: 100,
    color: "#000",
  },
  {
    id: "east",
    name: "East Hub",
    latitude: 49.05,
    longitude: -122.3,
    capacity: 100,
    color: "#111",
  },
];

const postalCodes = [
  { postal_code: "V6B 1A1", fsa: "V6B", latitude: 49.28, longitude: -123.11, segment: "urban" },
  { postal_code: "V2S 1A1", fsa: "V2S", latitude: 49.05, longitude: -122.31, segment: "suburban" },
  { postal_code: "V0M 1A1", fsa: "V0M", latitude: 49.3, longitude: -121.8, segment: "rural" },
];

assert.equal(nearestHub(postalCodes[0], hubs).hub.id, "west");
assert.equal(percentile([1, 2, 3, 4, 5], 50), 3);
assert.equal(percentile([1, 2, 3, 4, 5], 95), 5);

const result = allocatePostalCodes(postalCodes, hubs, {
  activeHubIds: new Set(["west", "east"]),
  baseDemand: 1,
  urbanMultiplier: 1.2,
  ruralMultiplier: 0.7,
  capacityMultiplier: 1,
  distancePenalty: 0,
});
assert.equal(result.assignedPostalCodeCount, 3);
assert.equal(result.activeHubCount, 2);
assert(result.totalDemand > 2.8 && result.totalDemand < 3.0);
assert.equal(result.summaries.some((summary) => summary.hub.id === "west"), true);

const eastOnly = allocatePostalCodes(postalCodes, hubs, {
  activeHubIds: new Set(["east"]),
  baseDemand: 1,
  urbanMultiplier: 1,
  ruralMultiplier: 1,
  capacityMultiplier: 1,
  distancePenalty: 0,
});
assert.equal(eastOnly.activeHubCount, 1);
assert.equal(eastOnly.summaries[0].postalCodeCount, 3);

const clusters = buildFsaClusters(
  [
    ...postalCodes,
    { postal_code: "V6B 1A2", fsa: "V6B", latitude: 49.29, longitude: -123.1, segment: "urban" },
  ],
  {
    baseDemand: 1,
    urbanMultiplier: 1,
    ruralMultiplier: 1,
  }
);
assert.equal(clusters.length, 3);
assert.equal(clusters.find((cluster) => cluster.fsa === "V6B").postalCodeCount, 2);

const namedHubs = [
  {
    id: "vancouver",
    name: "Vancouver",
    latitude: 49.2827,
    longitude: -123.1207,
    capacity: 2,
    color: "#2563eb",
  },
  {
    id: "richmond",
    name: "Richmond",
    latitude: 49.1666,
    longitude: -123.1336,
    capacity: 2,
    color: "#16a34a",
  },
  {
    id: "abbotsford",
    name: "Abbotsford",
    latitude: 49.0504,
    longitude: -122.3045,
    capacity: 2,
    color: "#0891b2",
  },
];
const planOptions = {
  activeHubIds: new Set(namedHubs.map((hub) => hub.id)),
  baseDemand: 1,
  urbanMultiplier: 1,
  ruralMultiplier: 1,
  capacityMultiplier: 1,
  distancePenalty: 0.012,
};
const inherited = assignClusters(clusters, namedHubs, planOptions, "inherited");
const optimized = assignClusters(clusters, namedHubs, planOptions, "optimized");
assert.equal(inherited.fsaSummaries.length, clusters.length);
assert.equal(optimized.fsaSummaries.length, clusters.length);
assert(optimized.summaries.filter((summary) => summary.postalCodeCount > 0).length > 1);
assert.equal(typeof comparePlans(inherited, optimized).distanceBurdenDeltaKm, "number");

const richmondOnlyCurrent = assignClusters(
  clusters,
  namedHubs,
  { ...planOptions, activeHubIds: new Set(["richmond"]) },
  "inherited"
);
const fullNetworkRecommendation = assignClusters(clusters, namedHubs, planOptions, "optimized");
assert.equal(
  richmondOnlyCurrent.summaries.filter((summary) => summary.postalCodeCount > 0).length,
  1
);
assert(fullNetworkRecommendation.summaries.filter((summary) => summary.postalCodeCount > 0).length > 1);

console.log("analytics tests passed");
