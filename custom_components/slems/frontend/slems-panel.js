/*
 * SLEMS sidebar panel.
 *
 * Plain custom element without build step. Home Assistant sets `hass`,
 * `panel` (with `panel.config` from panel.py), `narrow` and `route`.
 * Entities are found through the entity registry (`hass.entities`) by
 * platform "slems", their translation key and device, so renaming entities
 * does not break the panel.
 *
 * DOM sections are only replaced when their markup changed, so hover state
 * and inputs survive the frequent `hass` updates.
 */

const DOMAIN = "slems";
const STATS_REFRESH_MS = 5 * 60 * 1000;

// Categorical slots, validated for adjacent CVD separation in both modes.
const COLORS = {
  light: {
    pv: "#eda100",
    battery: "#1baf7a",
    grid: "#2a78d6",
    consumer: "#e87ba4",
    house: "#4a3aa7",
    grid_line: "#e1e0d9",
    axis: "#c3c2b7",
    muted: "#898781",
  },
  dark: {
    pv: "#c98500",
    battery: "#199e70",
    grid: "#3987e5",
    consumer: "#d55181",
    house: "#9085e9",
    grid_line: "#2c2c2a",
    axis: "#383835",
    muted: "#898781",
  },
};

const STRINGS = {
  en: {
    overview: "Overview",
    batteries: "Batteries",
    consumers: "Consumers",
    settings: "Settings",
    grid: "Grid",
    pv: "PV",
    house: "House",
    battery: "Battery",
    import: "Import",
    export: "Export",
    charging: "Charging",
    discharging: "Discharging",
    idle: "Idle",
    energyFlow: "Energy flow",
    today: "Today",
    dayChart: "Today: forecast and plan",
    dayChartTomorrow: "Tomorrow: forecast and plan",
    tomorrow: "Tomorrow",
    dayChartHint: "Hourly average power (left), total state of charge (right)",
    pvForecast: "PV forecast",
    pvActual: "PV measured",
    consumptionForecast: "Consumption forecast",
    consumptionActual: "Consumption measured",
    plannedCharge: "Planned charging",
    socForecast: "State of charge forecast",
    socActual: "State of charge measured",
    showTable: "Show table",
    showChart: "Show chart",
    hour: "Hour",
    now: "now",
    noData: "No forecast available yet",
    soc: "State of charge",
    power: "Power",
    acPower: "AC power",
    planned: "Planned",
    efficiency: "Round trip efficiency",
    state: "State",
    temperature: "Temperature",
    enabled: "Enabled",
    measured: "Measured",
    blocked: "blocked",
    saturated: "saturated",
    resting: "thermostat pause",
    controlOff: "control off",
    controlActive: "Control active",
    responseTime: "Response time",
    notControlled: "measured only",
    noBatteries: "No batteries configured.",
    noConsumers: "No consumers configured.",
    disabled: "disabled",
    notResponding: "not responding",
    unreadable: "cannot be read",
    unreadableText: "SLEMS cannot read the battery; it is neither planned with nor controlled.",
    gridStale: "Smart meter does not report",
    gridStaleText: "The batteries follow their own logic until the smart meter reports again.",
    notRespondingText: "does not deliver the commanded power ({reason}); handed back to its own logic, retried at {time}",
    notRespondingReasons: {
      charge_not_delivered: "no charging",
      discharge_not_delivered: "no discharging",
      write_failed: "command not confirmed",
    },
    allowedCharge: "Charging limited",
    allowedDischarge: "Discharging limited",
    limitReasons: { soc: "state of charge limit", power: "power limit", temperature: "temperature" },
    batteryLimits: "Limits: {name}",
    feedInLimitReasons: {
      disabled: "off",
      no_forecast: "no forecast",
      not_enough_surplus: "none – charge at once",
    },
    exportBelowTarget:
      "The maximum grid export while discharging ({limit}) is below the grid surplus target while discharging ({target}): the batteries control to {limit}.",
    settingHints: {
      discharge_max_grid_export:
        "Hard limit of the grid export while batteries discharge. 0 W: never feed battery energy into the grid. To switch the limit off, set it to the maximum.",
      night_reserve:
        "Energy that should remain in the batteries above their minimum state of charge when the night discharge ends (in the morning, when PV production exceeds the consumption). In % of the forecast consumption of the coming day, not of the state of charge. Example: 14 kWh forecast, 25 % → 3.5 kWh stay. If the PV forecast cannot refill the batteries from there, more energy stays.",
    },
    cellDelta: "Cell delta",
    cellDeltaHint: "live, meaningful only near full charge",
    topCellDelta: "Cell delta at top of charge",
    noTopMeasurement: "no measurement yet",
    statusGreen: "good",
    statusYellow: "minor imbalance",
    statusOrange: "moderate imbalance",
    statusRed: "high imbalance",
    balancingSuggested: "Cell balancing recommended",
    balancingSuggestedText:
      "The cells differed by {delta} mV at the top of the charge. Active cell balancing keeps the battery near full charge so the BMS can equalise the cells. It usually takes many hours.",
    startBalancing: "Start cell balancing",
    cancelBalancing: "Cancel cell balancing",
    balancing: "cell balancing",
    startBalancingTitle: "Start cell balancing for {name}?",
    startBalancingText:
      "The battery leaves the normal control: it charges into the top voltage range (from the PV surplus, at least 95 W; without surplus the other batteries cover it), stands by for a measurement, discharges a little and repeats until the cell delta is at most 30 mV (usually many hours, at most 24 h). Its discharge is fed into the grid unless the other batteries charge anyway.",
    startBalancingConfirm: "Start",
    balancingNeedsActive: "Cell balancing can only be started in operating mode active.",
    lastBalancing: "Last cell balancing",
    balancingResults: {
      done: "completed",
      cancelled: "cancelled",
      timeout: "stopped after 24 h",
      telemetry: "stopped: battery could not be read",
    },
    cancel: "Cancel",
    disableBatteryTitle: "Disable {name}?",
    disableBatteryText:
      "The battery is then no longer planned with or controlled by SLEMS and hands over to its own logic. If it is discharging right now, the other batteries take over within 5 seconds.",
    disableBatteryConfirm: "Disable",
    groups: {
      mode: "Operation",
      control: "Control",
      priority: "Battery priority and grid targets",
      temperature: "Temperature charge limit",
      gridFriendly: "Grid friendly charging",
      night: "Night discharge",
      peak: "Import peak shaving",
      rotation: "Several batteries",
    },
  },
  de: {
    overview: "Übersicht",
    batteries: "Batterien",
    consumers: "Verbraucher",
    settings: "Einstellungen",
    grid: "Netz",
    pv: "PV",
    house: "Haus",
    battery: "Batterie",
    import: "Bezug",
    export: "Einspeisung",
    charging: "Laden",
    discharging: "Entladen",
    idle: "Standby",
    energyFlow: "Energiefluss",
    today: "Heute",
    dayChart: "Heute: Prognose und Plan",
    dayChartTomorrow: "Morgen: Prognose und Plan",
    tomorrow: "Morgen",
    dayChartHint: "Mittlere Leistung pro Stunde (links), Gesamt-Ladezustand (rechts)",
    pvForecast: "PV-Prognose",
    pvActual: "PV gemessen",
    consumptionForecast: "Verbrauchsprognose",
    consumptionActual: "Verbrauch gemessen",
    plannedCharge: "Geplantes Laden",
    socForecast: "Ladezustand-Prognose",
    socActual: "Ladezustand gemessen",
    showTable: "Tabelle anzeigen",
    showChart: "Diagramm anzeigen",
    hour: "Stunde",
    now: "jetzt",
    noData: "Noch keine Prognose verfügbar",
    soc: "Ladezustand",
    power: "Leistung",
    acPower: "AC-Leistung",
    planned: "Geplant",
    efficiency: "Gesamtwirkungsgrad",
    state: "Status",
    temperature: "Temperatur",
    enabled: "Aktiviert",
    measured: "Gemessen",
    blocked: "gesperrt",
    saturated: "gesättigt",
    resting: "Thermostat-Pause",
    controlOff: "Steuerung aus",
    controlActive: "Steuerung aktiv",
    responseTime: "Reaktionszeit",
    notControlled: "nur gemessen",
    noBatteries: "Keine Batterien konfiguriert.",
    noConsumers: "Keine Verbraucher konfiguriert.",
    disabled: "deaktiviert",
    notResponding: "reagiert nicht",
    unreadable: "nicht lesbar",
    unreadableText: "SLEMS kann die Batterie nicht lesen; sie wird weder eingeplant noch gesteuert.",
    gridStale: "Smart Meter meldet nicht",
    gridStaleText: "Die Batterien folgen ihrer eigenen Logik, bis der Smart Meter wieder meldet.",
    notRespondingText: "liefert die vorgegebene Leistung nicht ({reason}); an die eigene Logik übergeben, neuer Versuch um {time}",
    notRespondingReasons: {
      charge_not_delivered: "lädt nicht",
      discharge_not_delivered: "entlädt nicht",
      write_failed: "Befehl nicht bestätigt",
    },
    allowedCharge: "Laden begrenzt",
    allowedDischarge: "Entladen begrenzt",
    limitReasons: { soc: "Ladezustandsgrenze", power: "Leistungsgrenze", temperature: "Temperatur" },
    batteryLimits: "Grenzen: {name}",
    feedInLimitReasons: {
      disabled: "aus",
      no_forecast: "keine Prognose",
      not_enough_surplus: "keine – sofort laden",
    },
    exportBelowTarget:
      "Die maximale Einspeisung beim Entladen ({limit}) liegt unter dem Ziel-Netzüberschuss beim Entladen ({target}): Die Batterien regeln auf {limit}.",
    settingHints: {
      discharge_max_grid_export:
        "Harte Grenze der Einspeisung, solange Batterien entladen. 0 W: nie Batterieenergie einspeisen. Zum Abschalten der Grenze auf das Maximum stellen.",
      night_reserve:
        "Energie, die am Ende der Nachtentladung (morgens, wenn die PV-Erzeugung den Verbrauch übersteigt) über dem minimalen Ladezustand in den Batterien bleiben soll. In % des prognostizierten Verbrauchs des kommenden Tages, nicht des Ladezustands. Beispiel: 14 kWh Prognose, 25 % → 3,5 kWh bleiben. Reicht die PV-Prognose nicht, um die Batterien von dort wieder zu füllen, bleibt mehr Energie.",
    },
    cellDelta: "Zell-Delta",
    cellDeltaHint: "live, nur nahe Vollladung aussagekräftig",
    topCellDelta: "Zell-Delta am oberen Ladeende",
    noTopMeasurement: "noch keine Messung",
    statusGreen: "gut",
    statusYellow: "leichtes Ungleichgewicht",
    statusOrange: "mittleres Ungleichgewicht",
    statusRed: "starkes Ungleichgewicht",
    balancingSuggested: "Zellausgleich empfohlen",
    balancingSuggestedText:
      "Die Zellen wichen am oberen Ladeende um {delta} mV voneinander ab. Der aktive Zellausgleich hält die Batterie nahe der Vollladung, damit das BMS die Zellen angleichen kann. Das dauert meist viele Stunden.",
    startBalancing: "Zellausgleich starten",
    cancelBalancing: "Zellausgleich abbrechen",
    balancing: "Zellausgleich",
    startBalancingTitle: "Zellausgleich für {name} starten?",
    startBalancingText:
      "Die Batterie verlässt die normale Steuerung: Sie lädt bis in den oberen Spannungsbereich (aus dem PV-Überschuss, mindestens mit 95 W; ohne Überschuss gleichen die anderen Batterien aus), ist für eine Messung im Standby, entlädt etwas und wiederholt das, bis das Zell-Delta höchstens 30 mV beträgt (meist viele Stunden, höchstens 24 h). Ihre Entladung wird eingespeist, außer die anderen Batterien laden ohnehin.",
    startBalancingConfirm: "Starten",
    balancingNeedsActive: "Der Zellausgleich kann nur im Betriebsmodus „Aktiv“ gestartet werden.",
    lastBalancing: "Letzter Zellausgleich",
    balancingResults: {
      done: "abgeschlossen",
      cancelled: "abgebrochen",
      timeout: "nach 24 h beendet",
      telemetry: "beendet: Batterie nicht lesbar",
    },
    cancel: "Abbrechen",
    disableBatteryTitle: "{name} deaktivieren?",
    disableBatteryText:
      "Die Batterie wird dann von SLEMS nicht mehr eingeplant und gesteuert und folgt ihrer eigenen Logik. Entlädt sie gerade, übernehmen die anderen Batterien innerhalb von 5 Sekunden.",
    disableBatteryConfirm: "Deaktivieren",
    groups: {
      mode: "Betrieb",
      control: "Regelung",
      priority: "Batterievorrang und Netz-Zielwerte",
      temperature: "Ladebegrenzung nach Temperatur",
      gridFriendly: "Netzdienliches Laden",
      night: "Nachtentladung",
      peak: "Bezugsspitzen abfangen",
      rotation: "Mehrere Batterien",
    },
  },
};

