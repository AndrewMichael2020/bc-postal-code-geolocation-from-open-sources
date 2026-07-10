export const DEFAULT_CONTROLS = {
  laborCostPerHour: 60,
  gasPricePerLitre: 1.7,
  fuelConsumptionLPer100Km: 11.5,
  maintenanceCostPerKm: 0.07,
  visitsPerPostalCode: 0.05,
  visitDurationMin: 45,
  includeQaPenalties: false,
};

export const QA_PENALTIES = new Map([
  ["snap warning", 5],
  ["long route", 3],
  ["very long route", 8],
  ["slow route", 5],
  ["very slow route", 10],
  ["high circuity", 3],
  ["forest/service road", 10],
  ["wilderness access", 15],
  ["terrain warning", 5],
  ["route detail warning", 2],
]);

export function mergeControls(defaults = {}, overrides = {}) {
  return {
    ...DEFAULT_CONTROLS,
    ...defaults,
    ...overrides,
    activeFacilityIds: overrides.activeFacilityIds ?? defaults.activeFacilityIds ?? null,
  };
}

export function vehicleCostPerKm(controls) {
  return (
    (controls.gasPricePerLitre * controls.fuelConsumptionLPer100Km) / 100 +
    controls.maintenanceCostPerKm
  );
}

export function laborCostPerMinute(controls) {
  return controls.laborCostPerHour / 60;
}

export function qaPenaltyPerVisit(warnings, controls) {
  if (!controls.includeQaPenalties) {
    return 0;
  }
  return warnings.reduce((sum, warning) => sum + (QA_PENALTIES.get(warning) ?? 0), 0);
}

export function routeCostPerVisit(candidate, controls) {
  return (
    candidate.durationMin * laborCostPerMinute(controls) +
    candidate.distanceKm * vehicleCostPerKm(controls) +
    qaPenaltyPerVisit(candidate.warnings, controls)
  );
}

export function weightedPercentile(items, valueField, weightField, percentileRank) {
  const weighted = items
    .map((item) => ({ value: item[valueField], weight: item[weightField] }))
    .filter((item) => Number.isFinite(item.value) && item.weight > 0)
    .sort((a, b) => a.value - b.value);
  if (!weighted.length) {
    return 0;
  }
  const totalWeight = weighted.reduce((sum, item) => sum + item.weight, 0);
  const threshold = totalWeight * (percentileRank / 100);
  let running = 0;
  for (const item of weighted) {
    running += item.weight;
    if (running >= threshold) {
      return item.value;
    }
  }
  return weighted[weighted.length - 1].value;
}

export function hydrateDemoData(asset) {
  const warningCatalog = asset.warningCatalog ?? [];
  const facilities = asset.facilities.map((row, index) => ({
    index,
    id: row[0],
    name: row[1],
    type: row[2],
    address: row[3],
    latitude: row[4],
    longitude: row[5],
    color: row[6],
  }));
  const postalCodes = asset.postalCodes.map((row, index) => ({
    index,
    id: row[0],
    postalCode: row[1],
    latitude: row[2],
    longitude: row[3],
    candidates: (asset.candidates[index] ?? []).map((candidate) => ({
      facility: facilities[candidate[0]],
      facilityIndex: candidate[0],
      durationMin: candidate[1],
      distanceKm: candidate[2],
      warnings: (candidate[3] ?? []).map((warningIndex) => warningCatalog[warningIndex]),
    })),
  }));
  return {
    schemaVersion: asset.schemaVersion,
    source: asset.source,
    defaults: asset.defaults ?? {},
    warningCatalog,
    facilities,
    postalCodes,
  };
}

export function activeFacilitySet(data, controls) {
  if (controls.activeFacilityIds instanceof Set) {
    return controls.activeFacilityIds;
  }
  return new Set(data.facilities.map((facility) => facility.id));
}

export function rankCandidates(postalCode, data, controls) {
  const activeIds = activeFacilitySet(data, controls);
  return postalCode.candidates
    .filter((candidate) => activeIds.has(candidate.facility.id))
    .map((candidate) => ({
      ...candidate,
      routeCost: routeCostPerVisit(candidate, controls),
    }))
    .sort((a, b) => a.routeCost - b.routeCost || a.durationMin - b.durationMin);
}

export function makeAssignment(postalCode, candidate, controls) {
  const visits = controls.visitsPerPostalCode;
  const travelHours = (candidate.durationMin * visits) / 60;
  const vehicleKm = candidate.distanceKm * visits;
  const weeklyLaborCost = candidate.durationMin * laborCostPerMinute(controls) * visits;
  const weeklyVehicleCost = candidate.distanceKm * vehicleCostPerKm(controls) * visits;
  const weeklyQaCost = qaPenaltyPerVisit(candidate.warnings, controls) * visits;
  const weeklyCost = weeklyLaborCost + weeklyVehicleCost + weeklyQaCost;
  const serviceHours = ((candidate.durationMin + controls.visitDurationMin) * visits) / 60;
  return {
    postalIndex: postalCode.index,
    postalCodeId: postalCode.id,
    postalCode: postalCode.postalCode,
    latitude: postalCode.latitude,
    longitude: postalCode.longitude,
    facility: candidate.facility,
    durationMin: candidate.durationMin,
    distanceKm: candidate.distanceKm,
    routeCost: candidate.routeCost,
    visits,
    travelHours,
    vehicleKm,
    weeklyLaborCost,
    weeklyVehicleCost,
    weeklyQaCost,
    weeklyCost,
    serviceHours,
    warnings: candidate.warnings,
    hasRouteNotes: candidate.warnings.length > 0,
  };
}

