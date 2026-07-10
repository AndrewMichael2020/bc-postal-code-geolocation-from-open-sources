export const DEFAULT_CONTROLS = {
  laborCostPerHour: 60,
  gasPricePerLitre: 1.7,
  fuelConsumptionLPer100Km: 11.5,
  maintenanceCostPerKm: 0.07,
  visitsPerPostalCode: 0.05,
  visitDurationMin: 30,
  siteOverrides: {},
};

export function mergeControls(defaults = {}, overrides = {}) {
  return {
    ...DEFAULT_CONTROLS,
    ...defaults,
    ...overrides,
    activeFacilityIds: overrides.activeFacilityIds ?? defaults.activeFacilityIds ?? null,
    siteOverrides: overrides.siteOverrides ?? defaults.siteOverrides ?? {},
  };
}

export function vehicleCostPerKm(controls) {
  return (
    (controls.gasPricePerLitre * controls.fuelConsumptionLPer100Km) / 100 +
    controls.maintenanceCostPerKm
  );
}

export function siteAssumptions(facility, controls) {
  const override = controls.siteOverrides?.[facility.id] ?? {};
  return {
    laborCostPerHour: override.laborCostPerHour ?? controls.laborCostPerHour,
    vehicleCostPerKm: override.vehicleCostPerKm ?? vehicleCostPerKm(controls),
    visitDurationMin: override.visitDurationMin ?? controls.visitDurationMin,
  };
}

export function laborCostPerMinute(controls, facility = null) {
  const hourly = facility ? siteAssumptions(facility, controls).laborCostPerHour : controls.laborCostPerHour;
  return hourly / 60;
}

export function routeCostPerVisit(candidate, controls) {
  const assumptions = siteAssumptions(candidate.facility, controls);
  return (
    candidate.durationMin * (assumptions.laborCostPerHour / 60) +
    candidate.distanceKm * assumptions.vehicleCostPerKm
  );
}

