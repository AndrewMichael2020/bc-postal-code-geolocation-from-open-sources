import {
  buildPlan,
  classifyVerdict,
  comparePlans,
  hydrateDemoData,
  mergeControls,
  vehicleCostPerKm,
} from "./analytics.js?v=osrm-home-health-20260710";

const state = {
  data: null,
  activeFacilityIds: new Set(),
  baselinePlan: null,
  optimizedPlan: null,
  currentPlan: null,
  showOptimized: false,
  controls: {},
  layers: {
    postalCodes: null,
    facilities: null,
  },
  recalculateTimer: null,
};

const formatNumber = new Intl.NumberFormat("en-CA", { maximumFractionDigits: 0 });
const formatOne = new Intl.NumberFormat("en-CA", {
  minimumFractionDigits: 1,
  maximumFractionDigits: 1,
});
const formatMoney = new Intl.NumberFormat("en-CA", {
  style: "currency",
  currency: "CAD",
  maximumFractionDigits: 0,
});
const formatMoneyOne = new Intl.NumberFormat("en-CA", {
  style: "currency",
  currency: "CAD",
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});
const formatPercent = new Intl.NumberFormat("en-CA", {
  style: "percent",
  maximumFractionDigits: 0,
});

const map = L.map("map", {
  preferCanvas: true,
  zoomControl: false,
  fadeAnimation: false,
  markerZoomAnimation: false,
  zoomAnimation: false,
}).setView([49.18, -122.55], 9);

L.control.zoom({ position: "bottomright" }).addTo(map);
L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
  maxZoom: 18,
  keepBuffer: 1,
  updateWhenIdle: true,
  attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
}).addTo(map);
const postalRenderer = L.canvas({ padding: 0.5 });

function setText(id, value) {
  const element = document.getElementById(id);
  if (element) {
    element.textContent = value;
  }
}

function setClassName(id, value) {
  const element = document.getElementById(id);
  if (element) {
    element.className = value;
  }
}

function readControls() {
  const controls = mergeControls(state.data.defaults, {
    laborCostPerHour: Number(document.getElementById("laborCostPerHour").value),
    gasPricePerLitre: Number(document.getElementById("gasPricePerLitre").value),
    fuelConsumptionLPer100Km: Number(document.getElementById("fuelConsumptionLPer100Km").value),
    maintenanceCostPerKm: Number(document.getElementById("maintenanceCostPerKm").value),
    visitsPerPostalCode: Number(document.getElementById("visitsPerPostalCode").value),
    visitDurationMin: Number(document.getElementById("visitDurationMin").value),
    capacityHoursPerFacility: Number(document.getElementById("capacityHoursPerFacility").value),
    maxExtraTravelMin: Number(document.getElementById("maxExtraTravelMin").value),
    maxExtraDistanceKm: Number(document.getElementById("maxExtraDistanceKm").value),
    maxRelativeCostPenalty: Number(document.getElementById("maxRelativeCostPenalty").value),
    allowGuardrailExceptions: document.getElementById("allowGuardrailExceptions").checked,
    includeQaPenalties: document.getElementById("includeQaPenalties").checked,
    activeFacilityIds: state.activeFacilityIds,
  });
  state.controls = controls;
  setText("laborCostPerHourValue", formatMoney.format(controls.laborCostPerHour));
  setText("gasPricePerLitreValue", formatMoneyOne.format(controls.gasPricePerLitre));
  setText("fuelConsumptionValue", `${formatOne.format(controls.fuelConsumptionLPer100Km)} L/100 km`);
  setText("maintenanceCostValue", formatMoneyOne.format(controls.maintenanceCostPerKm));
  setText("vehicleCostValue", `${formatMoneyOne.format(vehicleCostPerKm(controls))}/km`);
  setText("visitsPerPostalCodeValue", controls.visitsPerPostalCode.toFixed(2));
  setText("visitDurationValue", `${formatNumber.format(controls.visitDurationMin)} min`);
  setText("capacityHoursValue", `${formatNumber.format(controls.capacityHoursPerFacility)} h`);
  setText("maxExtraTravelValue", `${formatNumber.format(controls.maxExtraTravelMin)} min`);
  setText("maxExtraDistanceValue", `${formatNumber.format(controls.maxExtraDistanceKm)} km`);
  setText("maxRelativeCostValue", formatPercent.format(controls.maxRelativeCostPenalty));
  return controls;
}