// Settings tab: limits of every battery (translation keys of its entities).
const BATTERY_SETTINGS = ["min_soc", "max_soc", "max_charge_limit", "max_discharge_limit"];

// Settings tab: translation keys of the system entities per group.
const SETTING_GROUPS = [
  ["mode", ["operating_mode", "vacation"]],
  ["control", ["auto_gain", "control_gain", "control_interval", "surplus_average_window"]],
  [
    "priority",
    [
      "battery_priority_soc",
      "battery_share_when_secured",
      "charge_secured_buffer",
      "charge_grid_target",
      "discharge_grid_target",
      "discharge_max_grid_export",
    ],
  ],
  ["gridFriendly", ["grid_friendly_charging", "grid_friendly_buffer"]],
  ["night", ["night_discharge", "night_reserve"]],
  ["peak", ["peak_shaving", "peak_shaving_grid_limit", "peak_shaving_soc_threshold"]],
  ["temperature", ["temperature_limit", "temperature_high", "temperature_band", "temperature_floor", "temperature_low"]],
  [
    "rotation",
    ["rotation_soc_threshold", "rotation_min_interval", "rotation_ramp_rate", "rotation_ramp_max"],
  ],
];

/** Consecutive rows with a value for ``key``; a missing hour starts a new run. */
function runs(rows, key) {
  const result = [];
  let current = [];
  for (const row of rows) {
    if (row[key] === null || row[key] === undefined) {
      if (current.length) result.push(current);
      current = [];
    } else {
      current.push(row);
    }
  }
  if (current.length) result.push(current);
  return result;
}

/** One polyline per run of points; a single point becomes a dot. */
function polylines(pointRuns, color, dash) {
  return pointRuns
    .map((points) =>
      points.length === 1
        ? `<circle cx="${points[0][0]}" cy="${points[0][1]}" r="2.5" fill="${color}"/>`
        : `<polyline points="${points.map((p) => p.join(",")).join(" ")}" fill="none" stroke="${color}"
          stroke-width="2" stroke-linejoin="round" stroke-linecap="round" ${dash ? 'stroke-dasharray="5 4"' : ""}/>`
    )
    .join("");
}

// Smallest width of a battery box in the energy flow before they are stacked.
const MIN_FLOW_BOX_W = 130;

// SLEMS icon (copy of assets/icon.svg) in the crossing of the energy flow lines.
const ICON_URL = new URL("slems-icon.svg", import.meta.url).href;

const OVERVIEW_TILES = [
  "operating_mode",
  "control_status",
  "allocation_strategy",
  "battery_soc_total",
  "feed_in_limit",
  "expected_surplus_energy",
  "pv_forecast_today",
  "consumption_forecast_today",
  "pv_forecast_tomorrow",
  "consumption_forecast_tomorrow",
];

const escapeHtml = (value) =>
  String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

class SlemsPanel extends HTMLElement {
  constructor() {
    super();
    this._tab = "overview";
    this._showTable = false;
    this._chartDay = "today";
    // Settings whose explanation is shown (translation keys).
    this._openHints = new Set();
    this._stats = { pv: {}, house: {}, soc: {} };
    this._statsFetched = 0;
    this._sections = {};
    this._renderQueued = false;
  }

  set hass(hass) {
    this._hass = hass;
    this._queueRender();
  }

  set panel(panel) {
    this._config = panel?.config || {};
    this._queueRender();
  }

  set narrow(narrow) {
    this._narrow = narrow;
    this._queueRender();
  }

  connectedCallback() {
    if (!this.shadowRoot) {
      this.attachShadow({ mode: "open" });
      this._buildSkeleton();
    }
    this._statsTimer = setInterval(() => this._fetchStats(true), STATS_REFRESH_MS);
    this._queueRender();
  }

  disconnectedCallback() {
    clearInterval(this._statsTimer);
  }

  // --- helpers ---------------------------------------------------------------

  get _t() {
    const language = this._hass?.locale?.language || this._hass?.language || "en";
    return language.startsWith("de") ? STRINGS.de : STRINGS.en;
  }

  get _colors() {
    return this._hass?.themes?.darkMode ? COLORS.dark : COLORS.light;
  }

  _entityId(key, deviceId) {
    const device = deviceId ?? this._config?.system_device_id;
    const entry = Object.values(this._hass.entities || {}).find(
      (e) => e.platform === DOMAIN && e.translation_key === key && (!device || e.device_id === device)
    );
    return entry?.entity_id;
  }

  _state(key, deviceId) {
    const id = this._entityId(key, deviceId);
    return id ? this._hass.states[id] : undefined;
  }

  _number(stateObj) {
    const value = parseFloat(stateObj?.state);
    return Number.isFinite(value) ? value : null;
  }

  /** Value of an overview tile; the feed-in limit explains why it has none. */
  _tileValue(stateObj) {
    const reason = stateObj.attributes?.reason;
    const noLimit = this._t.feedInLimitReasons;
    if (this._number(stateObj) === null && reason && noLimit[reason] && this._entityId("feed_in_limit") === stateObj.entity_id) {
      return noLimit[reason];
    }
    return this._format(stateObj);
  }

  /** Power of a foreign entity in W (it may report W, kW or MW). */
  _powerW(stateObj) {
    const value = this._number(stateObj);
    if (value === null) return null;
    const factor = { kW: 1000, MW: 1000000 }[stateObj.attributes?.unit_of_measurement] ?? 1;
    return value * factor;
  }

  _format(stateObj) {
    if (!stateObj) return "–";
    if (this._hass.formatEntityState) return this._hass.formatEntityState(stateObj);
    const unit = stateObj.attributes.unit_of_measurement;
    return unit ? `${stateObj.state} ${unit}` : stateObj.state;
  }

  _name(stateObj) {
    if (!stateObj) return "";
    const entry = this._hass.entities?.[stateObj.entity_id];
    const device = entry?.device_id ? this._hass.devices?.[entry.device_id] : undefined;
    let name = stateObj.attributes.friendly_name || stateObj.entity_id;
    const prefix = device?.name_by_user || device?.name;
    if (prefix && name.startsWith(prefix + " ")) name = name.slice(prefix.length + 1);
    return name;
  }

  _percent(value) {
    const language = this._hass?.locale?.language || "en";
    return `${new Intl.NumberFormat(language, { maximumFractionDigits: 0 }).format(value)} %`;
  }

  _watts(value) {
    if (value === null || value === undefined) return "–";
    const language = this._hass?.locale?.language || "en";
    if (Math.abs(value) >= 1000) {
      return `${new Intl.NumberFormat(language, { maximumFractionDigits: 2 }).format(value / 1000)} kW`;
    }
    return `${new Intl.NumberFormat(language, { maximumFractionDigits: 0 }).format(value)} W`;
  }

