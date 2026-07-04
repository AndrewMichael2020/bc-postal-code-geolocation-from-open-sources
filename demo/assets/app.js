import { allocatePostalCodes } from "./analytics.js";

const state = {
  postalCodes: [],
  hubs: [],
  summary: null,
  activeHubIds: new Set(),
  controls: {
    baseDemand: 0.9,
    urbanMultiplier: 1.18,
    ruralMultiplier: 0.72,
    capacityMultiplier: 1,
    distancePenalty: 0.012,
  },
  layers: {
    fsa: null,
    hubs: null,
  },
};

const formatNumber = new Intl.NumberFormat("en-CA", { maximumFractionDigits: 0 });
const formatDecimal = new Intl.NumberFormat("en-CA", {
  minimumFractionDigits: 1,
  maximumFractionDigits: 1,
});
const formatPercent = new Intl.NumberFormat("en-CA", {
  style: "percent",
  maximumFractionDigits: 0,
});

const map = L.map("map", {
  preferCanvas: true,
  zoomControl: false,
}).setView([49.205, -122.72], 9);

L.control.zoom({ position: "bottomright" }).addTo(map);
L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
  maxZoom: 18,
  attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
}).addTo(map);

function setText(id, value) {
  document.getElementById(id).textContent = value;
}

function readControls() {
  state.controls.baseDemand = Number(document.getElementById("baseDemand").value);
  state.controls.urbanMultiplier = Number(document.getElementById("urbanMultiplier").value);
  state.controls.ruralMultiplier = Number(document.getElementById("ruralMultiplier").value);
  state.controls.capacityMultiplier = Number(document.getElementById("capacityMultiplier").value);
  state.controls.distancePenalty = Number(document.getElementById("distancePenalty").value);
  setText("baseDemandValue", state.controls.baseDemand.toFixed(1));
  setText("urbanMultiplierValue", state.controls.urbanMultiplier.toFixed(2));
  setText("ruralMultiplierValue", state.controls.ruralMultiplier.toFixed(2));
  setText("capacityMultiplierValue", state.controls.capacityMultiplier.toFixed(2));
  setText("distancePenaltyValue", state.controls.distancePenalty.toFixed(3));
}

function renderHubControls() {
  const container = document.getElementById("hubControls");
  container.replaceChildren();
  for (const hub of state.hubs) {
    const label = document.createElement("label");
    label.className = "hub-toggle";
    label.style.setProperty("--hub-color", hub.color);
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.checked = state.activeHubIds.has(hub.id);
    checkbox.addEventListener("change", () => {
      if (checkbox.checked) {
        state.activeHubIds.add(hub.id);
      } else if (state.activeHubIds.size > 1) {
        state.activeHubIds.delete(hub.id);
      } else {
        checkbox.checked = true;
      }
      recalculate();
    });
    const span = document.createElement("span");
    span.textContent = hub.name;
    label.append(checkbox, span);
    container.append(label);
  }
}

function renderMap(result) {
  if (state.layers.fsa) {
    state.layers.fsa.remove();
  }
  if (state.layers.hubs) {
    state.layers.hubs.remove();
  }

  state.layers.fsa = L.layerGroup();
  for (const fsa of result.fsaSummaries) {
    const radius = Math.max(5, Math.min(20, Math.sqrt(fsa.postalCodeCount) * 0.55));
    L.circleMarker([fsa.latitude, fsa.longitude], {
      renderer: L.canvas(),
      radius,
      color: "#111827",
      weight: 1,
      fillColor: fsa.color,
      fillOpacity: 0.62,
    })
      .bindPopup(
        `<strong>${fsa.fsa}</strong><br>${fsa.hubName}<br>${formatNumber.format(
          fsa.postalCodeCount
        )} postal codes<br>${formatNumber.format(fsa.demand)} demand units`
      )
      .addTo(state.layers.fsa);
  }
  state.layers.fsa.addTo(map);

  state.layers.hubs = L.layerGroup();
  for (const hub of state.hubs.filter((item) => state.activeHubIds.has(item.id))) {
    const icon = L.divIcon({
      className: "hub-marker",
      html: `<span style="background:${hub.color}"></span>`,
      iconSize: [22, 22],
      iconAnchor: [11, 11],
    });
    L.marker([hub.latitude, hub.longitude], { icon })
      .bindPopup(`<strong>${hub.name}</strong><br>${hub.municipality}`)
      .addTo(state.layers.hubs);
  }
  state.layers.hubs.addTo(map);
}

function renderKpis(result) {
  setText("postalCount", formatNumber.format(result.assignedPostalCodeCount));
  setText("demandCount", formatNumber.format(result.totalDemand));
  setText("medianProxy", `${formatDecimal.format(result.medianDistanceKm)} km`);
  setText("p95Proxy", `${formatDecimal.format(result.p95DistanceKm)} km`);
  setText("imbalanceScore", formatPercent.format(result.imbalanceScore));
  setText("overloadedHubs", formatNumber.format(result.overloadedHubCount));
  setText("datasetShape", `${formatNumber.format(state.summary.postal_code_count)} postal codes`);
}

function renderTable(result) {
  const tbody = document.getElementById("allocationBody");
  tbody.replaceChildren();
  for (const summary of result.summaries) {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td><span class="swatch" style="background:${summary.hub.color}"></span>${summary.hub.name}</td>
      <td>${formatNumber.format(summary.postalCodeCount)}</td>
      <td>${formatNumber.format(summary.demand)}</td>
      <td>${formatPercent.format(summary.utilization)}</td>
      <td>${formatDecimal.format(summary.p95DistanceKm)} km</td>
    `;
    if (summary.overloaded) {
      tr.classList.add("overloaded");
    }
    tbody.append(tr);
  }
}

function renderNarrative(result) {
  const heaviest = result.summaries[0];
  const lightest = result.summaries[result.summaries.length - 1];
  const message =
    result.overloadedHubCount > 0
      ? `${result.overloadedHubCount} hub(s) exceed the current capacity assumption. Rebalance by activating another hub, lowering demand pressure, or increasing capacity.`
      : `The current scenario keeps all active hubs within capacity. ${heaviest.hub.name} carries the largest modelled demand while ${lightest.hub.name} carries the lightest.`;
  setText("scenarioNarrative", message);
}

function recalculate() {
  readControls();
  const result = allocatePostalCodes(state.postalCodes, state.hubs, {
    ...state.controls,
    activeHubIds: state.activeHubIds,
  });
  renderKpis(result);
  renderTable(result);
  renderNarrative(result);
  renderMap(result);
}

async function loadJson(path) {
  const response = await fetch(path);
  if (!response.ok) {
    throw new Error(`Failed to load ${path}: ${response.status}`);
  }
  return response.json();
}

async function init() {
  const [postalCodes, hubs, summary] = await Promise.all([
    loadJson("./data/lower-mainland-postal-codes.json"),
    loadJson("./data/service-hubs.json"),
    loadJson("./data/demo-summary.json"),
  ]);
  state.postalCodes = postalCodes;
  state.hubs = hubs;
  state.summary = summary;
  state.activeHubIds = new Set(hubs.map((hub) => hub.id));
  renderHubControls();
  for (const input of document.querySelectorAll("input[type='range']")) {
    input.addEventListener("input", recalculate);
  }
  recalculate();
  document.body.classList.add("ready");
}

init().catch((error) => {
  console.error(error);
  document.getElementById("scenarioNarrative").textContent =
    "The demo data could not be loaded. Check the GitHub Pages asset paths.";
});