function renderFacilityControls() {
  const container = document.getElementById("facilityControls");
  container.replaceChildren();
  for (const facility of state.data.facilities) {
    const label = document.createElement("label");
    label.className = "hub-toggle";
    label.style.setProperty("--hub-color", facility.color);
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.checked = state.activeFacilityIds.has(facility.id);
    checkbox.addEventListener("change", () => {
      if (checkbox.checked) {
        state.activeFacilityIds.add(facility.id);
      } else if (state.activeFacilityIds.size > 1) {
        state.activeFacilityIds.delete(facility.id);
      } else {
        checkbox.checked = true;
      }
      state.showOptimized = false;
      recalculatePlans({ renderMapNow: true });
    });
    const span = document.createElement("span");
    span.textContent = facility.name;
    label.append(checkbox, span);
    container.append(label);
  }
}

function popupForAssignment(assignment) {
  const warnings = assignment.warnings.length ? assignment.warnings.join(", ") : "none";
  const exception = assignment.facility.id !== assignment.bestFacility.id;
  return `
    <strong>${assignment.postalCode}</strong><br>
    Assigned: ${assignment.facility.name}<br>
    Lowest cost: ${assignment.bestFacility.name}<br>
    Travel: ${formatOne.format(assignment.durationMin)} min, ${formatOne.format(assignment.distanceKm)} km<br>
    Cost per visit: ${formatMoneyOne.format(assignment.routeCost)}<br>
    Weekly visits: ${formatOne.format(assignment.visits)}<br>
    ${exception ? `Extra: ${formatOne.format(assignment.extraMinutes)} min, ${formatMoneyOne.format(assignment.extraCostPerVisit)} per visit<br>` : ""}
    Warnings: ${warnings}
  `;
}

function renderMap(result) {
  state.layers.postalCodes.clearLayers();
  state.layers.facilities.clearLayers();

  for (const assignment of result.assignments) {
    const isMoved = assignment.facility.id !== assignment.bestFacility.id;
    const hasWarning = assignment.warnings.length > 0 || assignment.breaches.length > 0;
    L.circleMarker([assignment.latitude, assignment.longitude], {
      renderer: postalRenderer,
      radius: isMoved ? 4.8 : 2.8,
      color: hasWarning ? "#92400e" : "#111827",
      weight: isMoved || hasWarning ? 1.4 : 0.45,
      fillColor: assignment.facility.color,
      fillOpacity: isMoved ? 0.82 : 0.58,
    })
      .bindPopup(popupForAssignment(assignment))
      .addTo(state.layers.postalCodes);
  }

  const visibleFacilityIds = new Set(result.summaries.filter((item) => item.postalCodeCount > 0).map((item) => item.facility.id));
  for (const facility of state.data.facilities.filter((item) => visibleFacilityIds.has(item.id))) {
    const icon = L.divIcon({
      className: "hub-marker",
      html: `<span style="background:${facility.color}"></span>`,
      iconSize: [24, 24],
      iconAnchor: [12, 12],
    });
    L.marker([facility.latitude, facility.longitude], { icon })
      .bindPopup(`<strong>${facility.name}</strong><br>${facility.type}<br>${facility.address}`)
      .addTo(state.layers.facilities);
  }
}

function renderKpis(result) {
  setText("postalCount", formatNumber.format(result.assignedPostalCodeCount));
  setText("weeklyCost", formatMoney.format(result.weeklyCost));
  setText("weeklyTravelHours", `${formatNumber.format(result.weeklyTravelHours)} h`);
  setText("vehicleCost", formatMoney.format(result.weeklyVehicleCost));
  setText("p95TravelTime", `${formatOne.format(result.p95DurationMin)} min`);
  setText("capacityShortfall", `${formatOne.format(result.shortfallHours)} h`);
  setText("exceptionCount", formatNumber.format(result.exceptionCount));
  setText("datasetShape", `${formatNumber.format(result.assignedPostalCodeCount)} postal codes`);
  setText("planMode", state.showOptimized ? "Capacity-aware plan" : "Lowest-cost baseline");
  setText(
    "networkMode",
    `${formatNumber.format(result.activeFacilityCount)} facilities | ${formatNumber.format(result.totalVisits)} modeled weekly visits`
  );
}