  _setSection(name, html) {
    const element = this.shadowRoot.getElementById(name);
    if (element && this._sections[name] !== html) {
      element.innerHTML = html;
      this._sections[name] = html;
    }
  }

  _queueRender() {
    if (this._renderQueued || !this.shadowRoot || !this._hass || !this._config) return;
    this._renderQueued = true;
    requestAnimationFrame(() => {
      this._renderQueued = false;
      this._render();
    });
  }

  // --- skeleton ----------------------------------------------------------------

  _buildSkeleton() {
    this.shadowRoot.innerHTML = `
      <style>${STYLE}</style>
      <div class="page">
        <header>
          <ha-menu-button id="menu"></ha-menu-button>
          <h1>SLEMS</h1>
          <nav id="tabs"></nav>
        </header>
        <main id="content"></main>
      </div>
      <dialog id="confirm">
        <h2 id="confirm-title"></h2>
        <p id="confirm-text"></p>
        <div class="dialog-buttons">
          <button class="link" data-answer="cancel"></button>
          <button class="danger" data-answer="confirm"></button>
        </div>
      </dialog>`;
    const dialog = this.shadowRoot.getElementById("confirm");
    dialog.addEventListener("click", (event) => {
      const answer = event.target.closest("button")?.dataset.answer;
      if (answer) dialog.close(answer);
      else if (event.target === dialog) dialog.close("cancel"); // click on the backdrop
    });
    dialog.addEventListener("close", () => {
      if (dialog.returnValue === "confirm") this._confirmAction?.();
      this._confirmAction = null;
    });
    this.shadowRoot.getElementById("tabs").addEventListener("click", (event) => {
      const tab = event.target.closest("button")?.dataset.tab;
      if (tab) {
        this._tab = tab;
        this._sections = {};
        this._render();
      }
    });
    const content = this.shadowRoot.getElementById("content");
    this._resizeObserver = new ResizeObserver(() => {
      const width = content.clientWidth;
      if (width !== this._lastWidth) {
        this._lastWidth = width;
        this._sections.daychart = undefined;
        this._queueRender();
      }
    });
    this._resizeObserver.observe(content);
    content.addEventListener("click", (event) => this._onClick(event));
    content.addEventListener("change", (event) => this._onChange(event));
    content.addEventListener("pointermove", (event) => this._onChartHover(event));
    content.addEventListener("pointerleave", () => this._hideTooltip(), true);
  }

  _render() {
    if (!this._hass || !this.shadowRoot) return;
    const menu = this.shadowRoot.getElementById("menu");
    menu.hass = this._hass;
    menu.narrow = this._narrow;
    const t = this._t;
    const tabs = ["overview", "batteries", "consumers", "settings"];
    this._setSection(
      "tabs",
      tabs
        .map((tab) => `<button data-tab="${tab}" class="${tab === this._tab ? "active" : ""}">${t[tab]}</button>`)
        .join("")
    );
    const content = this.shadowRoot.getElementById("content");
    content.style.setProperty("--c-pv", this._colors.pv);
    content.style.setProperty("--c-battery", this._colors.battery);
    content.style.setProperty("--c-grid", this._colors.grid);
    content.style.setProperty("--c-consumer", this._colors.consumer);
    content.style.setProperty("--c-house", this._colors.house);
    if (content.dataset.tab !== this._tab) {
      content.dataset.tab = this._tab;
      content.innerHTML = this._layout();
      this._sections = { tabs: this._sections.tabs };
    }
    if (this._tab === "overview") this._renderOverview();
    else if (this._tab === "batteries") this._renderBatteries();
    else if (this._tab === "consumers") this._renderConsumers();
    else this._renderSettings();
  }

  _layout() {
    if (this._tab === "overview") {
      return `
        <div id="banner"></div>
        <div class="grid two">
          <section class="card"><h2>${this._t.energyFlow}</h2><div id="flow"></div></section>
          <section class="card"><div id="tiles" class="tiles"></div></section>
        </div>
        <section class="card"><div id="daychart"></div></section>`;
    }
    return `<div id="${this._tab}-list" class="grid cards"></div>`;
  }

  // --- overview ------------------------------------------------------------------

  _renderOverview() {
    const t = this._t;
    this._setSection(
      "banner",
      this._state("control_status")?.state === "grid_stale"
        ? `<div class="problem"><ha-icon icon="mdi:alert-circle"></ha-icon><span><b>${t.gridStale}</b> – ${t.gridStaleText}</span></div>`
        : ""
    );
    this._renderFlow();
    this._setSection(
      "tiles",
      OVERVIEW_TILES.map((key) => this._state(key))
        .filter(Boolean)
        .map(
          (s) => `<div class="tile"><span class="label">${escapeHtml(this._name(s))}</span>
                  <span class="value">${escapeHtml(this._tileValue(s))}</span></div>`
        )
        .join("")
    );
    this._fetchStats(false);
    this._renderDayChart();
  }

  // --- energy flow ---------------------------------------------------------------
  //
  // Rounded boxes in a three column grid (PV on top; grid, hub, house in the
  // middle; one box per battery and per consumer below). The connectors are
  // drawn in an SVG overlay from the measured box positions, so the layout
  // adapts to any number of batteries and to the screen width. The DOM is
  // built once per set of batteries/consumers; afterwards only texts, classes
  // and animation speeds are updated so the animation keeps running smoothly.

  _flowNodes() {
    const t = this._t;
    const num = (key, device) => this._number(this._state(key, device));
    const grid = num("grid_power");
    const nodes = [
      { id: "pv", role: "pv", icon: "mdi:solar-power-variant", title: t.pv, power: num("pv_power") },
      {
        id: "grid",
        role: "grid",
        icon: "mdi:transmission-tower",
        title: t.grid,
        power: grid,
        detail: grid === null ? "" : grid > 10 ? t.import : grid < -10 ? t.export : "",
      },
      { id: "house", role: "house", icon: "mdi:home-lightning-bolt", title: t.house, power: num("house_power") },
    ];
    for (const battery of this._config.batteries || []) {
      const ac = this._state("ac_power", battery.device_id);
      // AC power is +discharge; the flow uses +charge.
      const power = ac ? (this._number(ac) === null ? null : -this._number(ac)) : num("battery_power", battery.device_id);
      const enabled = this._state("battery_enabled", battery.device_id)?.state !== "off";
      const balancing = this._state("cell_balancing", battery.device_id)?.state === "on";
      const notResponding = this._state("not_responding", battery.device_id)?.state === "on";
      const unreadable = this._state("battery_soc", battery.device_id)?.state === "unavailable";
      nodes.push({
        id: `battery-${battery.id}`,
        role: "battery",
        icon: "mdi:home-battery",
        title: battery.name,
        power,
        soc: num("battery_soc", battery.device_id),
        disabled: !enabled || balancing || notResponding || unreadable,
        detail: unreadable
          ? t.unreadable
          : !enabled
          ? t.disabled
          : notResponding
            ? t.notResponding
            : balancing
            ? t.balancing
            : power === null ? "" : power > 10 ? t.charging : power < -10 ? t.discharging : t.idle,
      });
    }
    for (const consumer of this._config.consumers || []) {
      nodes.push({
        id: `consumer-${consumer.id}`,
        role: "consumer",
        icon: consumer.type === "heat_pump" ? "mdi:heat-pump" : consumer.type === "heating_rod" ? "mdi:water-boiler" : "mdi:power-plug",
        title: consumer.name,
        power: this._powerW(this._hass.states[consumer.power_entity]),
      });
    }
    return nodes;
  }

  _renderFlow() {
    const container = this.shadowRoot.getElementById("flow");
    if (!container) return;
    const nodes = this._flowNodes();
    const key = nodes.map((n) => `${n.id}:${n.title}`).join("|") + this._t.pv;
    if (container.dataset.key !== key) {
      container.dataset.key = key;
      container.innerHTML = this._flowSkeleton(nodes);
      this._flowRoot = container.querySelector(".flow-root");
      this._flowObserver?.disconnect();
      this._flowObserver = new ResizeObserver(() => this._layoutFlow());
      this._flowObserver.observe(this._flowRoot);
    }
    for (const node of nodes) this._updateFlowNode(node);
    this._layoutFlow();
  }

  _flowSkeleton(nodes) {
    const box = (n) => `
      <div class="fbox ${n.role}" data-node="${n.id}">
        <div class="fbox-head">
          <span class="fbox-icon"><ha-icon icon="${n.icon}"></ha-icon></span>
          <span class="fbox-title">${escapeHtml(n.title)}</span>
        </div>
        <div class="fbox-value" data-field="value">–</div>
        ${n.role === "battery" ? `<div class="fbox-soc"><div class="bar"><div data-field="socbar"></div></div><span data-field="soc">–</span></div>` : ""}
        <div class="fbox-detail" data-field="detail"></div>
      </div>`;
    const byRole = (role) => nodes.filter((n) => n.role === role).map(box).join("");
    const hasConsumers = nodes.some((n) => n.role === "consumer");
    return `
      <div class="flow-root ${hasConsumers ? "" : "no-consumers"}">
        <svg class="flow-lines" aria-hidden="true"></svg>
        <div class="fcell top">${byRole("pv")}</div>
        <div class="fcell left">${byRole("grid")}</div>
        <div class="fcell center"><img class="hub" data-node="hub" src="${ICON_URL}" alt="SLEMS"></div>
        <div class="fcell right">${byRole("house")}</div>
        <div class="fcell batteries">${byRole("battery")}</div>
        ${hasConsumers ? `<div class="fcell consumers">${byRole("consumer")}</div>` : ""}
      </div>`;
  }

