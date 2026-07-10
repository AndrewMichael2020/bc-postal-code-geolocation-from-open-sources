export const DEFAULT_CONTROLS = {
  laborCostPerHour: 60,
  gasPricePerLitre: 1.7,
  fuelConsumptionLPer100Km: 11.5,
  maintenanceCostPerKm: 0.07,
  visitsPerPostalCode: 0.05,
  visitDurationMin: 45,
  capacityHoursPerFacility: 90,
  maxExtraTravelMin: 10,
  maxExtraDistanceKm: 10,
  maxRelativeCostPenalty: 0.25,
  allowGuardrailExceptions: false,
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

const EPSILON = 0.000001;
const MAX_REBALANCE_PASSES = 8;

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

export function guardrailBreaches(candidate, bestCandidate, controls) {
  const extraMinutes = candidate.durationMin - bestCandidate.durationMin;
  const extraDistanceKm = candidate.distanceKm - bestCandidate.distanceKm;
  const extraCost = candidate.routeCost - bestCandidate.routeCost;
  const relativeCostPenalty = bestCandidate.routeCost > 0 ? extraCost / bestCandidate.routeCost : 0;
  const breaches = [];
  if (extraMinutes > controls.maxExtraTravelMin + EPSILON) {
    breaches.push("extra travel time");
  }
  if (extraDistanceKm > controls.maxExtraDistanceKm + EPSILON) {
    breaches.push("extra road distance");
  }
  if (relativeCostPenalty > controls.maxRelativeCostPenalty + EPSILON) {
    breaches.push("extra visit cost");
  }
  return breaches;
}

export function makeAssignment(postalCode, candidate, bestCandidate, controls, reason = "lowest cost") {
  const visits = controls.visitsPerPostalCode;
  const travelHours = (candidate.durationMin * visits) / 60;
  const vehicleKm = candidate.distanceKm * visits;
  const weeklyLaborCost = candidate.durationMin * laborCostPerMinute(controls) * visits;
  const weeklyVehicleCost = candidate.distanceKm * vehicleCostPerKm(controls) * visits;
  const weeklyQaCost = qaPenaltyPerVisit(candidate.warnings, controls) * visits;
  const weeklyCost = weeklyLaborCost + weeklyVehicleCost + weeklyQaCost;
  const capacityHours = ((candidate.durationMin + controls.visitDurationMin) * visits) / 60;
  const extraMinutes = candidate.durationMin - bestCandidate.durationMin;
  const extraDistanceKm = candidate.distanceKm - bestCandidate.distanceKm;
  const extraCostPerVisit = candidate.routeCost - bestCandidate.routeCost;
  const relativeCostPenalty = bestCandidate.routeCost > 0 ? extraCostPerVisit / bestCandidate.routeCost : 0;
  const breaches = guardrailBreaches(candidate, bestCandidate, controls);
  return {
    postalIndex: postalCode.index,
    postalCodeId: postalCode.id,
    postalCode: postalCode.postalCode,
    latitude: postalCode.latitude,
    longitude: postalCode.longitude,
    facility: candidate.facility,
    bestFacility: bestCandidate.facility,
    durationMin: candidate.durationMin,
    distanceKm: candidate.distanceKm,
    routeCost: candidate.routeCost,
    bestDurationMin: bestCandidate.durationMin,
    bestDistanceKm: bestCandidate.distanceKm,
    bestRouteCost: bestCandidate.routeCost,
    visits,
    travelHours,
    vehicleKm,
    weeklyLaborCost,
    weeklyVehicleCost,
    weeklyQaCost,
    weeklyCost,
    capacityHours,
    extraMinutes,
    extraDistanceKm,
    extraCostPerVisit,
    relativeCostPenalty,
    warnings: candidate.warnings,
    breaches,
    reason,
    isException:
      candidate.facility.id !== bestCandidate.facility.id || breaches.length > 0 || candidate.warnings.length > 0,
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
    capacityHours: controls.capacityHoursPerFacility,
    usedCapacityHours: 0,
    shortfallHours: 0,
    utilization: 0,
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
    summary.usedCapacityHours += assignment.capacityHours;
    summary.assignments.push(assignment);
  }
  for (const summary of summaryByFacility.values()) {
    summary.shortfallHours = Math.max(0, summary.usedCapacityHours - summary.capacityHours);
    summary.utilization = summary.capacityHours ? summary.usedCapacityHours / summary.capacityHours : 0;
    summary.medianDurationMin = weightedPercentile(summary.assignments, "durationMin", "visits", 50);
    summary.p95DurationMin = weightedPercentile(summary.assignments, "durationMin", "visits", 95);
  }

  const summaries = [...summaryByFacility.values()].sort((a, b) => b.usedCapacityHours - a.usedCapacityHours);
  const exceptions = assignments
    .filter((assignment) => assignment.isException)
    .sort(
      (a, b) =>
        b.extraCostPerVisit * b.visits - a.extraCostPerVisit * a.visits ||
        b.extraMinutes - a.extraMinutes
    );
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
    usedCapacityHours: assignments.reduce((sum, item) => sum + item.capacityHours, 0),
    capacityHours: summaries.reduce((sum, item) => sum + item.capacityHours, 0),
    shortfallHours: summaries.reduce((sum, item) => sum + item.shortfallHours, 0),
    medianDurationMin: weightedPercentile(travelRows, "durationMin", "visits", 50),
    p95DurationMin: weightedPercentile(travelRows, "durationMin", "visits", 95),
    medianCostPerVisit: weightedPercentile(travelRows, "routeCost", "visits", 50),
    p95CostPerVisit: weightedPercentile(travelRows, "routeCost", "visits", 95),
    exceptionCount: exceptions.length,
    guardrailExceptionCount: exceptions.filter((assignment) => assignment.breaches.length > 0).length,
    warningExceptionCount: exceptions.filter((assignment) => assignment.warnings.length > 0).length,
    summaries,
    assignments,
    exceptions,
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
    assignments.push(makeAssignment(postalCode, candidates[0], candidates[0], controls));
  }
  return { assignments, unassigned };
}