function renderVerdict(result) {
  const verdict = state.showOptimized
    ? classifyVerdict(state.baselinePlan, state.optimizedPlan)
    : {
        label: result.shortfallHours > 0.1 ? "Baseline overloaded" : "Baseline feasible",
        tone: result.shortfallHours > 0.1 ? "warn" : "good",
        message:
          result.shortfallHours > 0.1
            ? "The lowest-cost plan is cheapest by route, but it exceeds available weekly capacity. Optimize to rebalance within guardrails."
            : "The lowest-cost plan is feasible under the current capacity assumptions. Optimization should avoid adding cost unless constraints change.",
      };
  setText("verdictLabel", verdict.label);
  setClassName("verdictLabel", `verdict ${verdict.tone}`);
  setText("scenarioNarrative", verdict.message);
}

function renderComparison() {
  if (!state.baselinePlan || !state.optimizedPlan) {
    return;
  }
  const delta = comparePlans(state.baselinePlan, state.optimizedPlan);
  setText("baselineCost", formatMoney.format(state.baselinePlan.weeklyCost));
  setText("optimizedCost", state.showOptimized ? formatMoney.format(state.optimizedPlan.weeklyCost) : "-");
  setText("baselineP95", `${formatOne.format(state.baselinePlan.p95DurationMin)} min`);
  setText("optimizedP95", state.showOptimized ? `${formatOne.format(state.optimizedPlan.p95DurationMin)} min` : "-");
  setText("baselineShortfall", `${formatOne.format(state.baselinePlan.shortfallHours)} h`);
  setText("optimizedShortfall", state.showOptimized ? `${formatOne.format(state.optimizedPlan.shortfallHours)} h` : "-");
  setText(
    "costDelta",
    state.showOptimized
      ? `${delta.weeklyCostDelta <= 0 ? "-" : "+"}${formatMoney.format(Math.abs(delta.weeklyCostDelta))}`
      : "Run optimization"
  );
  setText(
    "hoursDelta",
    state.showOptimized
      ? `${delta.weeklyTravelHoursDelta <= 0 ? "-" : "+"}${formatOne.format(Math.abs(delta.weeklyTravelHoursDelta))} h`
      : "-"
  );
  setText(
    "shortfallDelta",
    state.showOptimized
      ? `${delta.shortfallHoursDelta <= 0 ? "-" : "+"}${formatOne.format(Math.abs(delta.shortfallHoursDelta))} h`
      : "-"
  );
}

function renderFacilityTable(result) {
  const tbody = document.getElementById("allocationBody");
  tbody.replaceChildren();
  for (const summary of result.summaries.filter((item) => item.postalCodeCount > 0)) {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td><span class="swatch" style="background:${summary.facility.color}"></span>${summary.facility.name}</td>
      <td>${formatNumber.format(summary.postalCodeCount)}</td>
      <td>${formatOne.format(summary.visits)}</td>
      <td>${formatPercent.format(summary.utilization)}</td>
      <td>${formatOne.format(summary.p95DurationMin)} min</td>
      <td>${formatMoney.format(summary.weeklyCost)}</td>
    `;
    if (summary.shortfallHours > 0.1) {
      tr.classList.add("overloaded");
    }
    tbody.append(tr);
  }
}

function renderExceptionTable(result) {
  const tbody = document.getElementById("exceptionBody");
  tbody.replaceChildren();
  const exceptions = result.exceptions.slice(0, 80);
  if (!exceptions.length) {
    const tr = document.createElement("tr");
    tr.innerHTML = `<td colspan="7" class="empty-cell">No assignment exceptions under the current plan.</td>`;
    tbody.append(tr);
    return;
  }
  for (const assignment of exceptions) {
    const reasonParts = [];
    if (assignment.facility.id !== assignment.bestFacility.id) {
      reasonParts.push(assignment.reason);
    }
    reasonParts.push(...assignment.breaches, ...assignment.warnings);
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${assignment.postalCode}</td>
      <td>${assignment.facility.name}</td>
      <td>${assignment.bestFacility.name}</td>
      <td>${formatOne.format(assignment.extraMinutes)} min</td>
      <td>${formatOne.format(assignment.extraDistanceKm)} km</td>
      <td>${formatMoneyOne.format(assignment.extraCostPerVisit)}</td>
      <td>${reasonParts.join(", ") || "review"}</td>
    `;
    tbody.append(tr);
  }
}