  _updateFlowNode(n) {
    const element = this._flowRoot?.querySelector(`[data-node="${n.id}"]`);
    if (!element) return;
    const set = (field, text) => {
      const target = element.querySelector(`[data-field="${field}"]`);
      if (target && target.textContent !== text) target.textContent = text;
    };
    const shown = n.role === "grid" || n.role === "battery" ? Math.abs(n.power ?? 0) : n.power;
    set("value", n.power === null ? "–" : this._watts(shown));
    set("detail", n.detail || "");
    if (n.role === "battery") {
      set("soc", n.soc === null ? "–" : `${Math.round(n.soc)} %`);
      const bar = element.querySelector('[data-field="socbar"]');
      if (bar) bar.style.width = `${Math.max(0, Math.min(100, n.soc ?? 0))}%`;
    }
    element.classList.toggle("disabled", Boolean(n.disabled));
    element.classList.toggle("active", n.power !== null && Math.abs(n.power) >= 10);
    this._flowPower = this._flowPower || {};
    this._flowPower[n.id] = n.power;
  }

  _layoutFlow() {
    const root = this._flowRoot;
    if (!root || !root.isConnected) return;
    // Batteries side by side only if every box keeps a readable width,
    // otherwise one below the other (phones, many batteries).
    const batteryCell = root.querySelector(".fcell.batteries");
    if (batteryCell) {
      const count = batteryCell.querySelectorAll("[data-node]").length;
      const gap = parseFloat(getComputedStyle(batteryCell).columnGap) || 0;
      const fits = count * MIN_FLOW_BOX_W + (count - 1) * gap <= batteryCell.clientWidth;
      batteryCell.classList.toggle("stacked", !fits);
    }
    const base = root.getBoundingClientRect();
    if (!base.width) return;
    const rect = (id) => {
      const element = root.querySelector(`[data-node="${id}"]`);
      if (!element) return null;
      const r = element.getBoundingClientRect();
      return { x: r.left - base.left, y: r.top - base.top, w: r.width, h: r.height };
    };
    const hub = rect("hub");
    if (!hub) return;
    const hx = hub.x + hub.w / 2;
    const hy = hub.y + hub.h / 2;
    const c = this._colors;
    const connectors = [];
    // Each connector is defined in the direction of positive power.
    const pv = rect("pv");
    if (pv) connectors.push({ id: "pv", color: c.pv, points: [[pv.x + pv.w / 2, pv.y + pv.h], [hx, hy]] });
    // Horizontal connectors run at the height of the hub (inside both boxes
    // even if they differ in height), so they stay straight.
    const level = (r) => Math.min(Math.max(hy, r.y + 12), r.y + r.h - 12);
    const grid = rect("grid");
    if (grid) connectors.push({ id: "grid", color: c.grid, points: [[grid.x + grid.w, level(grid)], [hx, hy]] });
    const house = rect("house");
    if (house) connectors.push({ id: "house", color: c.house, points: [[hx, hy], [house.x, level(house)]] });
    const batteries = [...root.querySelectorAll(".fcell.batteries [data-node]")].map((e) => e.dataset.node);
    const batteryRects = batteries.map(rect);
    if (batteryRects.length && batteryCell?.classList.contains("stacked")) {
      // Stacked: a trunk runs down left of the boxes (the consumers' trunk
      // is on the right) and branches into the left edge of every box.
      const trunkX = Math.min(...batteryRects.map((r) => r.x)) - 10;
      const bendY = (hub.y + hub.h + batteryRects[0].y) / 2;
      batteries.forEach((id, i) => {
        const r = batteryRects[i];
        const y = r.y + r.h / 2;
        connectors.push({
          id,
          color: c.battery,
          points: [[hx, hy], [hx, bendY], [trunkX, bendY], [trunkX, y], [r.x, y]],
        });
      });
    } else if (batteryRects.length) {
      const busY = (hy + Math.min(...batteryRects.map((r) => r.y))) / 2;
      batteries.forEach((id, i) => {
        const r = batteryRects[i];
        connectors.push({ id, color: c.battery, points: [[hx, hy], [hx, busY], [r.x + r.w / 2, busY], [r.x + r.w / 2, r.y]] });
      });
    }
    const consumers = [...root.querySelectorAll(".fcell.consumers [data-node]")].map((e) => e.dataset.node);
    if (house && consumers.length) {
      // Consumers are stacked below the house: a trunk runs down along the
      // left of the column and branches into the left edge of every box.
      const consumerRects = consumers.map(rect);
      const trunkX = Math.min(...consumerRects.map((r) => r.x)) - 10;
      const startX = house.x + house.w / 2;
      const startY = house.y + house.h;
      const bendY = (startY + consumerRects[0].y) / 2;
      consumers.forEach((id, i) => {
        const r = consumerRects[i];
        const y = r.y + r.h / 2;
        connectors.push({
          id,
          color: c.consumer,
          points: [[startX, startY], [startX, bendY], [trunkX, bendY], [trunkX, y], [r.x, y]],
        });
      });
    }
    const svg = root.querySelector(".flow-lines");
    svg.setAttribute("viewBox", `0 0 ${base.width} ${base.height}`);
    const markup = connectors
      .map((connector) => {
        const d = roundedPath(connector.points, 12);
        const power = this._flowPower?.[connector.id] ?? null;
        const active = power !== null && Math.abs(power) >= 10;
        // Faster dots for higher power, quantised to avoid restarting the animation.
        const duration = active ? Math.max(0.5, Math.min(4, Math.round((2500 / Math.abs(power)) * 4) / 4)) : 0;
        const width = active ? Math.min(5, 2 + Math.abs(power) / 1500) : 2;
        return `<path class="fline-base" d="${d}"/>
          ${active ? `<path class="fline ${power < 0 ? "reverse" : ""}" d="${d}" style="stroke:${connector.color};stroke-width:${width.toFixed(1)};animation-duration:${duration}s"/>` : ""}`;
      })
      .join("");
    if (svg.dataset.markup !== markup) {
      svg.dataset.markup = markup;
      svg.innerHTML = markup;
    }
  }

  async _fetchStats(force) {
    if (!this._hass || (!force && Date.now() - this._statsFetched < STATS_REFRESH_MS)) return;
    this._statsFetched = Date.now();
    const pvId = this._entityId("pv_power");
    const houseId = this._entityId("house_power");
    const socId = this._entityId("battery_soc_total");
    const ids = [pvId, houseId, socId].filter(Boolean);
    if (!ids.length) return;
    const start = new Date();
    start.setHours(0, 0, 0, 0);
    try {
      const result = await this._hass.callWS({
        type: "recorder/statistics_during_period",
        start_time: start.toISOString(),
        end_time: new Date().toISOString(),
        statistic_ids: ids,
        period: "hour",
        types: ["mean"],
        units: { power: "W" },
      });
      const byHour = (rows) =>
        Object.fromEntries((rows || []).map((row) => [new Date(row.start).getHours(), row.mean]));
      this._stats = { pv: byHour(result[pvId]), house: byHour(result[houseId]), soc: byHour(result[socId]) };
      this._sections.daychart = undefined;
      this._queueRender();
    } catch (err) {
      // Statistics are optional for the chart; keep the forecast visible.
      console.debug("SLEMS: statistics not available", err);
    }
  }

  _chartRows() {
    const attributes = this._state("feed_in_limit")?.attributes || {};
    const today = this._chartDay === "today";
    const plan = (today ? attributes.day_plan : attributes.day_plan_tomorrow) || [];
    return plan.map((row, hour) => ({
      hour,
      pvForecast: row.pv_wh,
      consumptionForecast: row.consumption_wh,
      plannedCharge: row.planned_charge_w,
      // Projected total state of charge at the end of the hour.
      socForecast: row.soc_pct,
      // Measured values exist for today only.
      pvActual: today ? this._stats.pv[hour] ?? null : null,
      consumptionActual: today ? this._stats.house[hour] ?? null : null,
      socActual: today ? this._stats.soc[hour] ?? null : null,
    }));
  }

  /** Where the projected state of charge line starts: [hour of day, %]. */
  _socStart() {
    if (this._chartDay === "today") {
      const now = new Date();
      const soc = this._number(this._state("battery_soc_total"));
      return soc === null ? null : [now.getHours() + now.getMinutes() / 60, soc];
    }
    const today = this._state("feed_in_limit")?.attributes?.day_plan || [];
    const last = today[today.length - 1]?.soc_pct;
    return last === null || last === undefined ? null : [0, last];
  }

  _renderDayChart() {
    const t = this._t;
    const rows = this._chartRows();
    const day = this._chartDay;
    const header = `
      <div class="chart-head">
        <div><h2>${day === "today" ? t.dayChart : t.dayChartTomorrow}</h2><span class="hint">${t.dayChartHint}</span></div>
        <div class="chart-actions">
          <div class="segmented" role="group">
            <button data-action="day-today" class="${day === "today" ? "active" : ""}" aria-pressed="${day === "today"}">${t.today}</button>
            <button data-action="day-tomorrow" class="${day === "tomorrow" ? "active" : ""}" aria-pressed="${day === "tomorrow"}">${t.tomorrow}</button>
          </div>
          <button class="link" data-action="toggle-table">${this._showTable ? t.showChart : t.showTable}</button>
        </div>
      </div>`;
    if (!rows.length) {
      this._setSection("daychart", `${header}<p class="empty">${t.noData}</p>`);
      return;
    }
    if (this._showTable) {
      this._setSection("daychart", header + this._chartTable(rows));
      return;
    }
    this._chartData = rows;
    this._setSection(
      "daychart",
      header + this._legend(rows) + this._chartSvg(rows) + `<div class="tooltip" id="tooltip" hidden></div>`
    );
  }

