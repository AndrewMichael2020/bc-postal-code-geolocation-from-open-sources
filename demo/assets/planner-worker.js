let advancedAssetPromise = null;

function loadAdvancedAsset() {
  if (!advancedAssetPromise) {
    advancedAssetPromise = fetch("../data/fha-home-health-advanced-candidates.json").then((response) => {
      if (!response.ok) throw new Error(`Advanced route data could not be loaded (${response.status}).`);
      return response.json();
    });
  }
  return advancedAssetPromise;
}

function baseVehicleCost(controls) {
  return (
    (controls.gasPricePerLitre * controls.fuelConsumptionLPer100Km) / 100 +
    controls.maintenanceCostPerKm
  );
}

function candidateCost(candidate, facilityId, controls) {
  const override = controls.siteOverrides?.[facilityId] ?? {};
  const laborCost = override.laborCostPerHour ?? controls.laborCostPerHour;
  const vehicleCost = override.vehicleCostPerKm ?? baseVehicleCost(controls);
  return candidate[1] * (laborCost / 60) + candidate[2] * vehicleCost;
}

function targetCounts(facilityIds, shares, total) {
  const normalizedTotal = facilityIds.reduce((sum, id) => sum + Math.max(0, shares?.[id] ?? 0), 0);
  const equalShare = 1 / facilityIds.length;
  const values = facilityIds.map((id, index) => {
    const share = normalizedTotal ? Math.max(0, shares?.[id] ?? 0) / normalizedTotal : equalShare;
    const exact = share * total;
    return { index, floor: Math.floor(exact), remainder: exact - Math.floor(exact) };
  });
  let remaining = total - values.reduce((sum, item) => sum + item.floor, 0);
  values.sort((a, b) => b.remainder - a.remainder || a.index - b.index);
  for (let index = 0; index < remaining; index += 1) values[index].floor += 1;
  values.sort((a, b) => a.index - b.index);
  return values.map((item) => item.floor);
}

function chooseLowestCost(asset, controls) {
  return asset.candidates.map((candidates) => {
    let best = candidates[0];
    let bestCost = candidateCost(best, asset.facilityIds[best[0]], controls);
    for (let index = 1; index < candidates.length; index += 1) {
      const candidate = candidates[index];
      const cost = candidateCost(candidate, asset.facilityIds[candidate[0]], controls);
      if (cost < bestCost || (cost === bestCost && candidate[1] < best[1])) {
        best = candidate;
        bestCost = cost;
      }
    }
    return best;
  });
}

function applyTargetShares(asset, controls, selections, shares) {
  const targets = targetCounts(asset.facilityIds, shares, selections.length);
  const counts = Array(asset.facilityIds.length).fill(0);
  for (const selection of selections) counts[selection[0]] += 1;

  const underTarget = new Set();
  for (let index = 0; index < targets.length; index += 1) {
    if (counts[index] < targets[index]) underTarget.add(index);
  }

  const options = [];
  for (let postalIndex = 0; postalIndex < selections.length; postalIndex += 1) {
    const current = selections[postalIndex];
    const origin = current[0];
    if (counts[origin] <= targets[origin]) continue;
    const currentCost = candidateCost(current, asset.facilityIds[origin], controls);
    for (const candidate of asset.candidates[postalIndex]) {
      const destination = candidate[0];
      if (!underTarget.has(destination)) continue;
      options.push({
        postalIndex,
        origin,
        destination,
        candidate,
        extraCost: candidateCost(candidate, asset.facilityIds[destination], controls) - currentCost,
      });
    }
  }

  options.sort((a, b) => a.extraCost - b.extraCost || a.candidate[1] - b.candidate[1]);
  let movedCount = 0;
  for (const option of options) {
    if (selections[option.postalIndex][0] !== option.origin) continue;
    if (counts[option.origin] <= targets[option.origin]) continue;
    if (counts[option.destination] >= targets[option.destination]) continue;
    selections[option.postalIndex] = option.candidate;
    counts[option.origin] -= 1;
    counts[option.destination] += 1;
    movedCount += 1;
  }

  return { selections, counts, targets, movedCount };
}

self.addEventListener("message", async (event) => {
  if (event.data?.type !== "plan") return;
  const { requestId, controls, targetShares } = event.data;
  try {
    self.postMessage({ type: "status", requestId, status: "loading" });
    const asset = await loadAdvancedAsset();
    self.postMessage({ type: "status", requestId, status: "planning" });
    let selections = chooseLowestCost(asset, controls);
    let details = { movedCount: 0, counts: null, targets: null };
    if (targetShares) {
      details = applyTargetShares(asset, controls, [...selections], targetShares);
      selections = details.selections;
    }
    self.postMessage({
      type: "result",
      requestId,
      selections,
      movedCount: details.movedCount,
      counts: details.counts,
      targets: details.targets,
    });
  } catch (error) {
    self.postMessage({ type: "error", requestId, message: error.message || String(error) });
  }
});
