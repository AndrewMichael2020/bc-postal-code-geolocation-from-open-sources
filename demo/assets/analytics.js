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

export function buildFsaClusters(postalCodes, options) {
  const clusters = new Map();
  for (const postalCode of postalCodes) {
    if (!clusters.has(postalCode.fsa)) {
      clusters.set(postalCode.fsa, {
        fsa: postalCode.fsa,
        postalCodeCount: 0,
        demand: 0,
        latitudeTotal: 0,
        longitudeTotal: 0,
        segments: { urban: 0, suburban: 0, rural: 0 },
      });
    }
    const cluster = clusters.get(postalCode.fsa);
    const demand = options.baseDemand * segmentDemandWeight(postalCode.segment, options);
    cluster.postalCodeCount += 1;
    cluster.demand += demand;
    cluster.latitudeTotal += postalCode.latitude;
    cluster.longitudeTotal += postalCode.longitude;
    cluster.segments[postalCode.segment] += 1;
  }
  return [...clusters.values()]
    .map((cluster) => ({
      ...cluster,
      latitude: cluster.latitudeTotal / cluster.postalCodeCount,
      longitude: cluster.longitudeTotal / cluster.postalCodeCount,
    }))
    .sort((a, b) => a.fsa.localeCompare(b.fsa));
}

export function inheritedPlanHub(cluster, activeHubs) {
  const byId = new Map(activeHubs.map((hub) => [hub.id, hub]));
  const fallback = nearestHub(cluster, activeHubs)?.hub ?? activeHubs[0];
  if (cluster.longitude < -123.15) {
    return byId.get("richmond") ?? fallback;
  }
  if (cluster.latitude >= 49.29 && cluster.longitude < -122.78) {
    return byId.get("north_vancouver") ?? fallback;
  }
  if (cluster.longitude > -122.08) {
    return byId.get("chilliwack") ?? fallback;
  }
  if (cluster.longitude > -122.46) {
    return byId.get("abbotsford") ?? fallback;
  }
  if (cluster.longitude > -122.75 && cluster.latitude < 49.2) {
    return byId.get("surrey") ?? fallback;
  }
  if (cluster.longitude > -122.92) {
    return byId.get("burnaby") ?? fallback;
  }
  return byId.get("vancouver") ?? fallback;
}

export function optimizedPlanHub(cluster, activeHubs, runningDemand, options) {
  const averageCapacity =
    activeHubs.reduce((sum, hub) => sum + hub.capacity * options.capacityMultiplier, 0) /
    activeHubs.length;
  let best = null;
  for (const hub of activeHubs) {
    const distanceKm = haversineKm(cluster.latitude, cluster.longitude, hub.latitude, hub.longitude);
    const projectedDemand = (runningDemand.get(hub.id) ?? 0) + cluster.demand;
    const overloadRatio = Math.max(
      0,
      projectedDemand / (hub.capacity * options.capacityMultiplier) - 1
    );
    const balanceRatio = averageCapacity ? projectedDemand / averageCapacity : 0;
    const score =
      distanceKm * (1 + options.distancePenalty * 35) +
      overloadRatio * 55 +
      Math.max(0, balanceRatio - 1.15) * 16;
    if (!best || score < best.score) {
      best = { hub, distanceKm, score };
    }
  }
  return best;
}

export function assignClusters(clusters, hubs, options, mode = "optimized") {
  const activeHubs = hubs.filter((hub) => options.activeHubIds.has(hub.id));
  const allocations = new Map();
  const fsaSummaries = [];
  const distances = [];
  let totalDemand = 0;
  const runningDemand = new Map();

  for (const hub of activeHubs) {
    allocations.set(hub.id, {
      hub,
      postalCodeCount: 0,
      demand: 0,
      distanceBurdenKm: 0,
      distances: [],
      overloaded: false,
    });
    runningDemand.set(hub.id, 0);
  }

  const orderedClusters =
    mode === "optimized" ? [...clusters].sort((a, b) => b.demand - a.demand) : clusters;

  for (const cluster of orderedClusters) {
    if (!activeHubs.length) {
      continue;
    }
    const assignment =
      mode === "inherited"
        ? { hub: inheritedPlanHub(cluster, activeHubs) }
        : optimizedPlanHub(cluster, activeHubs, runningDemand, options);
    if (!assignment?.hub) {
      continue;
    }
    const distanceKm = haversineKm(
      cluster.latitude,
      cluster.longitude,
      assignment.hub.latitude,
      assignment.hub.longitude
    );
    const allocation = allocations.get(assignment.hub.id);
    allocation.postalCodeCount += cluster.postalCodeCount;
    allocation.demand += cluster.demand;
    allocation.distanceBurdenKm += distanceKm * cluster.demand;
    allocation.distances.push(distanceKm);
    distances.push(distanceKm);
    totalDemand += cluster.demand;
    runningDemand.set(assignment.hub.id, (runningDemand.get(assignment.hub.id) ?? 0) + cluster.demand);
    fsaSummaries.push({
      fsa: cluster.fsa,
      hubId: assignment.hub.id,
      hubName: assignment.hub.name,
      color: assignment.hub.color,
      postalCodeCount: cluster.postalCodeCount,
      demand: cluster.demand,
      latitude: cluster.latitude,
      longitude: cluster.longitude,
    });
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
      meanDistanceKm: allocation.demand ? allocation.distanceBurdenKm / allocation.demand : 0,
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

  return {
    mode,
    activeHubCount: activeHubs.length,
    postalCodeCount: clusters.reduce((sum, cluster) => sum + cluster.postalCodeCount, 0),
    fsaCount: clusters.length,
    assignedPostalCodeCount: summaries.reduce((sum, item) => sum + item.postalCodeCount, 0),
    totalDemand,
    medianDistanceKm: percentile(distances, 50),
    p95DistanceKm: percentile(distances, 95),
    distanceBurdenKm: summaries.reduce((sum, item) => sum + item.distanceBurdenKm, 0),
    imbalanceScore,
    overloadedHubCount: summaries.filter((summary) => summary.overloaded).length,
    summaries: summaries.sort((a, b) => b.demand - a.demand),
    fsaSummaries: fsaSummaries.sort((a, b) => a.fsa.localeCompare(b.fsa)),
  };
}

export function allocatePostalCodes(postalCodes, hubs, options) {
  const clusters = buildFsaClusters(postalCodes, options);
  return assignClusters(clusters, hubs, options, "optimized");
}

export function comparePlans(before, after) {
  return {
    p95DistanceDeltaKm: after.p95DistanceKm - before.p95DistanceKm,
    medianDistanceDeltaKm: after.medianDistanceKm - before.medianDistanceKm,
    distanceBurdenDeltaKm: after.distanceBurdenKm - before.distanceBurdenKm,
    imbalanceDelta: after.imbalanceScore - before.imbalanceScore,
    overloadedHubDelta: after.overloadedHubCount - before.overloadedHubCount,
  };
}