  _legend(rows) {
    const t = this._t;
    const has = (key) => rows.some((r) => r[key] !== null && r[key] !== undefined && r[key] !== 0);
    const item = (color, label, style) =>
      `<span class="legend-item"><svg width="22" height="10">${
        style === "bar"
          ? `<rect x="4" y="0" width="14" height="10" rx="2" fill="${color}"/>`
          : `<line x1="1" y1="5" x2="21" y2="5" stroke="${color}" stroke-width="2" ${style === "dash" ? 'stroke-dasharray="4 3"' : ""}/>`
      }</svg>${label}</span>`;
    const c = this._colors;
    return `<div class="legend">
      ${item(c.pv, t.pvForecast, "dash")}${has("pvActual") ? item(c.pv, t.pvActual, "solid") : ""}
      ${item(c.house, t.consumptionForecast, "dash")}${has("consumptionActual") ? item(c.house, t.consumptionActual, "solid") : ""}
      ${has("plannedCharge") ? item(c.battery, t.plannedCharge, "bar") : ""}
      ${has("socForecast") ? item(c.battery, t.socForecast, "dash") : ""}${has("socActual") ? item(c.battery, t.socActual, "solid") : ""}</div>`;
  }

  _chartSvg(rows) {
    const c = this._colors;
    // Draw in real pixels so text keeps its size on narrow screens.
    const available = this.shadowRoot.getElementById("daychart")?.clientWidth || 720;
    const width = Math.max(280, Math.round(available));
    const height = width < 500 ? 220 : 260;
    // The total state of charge is drawn on top with its own scale (0–100 %) on the right.
    const hasSoc = rows.some((r) => r.socForecast !== null && r.socForecast !== undefined) ||
      rows.some((r) => r.socActual !== null && r.socActual !== undefined);
    const pad = { left: 52, right: hasSoc ? 46 : 22, top: 10, bottom: 26 };
    const plotW = width - pad.left - pad.right;
    const plotH = height - pad.top - pad.bottom;
    const ys = (value) => pad.top + plotH - (Math.max(0, Math.min(100, value)) / 100) * plotH;
    const plotBottom = pad.top + plotH;
    const values = rows.flatMap((r) => [r.pvForecast, r.consumptionForecast, r.plannedCharge, r.pvActual, r.consumptionActual]);
    const max = Math.max(100, ...values.filter((v) => v !== null && v !== undefined));
    const step = niceStep(max / 4);
    const top = Math.ceil(max / step) * step;
    const x = (hour) => pad.left + (hour / 24) * plotW;
    const y = (value) => pad.top + plotH - (value / top) * plotH;
    this._chartGeometry = { pad, plotW, width };
    const socLines = [];
    if (hasSoc) {
      for (const v of [0, 25, 50, 75, 100]) {
        socLines.push(`<text x="${width - pad.right + 6}" y="${ys(v) + 4}" text-anchor="start" class="tick">${escapeHtml(this._percent(v))}</text>`);
      }
      const start = this._socStart();
      const forecast = runs(rows, "socForecast").map((run) => run.map((r) => [x(r.hour + 1), ys(r.socForecast)]));
      if (start && forecast.length) forecast[0].unshift([x(start[0]), ys(start[1])]);
      const actual = runs(rows, "socActual").map((run) => run.map((r) => [x(r.hour + 0.5), ys(r.socActual)]));
      socLines.push(polylines(forecast, c.battery, true), polylines(actual, c.battery, false));
    }

    const gridLines = [];
    for (let v = 0; v <= top; v += step) {
      gridLines.push(`<line x1="${pad.left}" x2="${width - pad.right}" y1="${y(v)}" y2="${y(v)}" stroke="${v === 0 ? c.axis : c.grid_line}" stroke-width="1"/>
        <text x="${pad.left - 6}" y="${y(v) + 4}" text-anchor="end" class="tick">${escapeHtml(this._watts(v))}</text>`);
    }
    const hourTicks = (width < 500 ? [0, 6, 12, 18, 24] : [0, 3, 6, 9, 12, 15, 18, 21, 24])
      .map((h) => `<text x="${x(h)}" y="${height - 8}" text-anchor="middle" class="tick">${String(h).padStart(2, "0")}:00</text>`)
      .join("");
    const slot = plotW / 24;
    const bars = rows
      .filter((r) => r.plannedCharge > 0)
      .map((r) => {
        const barW = Math.max(2, slot - 6);
        const h = Math.max(1, plotH - (y(r.plannedCharge) - pad.top));
        return `<path d="${roundedTopBar(x(r.hour) + 3, y(r.plannedCharge), barW, h)}" fill="${c.battery}"/>`;
      })
      .join("");
    const path = (key, color, dash) =>
      polylines(runs(rows, key).map((run) => run.map((r) => [x(r.hour + 0.5), y(r[key])])), color, dash);
    const now = new Date();
    const nowHour = now.getHours() + now.getMinutes() / 60;
    const showNow = this._chartDay === "today";
    return `
      <div class="chart-wrap"><svg viewBox="0 0 ${width} ${height}" width="${width}" height="${height}" class="chart" role="img" aria-label="${this._t.dayChart}">
        ${gridLines.join("")}${hourTicks}${bars}
        ${path("pvForecast", c.pv, true)}${path("pvActual", c.pv, false)}
        ${path("consumptionForecast", c.house, true)}${path("consumptionActual", c.house, false)}
        ${socLines.join("")}
        ${showNow ? `<line x1="${x(nowHour)}" x2="${x(nowHour)}" y1="${pad.top}" y2="${plotBottom}" stroke="${c.muted}" stroke-dasharray="2 3"/>
        <text x="${x(nowHour) + 4}" y="${pad.top + 10}" class="tick">${this._t.now}</text>` : ""}
        <line id="crosshair" x1="0" x2="0" y1="${pad.top}" y2="${plotBottom}" stroke="${c.muted}" visibility="hidden"/>
        <rect class="hit" x="${pad.left}" y="${pad.top}" width="${plotW}" height="${plotBottom - pad.top}" fill="transparent"/>
      </svg></div>`;
  }

  _chartTable(rows) {
    const t = this._t;
    const cell = (v) => (v === null || v === undefined ? "–" : escapeHtml(this._watts(v)));
    const percent = (v) => (v === null || v === undefined ? "–" : escapeHtml(this._percent(v)));
    return `<div class="table-wrap"><table>
      <thead><tr><th>${t.hour}</th><th>${t.pvForecast}</th><th>${t.pvActual}</th><th>${t.consumptionForecast}</th>
      <th>${t.consumptionActual}</th><th>${t.plannedCharge}</th><th>${t.socForecast}</th><th>${t.socActual}</th></tr></thead>
      <tbody>${rows
        .map(
          (r) => `<tr><td>${String(r.hour).padStart(2, "0")}:00</td><td>${cell(r.pvForecast)}</td><td>${cell(r.pvActual)}</td>
          <td>${cell(r.consumptionForecast)}</td><td>${cell(r.consumptionActual)}</td><td>${cell(r.plannedCharge)}</td>
          <td>${percent(r.socForecast)}</td><td>${percent(r.socActual)}</td></tr>`
        )
        .join("")}</tbody></table></div>`;
  }

  _onChartHover(event) {
    const svg = event.target.closest?.("svg.chart");
    if (!svg || !this._chartData || !this._chartGeometry) return;
    const { pad, plotW, width } = this._chartGeometry;
    const rect = svg.getBoundingClientRect();
    const viewX = ((event.clientX - rect.left) / rect.width) * width;
    const hour = Math.floor(((viewX - pad.left) / plotW) * 24);
    const row = this._chartData[hour];
    const tooltip = this.shadowRoot.getElementById("tooltip");
    const crosshair = this.shadowRoot.getElementById("crosshair");
    if (!row || !tooltip) {
      this._hideTooltip();
      return;
    }
    const cx = pad.left + ((hour + 0.5) / 24) * plotW;
    crosshair.setAttribute("x1", cx);
    crosshair.setAttribute("x2", cx);
    crosshair.setAttribute("visibility", "visible");
    const t = this._t;
    const c = this._colors;
    const entry = (color, label, value) =>
      value === null || value === undefined
        ? ""
        : `<div><span class="swatch" style="background:${color}"></span>${label}<b>${escapeHtml(this._watts(value))}</b></div>`;
    const percentEntry = (color, label, value) =>
      value === null || value === undefined
        ? ""
        : `<div><span class="swatch" style="background:${color}"></span>${label}<b>${escapeHtml(this._percent(value))}</b></div>`;
    tooltip.innerHTML = `<div class="tt-title">${String(hour).padStart(2, "0")}:00–${String(hour + 1).padStart(2, "0")}:00</div>
      ${entry(c.pv, t.pvForecast, row.pvForecast)}${entry(c.pv, t.pvActual, row.pvActual)}
      ${entry(c.house, t.consumptionForecast, row.consumptionForecast)}${entry(c.house, t.consumptionActual, row.consumptionActual)}
      ${entry(c.battery, t.plannedCharge, row.plannedCharge)}
      ${percentEntry(c.battery, t.socForecast, row.socForecast)}${percentEntry(c.battery, t.socActual, row.socActual)}`;
    tooltip.hidden = false;
    // Position relative to the chart card, next to the cursor.
    const card = this.shadowRoot.getElementById("daychart").getBoundingClientRect();
    const left = (cx / width) * rect.width + (rect.left - card.left);
    const flip = left > card.width * 0.6;
    const top = Math.min(event.clientY - card.top + 12, card.height - tooltip.offsetHeight - 8);
    tooltip.style.left = `${flip ? left - tooltip.offsetWidth - 12 : left + 12}px`;
    tooltip.style.top = `${Math.max(0, top)}px`;
  }

  _hideTooltip() {
    const tooltip = this.shadowRoot?.getElementById("tooltip");
    if (tooltip) tooltip.hidden = true;
    this.shadowRoot?.getElementById("crosshair")?.setAttribute("visibility", "hidden");
  }

  // --- batteries & consumers -----------------------------------------------------

