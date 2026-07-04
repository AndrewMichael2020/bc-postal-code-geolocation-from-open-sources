export const KM_PER_MINUTE_PROXY = 0.9;

export function haversineKm(aLat, aLon, bLat, bLon) {
  const radiusKm = 6371.0088;
  const toRad = (degrees) => (degrees * Math.PI) / 180;
  const dLat = toRad(bLat - aLat);
  const dLon = toRad(bLon - aLon);
  const lat1 = toRad(aLat);
  const lat2 = toRad(bLat);
  const h =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(lat1) * Math.cos(lat2) * Math.sin(dLon / 2) ** 2;
  return 2 * radiusKm * Math.asin(Math.sqrt(h));
}

export function nearestHub(postalCode, hubs) {
  if (!hubs.length) {
    return null;
  }
  let best = null;
  for (const hub of hubs) {
    const distanceKm = haversineKm(
      postalCode.latitude,
      postalCode.longitude,
      hub.latitude,
      hub.longitude
    );
    if (!best || distanceKm < best.distanceKm) {
      best = { hub, distanceKm };
    }
  }
  return best;
}

export function segmentDemandWeight(segment, options) {
  if (segment === "urban") {
    return options.urbanMultiplier;
  }
  if (segment === "rural") {
    return options.ruralMultiplier;
  }
  return 1;
}

export function percentile(values, percentileRank) {
  if (!values.length) {
    return 0;
  }
  const sorted = [...values].sort((a, b) => a - b);
  const index = Math.min(
    sorted.length - 1,
    Math.max(0, Math.ceil((percentileRank / 100) * sorted.length) - 1)
  );
  return sorted[index];
}

export function allocatePostalCodes(postalCodes, hubs, options) {
  const activeHubs = hubs.filter((hub) => options.activeHubIds.has(hub.id));
  const allocations = new Map();
  const fsaGroups = new Map();
  const distances = [];
  let totalDemand = 0;

  for (const hub of activeHubs) {
    allocations.set(hub.id, {
      hub,
      postalCodeCount: 0,
      demand: 0,
      distanceBurdenKm: 0,
      distances: [],
      overloaded: false,
    });
  }

  for (const postalCode of postalCodes) {
    const assignment = nearestHub(postalCode, activeHubs);
    if (!assignment) {
      continue;
    }
    const demand =
      options.baseDemand *
      segmentDemandWeight(postalCode.segment, options) *
      (1 + assignment.distanceKm * options.distancePenalty);
    const allocation = allocations.get(assignment.hub.id);
    allocation.postalCodeCount += 1;
    allocation.demand += demand;
    allocation.distanceBurdenKm += assignment.distanceKm * demand;
    allocation.distances.push(assignment.distanceKm);
    distances.push(assignment.distanceKm);
    totalDemand += demand;

    const fsa = postalCode.fsa;
    if (!fsaGroups.has(fsa)) {
      fsaGroups.set(fsa, {
        fsa,
        hubId: assignment.hub.id,
        hubName: assignment.hub.name,
        color: assignment.hub.color,
        postalCodeCount: 0,
        demand: 0,
        latitudeTotal: 0,
        longitudeTotal: 0,
      });
    }
    const fsaGroup = fsaGroups.get(fsa);
    fsaGroup.postalCodeCount += 1;
    fsaGroup.demand += demand;
    fsaGroup.latitudeTotal += postalCode.latitude;
    fsaGroup.longitudeTotal += postalCode.longitude;
  }

  const summaries = [...allocations.values()].map((allocation) => {
    const capacity = allocation.hub.capacity * options.capacityMultiplier;
    allocation.overloaded = allocation.demand > capacity;
    return {
      hub: allocation.hub,
      postalCodeCount: allocation.postalCodeCount,
      demand: allocation.demand,
      capacity,
      utilization: capacity ? allocation.demand / capacity : 0,
      medianDistanceKm: percentile(allocation.distances, 50),
      p95DistanceKm: percentile(allocation.distances, 95),
      distanceBurdenKm: allocation.distanceBurdenKm,
      overloaded: allocation.overloaded,
    };
  });

  const demandValues = summaries.map((summary) => summary.demand);
  const averageDemand = demandValues.length
    ? demandValues.reduce((sum, value) => sum + value, 0) / demandValues.length
    : 0;
  const imbalanceScore = averageDemand
    ? Math.max(...demandValues.map((value) => Math.abs(value - averageDemand))) / averageDemand
    : 0;

  const fsaSummaries = [...fsaGroups.values()].map((fsaGroup) => ({
    ...fsaGroup,
    latitude: fsaGroup.latitudeTotal / fsaGroup.postalCodeCount,
    longitude: fsaGroup.longitudeTotal / fsaGroup.postalCodeCount,
  }));

  return {
    activeHubCount: activeHubs.length,
    postalCodeCount: postalCodes.length,
    assignedPostalCodeCount: summaries.reduce((sum, item) => sum + item.postalCodeCount, 0),
    totalDemand,
    medianDistanceKm: percentile(distances, 50),
    p95DistanceKm: percentile(distances, 95),
    imbalanceScore,
    overloadedHubCount: summaries.filter((summary) => summary.overloaded).length,
    summaries: summaries.sort((a, b) => b.demand - a.demand),
    fsaSummaries,
  };
}