function candidateMoveOptions(assignment, data, controls) {
  const postalCode = data.postalCodes[assignment.postalIndex];
  const candidates = rankCandidates(postalCode, data, controls);
  const bestCandidate = candidates[0];
  if (!bestCandidate) {
    return [];
  }
  const options = [];
  for (const candidate of candidates) {
    if (candidate.facility.id === assignment.facility.id) {
      continue;
    }
    const breaches = guardrailBreaches(candidate, bestCandidate, controls);
    if (breaches.length && !controls.allowGuardrailExceptions) {
      continue;
    }
    const moved = makeAssignment(postalCode, candidate, bestCandidate, controls, "capacity move");
    options.push({ moved, extraWeeklyCost: moved.weeklyCost - assignment.weeklyCost });
  }
  return options;
}

export function optimizeAssignments(data, controls) {
  const base = lowestCostAssignments(data, controls);
  const assignments = [...base.assignments];
  let blockedMoveCount = 0;

  for (let pass = 0; pass < MAX_REBALANCE_PASSES; pass += 1) {
    let movedInPass = false;
    let plan = summarizeAssignments(assignments, data, controls, "optimized");
    const overloaded = plan.summaries
      .filter((summary) => summary.shortfallHours > EPSILON)
      .sort((a, b) => b.shortfallHours - a.shortfallHours);
    if (!overloaded.length) {
      break;
    }

    for (const overloadedFacility of overloaded) {
      plan = summarizeAssignments(assignments, data, controls, "optimized");
      const loadByFacility = new Map(
        plan.summaries.map((summary) => [summary.facility.id, summary.usedCapacityHours])
      );
      const capacityByFacility = new Map(
        plan.summaries.map((summary) => [summary.facility.id, summary.capacityHours])
      );
      const currentShortfall = () =>
        Math.max(
          0,
          (loadByFacility.get(overloadedFacility.facility.id) ?? 0) -
            (capacityByFacility.get(overloadedFacility.facility.id) ?? 0)
        );
      if (currentShortfall() <= EPSILON) {
        continue;
      }

      const options = [];
      for (let index = 0; index < assignments.length; index += 1) {
        const assignment = assignments[index];
        if (assignment.facility.id !== overloadedFacility.facility.id) {
          continue;
        }
        for (const option of candidateMoveOptions(assignment, data, controls)) {
          const targetId = option.moved.facility.id;
          const targetSpare =
            (capacityByFacility.get(targetId) ?? 0) - (loadByFacility.get(targetId) ?? 0);
          if (targetSpare + EPSILON < option.moved.capacityHours) {
            blockedMoveCount += 1;
            continue;
          }
          options.push({ ...option, index });
        }
      }

      options.sort(
        (a, b) =>
          a.extraWeeklyCost / Math.max(a.moved.capacityHours, EPSILON) -
            b.extraWeeklyCost / Math.max(b.moved.capacityHours, EPSILON) ||
          a.moved.extraMinutes - b.moved.extraMinutes
      );

      for (const option of options) {
        if (currentShortfall() <= EPSILON) {
          break;
        }
        const current = assignments[option.index];
        if (current.facility.id !== overloadedFacility.facility.id) {
          continue;
        }
        const targetId = option.moved.facility.id;
        const targetLoad = loadByFacility.get(targetId) ?? 0;
        const targetCapacity = capacityByFacility.get(targetId) ?? 0;
        if (targetLoad + option.moved.capacityHours > targetCapacity + EPSILON) {
          continue;
        }
        assignments[option.index] = option.moved;
        loadByFacility.set(current.facility.id, (loadByFacility.get(current.facility.id) ?? 0) - current.capacityHours);
        loadByFacility.set(targetId, targetLoad + option.moved.capacityHours);
        movedInPass = true;
      }
    }

    if (!movedInPass) {
      break;
    }
  }

  const result = summarizeAssignments(assignments, data, controls, "optimized");
  result.blockedMoveCount = blockedMoveCount;
  result.unassigned = base.unassigned;
  return result;
}