function recalculatePlans({ renderMapNow = true } = {}) {
  const controls = readControls();
  state.baselinePlan = buildPlan(state.data, controls, "lowest_cost");
  state.optimizedPlan = buildPlan(state.data, controls, "optimized");
  state.currentPlan = state.showOptimized ? state.optimizedPlan : state.baselinePlan;
  renderKpis(state.currentPlan);
  renderVerdict(state.currentPlan);
  renderComparison();
  renderFacilityTable(state.currentPlan);
  renderExceptionTable(state.currentPlan);
  if (renderMapNow) {
    requestAnimationFrame(() => renderMap(state.currentPlan));
  }
}

function scheduleRecalculatePlans() {
  readControls();
  if (state.recalculateTimer) {
    window.clearTimeout(state.recalculateTimer);
  }
  state.recalculateTimer = window.setTimeout(() => {
    state.recalculateTimer = null;
    recalculatePlans({ renderMapNow: true });
  }, 180);
}

function stepControl(controlId, direction) {
  const input = document.getElementById(controlId);
  const step = Number(input.step || 1);
  const min = Number(input.min);
  const max = Number(input.max);
  const current = Number(input.value);
  const decimals = (input.step.split(".")[1] || "").length;
  const next = Math.min(max, Math.max(min, current + step * direction));
  input.value = next.toFixed(decimals);
  scheduleRecalculatePlans();
}

function showOptimizedPlan() {
  state.showOptimized = true;
  recalculatePlans({ renderMapNow: true });
}

function showBaselinePlan() {
  state.showOptimized = false;
  recalculatePlans({ renderMapNow: true });
}

async function loadJson(path) {
  const response = await fetch(path);
  if (!response.ok) {
    throw new Error(`Failed to load ${path}: ${response.status}`);
  }
  return response.json();
}

function initializeControls(defaults) {
  for (const [id, value] of Object.entries({
    laborCostPerHour: defaults.laborCostPerHour,
    gasPricePerLitre: defaults.gasPricePerLitre,
    fuelConsumptionLPer100Km: defaults.fuelConsumptionLPer100Km,
    maintenanceCostPerKm: defaults.maintenanceCostPerKm,
    visitsPerPostalCode: defaults.visitsPerPostalCode,
    visitDurationMin: defaults.visitDurationMin,
    capacityHoursPerFacility: defaults.capacityHoursPerFacility,
    maxExtraTravelMin: defaults.maxExtraTravelMin,
    maxExtraDistanceKm: defaults.maxExtraDistanceKm,
    maxRelativeCostPenalty: defaults.maxRelativeCostPenalty,
  })) {
    const input = document.getElementById(id);
    if (input) {
      input.value = String(value);
    }
  }
}

async function init() {
  const asset = await loadJson("./data/fha-home-health-demo.json");
  state.data = hydrateDemoData(asset);
  initializeControls(mergeControls(state.data.defaults));
  state.activeFacilityIds = new Set(state.data.facilities.map((facility) => facility.id));
  state.layers.postalCodes = L.layerGroup().addTo(map);
  state.layers.facilities = L.layerGroup().addTo(map);
  renderFacilityControls();
  for (const input of document.querySelectorAll("input[type='range']")) {
    input.addEventListener("input", scheduleRecalculatePlans);
  }
  for (const checkbox of document.querySelectorAll("input[type='checkbox'].scenario-toggle")) {
    checkbox.addEventListener("change", scheduleRecalculatePlans);
  }
  for (const button of document.querySelectorAll(".stepper")) {
    button.addEventListener("click", () => {
      stepControl(button.dataset.control, Number(button.dataset.step));
    });
  }
  document.getElementById("optimizeButton").addEventListener("click", showOptimizedPlan);
  document.getElementById("baselineButton").addEventListener("click", showBaselinePlan);
  recalculatePlans();
  document.body.classList.add("ready");
}

init().catch((error) => {
  console.error(error);
  document.getElementById("scenarioNarrative").textContent =
    "The OSRM demo data could not be loaded. Check the GitHub Pages asset paths.";
});