function emptyFacilitySummary(facility, controls) {
  return {
    facility,
    postalCodeCount: 0,
    visits: 0,
    travelHours: 0,
    vehicleKm: 0,
    weeklyLaborCost: 0,
    weeklyVehicleCost: 0,
    weeklyQaCost: 0,
    weeklyCost: 0,
    serviceHours: 0,
    workloadShare: 0,
    workloadIndex: 0,
    p95DurationMin: 0,
    medianDurationMin: 0,
    assignments: [],
  };
}

export function summarizeAssignments(assignments, data, controls, mode) {
  const activeIds = activeFacilitySet(data, controls);
  const summaryByFacility = new Map();
  for (const facility of data.facilities.filter((item) => activeIds.has(item.id))) {
    summaryByFacility.set(facility.id, emptyFacilitySummary(facility, controls));
  }
  for (const assignment of assignments) {
    const summary = summaryByFacility.get(assignment.facility.id);
    if (!summary) {
      continue;
    }
    summary.postalCodeCount += 1;
    summary.visits += assignment.visits;
    summary.travelHours += assignment.travelHours;
    summary.vehicleKm += assignment.vehicleKm;
    summary.weeklyLaborCost += assignment.weeklyLaborCost;
    summary.weeklyVehicleCost += assignment.weeklyVehicleCost;
    summary.weeklyQaCost += assignment.weeklyQaCost;
    summary.weeklyCost += assignment.weeklyCost;
    summary.serviceHours += assignment.serviceHours;
    summary.assignments.push(assignment);
  }
  const totalServiceHours = assignments.reduce((sum, item) => sum + item.serviceHours, 0);
  const workloadCeilingHours = Math.max(
    0,
    ...[...summaryByFacility.values()].map((summary) => summary.serviceHours)
  );
  for (const summary of summaryByFacility.values()) {
    summary.workloadShare = totalServiceHours ? summary.serviceHours / totalServiceHours : 0;
    summary.workloadIndex = workloadCeilingHours ? summary.serviceHours / workloadCeilingHours : 0;
    summary.medianDurationMin = weightedPercentile(summary.assignments, "durationMin", "visits", 50);
    summary.p95DurationMin = weightedPercentile(summary.assignments, "durationMin", "visits", 95);
  }

  const summaries = [...summaryByFacility.values()].sort((a, b) => b.serviceHours - a.serviceHours);
  const routeNotes = assignments
    .filter((assignment) => assignment.hasRouteNotes)
    .sort((a, b) => b.durationMin - a.durationMin || b.distanceKm - a.distanceKm);
  const travelRows = assignments.filter((assignment) => assignment.visits > 0);
  return {
    mode,
    activeFacilityCount: activeIds.size,
    assignedPostalCodeCount: assignments.length,
    totalVisits: assignments.reduce((sum, item) => sum + item.visits, 0),
    weeklyTravelHours: assignments.reduce((sum, item) => sum + item.travelHours, 0),
    weeklyVehicleKm: assignments.reduce((sum, item) => sum + item.vehicleKm, 0),
    weeklyLaborCost: assignments.reduce((sum, item) => sum + item.weeklyLaborCost, 0),
    weeklyVehicleCost: assignments.reduce((sum, item) => sum + item.weeklyVehicleCost, 0),
    weeklyQaCost: assignments.reduce((sum, item) => sum + item.weeklyQaCost, 0),
    weeklyCost: assignments.reduce((sum, item) => sum + item.weeklyCost, 0),
    totalServiceHours,
    averageServiceHours: summaries.length ? totalServiceHours / summaries.length : 0,
    workloadCeilingHours,
    medianDurationMin: weightedPercentile(travelRows, "durationMin", "visits", 50),
    p95DurationMin: weightedPercentile(travelRows, "durationMin", "visits", 95),
    medianCostPerVisit: weightedPercentile(travelRows, "routeCost", "visits", 50),
    p95CostPerVisit: weightedPercentile(travelRows, "routeCost", "visits", 95),
    routeNoteCount: routeNotes.length,
    coverageRate: data.postalCodes.length ? assignments.length / data.postalCodes.length : 0,
    summaries,
    assignments,
    routeNotes,
  };
}

export function lowestCostAssignments(data, controls) {
  const assignments = [];
  const unassigned = [];
  for (const postalCode of data.postalCodes) {
    const candidates = rankCandidates(postalCode, data, controls);
    if (!candidates.length) {
      unassigned.push(postalCode);
      continue;
    }
    assignments.push(makeAssignment(postalCode, candidates[0], controls));
  }
  return { assignments, unassigned };
}

export function buildPlan(data, controls) {
  const mergedControls = mergeControls(data.defaults, controls);
  const base = lowestCostAssignments(data, mergedControls);
  const result = summarizeAssignments(base.assignments, data, mergedControls, "travel_efficient");
  result.unassigned = base.unassigned;
  return result;
}