export function buildPlan(data, controls, mode = "lowest_cost") {
  const mergedControls = mergeControls(data.defaults, controls);
  if (mode === "optimized") {
    return optimizeAssignments(data, mergedControls);
  }
  const base = lowestCostAssignments(data, mergedControls);
  const result = summarizeAssignments(base.assignments, data, mergedControls, "lowest_cost");
  result.unassigned = base.unassigned;
  return result;
}

export function comparePlans(before, after) {
  return {
    weeklyCostDelta: after.weeklyCost - before.weeklyCost,
    weeklyTravelHoursDelta: after.weeklyTravelHours - before.weeklyTravelHours,
    weeklyVehicleCostDelta: after.weeklyVehicleCost - before.weeklyVehicleCost,
    p95DurationDelta: after.p95DurationMin - before.p95DurationMin,
    shortfallHoursDelta: after.shortfallHours - before.shortfallHours,
    exceptionDelta: after.exceptionCount - before.exceptionCount,
  };
}

export function classifyVerdict(before, after) {
  const delta = comparePlans(before, after);
  if (after.shortfallHours > 0.1) {
    return {
      label: "Not feasible",
      tone: "bad",
      message: `Capacity remains short by ${after.shortfallHours.toFixed(1)} weekly hours under the guardrails. Add capacity or allow reviewed exceptions.`,
    };
  }
  if (delta.weeklyCostDelta <= 0 && after.guardrailExceptionCount === 0) {
    return {
      label: "Recommended",
      tone: "good",
      message: "The optimized plan is feasible and does not increase weekly travel cost under the current assumptions.",
    };
  }
  if (delta.shortfallHoursDelta < -0.1 || delta.weeklyCostDelta <= 0) {
    return {
      label: "Trade-off",
      tone: "warn",
      message: "The optimized plan improves one operating constraint but introduces cost or exception trade-offs for review.",
    };
  }
  return {
    label: "Not recommended",
    tone: "bad",
    message: "The optimized plan does not improve capacity enough to justify its extra travel cost and exceptions.",
  };
}
