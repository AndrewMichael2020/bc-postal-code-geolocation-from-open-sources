import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import vm from "node:vm";

const asset = {
  facilityIds: ["A", "B"],
  candidates: Array.from({ length: 4 }, () => [
    [0, 1, 1, []],
    [1, 5, 5, []],
  ]),
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
assert.deepEqual([...onlyB.counts], [0, 4]);
assert.deepEqual([...onlyB.targets], [0, 4]);
assert(onlyB.selections.every((selection) => selection[0] === 1));

const onlyA = await plan(2, { A: 1, B: 0 });
assert.deepEqual([...onlyA.counts], [4, 0]);
assert.deepEqual([...onlyA.targets], [4, 0]);
assert(onlyA.selections.every((selection) => selection[0] === 0));

console.log("planner worker target-share tests passed");