  _renderBatteries() {
    const t = this._t;
    const batteries = this._config.batteries || [];
    if (!batteries.length) {
      this._setSection("batteries-list", `<p class="empty">${t.noBatteries}</p>`);
      return;
    }
    this._setSection(
      "batteries-list",
      batteries
        .map((b) => {
          const s = (key) => this._state(key, b.device_id);
          const soc = this._number(s("battery_soc"));
          const enabled = s("battery_enabled");
          const notResponding = s("not_responding");
          const limited = (key, label) => {
            const st = s(key);
            const reason = st?.attributes?.reason;
            return reason ? [`${label} (${t.limitReasons[reason] || reason})`, st] : [label, undefined];
          };
          const rows = [
            [t.power, s("battery_power")],
            [t.acPower, s("ac_power")],
            [t.planned, s("planned_power")],
            [t.efficiency, s("round_trip_efficiency")],
            [t.state, s("inverter_state")],
            [t.temperature, s("internal_temperature")],
            limited("allowed_charge_power", t.allowedCharge),
            limited("allowed_discharge_power", t.allowedDischarge),
          ].filter(([, st]) => st);
          let problem = "";
          if (s("battery_soc")?.state === "unavailable") {
            problem = `<div class="problem"><ha-icon icon="mdi:alert-circle"></ha-icon><span><b>${t.unreadable}</b> – ${t.unreadableText}</span></div>`;
          } else if (notResponding?.state === "on") {
            const attrs = notResponding.attributes;
            const retry = attrs.retry_at ? new Date(attrs.retry_at).toLocaleTimeString(this._hass.locale?.language, { hour: "2-digit", minute: "2-digit" }) : "–";
            const text = t.notRespondingText
              .replace("{reason}", t.notRespondingReasons[attrs.reason] || attrs.reason || "–")
              .replace("{time}", retry);
            problem = `<div class="problem"><ha-icon icon="mdi:alert-circle"></ha-icon><span><b>${t.notResponding}</b> – ${escapeHtml(text)}</span></div>`;
          }
          return `<section class="card">
            <div class="card-head"><h2>${escapeHtml(b.name)}</h2>
              ${enabled ? this._toggle(enabled, t.enabled, b.name) : ""}</div>
            ${problem}
            <div class="soc"><div class="soc-bar"><div style="width:${soc ?? 0}%"></div></div>
              <span>${soc === null ? "–" : Math.round(soc) + " %"}</span></div>
            <dl>${rows.map(([label, st]) => `<dt>${label}</dt><dd>${escapeHtml(this._format(st))}</dd>`).join("")}</dl>
            ${this._cellSection(b)}
          </section>`;
        })
        .join("")
    );
  }

  _cellSection(battery) {
    const t = this._t;
    const s = (key) => this._state(key, battery.device_id);
    const live = s("cell_delta");
    const top = s("top_cell_delta");
    const phase = s("balancing_phase");
    const switchState = s("cell_balancing");
    if (!live && !top) return "";
    const status = top?.attributes?.status;
    const statusLabel = { green: t.statusGreen, yellow: t.statusYellow, orange: t.statusOrange, red: t.statusRed }[status];
    const icon = { green: "mdi:check-circle", yellow: "mdi:alert-circle-outline", orange: "mdi:alert", red: "mdi:alert-octagon" }[status];
    const topValue =
      top && this._number(top) !== null
        ? `${escapeHtml(this._format(top))}${status ? ` <span class="status ${status}"><ha-icon icon="${icon}"></ha-icon>${statusLabel}</span>` : ""}`
        : `<span class="muted">${t.noTopMeasurement}</span>`;
    const balancing = switchState?.state === "on";
    const active = this._state("operating_mode")?.state === "active";
    const disabled = active ? "" : ` disabled title="${escapeHtml(t.balancingNeedsActive)}"`;
    const hint = active ? "" : `<span class="muted balancing-hint">${t.balancingNeedsActive}</span>`;
    let action = "";
    if (switchState && balancing) {
      action = `<div class="balancing-run"><ha-icon icon="mdi:scale-balance"></ha-icon>
          <span>${escapeHtml(this._format(phase))}</span>
          <button class="link" data-action="balancing-off" data-entity="${switchState.entity_id}">${t.cancelBalancing}</button></div>`;
    } else if (switchState && top?.attributes?.suggest_balancing) {
      const text = t.balancingSuggestedText.replace("{delta}", Math.round(this._number(top)));
      action = `<div class="suggestion" title="${escapeHtml(text)}">
          <ha-icon icon="mdi:scale-unbalanced"></ha-icon>
          <span class="suggestion-text"><b>${t.balancingSuggested}</b><span>${escapeHtml(text)}</span></span>
          ${hint}<button class="primary" data-action="balancing-on" data-entity="${switchState.entity_id}" data-name="${escapeHtml(battery.name)}"${disabled}>${t.startBalancing}</button>
        </div>`;
    } else if (switchState && switchState.state !== "unavailable") {
      action = `<button class="link start-balancing" data-action="balancing-on" data-entity="${switchState.entity_id}" data-name="${escapeHtml(battery.name)}"${disabled}>${t.startBalancing}</button>${hint}`;
    }
    const result = t.balancingResults[phase?.attributes?.last_result];
    const lastRun = !balancing && result ? `<dt>${t.lastBalancing}</dt><dd>${escapeHtml(result)}</dd>` : "";
    return `<div class="cells">
        <dl>
          <dt>${t.cellDelta}<span class="sub">${t.cellDeltaHint}</span></dt><dd>${escapeHtml(this._format(live))}</dd>
          <dt>${t.topCellDelta}</dt><dd>${topValue}</dd>
          ${lastRun}
        </dl>
        ${action}
      </div>`;
  }

  _renderConsumers() {
    const t = this._t;
    const consumers = this._config.consumers || [];
    if (!consumers.length) {
      this._setSection("consumers-list", `<p class="empty">${t.noConsumers}</p>`);
      return;
    }
    this._setSection(
      "consumers-list",
      consumers
        .map((c) => {
          const measured = this._hass.states[c.power_entity];
          const planned = this._state("planned_power", c.device_id);
          const attrs = planned?.attributes || {};
          const control = this._state("consumer_control", c.device_id);
          const chips = [
            !c.controllable ? t.notControlled : "",
            control?.state === "off" ? t.controlOff : "",
            attrs.blocked ? t.blocked : "",
            attrs.saturated ? t.saturated : "",
            attrs.resting ? t.resting : "",
          ]
            .filter(Boolean)
            .map((chip) => `<span class="chip">${chip}</span>`)
            .join("");
          const response =
            attrs.response_time_s !== null && attrs.response_time_s !== undefined
              ? `${Math.round(attrs.response_time_s)} s`
              : "–";
          return `<section class="card">
            <div class="card-head"><h2>${escapeHtml(c.name)}</h2><div class="chips">${chips}${control ? this._toggle(control, t.controlActive) : ""}</div></div>
            <dl>
              <dt>${t.measured}</dt><dd>${escapeHtml(this._format(measured))}</dd>
              ${planned ? `<dt>${t.planned}</dt><dd>${escapeHtml(this._format(planned))}</dd>` : ""}
              ${c.controllable ? `<dt>${t.responseTime}</dt><dd>${response}</dd>` : ""}
            </dl></section>`;
        })
        .join("")
    );
  }

  // --- settings ----------------------------------------------------------------

  _renderSettings() {
    const t = this._t;
    this._setSection(
      "settings-list",
      SETTING_GROUPS.map(([group, keys]) => {
        const rows = keys.map((key) => this._state(key)).filter(Boolean);
        if (!rows.length) return "";
        return `<section class="card"><h2>${t.groups[group]}</h2>
          <div class="settings">${rows.map((s) => this._control(s)).join("")}</div>${
            group === "priority" ? this._exportNote() : ""
          }</section>`;
      }).join("") +
        (this._config.batteries || [])
          .map((b) => {
            const rows = BATTERY_SETTINGS.map((key) => this._state(key, b.device_id)).filter(Boolean);
            if (!rows.length) return "";
            return `<section class="card"><h2>${escapeHtml(t.batteryLimits.replace("{name}", b.name))}</h2>
              <div class="settings">${rows.map((s) => this._control(s)).join("")}</div></section>`;
          })
          .join("")
    );
  }

  /** Note when the export limit silently overrides the discharge target. */
  _exportNote() {
    const limit = this._number(this._state("discharge_max_grid_export"));
    const target = this._number(this._state("discharge_grid_target"));
    if (limit === null || target === null || limit >= target) return "";
    const text = this._t.exportBelowTarget
      .replaceAll("{limit}", this._watts(limit))
      .replace("{target}", this._watts(target));
    return `<p class="setting-note"><ha-icon icon="mdi:information-outline"></ha-icon>${escapeHtml(text)}</p>`;
  }

  _toggle(stateObj, label, confirmOffName = null) {
    const confirm = confirmOffName ? ` data-confirm-off="${escapeHtml(confirmOffName)}"` : "";
    return `<label class="switch" title="${escapeHtml(label)}">
      <input type="checkbox" data-entity="${stateObj.entity_id}" data-kind="switch"${confirm} ${stateObj.state === "on" ? "checked" : ""}>
      <span></span></label>`;
  }

