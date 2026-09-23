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
    dayChartHint: "Hourly average power",
    pvForecast: "PV forecast",
    pvActual: "PV measured",
    consumptionForecast: "Consumption forecast",
    consumptionActual: "Consumption measured",
    plannedCharge: "Planned charging",
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
    responseTime: "Response time",
    notControlled: "measured only",
    noBatteries: "No batteries configured.",
    noConsumers: "No consumers configured.",
    groups: {
      mode: "Operation",
      control: "Control",
      priority: "Battery priority and grid targets",
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
    idle: "Ruhe",
    energyFlow: "Energiefluss",
    today: "Heute",
    dayChart: "Heute: Prognose und Plan",
    dayChartHint: "Mittlere Leistung pro Stunde",
    pvForecast: "PV-Prognose",
    pvActual: "PV gemessen",
    consumptionForecast: "Verbrauchsprognose",
    consumptionActual: "Verbrauch gemessen",
    plannedCharge: "Geplantes Laden",
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
    responseTime: "Reaktionszeit",
    notControlled: "nur gemessen",
    noBatteries: "Keine Batterien konfiguriert.",
    noConsumers: "Keine Verbraucher konfiguriert.",
    groups: {
      mode: "Betrieb",
      control: "Regelung",
      priority: "Batterievorrang und Netz-Zielwerte",
      gridFriendly: "Netzdienliches Laden",
      night: "Nachtentladung",
      peak: "Bezugsspitzen abfangen",
      rotation: "Mehrere Batterien",
    },
  },
};

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
  ["gridFriendly", ["grid_friendly_charging"]],
  ["night", ["night_discharge", "night_reserve"]],
  ["peak", ["peak_shaving", "peak_shaving_grid_limit", "peak_shaving_soc_threshold"]],
  [
    "rotation",
    ["rotation_soc_threshold", "rotation_min_interval", "rotation_ramp_rate", "rotation_ramp_max"],
  ],
];

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
    this._stats = { pv: {}, house: {} };
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
      </div>`;
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
    this._renderFlow();
    this._setSection(
      "tiles",
      OVERVIEW_TILES.map((key) => this._state(key))
        .filter(Boolean)
        .map(
          (s) => `<div class="tile"><span class="label">${escapeHtml(this._name(s))}</span>
                  <span class="value">${escapeHtml(this._format(s))}</span></div>`
        )
        .join("")
    );
    this._fetchStats(false);
    this._renderDayChart();
  }

  _renderFlow() {
    const t = this._t;
    const grid = this._number(this._state("grid_power"));
    const pv = this._number(this._state("pv_power"));
    const house = this._number(this._state("house_power"));
    const battery = this._number(this._state("battery_power_total"));
    const soc = this._number(this._state("battery_soc_total"));
    const consumers = (this._config.consumers || []).map((c) => ({
      name: c.name,
      power: this._number(this._hass.states[c.power_entity]),
    }));

    // Nodes in a 400 × 300 view box; flows run through the hub in the middle.
    const hub = [200, 150];
    const nodes = {
      pv: [200, 40],
      grid: [50, 150],
      house: [350, 150],
      battery: [200, 260],
    };
    const line = (from, to, power, color, key) => {
      const active = power !== null && Math.abs(power) >= 10;
      const [a, b] = power !== null && power < 0 ? [to, from] : [from, to];
      const duration = active ? Math.max(0.6, Math.min(6, 3000 / Math.abs(power))) : 0;
      return `<line class="base" x1="${from[0]}" y1="${from[1]}" x2="${to[0]}" y2="${to[1]}"/>
        ${active ? `<line class="flow" data-key="${key}" style="stroke:${color};animation-duration:${duration.toFixed(2)}s"
          x1="${a[0]}" y1="${a[1]}" x2="${b[0]}" y2="${b[1]}"/>` : ""}`;
    };
    const svg = `
      <svg viewBox="0 0 400 300" class="flow-svg" role="img" aria-label="${t.energyFlow}">
        ${line(nodes.pv, hub, pv, this._colors.pv, "pv")}
        ${line(nodes.grid, hub, grid, this._colors.grid, "grid")}
        ${line(hub, nodes.house, house, this._colors.house, "house")}
        ${line(hub, nodes.battery, battery, this._colors.battery, "battery")}
        <circle cx="${hub[0]}" cy="${hub[1]}" r="4" class="hub"/>
      </svg>`;
    const node = (key, [x, y], icon, label, value, detail = "") => `
      <div class="node ${key}" style="left:${(x / 400) * 100}%;top:${(y / 300) * 100}%">
        <ha-icon icon="${icon}"></ha-icon>
        <span class="node-value">${escapeHtml(value)}</span>
        <span class="node-label">${escapeHtml(label)}${detail ? ` · ${escapeHtml(detail)}` : ""}</span>
      </div>`;
    const gridDetail = grid === null ? "" : grid > 10 ? t.import : grid < -10 ? t.export : "";
    const batteryDetail = battery === null ? "" : battery > 10 ? t.charging : battery < -10 ? t.discharging : t.idle;
    const html = `
      <div class="flow-box">
        ${svg}
        ${node("pv", nodes.pv, "mdi:solar-power-variant", t.pv, this._watts(pv))}
        ${node("grid", nodes.grid, "mdi:transmission-tower", t.grid, this._watts(grid === null ? null : Math.abs(grid)), gridDetail)}
        ${node("house", nodes.house, "mdi:home", t.house, this._watts(house))}
        ${node(
          "battery",
          nodes.battery,
          "mdi:battery-charging-high",
          soc === null ? t.battery : `${Math.round(soc)} %`,
          this._watts(battery === null ? null : Math.abs(battery)),
          batteryDetail
        )}
      </div>
      ${
        consumers.length
          ? `<div class="consumer-list">${consumers
              .map(
                (c) => `<div class="consumer-chip"><span class="node-label">${escapeHtml(c.name)}</span>
                  <span class="node-value">${escapeHtml(this._watts(c.power))}</span></div>`
              )
              .join("")}</div>`
          : ""
      }`;
    this._setSection("flow", html);
  }

  async _fetchStats(force) {
    if (!this._hass || (!force && Date.now() - this._statsFetched < STATS_REFRESH_MS)) return;
    this._statsFetched = Date.now();
    const pvId = this._entityId("pv_power");
    const houseId = this._entityId("house_power");
    const ids = [pvId, houseId].filter(Boolean);
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
      this._stats = { pv: byHour(result[pvId]), house: byHour(result[houseId]) };
      this._sections.daychart = undefined;
      this._queueRender();
    } catch (err) {
      // Statistics are optional for the chart; keep the forecast visible.
      console.debug("SLEMS: statistics not available", err);
    }
  }

  _chartRows() {
    const plan = this._state("feed_in_limit")?.attributes?.day_plan || [];
    return plan.map((row, hour) => ({
      hour,
      pvForecast: row.pv_wh,
      consumptionForecast: row.consumption_wh,
      plannedCharge: row.planned_charge_w,
      pvActual: this._stats.pv[hour] ?? null,
      consumptionActual: this._stats.house[hour] ?? null,
    }));
  }

  _renderDayChart() {
    const t = this._t;
    const rows = this._chartRows();
    const header = `
      <div class="chart-head">
        <div><h2>${t.dayChart}</h2><span class="hint">${t.dayChartHint}</span></div>
        <button class="link" data-action="toggle-table">${this._showTable ? t.showChart : t.showTable}</button>
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
    this._setSection("daychart", header + this._legend() + this._chartSvg(rows) + `<div class="tooltip" id="tooltip" hidden></div>`);
  }

  _legend() {
    const t = this._t;
    const item = (color, label, style) =>
      `<span class="legend-item"><svg width="22" height="10">${
        style === "bar"
          ? `<rect x="4" y="0" width="14" height="10" rx="2" fill="${color}"/>`
          : `<line x1="1" y1="5" x2="21" y2="5" stroke="${color}" stroke-width="2" ${style === "dash" ? 'stroke-dasharray="4 3"' : ""}/>`
      }</svg>${label}</span>`;
    const c = this._colors;
    return `<div class="legend">
      ${item(c.pv, t.pvForecast, "dash")}${item(c.pv, t.pvActual, "solid")}
      ${item(c.house, t.consumptionForecast, "dash")}${item(c.house, t.consumptionActual, "solid")}
      ${item(c.battery, t.plannedCharge, "bar")}</div>`;
  }

  _chartSvg(rows) {
    const c = this._colors;
    // Draw in real pixels so text keeps its size on narrow screens.
    const available = this.shadowRoot.getElementById("daychart")?.clientWidth || 720;
    const width = Math.max(280, Math.round(available));
    const height = width < 500 ? 220 : 260;
    const pad = { left: 52, right: 22, top: 10, bottom: 26 };
    const plotW = width - pad.left - pad.right;
    const plotH = height - pad.top - pad.bottom;
    const values = rows.flatMap((r) => [r.pvForecast, r.consumptionForecast, r.plannedCharge, r.pvActual, r.consumptionActual]);
    const max = Math.max(100, ...values.filter((v) => v !== null && v !== undefined));
    const step = niceStep(max / 4);
    const top = Math.ceil(max / step) * step;
    const x = (hour) => pad.left + (hour / 24) * plotW;
    const y = (value) => pad.top + plotH - (value / top) * plotH;
    this._chartGeometry = { pad, plotW, width };

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
    const path = (key, color, dash) => {
      const points = rows.filter((r) => r[key] !== null && r[key] !== undefined).map((r) => [x(r.hour + 0.5), y(r[key])]);
      if (points.length < 2) return "";
      return `<polyline points="${points.map((p) => p.join(",")).join(" ")}" fill="none" stroke="${color}"
        stroke-width="2" stroke-linejoin="round" stroke-linecap="round" ${dash ? 'stroke-dasharray="5 4"' : ""}/>`;
    };
    const now = new Date();
    const nowHour = now.getHours() + now.getMinutes() / 60;
    return `
      <div class="chart-wrap"><svg viewBox="0 0 ${width} ${height}" width="${width}" height="${height}" class="chart" role="img" aria-label="${this._t.dayChart}">
        ${gridLines.join("")}${hourTicks}${bars}
        ${path("pvForecast", c.pv, true)}${path("pvActual", c.pv, false)}
        ${path("consumptionForecast", c.house, true)}${path("consumptionActual", c.house, false)}
        <line x1="${x(nowHour)}" x2="${x(nowHour)}" y1="${pad.top}" y2="${pad.top + plotH}" stroke="${c.muted}" stroke-dasharray="2 3"/>
        <text x="${x(nowHour) + 4}" y="${pad.top + 10}" class="tick">${this._t.now}</text>
        <line id="crosshair" x1="0" x2="0" y1="${pad.top}" y2="${pad.top + plotH}" stroke="${c.muted}" visibility="hidden"/>
        <rect class="hit" x="${pad.left}" y="${pad.top}" width="${plotW}" height="${plotH}" fill="transparent"/>
      </svg></div>`;
  }

  _chartTable(rows) {
    const t = this._t;
    const cell = (v) => (v === null || v === undefined ? "–" : escapeHtml(this._watts(v)));
    return `<div class="table-wrap"><table>
      <thead><tr><th>${t.hour}</th><th>${t.pvForecast}</th><th>${t.pvActual}</th><th>${t.consumptionForecast}</th>
      <th>${t.consumptionActual}</th><th>${t.plannedCharge}</th></tr></thead>
      <tbody>${rows
        .map(
          (r) => `<tr><td>${String(r.hour).padStart(2, "0")}:00</td><td>${cell(r.pvForecast)}</td><td>${cell(r.pvActual)}</td>
          <td>${cell(r.consumptionForecast)}</td><td>${cell(r.consumptionActual)}</td><td>${cell(r.plannedCharge)}</td></tr>`
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
    tooltip.innerHTML = `<div class="tt-title">${String(hour).padStart(2, "0")}:00–${String(hour + 1).padStart(2, "0")}:00</div>
      ${entry(c.pv, t.pvForecast, row.pvForecast)}${entry(c.pv, t.pvActual, row.pvActual)}
      ${entry(c.house, t.consumptionForecast, row.consumptionForecast)}${entry(c.house, t.consumptionActual, row.consumptionActual)}
      ${entry(c.battery, t.plannedCharge, row.plannedCharge)}`;
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
          const rows = [
            [t.power, s("battery_power")],
            [t.acPower, s("ac_power")],
            [t.planned, s("planned_power")],
            [t.efficiency, s("round_trip_efficiency")],
            [t.state, s("inverter_state")],
            [t.temperature, s("internal_temperature")],
          ].filter(([, st]) => st);
          return `<section class="card">
            <div class="card-head"><h2>${escapeHtml(b.name)}</h2>
              ${enabled ? this._toggle(enabled, t.enabled) : ""}</div>
            <div class="soc"><div class="soc-bar"><div style="width:${soc ?? 0}%"></div></div>
              <span>${soc === null ? "–" : Math.round(soc) + " %"}</span></div>
            <dl>${rows.map(([label, st]) => `<dt>${label}</dt><dd>${escapeHtml(this._format(st))}</dd>`).join("")}</dl>
          </section>`;
        })
        .join("")
    );
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
          const chips = [
            !c.controllable ? t.notControlled : "",
            attrs.blocked ? t.blocked : "",
            attrs.saturated ? t.saturated : "",
          ]
            .filter(Boolean)
            .map((chip) => `<span class="chip">${chip}</span>`)
            .join("");
          const response =
            attrs.response_time_s !== null && attrs.response_time_s !== undefined
              ? `${Math.round(attrs.response_time_s)} s`
              : "–";
          return `<section class="card">
            <div class="card-head"><h2>${escapeHtml(c.name)}</h2><div>${chips}</div></div>
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
          <div class="settings">${rows.map((s) => this._control(s)).join("")}</div></section>`;
      }).join("")
    );
  }

  _toggle(stateObj, label) {
    return `<label class="switch" title="${escapeHtml(label)}">
      <input type="checkbox" data-entity="${stateObj.entity_id}" data-kind="switch" ${stateObj.state === "on" ? "checked" : ""}>
      <span></span></label>`;
  }

  _control(stateObj) {
    const domain = stateObj.entity_id.split(".")[0];
    const name = escapeHtml(this._name(stateObj));
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
    return `<div class="setting"><span>${name}</span><span class="number">
      <input type="number" data-entity="${stateObj.entity_id}" data-kind="number" value="${escapeHtml(stateObj.state)}"
        min="${a.min}" max="${a.max}" step="${a.step}"><span class="unit">${escapeHtml(a.unit_of_measurement || "")}</span></span></div>`;
  }

  // --- events ------------------------------------------------------------------

  _onClick(event) {
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
  .hint, .tick, .node-label, .label, dt, .unit, .empty { color: var(--secondary-text-color); }
  .hint { font-size: 12px; }
  .tiles { display: grid; grid-template-columns: repeat(auto-fill, minmax(150px, 1fr)); gap: 12px; }
  .tile { display: flex; flex-direction: column; gap: 4px; padding: 8px 0; border-bottom: 1px solid var(--divider-color); }
  .tile .label { font-size: 12px; }
  .tile .value { font-size: 18px; }
  .flow-box { position: relative; width: 100%; aspect-ratio: 4 / 3; max-height: 420px; margin: 0 auto;
    container-type: inline-size; }
  .flow-svg { position: absolute; inset: 0; width: 100%; height: 100%; }
  .flow-svg .base { stroke: var(--divider-color); stroke-width: 2; }
  .flow-svg .flow { stroke-width: 3; stroke-dasharray: 3 12; stroke-linecap: round; animation: dash linear infinite; }
  .flow-svg .hub { fill: var(--divider-color); }
  @keyframes dash { to { stroke-dashoffset: -15; } }
  @media (prefers-reduced-motion: reduce) { .flow-svg .flow { animation: none; stroke-dasharray: none; } }
  .node { position: absolute; transform: translate(-50%, -50%); display: flex; flex-direction: column; align-items: center;
    gap: 2px; background: var(--card-background-color); border: 2px solid var(--divider-color); border-radius: 50%;
    width: min(88px, 23cqw); height: min(88px, 23cqw); justify-content: center; text-align: center;
    box-sizing: border-box; overflow: hidden; }
  .node.pv { border-color: var(--c-pv); } .node.grid { border-color: var(--c-grid); }
  .node.house { border-color: var(--c-house); } .node.battery { border-color: var(--c-battery); }
  .node ha-icon { --mdc-icon-size: min(22px, 5.5cqw); color: var(--secondary-text-color); }
  .node-value { font-size: clamp(11px, 3.6cqw, 14px); font-weight: 500; }
  .node-label { font-size: clamp(9px, 2.8cqw, 11px); line-height: 1.2; padding: 0 4px; }
  .consumer-list { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 12px; }
  .consumer-chip { display: flex; flex-direction: column; padding: 4px 10px; border-radius: 8px;
    border: 2px solid var(--c-consumer); }
  .chart-head { display: flex; justify-content: space-between; align-items: flex-start; gap: 12px; }
  .chart-head h2 { margin-bottom: 2px; }
  button.link { background: none; border: none; color: var(--primary-color); cursor: pointer; font: inherit; padding: 4px; }
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
  .chip { font-size: 11px; padding: 2px 8px; border-radius: 10px; border: 1px solid var(--divider-color);
    color: var(--secondary-text-color); margin-left: 4px; }
  .settings { display: flex; flex-direction: column; gap: 10px; }
  .setting { display: flex; justify-content: space-between; align-items: center; gap: 12px; }
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
`;

customElements.define("slems-panel", SlemsPanel);