export function weightedPercentile(items, valueField, weightField, percentileRank) {
  const weighted = items
    .map((item) => ({ value: item[valueField], weight: item[weightField] }))
    .filter((item) => Number.isFinite(item.value) && item.weight > 0)
    .sort((a, b) => a.value - b.value);
  if (!weighted.length) return 0;
  const threshold = weighted.reduce((sum, item) => sum + item.weight, 0) * (percentileRank / 100);
  let running = 0;
  for (const item of weighted) {
    running += item.weight;
    if (running >= threshold) return item.value;
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
    candidates: (asset.candidates[index] ?? []).map((candidate) => compactCandidate(candidate, facilities, warningCatalog)),
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

function compactCandidate(row, facilities, warningCatalog) {
  return {
    facility: facilities[row[0]],
    facilityIndex: row[0],
    durationMin: row[1],
    distanceKm: row[2],
    warnings: (row[3] ?? []).map((warningIndex) => warningCatalog[warningIndex]),
  };
}

export function activeFacilitySet(data, controls) {
  if (controls.activeFacilityIds instanceof Set) return controls.activeFacilityIds;
  return new Set(data.facilities.map((facility) => facility.id));
}

export function rankCandidates(postalCode, data, controls) {
  const activeIds = activeFacilitySet(data, controls);
  return postalCode.candidates
    .filter((candidate) => activeIds.has(candidate.facility.id))
    .map((candidate) => ({ ...candidate, routeCost: routeCostPerVisit(candidate, controls) }))
    .sort((a, b) => a.routeCost - b.routeCost || a.durationMin - b.durationMin);
}

export function makeAssignment(postalCode, candidate, controls) {
  const visits = controls.visitsPerPostalCode;
  const assumptions = siteAssumptions(candidate.facility, controls);
  const travelHours = (candidate.durationMin * visits) / 60;
  const careHours = (assumptions.visitDurationMin * visits) / 60;
  const providerHours = travelHours + careHours;
  const vehicleKm = candidate.distanceKm * visits;
  const weeklyTravelLaborCost = travelHours * assumptions.laborCostPerHour;
  const weeklyCareLaborCost = careHours * assumptions.laborCostPerHour;
  const weeklyVehicleCost = vehicleKm * assumptions.vehicleCostPerKm;
  const weeklyTravelCost = weeklyTravelLaborCost + weeklyVehicleCost;
  const weeklyDeliveryCost = weeklyTravelCost + weeklyCareLaborCost;
  return {
    postalIndex: postalCode.index,
    postalCodeId: postalCode.id,
    postalCode: postalCode.postalCode,
    latitude: postalCode.latitude,
    longitude: postalCode.longitude,
    facility: candidate.facility,
    durationMin: candidate.durationMin,
    distanceKm: candidate.distanceKm,
    routeCost: candidate.routeCost ?? routeCostPerVisit(candidate, controls),
    deliveryCostPerVisit:
      (candidate.routeCost ?? routeCostPerVisit(candidate, controls)) +
      (assumptions.visitDurationMin * assumptions.laborCostPerHour) / 60,
    visits,
    visitDurationMin: assumptions.visitDurationMin,
    laborCostPerHour: assumptions.laborCostPerHour,
    vehicleCostPerKm: assumptions.vehicleCostPerKm,
    travelHours,
    careHours,
    providerHours,
    vehicleKm,
    weeklyTravelLaborCost,
    weeklyCareLaborCost,
    weeklyVehicleCost,
    weeklyTravelCost,
    weeklyDeliveryCost,
    warnings: candidate.warnings,
    hasRouteNotes: candidate.warnings.length > 0,
  };
}

function emptyFacilitySummary(facility) {
  return {
    facility,
    postalCodeCount: 0,
    visits: 0,
    visitShare: 0,
    travelHours: 0,
    careHours: 0,
    providerHours: 0,
    vehicleKm: 0,
    weeklyTravelCost: 0,
    weeklyDeliveryCost: 0,
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
    summaryByFacility.set(facility.id, emptyFacilitySummary(facility));
  }
  for (const assignment of assignments) {
    const summary = summaryByFacility.get(assignment.facility.id);
    if (!summary) continue;
    summary.postalCodeCount += 1;
    summary.visits += assignment.visits;
    summary.travelHours += assignment.travelHours;
    summary.careHours += assignment.careHours;
    summary.providerHours += assignment.providerHours;
    summary.vehicleKm += assignment.vehicleKm;
    summary.weeklyTravelCost += assignment.weeklyTravelCost;
    summary.weeklyDeliveryCost += assignment.weeklyDeliveryCost;
    summary.assignments.push(assignment);
  }

  const totalVisits = assignments.reduce((sum, item) => sum + item.visits, 0);
  const totalProviderHours = assignments.reduce((sum, item) => sum + item.providerHours, 0);
  const workloadCeilingHours = Math.max(
    0,
    ...[...summaryByFacility.values()].map((summary) => summary.providerHours)
  );
  for (const summary of summaryByFacility.values()) {
    summary.visitShare = totalVisits ? summary.visits / totalVisits : 0;
    summary.workloadShare = totalProviderHours ? summary.providerHours / totalProviderHours : 0;
    summary.workloadIndex = workloadCeilingHours ? summary.providerHours / workloadCeilingHours : 0;
    summary.medianDurationMin = weightedPercentile(summary.assignments, "durationMin", "visits", 50);
    summary.p95DurationMin = weightedPercentile(summary.assignments, "durationMin", "visits", 95);
  }

  const summaries = [...summaryByFacility.values()].sort((a, b) => b.providerHours - a.providerHours);
  const routeNotes = assignments
    .filter((assignment) => assignment.hasRouteNotes)
    .sort((a, b) => b.durationMin - a.durationMin || b.distanceKm - a.distanceKm);
  const travelRows = assignments.filter((assignment) => assignment.visits > 0);
  return {
    mode,
    activeFacilityCount: activeIds.size,
    assignedPostalCodeCount: assignments.length,
    totalVisits,
    weeklyTravelHours: assignments.reduce((sum, item) => sum + item.travelHours, 0),
    weeklyCareHours: assignments.reduce((sum, item) => sum + item.careHours, 0),
    totalProviderHours,
    weeklyVehicleKm: assignments.reduce((sum, item) => sum + item.vehicleKm, 0),
    weeklyTravelCost: assignments.reduce((sum, item) => sum + item.weeklyTravelCost, 0),
    weeklyDeliveryCost: assignments.reduce((sum, item) => sum + item.weeklyDeliveryCost, 0),
    weeklyVehicleCost: assignments.reduce((sum, item) => sum + item.weeklyVehicleCost, 0),
    averageProviderHours: summaries.length ? totalProviderHours / summaries.length : 0,
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

export function buildPlanFromCompactSelections(data, controls, selectionRows, mode = "site_scenario") {
  const mergedControls = mergeControls(data.defaults, controls);
  const assignments = selectionRows.map((row, index) => {
    const candidate = compactCandidate(row, data.facilities, data.warningCatalog);
    candidate.routeCost = routeCostPerVisit(candidate, mergedControls);
    return makeAssignment(data.postalCodes[index], candidate, mergedControls);
  });
  const result = summarizeAssignments(assignments, data, mergedControls, mode);
  result.unassigned = [];
  return result;
}