  _control(stateObj) {
    const domain = stateObj.entity_id.split(".")[0];
    const key = this._hass.entities?.[stateObj.entity_id]?.translation_key;
    const hint = this._t.settingHints[key];
    // Title for the mouse, a click on the icon opens the text (touch screens).
    const name = hint
      ? `${escapeHtml(this._name(stateObj))}<button class="info" data-action="toggle-hint" data-key="${key}" title="${escapeHtml(hint)}" aria-label="${escapeHtml(hint)}"><ha-icon icon="mdi:information-outline"></ha-icon></button>${
          this._openHints.has(key) ? `<span class="setting-hint">${escapeHtml(hint)}</span>` : ""
        }`
      : escapeHtml(this._name(stateObj));
    if (domain === "switch") {
      return `<div class="setting"><span>${name}</span>${this._toggle(stateObj, name)}</div>`;
    }
    if (domain === "select") {
      const options = stateObj.attributes.options || [];
      const label = (option) =>
        this._hass.formatEntityState ? this._hass.formatEntityState(stateObj, option) : option;
      return `<div class="setting"><span>${name}</span><select data-entity="${stateObj.entity_id}" data-kind="select">
        ${options.map((o) => `<option value="${escapeHtml(o)}" ${o === stateObj.state ? "selected" : ""}>${escapeHtml(label(o))}</option>`).join("")}
        </select></div>`;
    }
    const a = stateObj.attributes;
    // As many decimals as the step has (7000, 12, 0.5).
    const decimals = String(a.step ?? 1).split(".")[1]?.length ?? 0;
    const numeric = parseFloat(stateObj.state);
    const value = Number.isFinite(numeric) ? numeric.toFixed(decimals) : stateObj.state;
    return `<div class="setting"><span>${name}</span><span class="number">
      <input type="number" data-entity="${stateObj.entity_id}" data-kind="number" value="${escapeHtml(value)}"
        min="${a.min}" max="${a.max}" step="${a.step}"><span class="unit">${escapeHtml(a.unit_of_measurement || "")}</span></span></div>`;
  }

  // --- events ------------------------------------------------------------------

  _confirm(title, text, confirmLabel, action, danger = true) {
    const dialog = this.shadowRoot.getElementById("confirm");
    this.shadowRoot.getElementById("confirm-title").textContent = title;
    this.shadowRoot.getElementById("confirm-text").textContent = text;
    dialog.querySelector('[data-answer="cancel"]').textContent = this._t.cancel;
    const confirmButton = dialog.querySelector('[data-answer="confirm"]');
    confirmButton.textContent = confirmLabel;
    confirmButton.className = danger ? "danger" : "primary";
    this._confirmAction = action;
    dialog.returnValue = "";
    dialog.showModal();
    dialog.querySelector('[data-answer="cancel"]').focus();
  }

  _onClick(event) {
    const balancingButton = event.target.closest("[data-action='balancing-on'], [data-action='balancing-off']");
    if (balancingButton) {
      const entityId = balancingButton.dataset.entity;
      if (balancingButton.dataset.action === "balancing-off") {
        this._hass.callService("switch", "turn_off", { entity_id: entityId });
      } else {
        const t = this._t;
        this._confirm(
          t.startBalancingTitle.replace("{name}", balancingButton.dataset.name),
          t.startBalancingText,
          t.startBalancingConfirm,
          () => this._hass.callService("switch", "turn_on", { entity_id: entityId }),
          false
        );
      }
      return;
    }
    const dayButton = event.target.closest("[data-action='day-today'], [data-action='day-tomorrow']");
    if (dayButton) {
      this._chartDay = dayButton.dataset.action === "day-today" ? "today" : "tomorrow";
      this._sections.daychart = undefined;
      this._render();
      return;
    }
    const hintButton = event.target.closest("[data-action='toggle-hint']");
    if (hintButton) {
      const key = hintButton.dataset.key;
      if (!this._openHints.delete(key)) this._openHints.add(key);
      this._render();
      return;
    }
    if (event.target.closest("[data-action='toggle-table']")) {
      this._showTable = !this._showTable;
      this._sections.daychart = undefined;
      this._render();
    }
  }

  _onChange(event) {
    const target = event.target;
    const entityId = target.dataset?.entity;
    if (!entityId) return;
    const kind = target.dataset.kind;
    if (kind === "switch" && !target.checked && target.dataset.confirmOff) {
      // Keep the switch on until the user confirmed.
      target.checked = true;
      const t = this._t;
      this._confirm(
        t.disableBatteryTitle.replace("{name}", target.dataset.confirmOff),
        t.disableBatteryText,
        t.disableBatteryConfirm,
        () => this._hass.callService("switch", "turn_off", { entity_id: entityId })
      );
      return;
    }
    if (kind === "switch") {
      this._hass.callService("switch", target.checked ? "turn_on" : "turn_off", { entity_id: entityId });
    } else if (kind === "select") {
      this._hass.callService("select", "select_option", { entity_id: entityId, option: target.value });
    } else if (kind === "number") {
      const value = parseFloat(target.value);
      if (Number.isFinite(value)) {
        this._hass.callService("number", "set_value", { entity_id: entityId, value });
      }
    }
  }
}

function niceStep(raw) {
  const magnitude = 10 ** Math.floor(Math.log10(raw));
  const normalized = raw / magnitude;
  const nice = normalized <= 1 ? 1 : normalized <= 2 ? 2 : normalized <= 2.5 ? 2.5 : normalized <= 5 ? 5 : 10;
  return nice * magnitude;
}

function roundedPath(points, radius) {
  // Orthogonal polyline with rounded corners.
  let d = `M${points[0][0]},${points[0][1]}`;
  for (let i = 1; i < points.length - 1; i++) {
    const [px, py] = points[i - 1];
    const [x, y] = points[i];
    const [nx, ny] = points[i + 1];
    const inLen = Math.hypot(x - px, y - py);
    const outLen = Math.hypot(nx - x, ny - y);
    const r = Math.min(radius, inLen / 2, outLen / 2);
    const ax = x - ((x - px) / (inLen || 1)) * r;
    const ay = y - ((y - py) / (inLen || 1)) * r;
    const bx = x + ((nx - x) / (outLen || 1)) * r;
    const by = y + ((ny - y) / (outLen || 1)) * r;
    d += ` L${ax},${ay} Q${x},${y} ${bx},${by}`;
  }
  const last = points[points.length - 1];
  return `${d} L${last[0]},${last[1]}`;
}

function roundedTopBar(x, y, width, height) {
  const r = Math.min(4, width / 2, height);
  return `M${x},${y + height} V${y + r} Q${x},${y} ${x + r},${y} H${x + width - r} Q${x + width},${y} ${x + width},${y + r} V${y + height} Z`;
}

