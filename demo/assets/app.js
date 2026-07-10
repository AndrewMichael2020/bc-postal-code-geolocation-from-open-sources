import {
  buildPlan,
  hydrateDemoData,
  mergeControls,
  vehicleCostPerKm,
} from "./analytics.js?v=osrm-home-health-20260710b";

const state = {
  data: null,
  activeFacilityIds: new Set(),
  currentPlan: null,
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
  return controls;
}

function popupForAssignment(assignment) {
  const warnings = assignment.warnings.length ? assignment.warnings.join(", ") : "none";
  return `
    <strong>${assignment.postalCode}</strong><br>
    Provider base: ${assignment.facility.name}<br>
    Travel: ${formatOne.format(assignment.durationMin)} min, ${formatOne.format(assignment.distanceKm)} km<br>
    Cost per visit: ${formatMoneyOne.format(assignment.routeCost)}<br>
    Weekly visits: ${formatOne.format(assignment.visits)}<br>
    Route considerations: ${warnings}
  `;
}

function renderMap(result) {
  state.layers.postalCodes.clearLayers();
  state.layers.facilities.clearLayers();

  for (const assignment of result.assignments) {
    const hasWarning = assignment.warnings.length > 0;
    L.circleMarker([assignment.latitude, assignment.longitude], {
      renderer: postalRenderer,
      radius: 2.8,
      color: hasWarning ? "#92400e" : "#111827",
      weight: hasWarning ? 1.4 : 0.45,
      fillColor: assignment.facility.color,
      fillOpacity: 0.58,
    })
      .bindPopup(popupForAssignment(assignment))
      .addTo(state.layers.postalCodes);
  }

  for (const summary of result.summaries.filter((item) => item.postalCodeCount > 0)) {
    const { facility } = summary;
    const radius = 8 + 12 * Math.sqrt(summary.workloadIndex);
    L.circleMarker([facility.latitude, facility.longitude], {
      radius,
      color: "#ffffff",
      weight: 2.5,
      fillColor: facility.color,
      fillOpacity: 0.96,
    })
      .bindPopup(
        `<strong>${facility.name}</strong><br>${facility.type}<br>${facility.address}<br>` +
          `Modeled weekly service: ${formatOne.format(summary.serviceHours)} h<br>` +
          `Share of modeled work: ${formatPercent.format(summary.workloadShare)}`
      )
      .addTo(state.layers.facilities);
  }
}

function renderKpis(result) {
  setText("postalCount", formatNumber.format(result.assignedPostalCodeCount));
  setText("weeklyCost", formatMoney.format(result.weeklyCost));
  setText("weeklyTravelHours", `${formatNumber.format(result.weeklyTravelHours)} h`);
  setText("vehicleCost", formatMoney.format(result.weeklyVehicleCost));
  setText("p95TravelTime", `${formatOne.format(result.p95DurationMin)} min`);
  setText("totalServiceHours", `${formatNumber.format(result.totalServiceHours)} h`);
  setText("routeNoteCount", formatNumber.format(result.routeNoteCount));
  setText("coverageRate", formatPercent.format(result.coverageRate));
  setText("busiestBaseHours", `${formatOne.format(result.workloadCeilingHours)} h`);
  setText("averageBaseHours", `${formatOne.format(result.averageServiceHours)} h`);
  setText("datasetShape", `${formatNumber.format(result.assignedPostalCodeCount)} postal codes`);
  setText("planMode", "Travel-efficient plan");
  setText(
    "networkMode",
    `${formatNumber.format(result.activeFacilityCount)} facilities | ${formatNumber.format(result.totalVisits)} modeled weekly visits`
  );
}

function renderVerdict(result) {
  setText("verdictLabel", "Coverage mapped");
  setClassName("verdictLabel", "verdict good");
  setText(
    "scenarioNarrative",
    `${formatNumber.format(result.assignedPostalCodeCount)} postal codes are matched to their lowest-cost available OSRM route across ${formatNumber.format(result.activeFacilityCount)} provider bases. Facility hours describe modeled workload only.`
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
      <td>${formatOne.format(summary.serviceHours)} h</td>
      <td>${formatPercent.format(summary.workloadShare)}</td>
      <td>${formatOne.format(summary.p95DurationMin)} min</td>
      <td>${formatMoney.format(summary.weeklyCost)}</td>
    `;
    tbody.append(tr);
  }
}

function renderRouteNotesTable(result) {
  const tbody = document.getElementById("routeNotesBody");
  tbody.replaceChildren();
  const routeNotes = result.routeNotes.slice(0, 80);
  if (!routeNotes.length) {
    const tr = document.createElement("tr");
    tr.innerHTML = `<td colspan="6" class="empty-cell">No additional route considerations in this view.</td>`;
    tbody.append(tr);
    return;
  }
  for (const assignment of routeNotes) {
    const reasonParts = [];
    reasonParts.push(...assignment.warnings);
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${assignment.postalCode}</td>
      <td>${assignment.facility.name}</td>
      <td>${formatOne.format(assignment.durationMin)} min</td>
      <td>${formatOne.format(assignment.distanceKm)} km</td>
      <td>${formatMoneyOne.format(assignment.routeCost)}</td>
      <td>${reasonParts.join(", ") || "route note"}</td>
    `;
    tbody.append(tr);
  }
}

function recalculatePlans({ renderMapNow = true } = {}) {
  const controls = readControls();
  state.currentPlan = buildPlan(state.data, controls);
  renderKpis(state.currentPlan);
  renderVerdict(state.currentPlan);
  renderFacilityTable(state.currentPlan);
  renderRouteNotesTable(state.currentPlan);
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
  recalculatePlans();
  document.body.classList.add("ready");
}

init().catch((error) => {
  console.error(error);
  document.getElementById("scenarioNarrative").textContent =
    "The OSRM demo data could not be loaded. Check the GitHub Pages asset paths.";
});
