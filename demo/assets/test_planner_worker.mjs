import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import vm from "node:vm";

const asset = {
  facilityIds: ["A", "B", "C"],
  candidates: [
    [[0, 0, 0, []], [1, 1, 0, []], [2, 2, 0, []]],
    [[0, 0, 0, []], [1, 1.1, 0, []], [2, 100, 0, []]],
    ...Array.from({ length: 5 }, () => [[0, 0, 0, []], [1, 200, 0, []], [2, 200, 0, []]]),
  ],
};
const listeners = new Map();
const messages = [];
const context = vm.createContext({
  fetch: async () => ({ ok: true, json: async () => asset }),
  self: {
    addEventListener: (type, listener) => listeners.set(type, listener),
    postMessage: (message) => messages.push(message),
  },
});
vm.runInContext(await readFile(new URL("./planner-worker.js", import.meta.url), "utf8"), context);

const controls = {
  laborCostPerHour: 60,
  gasPricePerLitre: 1.7,
  fuelConsumptionLPer100Km: 11.5,
  maintenanceCostPerKm: 0.07,
  siteOverrides: {},
};

async function plan(requestId, targetShares) {
  messages.length = 0;
  await listeners.get("message")({ data: { type: "plan", requestId, controls, targetShares } });
  const result = messages.find((message) => message.type === "result");
  assert(result, "worker should return a result");
  return result;
}

const onlyB = await plan(1, { A: 0, B: 1 });
assert.deepEqual([...onlyB.counts], [0, 7, 0]);
assert.deepEqual([...onlyB.targets], [0, 7, 0]);
assert(onlyB.selections.every((selection) => selection[0] === 1));

const onlyA = await plan(2, { A: 1, B: 0 });
assert.deepEqual([...onlyA.counts], [7, 0, 0]);
assert.deepEqual([...onlyA.targets], [7, 0, 0]);
assert(onlyA.selections.every((selection) => selection[0] === 0));

const fractional = await plan(3, { A: 0.5, B: 0.3, C: 0.2 });
assert.deepEqual([...fractional.targets], [4, 2, 1]);
assert.deepEqual([...fractional.counts], [4, 2, 1]);

const defensiveEqual = await plan(4, { A: 0, B: 0, C: 0 });
assert.deepEqual([...defensiveEqual.targets], [3, 2, 2]);
assert.deepEqual([...defensiveEqual.counts], [3, 2, 2]);

const locallyImproved = await plan(5, { A: 5, B: 1, C: 1 });
assert.deepEqual([...locallyImproved.counts], [5, 1, 1]);
assert.equal(locallyImproved.selections.reduce((sum, row) => sum + row[1], 0), 3.1);
assert(locallyImproved.swapCount > 0);
assert(locallyImproved.swapSavings > 90);

console.log("planner worker target-share tests passed");