const STYLE = `
  :host { display: block; min-height: 100%; background: var(--primary-background-color); color: var(--primary-text-color);
    font-family: var(--ha-font-family-body, system-ui, -apple-system, "Segoe UI", sans-serif); }
  .page { max-width: 1280px; margin: 0 auto; padding: 0 16px 24px; }
  header { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; min-height: 56px; }
  h1 { font-size: 20px; font-weight: 500; margin: 0 16px 0 0; }
  nav { display: flex; gap: 4px; flex-wrap: wrap; }
  nav button { background: none; border: none; color: var(--secondary-text-color); font: inherit; padding: 8px 12px;
    border-radius: 8px; cursor: pointer; }
  nav button.active { color: var(--primary-color); background: color-mix(in srgb, var(--primary-color) 12%, transparent); }
  .grid { display: grid; gap: 16px; margin-bottom: 16px; }
  .grid.two { grid-template-columns: minmax(0, 3fr) minmax(0, 2fr); }
  .grid.cards { grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); }
  @media (max-width: 860px) { .grid.two { grid-template-columns: minmax(0, 1fr); } }
  .card { background: var(--card-background-color); border-radius: var(--ha-card-border-radius, 12px);
    border: 1px solid var(--divider-color); padding: 16px; box-sizing: border-box; min-width: 0; }
  h2 { font-size: 16px; font-weight: 500; margin: 0 0 12px; }
  .hint, .tick, .label, dt, .unit, .empty { color: var(--secondary-text-color); }
  .hint { font-size: 12px; }
  .tiles { display: grid; grid-template-columns: repeat(auto-fill, minmax(150px, 1fr)); gap: 12px; }
  .tile { display: flex; flex-direction: column; gap: 4px; padding: 8px 0; border-bottom: 1px solid var(--divider-color); }
  .tile .label { font-size: 12px; }
  .tile .value { font-size: 18px; }
  .flow-root { position: relative; display: grid; grid-template-columns: repeat(3, minmax(0, 1fr));
    grid-template-areas: ". top ." "left center right" "batteries batteries consumers";
    row-gap: 44px; column-gap: 16px; align-items: center; padding: 4px 0 8px; }
  .flow-root.no-consumers { grid-template-areas: ". top ." "left center right" "batteries batteries batteries"; }
  .fcell { display: flex; justify-content: center; gap: 12px; position: relative; z-index: 1; }
  .fcell.consumers, .fcell.batteries.stacked { flex-direction: column; align-items: center; }
  .fcell.top { grid-area: top; } .fcell.left { grid-area: left; } .fcell.center { grid-area: center; }
  .fcell.right { grid-area: right; } .fcell.batteries { grid-area: batteries; align-items: flex-start; }
  .fcell.consumers { grid-area: consumers; }
  .flow-lines { position: absolute; inset: 0; width: 100%; height: 100%; overflow: visible; z-index: 0; }
  .fline-base { fill: none; stroke: var(--divider-color); stroke-width: 2; }
  .fline { fill: none; stroke-dasharray: 2 10; stroke-linecap: round; animation: fdash linear infinite; }
  .fline.reverse { animation-direction: reverse; }
  @keyframes fdash { to { stroke-dashoffset: -12; } }
  @media (prefers-reduced-motion: reduce) { .fline { animation: none; stroke-dasharray: none; } }
  .hub { width: 60px; height: 60px; display: block; }
  @media (max-width: 500px) { .hub { width: 45px; height: 45px; } }
  .fbox { --accent: var(--divider-color); background: var(--card-background-color); border: 1px solid var(--divider-color);
    border-radius: 12px; padding: 10px 12px; min-width: 0; width: 100%; max-width: 170px; min-height: 104px;
    flex: 1 1 0; box-sizing: border-box; box-shadow: inset 0 3px 0 var(--accent); transition: opacity 0.2s;
    display: flex; flex-direction: column; }
  .fbox.pv { --accent: var(--c-pv); } .fbox.grid { --accent: var(--c-grid); } .fbox.house { --accent: var(--c-house); }
  .fbox.battery { --accent: var(--c-battery); } .fbox.consumer { --accent: var(--c-consumer); }
  .fbox.disabled { opacity: 0.55; }
  .fbox-head { display: flex; align-items: center; gap: 8px; min-width: 0; }
  .fbox-icon { display: inline-flex; align-items: center; justify-content: center; width: 28px; height: 28px; flex-shrink: 0;
    border-radius: 8px; background: color-mix(in srgb, var(--accent) 16%, transparent); }
  .fbox-icon ha-icon { --mdc-icon-size: 18px; color: var(--accent); }
  .fbox-title { font-size: 12px; color: var(--secondary-text-color); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .fbox-value { font-size: 20px; font-weight: 500; margin-top: 6px; font-variant-numeric: tabular-nums; white-space: nowrap; }
  .fbox-detail { font-size: 12px; color: var(--secondary-text-color); min-height: 16px; margin-top: auto; }
  .fbox-soc { display: flex; align-items: center; gap: 6px; margin-top: 6px; font-size: 12px; }
  .fbox-soc .bar { flex: 1; height: 6px; border-radius: 3px; background: var(--divider-color); overflow: hidden; min-width: 40px; }
  .fbox-soc .bar div { height: 100%; border-radius: 3px; background: var(--c-battery); transition: width 0.4s; }
  @media (max-width: 600px) {
    .flow-root { column-gap: 8px; row-gap: 36px; }
    .fbox { padding: 8px; min-height: 96px; }
    .fbox-value { font-size: 16px; }
    .fbox-icon { width: 24px; height: 24px; }
  }
  .chart-head { display: flex; justify-content: space-between; align-items: flex-start; gap: 12px; }
  .chart-head h2 { margin-bottom: 2px; }
  button.link { background: none; border: none; color: var(--primary-color); cursor: pointer; font: inherit; padding: 4px; }
  .chart-actions { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; justify-content: flex-end; }
  .segmented { display: inline-flex; border: 1px solid var(--divider-color); border-radius: 8px; overflow: hidden; }
  .segmented button { background: none; border: none; font: inherit; font-size: 13px; padding: 4px 12px; cursor: pointer;
    color: var(--secondary-text-color); }
  .segmented button + button { border-left: 1px solid var(--divider-color); }
  .segmented button.active { color: var(--primary-color); background: color-mix(in srgb, var(--primary-color) 12%, transparent); }
  .legend { display: flex; flex-wrap: wrap; gap: 4px 16px; margin: 12px 0 4px; font-size: 12px; color: var(--primary-text-color); }
  .legend-item { display: inline-flex; align-items: center; gap: 6px; }
  .chart-wrap { position: relative; }
  .chart { display: block; max-width: 100%; }
  .chart .tick { font-size: 11px; fill: var(--secondary-text-color); font-variant-numeric: tabular-nums; }
  #daychart { position: relative; }
  .tooltip { position: absolute; z-index: 2; pointer-events: none; background: var(--card-background-color);
    border: 1px solid var(--divider-color); border-radius: 8px; padding: 8px 10px; font-size: 12px;
    box-shadow: 0 2px 8px rgba(0,0,0,0.15); min-width: 180px; }
  .tooltip div { display: flex; align-items: center; gap: 6px; }
  .tooltip b { margin-left: auto; font-weight: 500; font-variant-numeric: tabular-nums; }
  .tt-title { font-weight: 500; margin-bottom: 4px; }
  .swatch { width: 10px; height: 10px; border-radius: 2px; display: inline-block; }
  .table-wrap { overflow-x: auto; margin-top: 12px; }
  table { border-collapse: collapse; width: 100%; font-size: 13px; font-variant-numeric: tabular-nums; }
  th, td { text-align: right; padding: 4px 8px; border-bottom: 1px solid var(--divider-color); white-space: nowrap; }
  th:first-child, td:first-child { text-align: left; }
  th { color: var(--secondary-text-color); font-weight: 500; }
  .card-head { display: flex; justify-content: space-between; align-items: center; gap: 8px; }
  .card-head h2 { margin: 0; }
  dl { display: grid; grid-template-columns: auto auto; gap: 6px 12px; margin: 12px 0 0; }
  dd { margin: 0; text-align: right; }
  .soc { display: flex; align-items: center; gap: 8px; margin-top: 12px; }
  .soc-bar { flex: 1; height: 8px; border-radius: 4px; background: var(--divider-color); overflow: hidden; }
  .soc-bar div { height: 100%; background: var(--c-battery); border-radius: 4px; }
  .chips { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; justify-content: flex-end; }
  .chip { font-size: 11px; padding: 2px 8px; border-radius: 10px; border: 1px solid var(--divider-color);
    color: var(--secondary-text-color); margin-left: 4px; }
  .settings { display: flex; flex-direction: column; gap: 10px; }
  .setting { display: flex; justify-content: space-between; align-items: center; gap: 12px; }
  .setting button.info { background: none; border: none; padding: 0 0 0 4px; cursor: pointer; color: var(--secondary-text-color);
    vertical-align: middle; line-height: 0; }
  .setting button.info ha-icon { --mdc-icon-size: 16px; }
  .setting-note { display: flex; gap: 6px; align-items: flex-start; margin: 12px 0 0; font-size: 12px; line-height: 1.35;
    color: var(--secondary-text-color); }
  .setting-note ha-icon { --mdc-icon-size: 16px; flex: none; color: var(--warning-color, #fab219); }
  .setting-hint { display: block; margin-top: 4px; font-size: 12px; color: var(--secondary-text-color); line-height: 1.35; }
  .setting input[type=number] { width: 90px; font: inherit; padding: 4px 6px; border-radius: 6px; text-align: right;
    border: 1px solid var(--divider-color); background: var(--card-background-color); color: var(--primary-text-color); }
  .setting select { font: inherit; padding: 4px 6px; border-radius: 6px; border: 1px solid var(--divider-color);
    background: var(--card-background-color); color: var(--primary-text-color); max-width: 60%; }
  .number { display: inline-flex; align-items: center; gap: 4px; white-space: nowrap; }
  .switch { position: relative; display: inline-block; width: 36px; height: 20px; flex-shrink: 0; }
  .switch input { opacity: 0; width: 0; height: 0; }
  .switch span { position: absolute; inset: 0; background: var(--divider-color); border-radius: 10px; cursor: pointer; transition: background 0.2s; }
  .switch span::before { content: ""; position: absolute; width: 16px; height: 16px; left: 2px; top: 2px; border-radius: 50%;
    background: var(--card-background-color); transition: transform 0.2s; }
  .switch input:checked + span { background: var(--primary-color); }
  .switch input:checked + span::before { transform: translateX(16px); }
  dialog { border: none; border-radius: var(--ha-card-border-radius, 12px); padding: 24px; max-width: 420px;
    width: calc(100% - 32px); box-sizing: border-box; background: var(--card-background-color);
    color: var(--primary-text-color); box-shadow: 0 8px 24px rgba(0,0,0,0.3); }
  dialog::backdrop { background: rgba(0,0,0,0.4); }
  dialog p { color: var(--secondary-text-color); line-height: 1.4; margin: 0 0 20px; }
  .dialog-buttons { display: flex; justify-content: flex-end; gap: 8px; }
  button.danger, button.primary { background: var(--error-color, #d03b3b); color: #fff; border: none; border-radius: 8px;
    padding: 8px 16px; font: inherit; cursor: pointer; }
  button.primary { background: var(--primary-color); }
  .cells { margin-top: 12px; padding-top: 12px; border-top: 1px solid var(--divider-color); }
  .cells dl { margin-top: 0; }
  .cells dt .sub { display: block; font-size: 11px; }
  .muted { color: var(--secondary-text-color); }
  .status { display: inline-flex; align-items: center; gap: 2px; font-size: 12px; margin-left: 6px; }
  .status ha-icon { --mdc-icon-size: 16px; }
  .status.green ha-icon { color: #0ca30c; } .status.yellow ha-icon { color: #fab219; }
  .status.orange ha-icon { color: #ec835a; } .status.red ha-icon { color: #d03b3b; }
  .suggestion { display: grid; grid-template-columns: auto 1fr; gap: 6px 10px; margin-top: 12px; padding: 10px;
    border-radius: 8px; border: 1px solid #ec835a; background: color-mix(in srgb, #ec835a 10%, transparent); }
  .suggestion ha-icon { color: #ec835a; }
  .suggestion-text { display: flex; flex-direction: column; gap: 4px; font-size: 13px; }
  .suggestion-text span { color: var(--secondary-text-color); }
  .suggestion button { grid-column: 1 / -1; justify-self: end; }
  .balancing-run { display: flex; align-items: center; gap: 8px; margin-top: 12px; font-size: 13px; flex-wrap: wrap; }
  .balancing-run ha-icon { color: var(--c-battery); }
  .balancing-run button { margin-left: auto; }
  .start-balancing { margin-top: 8px; padding-left: 0; }
  .problem { display: flex; gap: 8px; align-items: flex-start; margin: 4px 0 12px; padding: 8px 10px; border-radius: 8px;
    font-size: 13px; border: 1px solid var(--error-color, #d03b3b); background: color-mix(in srgb, var(--error-color, #d03b3b) 10%, transparent); }
  .problem ha-icon { color: var(--error-color, #d03b3b); --mdc-icon-size: 18px; flex: none; }
  button:disabled { opacity: 0.5; cursor: not-allowed; }
  .balancing-hint { display: block; grid-column: 1 / -1; font-size: 12px; }
`;

// An open browser tab loads a newer module after an update of SLEMS while the
// element is already defined; the new version is used after a page reload.
if (!customElements.get("slems-panel")) {
  customElements.define("slems-panel", SlemsPanel);
}
