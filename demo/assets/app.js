import {
  buildPlan,
  buildPlanFromCompactSelections,
  hydrateDemoData,
  mergeControls,
  siteAssumptions,
  vehicleCostPerKm,
} from "./analytics.js?v=osrm-home-health-20260710d";

const state = {
  data: null,
  activeFacilityIds: new Set(),
  currentPlan: null,
  referencePlan: null,
  advancedSelections: null,
  siteOverrides: {},
  targetShares: {},
  targetMixEnabled: false,
  targetMixApplied: false,
  selectedFacilityId: null,
  controls: {},
  assignmentGroups: [],
  routeFilter: "all",
  highlightRouteNotes: true,
  layers: { postalCodes: null, facilities: null, selection: null },
  recalculateTimer: null,
  worker: null,
  workerRequestId: 0,
  workerPending: new Map(),
};

const formatNumber = new Intl.NumberFormat("en-CA", { maximumFractionDigits: 0 });
const formatOne = new Intl.NumberFormat("en-CA", { minimumFractionDigits: 1, maximumFractionDigits: 1 });
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
const formatPercent = new Intl.NumberFormat("en-CA", { style: "percent", maximumFractionDigits: 1 });

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
  if (element) element.textContent = value;
}

function setClassName(id, value) {
  const element = document.getElementById(id);
  if (element) element.className = value;
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function readControls() {
  const controls = mergeControls(state.data.defaults, {
    laborCostPerHour: Number(document.getElementById("laborCostPerHour").value),
    gasPricePerLitre: Number(document.getElementById("gasPricePerLitre").value),
    fuelConsumptionLPer100Km: Number(document.getElementById("fuelConsumptionLPer100Km").value),
    maintenanceCostPerKm: Number(document.getElementById("maintenanceCostPerKm").value),
    visitsPerPostalCode: Number(document.getElementById("visitsPerPostalCode").value),
    visitDurationMin: Number(document.getElementById("visitDurationMin").value),
    siteOverrides: state.siteOverrides,
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

function hasRoutingOverrides() {
  return Object.values(state.siteOverrides).some(
    (override) => override.laborCostPerHour != null || override.vehicleCostPerKm != null
  );
}

function compactDelta(value, formatter, suffix = "") {
  const sign = value > 0 ? "+" : value < 0 ? "-" : "";
  return `${sign}${formatter(Math.abs(value))}${suffix}`;
}

function routeNoteMatches(assignment) {
  if (!assignment.warnings.length) return false;
  if (state.routeFilter === "all") return true;
  const matchers = {
    snap: ["snap warning"],
    long: ["long route", "very long route"],
    slow: ["slow route", "very slow route"],
    circuity: ["high circuity"],
    forest: ["forest/service road"],
    wilderness: ["wilderness access"],
    terrain: ["terrain warning"],
    detail: ["route detail warning"],
  };
  return assignment.warnings.some((warning) => (matchers[state.routeFilter] ?? []).includes(warning));
}

function coordinateKey(latitude, longitude) {
  return `${latitude.toFixed(6)}|${longitude.toFixed(6)}`;
}

function rebuildAssignmentGroups(result) {
  const groups = new Map();
  for (const assignment of result.assignments) {
    const key = coordinateKey(assignment.latitude, assignment.longitude);
    if (!groups.has(key)) {
      groups.set(key, { latitude: assignment.latitude, longitude: assignment.longitude, assignments: [] });
    }
    groups.get(key).assignments.push(assignment);
  }
  state.assignmentGroups = [...groups.values()];
}

function postalCardHtml(group, selectedIndex = 0) {
  const assignment = group.assignments[selectedIndex];
  const warningText = assignment.warnings.length ? assignment.warnings.join(", ") : "No additional route notes";
  const selector =
    group.assignments.length > 1
      ? `<label class="postal-card-select">Postal codes at this location
          <select id="postalCardSelector">${group.assignments
            .map(
              (item, index) =>
                `<option value="${index}"${index === selectedIndex ? " selected" : ""}>${escapeHtml(item.postalCode)}</option>`
            )
            .join("")}</select>
        </label>`
      : "";
  return `<article class="postal-card">
    <header><strong>${escapeHtml(assignment.postalCode)}</strong><span>${escapeHtml(assignment.postalCodeId)}</span></header>
    ${selector}
    <dl>
      <div><dt>Provider base</dt><dd>${escapeHtml(assignment.facility.name)}</dd></div>
      <div><dt>One-way travel</dt><dd>${formatOne.format(assignment.durationMin)} min</dd></div>
      <div><dt>Road distance</dt><dd>${formatOne.format(assignment.distanceKm)} km</dd></div>
      <div><dt>Travel cost / visit</dt><dd>${formatMoneyOne.format(assignment.routeCost)}</dd></div>
      <div><dt>In-home care</dt><dd>${formatNumber.format(assignment.visitDurationMin)} min</dd></div>
      <div><dt>Provider time / visit</dt><dd>${formatOne.format((assignment.durationMin + assignment.visitDurationMin) / 60)} h</dd></div>
      <div><dt>Delivery cost / visit</dt><dd>${formatMoneyOne.format(assignment.deliveryCostPerVisit)}</dd></div>
      <div><dt>Weekly visits</dt><dd>${formatOne.format(assignment.visits)}</dd></div>
    </dl>
    <p><span>Route notes</span>${escapeHtml(warningText)}</p>
  </article>`;
}

function openPostalCard(group, selectedIndex = 0) {
  state.layers.selection.clearLayers();
  L.circleMarker([group.latitude, group.longitude], {
    radius: 7,
    color: "#111827",
    weight: 2,
    fillOpacity: 0,
    interactive: false,
  }).addTo(state.layers.selection);
  const popup = L.popup({ className: "postal-card-popup", maxWidth: 380, closeButton: true })
    .setLatLng([group.latitude, group.longitude])
    .setContent(postalCardHtml(group, selectedIndex))
    .openOn(map);

  const attachSelector = () => {
    const selector = document.getElementById("postalCardSelector");
    if (!selector) return;
    selector.addEventListener("change", () => {
      popup.setContent(postalCardHtml(group, Number(selector.value)));
      window.setTimeout(attachSelector, 0);
    });
  };
  window.setTimeout(attachSelector, 0);
}

function handleMapClick(event) {
  const tolerance = 14;
  const clickPoint = map.latLngToContainerPoint(event.latlng);
  const northwest = map.containerPointToLatLng([clickPoint.x - tolerance, clickPoint.y - tolerance]);
  const southeast = map.containerPointToLatLng([clickPoint.x + tolerance, clickPoint.y + tolerance]);
  let nearest = null;
  let nearestDistance = Infinity;
  for (const group of state.assignmentGroups) {
    if (
      group.latitude > northwest.lat ||
      group.latitude < southeast.lat ||
      group.longitude < northwest.lng ||
      group.longitude > southeast.lng
    ) {
      continue;
    }
    const point = map.latLngToContainerPoint([group.latitude, group.longitude]);
    const distance = Math.hypot(point.x - clickPoint.x, point.y - clickPoint.y);
    if (distance < nearestDistance) {
      nearest = group;
      nearestDistance = distance;
    }
  }
  if (nearest && nearestDistance <= tolerance) openPostalCard(nearest);
}

function renderMap(result) {
  state.layers.postalCodes.clearLayers();
  state.layers.facilities.clearLayers();
  state.layers.selection.clearLayers();
  rebuildAssignmentGroups(result);

  for (const assignment of result.assignments) {
    const highlighted = state.highlightRouteNotes && routeNoteMatches(assignment);
    L.circleMarker([assignment.latitude, assignment.longitude], {
      renderer: postalRenderer,
      radius: 2.8,
      color: highlighted ? "#92400e" : "#111827",
      weight: highlighted ? 1.4 : 0.45,
      fillColor: assignment.facility.color,
      fillOpacity: 0.58,
      interactive: false,
    }).addTo(state.layers.postalCodes);
  }

  for (const summary of result.summaries.filter((item) => item.postalCodeCount > 0)) {
    const { facility } = summary;
    const marker = L.circleMarker([facility.latitude, facility.longitude], {
      radius: 8 + 12 * Math.sqrt(summary.workloadIndex),
      color: "#ffffff",
      weight: 2.5,
      fillColor: facility.color,
      fillOpacity: 0.96,
      bubblingMouseEvents: false,
    })
      .bindPopup(
        `<strong>${escapeHtml(facility.name)}</strong><br>${escapeHtml(facility.type)}<br>${escapeHtml(facility.address)}<br>` +
          `Modeled provider time: ${formatOne.format(summary.providerHours)} h<br>` +
          `Share of modeled work: ${formatPercent.format(summary.workloadShare)}`
      )
      .on("click", () => selectFacility(facility.id));
    marker.addTo(state.layers.facilities);
  }
}

function renderKpis(result) {
  setText("postalCount", formatNumber.format(result.assignedPostalCodeCount));
  setText("weeklyTravelCost", formatMoney.format(result.weeklyTravelCost));
  setText("weeklyDeliveryCost", formatMoney.format(result.weeklyDeliveryCost));
  setText("weeklyTravelHours", `${formatNumber.format(result.weeklyTravelHours)} h`);
  setText("weeklyCareHours", `${formatNumber.format(result.weeklyCareHours)} h`);
  setText("totalProviderHours", `${formatNumber.format(result.totalProviderHours)} h`);
  setText("p95TravelTime", `${formatOne.format(result.p95DurationMin)} min`);
  setText("routeNoteCount", formatNumber.format(result.routeNoteCount));
  setText("coverageRate", formatPercent.format(result.coverageRate));
  setText("busiestBaseHours", `${formatOne.format(result.workloadCeilingHours)} h`);
  setText("averageBaseHours", `${formatOne.format(result.averageProviderHours)} h`);
  setText("datasetShape", `${formatNumber.format(result.assignedPostalCodeCount)} postal codes`);
  setText("planMode", state.targetMixApplied ? "Target workload scenario" : "Travel-efficient plan");
  setText(
    "networkMode",
    `${formatNumber.format(result.activeFacilityCount)} facilities | ${formatNumber.format(result.totalVisits)} modeled weekly visits`
  );
  setText("verdictLabel", "Coverage mapped");
  setClassName("verdictLabel", "verdict good");
  setText(
    "scenarioNarrative",
    `${formatNumber.format(result.assignedPostalCodeCount)} postal codes are mapped across ${formatNumber.format(result.activeFacilityCount)} provider bases. Provider time separates one-way travel from ${formatNumber.format(state.controls.visitDurationMin)}-minute in-home care.`
  );
}

function renderScenarioDelta(result) {
  const section = document.getElementById("scenarioDelta");
  const returnButton = document.getElementById("returnTravelPlanButton");
  if (!state.targetMixApplied || !state.referencePlan) {
    section.hidden = true;
    returnButton.hidden = true;
    return;
  }
  section.hidden = false;
  returnButton.hidden = false;
  setText(
    "scenarioCostChange",
    compactDelta(result.weeklyTravelCost - state.referencePlan.weeklyTravelCost, (value) => formatMoney.format(value))
  );
  setText(
    "scenarioHoursChange",
    compactDelta(result.weeklyTravelHours - state.referencePlan.weeklyTravelHours, (value) => formatOne.format(value), " h")
  );
  const moved = result.assignments.reduce(
    (count, assignment, index) =>
      count + (assignment.facility.id !== state.referencePlan.assignments[index].facility.id ? 1 : 0),
    0
  );
  setText("scenarioAreasMoved", formatNumber.format(moved));
}

function renderFacilityTable(result) {
  const tbody = document.getElementById("allocationBody");
  tbody.replaceChildren();
  for (const summary of result.summaries.filter((item) => item.postalCodeCount > 0)) {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td><button class="facility-select-button" type="button" data-facility-id="${escapeHtml(summary.facility.id)}"><span class="swatch" style="background:${summary.facility.color}"></span>${escapeHtml(summary.facility.name)}</button></td>
      <td>${formatNumber.format(summary.postalCodeCount)}</td>
      <td>${formatOne.format(summary.visits)}</td>
      <td>${formatOne.format(summary.providerHours)} h</td>
      <td>${formatPercent.format(summary.workloadShare)}</td>
      <td>${formatOne.format(summary.p95DurationMin)} min</td>
      <td>${formatMoney.format(summary.weeklyDeliveryCost)}</td>`;
    tbody.append(tr);
  }
  for (const button of tbody.querySelectorAll(".facility-select-button")) {
    button.addEventListener("click", () => selectFacility(button.dataset.facilityId));
  }
}

function renderRouteNotesTable(result) {
  const tbody = document.getElementById("routeNotesBody");
  tbody.replaceChildren();
  const routeNotes = result.routeNotes.filter(routeNoteMatches);
  setText("routeFilterCount", `${formatNumber.format(routeNotes.length)} matching areas`);
  const visibleRows = routeNotes.slice(0, 80);
  if (!visibleRows.length) {
    const tr = document.createElement("tr");
    tr.innerHTML = `<td colspan="6" class="empty-cell">No route notes match this filter.</td>`;
    tbody.append(tr);
    return;
  }
  for (const assignment of visibleRows) {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${escapeHtml(assignment.postalCode)}</td>
      <td>${escapeHtml(assignment.facility.name)}</td>
      <td>${formatOne.format(assignment.durationMin)} min</td>
      <td>${formatOne.format(assignment.distanceKm)} km</td>
      <td>${formatMoneyOne.format(assignment.routeCost)}</td>
      <td>${escapeHtml(assignment.warnings.join(", "))}</td>`;
    tbody.append(tr);
  }
}

function initializeTargetShares(plan) {
  state.targetShares = Object.fromEntries(
    state.data.facilities.map((facility) => {
      const summary = plan.summaries.find((item) => item.facility.id === facility.id);
      return [facility.id, summary?.visitShare ?? 0];
    })
  );
}

function updateSiteEditor() {
  if (!state.currentPlan || !state.selectedFacilityId) return;
  const facility = state.data.facilities.find((item) => item.id === state.selectedFacilityId);
  const assumptions = siteAssumptions(facility, state.controls);
  const override = state.siteOverrides[facility.id] ?? {};
  const summary = state.currentPlan.summaries.find((item) => item.facility.id === facility.id);
  document.getElementById("siteLaborCostPerHour").value = String(assumptions.laborCostPerHour);
  document.getElementById("siteVehicleCostPerKm").value = String(assumptions.vehicleCostPerKm);
  document.getElementById("siteVisitDurationMin").value = String(assumptions.visitDurationMin);
  document.getElementById("targetShare").value = String((state.targetShares[facility.id] ?? 0) * 100);
  setText("siteLaborCostValue", formatMoney.format(assumptions.laborCostPerHour));
  setText("siteVehicleCostValue", `${formatMoneyOne.format(assumptions.vehicleCostPerKm)}/km`);
  setText("siteVisitDurationValue", `${formatNumber.format(assumptions.visitDurationMin)} min`);
  setText("siteCurrentShare", formatPercent.format(summary?.visitShare ?? 0));
  setText("targetShareValue", `${formatOne.format((state.targetShares[facility.id] ?? 0) * 100)}%`);
  setText("siteOverrideStatus", Object.keys(override).length ? "Site values" : "Global values");
  document.getElementById("targetShare").disabled = !state.targetMixEnabled;
  document.getElementById("previewTargetButton").disabled = !state.targetMixEnabled;
}

function selectFacility(facilityId) {
  state.selectedFacilityId = facilityId;
  document.getElementById("facilitySelect").value = facilityId;
  updateSiteEditor();
  document.getElementById("providerBaseSettings").scrollIntoView({ block: "nearest", behavior: "smooth" });
}

function renderAll(result, { renderMapNow = true } = {}) {
  state.currentPlan = result;
  renderKpis(result);
  renderScenarioDelta(result);
  renderFacilityTable(result);
  renderRouteNotesTable(result);
  updateSiteEditor();
  if (renderMapNow) requestAnimationFrame(() => renderMap(result));
}

function workerInstance() {
  if (!state.worker) {
    state.worker = new Worker("./assets/planner-worker.js?v=osrm-home-health-20260710d");
    state.worker.addEventListener("message", (event) => {
      const pending = state.workerPending.get(event.data.requestId);
      if (!pending) return;
      if (event.data.type === "status") {
        setText("planningStatus", event.data.status === "loading" ? "Loading all routes..." : "Calculating scenario...");
      } else if (event.data.type === "result") {
        state.workerPending.delete(event.data.requestId);
        setText("planningStatus", "");
        pending.resolve(event.data);
      } else if (event.data.type === "error") {
        state.workerPending.delete(event.data.requestId);
        setText("planningStatus", "Scenario data needs attention");
        pending.reject(new Error(event.data.message));
      }
    });
  }
  return state.worker;
}

function requestWorkerPlan(controls, targetShares = null) {
  const requestId = ++state.workerRequestId;
  return new Promise((resolve, reject) => {
    state.workerPending.set(requestId, { resolve, reject });
    workerInstance().postMessage({ type: "plan", requestId, controls, targetShares });
  });
}

async function recalculatePlans({ renderMapNow = true } = {}) {
  const controls = readControls();
  try {
    if (state.targetMixApplied) {
      const referenceResult = await requestWorkerPlan(controls, null);
      state.referencePlan = buildPlanFromCompactSelections(dataOrThrow(), controls, referenceResult.selections);
      const scenarioResult = await requestWorkerPlan(controls, state.targetShares);
      state.advancedSelections = scenarioResult.selections;
      renderAll(
        buildPlanFromCompactSelections(dataOrThrow(), controls, scenarioResult.selections, "target_workload"),
        { renderMapNow }
      );
    } else if (hasRoutingOverrides()) {
      const result = await requestWorkerPlan(controls, null);
      state.advancedSelections = result.selections;
      state.referencePlan = buildPlanFromCompactSelections(dataOrThrow(), controls, result.selections);
      renderAll(state.referencePlan, { renderMapNow });
    } else if (state.advancedSelections) {
      state.referencePlan = buildPlanFromCompactSelections(dataOrThrow(), controls, state.advancedSelections);
      renderAll(state.referencePlan, { renderMapNow });
    } else {
      state.referencePlan = buildPlan(dataOrThrow(), controls);
      renderAll(state.referencePlan, { renderMapNow });
    }
    if (!state.targetMixEnabled) initializeTargetShares(state.referencePlan);
  } catch (error) {
    console.error(error);
    setText("planningStatus", "Scenario data could not be loaded");
  }
}

function dataOrThrow() {
  if (!state.data) throw new Error("Demo data has not loaded.");
  return state.data;
}

function scheduleRecalculatePlans() {
  readControls();
  if (state.recalculateTimer) window.clearTimeout(state.recalculateTimer);
  state.recalculateTimer = window.setTimeout(() => {
    state.recalculateTimer = null;
    recalculatePlans({ renderMapNow: true });
  }, 240);
}

function stepControl(controlId, direction) {
  const input = document.getElementById(controlId);
  const step = Number(input.step || 1);
  const decimals = (input.step.split(".")[1] || "").length;
  const next = Math.min(Number(input.max), Math.max(Number(input.min), Number(input.value) + step * direction));
  input.value = next.toFixed(decimals);
  input.dispatchEvent(new Event("input", { bubbles: true }));
}

function updateSiteOverride(field, value) {
  const facilityId = state.selectedFacilityId;
  state.siteOverrides[facilityId] = { ...(state.siteOverrides[facilityId] ?? {}), [field]: value };
  updateSiteEditor();
  scheduleRecalculatePlans();
}

function resetSelectedSite() {
  delete state.siteOverrides[state.selectedFacilityId];
  state.advancedSelections = null;
  updateSiteEditor();
  scheduleRecalculatePlans();
}

function adjustTargetShare(facilityId, nextPercent) {
  const nextShare = Math.max(0, Math.min(1, nextPercent / 100));
  const previousShare = state.targetShares[facilityId] ?? 0;
  const remaining = 1 - nextShare;
  const previousRemaining = 1 - previousShare;
  const otherFacilities = state.data.facilities.filter((facility) => facility.id !== facilityId);
  if (previousRemaining > 0) {
    for (const facility of otherFacilities) {
      state.targetShares[facility.id] = (state.targetShares[facility.id] ?? 0) * (remaining / previousRemaining);
    }
  } else {
    for (const facility of otherFacilities) state.targetShares[facility.id] = remaining / otherFacilities.length;
  }
  state.targetShares[facilityId] = nextShare;
  updateSiteEditor();
  setText("targetShareTotal", formatPercent.format(Object.values(state.targetShares).reduce((a, b) => a + b, 0)));
}

async function previewTargetMix() {
  state.targetMixApplied = true;
  await recalculatePlans({ renderMapNow: true });
}

async function returnToTravelPlan() {
  state.targetMixApplied = false;
  state.advancedSelections = null;
  await recalculatePlans({ renderMapNow: true });
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
    if (input) input.value = String(value);
  }
}

async function loadJson(path) {
  const response = await fetch(path);
  if (!response.ok) throw new Error(`Failed to load ${path}: ${response.status}`);
  return response.json();
}

async function init() {
  const asset = await loadJson("./data/fha-home-health-demo.json");
  state.data = hydrateDemoData(asset);
  initializeControls(mergeControls(state.data.defaults));
  state.activeFacilityIds = new Set(state.data.facilities.map((facility) => facility.id));
  state.selectedFacilityId = state.data.facilities[0].id;
  state.layers.postalCodes = L.layerGroup().addTo(map);
  state.layers.facilities = L.layerGroup().addTo(map);
  state.layers.selection = L.layerGroup().addTo(map);
  map.on("click", handleMapClick);

  const facilitySelect = document.getElementById("facilitySelect");
  for (const facility of state.data.facilities) {
    const option = document.createElement("option");
    option.value = facility.id;
    option.textContent = facility.name;
    facilitySelect.append(option);
  }
  facilitySelect.value = state.selectedFacilityId;
  facilitySelect.addEventListener("change", () => selectFacility(facilitySelect.value));

  for (const input of document.querySelectorAll("input[type='range'].global-control")) {
    input.addEventListener("input", scheduleRecalculatePlans);
  }
  for (const button of document.querySelectorAll(".stepper")) {
    button.addEventListener("click", () => stepControl(button.dataset.control, Number(button.dataset.step)));
  }

  document.getElementById("siteLaborCostPerHour").addEventListener("input", (event) =>
    updateSiteOverride("laborCostPerHour", Number(event.target.value))
  );
  document.getElementById("siteVehicleCostPerKm").addEventListener("input", (event) =>
    updateSiteOverride("vehicleCostPerKm", Number(event.target.value))
  );
  document.getElementById("siteVisitDurationMin").addEventListener("input", (event) =>
    updateSiteOverride("visitDurationMin", Number(event.target.value))
  );
  document.getElementById("resetSiteButton").addEventListener("click", resetSelectedSite);
  document.getElementById("highlightRouteNotes").addEventListener("change", (event) => {
    state.highlightRouteNotes = event.target.checked;
    renderMap(state.currentPlan);
  });
  document.getElementById("routeNoteFilter").addEventListener("change", (event) => {
    state.routeFilter = event.target.value;
    renderMap(state.currentPlan);
    renderRouteNotesTable(state.currentPlan);
  });
  document.getElementById("enableTargetMix").addEventListener("change", async (event) => {
    state.targetMixEnabled = event.target.checked;
    if (!state.targetMixEnabled && state.targetMixApplied) await returnToTravelPlan();
    updateSiteEditor();
  });
  document.getElementById("targetShare").addEventListener("input", (event) =>
    adjustTargetShare(state.selectedFacilityId, Number(event.target.value))
  );
  document.getElementById("previewTargetButton").addEventListener("click", previewTargetMix);
  document.getElementById("returnTravelPlanButton").addEventListener("click", returnToTravelPlan);

  await recalculatePlans();
  initializeTargetShares(state.currentPlan);
  setText("targetShareTotal", "100%");
  updateSiteEditor();
  document.body.classList.add("ready");
}

init().catch((error) => {
  console.error(error);
  setText("scenarioNarrative", "The OSRM demo data could not be loaded. Check the GitHub Pages asset paths.");
});
