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

// Days of a forecast comparison before its accuracy is shown.
const MIN_ACCURACY_DAYS = 7;

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
    dayChartHint: "Mean power per half hour (left), total state of charge (right)",
    pvForecast: "PV forecast",
    pvActual: "PV measured",
    consumptionForecast: "Consumption forecast",
    consumerForecast: "Consumers (planned)",
    consumptionActual: "Consumption measured",
    plannedCharge: "Planned charging",
    actualCharge: "Charging measured",
    exportForecast: "Feed-in forecast",
    exportActual: "Feed-in measured",
    socForecast: "State of charge forecast",
    socActual: "State of charge measured",
    showTable: "Show table",
    showChart: "Show chart",
    hour: "Time",
    now: "now",
    noData: "No forecast available yet",
    soc: "State of charge",
    planned: "Planned",
    storedEnergy: "Stored energy",
    menu: "Menu",
    enableBattery: "Enable",
    disableBattery: "Disable",
    pauseCommunication: "Pause communication (firmware update)",
    resumeCommunication: "Resume communication",
    resume: "Resume",
    paused: "communication paused",
    pausedManual: "Communication paused until {time} (e.g. for a firmware update).",
    pausedFirmware: "Firmware update detected: communication paused until {time}.",
    pauseTitle: "Pause communication with {name}?",
    pauseText: "SLEMS hands the battery back to its own logic and does not read or send anything for {min} minutes, e.g. during a firmware update. Afterwards it reconnects by itself; you can also resume earlier.",
    pauseConfirm: "Pause",
    details: "Details",
    deviceView: "Open device in Home Assistant",
    close: "Close",
    noDetails: "No details available.",
    model: "Model",
    deviceName: "Device name",
    emsVersion: "EMS firmware",
    vmsVersion: "VMS firmware",
    bmsVersion: "BMS firmware",
    commFirmware: "Communication module",
    macAddress: "MAC address",
    capacity: "Capacity",
    totalCharged: "Charged in total",
    totalDischarged: "Discharged in total",
    lastFullCharge: "Last full charge",
    fullChargeDue: "full charge due",
    fullChargeDueHint: "Not full for longer than the interval: charged first until it is full once (see settings, regular full charge).",
    fullChargeOverdue: "Not fully charged for a long time",
    fullChargeOverdueText: "{name} was last full {days} days ago. Without enough PV SLEMS charges it first at the next opportunity.",
    topDeltaMeasured: "Cell delta last measured",
    responseTime: "Response time",
    responseNotLearned: "not learned yet (all batteries: {value})",
    automatic: "automatic",
    learned: "learned",
    learnedWaiting: "Not enough data to learn yet; this value applies.",
    cycles: "Charge cycles",
    forecastAccuracy: "Forecast accuracy",
    forecastAccuracyHint: "Consumption: recalculated for the last 14 days; PV: recorded forecasts compared with the production",
    consumptionForecastTitle: "Consumption",
    pvForecastTitle: "PV",
    accuracy: "Accuracy",
    lastDays: "last {n} days",
    collectingData: "collecting data ({n}/{min} days)",
    tendency: "Tendency",
    tooHigh: "too high",
    tooLow: "too low",
    balanced: "balanced",
    hourlyCourse: "Course of the day",
    hourlyDeviation: "{pct} deviation per hour",
    dataBasis: "Data basis",
    consumptionBasis: "{days} days of consumption",
    heatPumpBasis: ", {days} days heat pump with temperature",
    pvBasis: "{days} days recorded",
    tomorrow: "Tomorrow (expected)",
    storedOf: "{stored} of {capacity} kWh",
    powerGridSide: "Power (grid side)",
    setPoint: "SLEMS set point",
    efficiency: "Round trip efficiency",
    state: "State",
    temperature: "Temperature",
    enabled: "Enabled",
    measured: "Measured",
    blocked: "blocked",
    saturated: "saturated",
    resting: "thermostat pause",
    controlOff: "control off",
    onlyWithControl: "Applies only while the control is active.",
    capSection: "Feed-in cap",
    capRole: "Role",
    targetSection: "Daily target",
    targetKind: "Kind",
    targetHours: "Hours",
    targetEnergy: "Energy",
    targetSensor: "Sensor",
    targetEarliestOn: "Earliest start",
    targetEarliest: "From",
    targetWaiting: "waits until {time}",
    showSettings: "Show settings",
    hideSettings: "Hide settings",
    targetMin: "Minimum temperature",
    targetMax: "Target temperature",
    targetDeadline: "Until",
    targetSource: "Source",
    targetPriority: "Before the batteries when short",
    targetProgress: "Today",
    targetUntil: "until {time}",
    targetLatest: "forced from {time}",
    targetDone: "reached",
    targetEnergy: "about {energy} to go",
    targetEnergyLearning: "energy still being learned",
    targetForced: "running forced",
    targetBoost: "before the batteries",
    targetMinShort: "min.",
    targetGoalTemp: "target",
    targetBoostChip: "priority",
    targetForcedChip: "forced",
    storageCapacity: "Storage left",
    storageLearning: "learning ({runs}/3 heating runs, {marks}/2 thermostat cycles)",
    storageUntilCycling: "{energy} until it cycles",
    storageUntilFull: "{cycling} until it cycles, {full} until full",
    controlActive: "Control active",
    responseTime: "Response time on/off (own sensor)",
    gridResponseTime: "Response time on/off at the meter",
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
    batteryLimits: "Limits: {name}",
    feedInLimitReasons: {
      disabled: "off",
      no_forecast: "no forecast",
      not_enough_surplus: "none – charge at once",
    },
    exportBelowTarget:
      "The maximum grid export while discharging ({limit}) is below the grid surplus target while discharging ({target}): the batteries control to {limit}.",
    feedInCap: "Feed-in cap",
    capAbsorb: "absorb {absorb}",
    capExport: "feed in {export} before (until {time})",
    capNoPeak: "no peak above {limit}",
    capProblems: {
      battery_too_small: "Feed-in cap: batteries too small",
      too_late: "Feed-in cap: not enough time to make room",
      charge_power_too_low: "Feed-in cap: charge power too low",
      limit_exceeded: "Feed-in above the limit",
    },
    capProblemTexts: {
      battery_too_small: "The batteries cannot hold the forecast energy above the limit ({space} needed); the rest is curtailed.",
      too_late: "Not enough time or power left to feed in {export} before the peak; part of the surplus will be curtailed.",
      charge_power_too_low: "The batteries cannot charge fast enough; about {curtailed} would be curtailed. A consumer with the role \"Supporting\" or \"Normal\" for the feed-in cap can take it.",
      limit_exceeded: "The grid export has been above the limit for more than 5 minutes.",
    },
    capNote: "Limit {limit}. Buffer in use: {buffer}{source}, at least {min} per peak.",
    capBufferShort: "Feed-in cap: forecast within the buffer zone",
    capBufferShortText:
      "The forecast is within the buffer zone: the expected energy above the limit fits into the batteries, only the full safety buffer does not.",
    capMissing: "Not part of the planning right now: {batteries}.",
    capTakeover: "Feed-in cap: consumers take the rest",
    capTakeoverText: "{energy} do not fit into the batteries and are expected to go to these consumers instead of being curtailed: {consumers}.",
    batteryMissing: {
      balancing: "cell balancing",
      paused: "communication paused",
      disabled: "disabled",
      not_responding: "not responding",
      unreadable: "cannot be read",
    },
    capBufferAuto: " (learned from {days} days)",
    capBufferWaiting: " (fixed; automatic from 14 recorded days, {days} so far)",
    capLine: "PV limit of the feed-in cap",
    capExcess: "PV above the limit",
    capCurtailed: "Curtailed",
    capLostReasons: { full: "batteries full", charge_power: "charge power too low" },
    simulation: "Simulation",
    simIntro:
      "Try other settings: the chart and the key figures show how today and tomorrow would look, calculated from the current state of charge and the current forecasts. Nothing here is saved or used by SLEMS; grey dotted: the plan with the current settings. The daily targets of the consumers are planned as in the real plan; otherwise consumers controlled by SLEMS are not simulated.",
    simToday: "Simulation: today",
    simTomorrow: "Simulation: tomorrow (expected)",
    simReset: "Reset to the current settings",
    simAfternoon:
      "It is past noon: the simulation only changes the rest of today from now on; the values before are measured and not simulated. Choose tomorrow for a whole simulated day.",
    simShowTomorrow: "Show tomorrow",
    simForecast: "Forecast",
    simPv: "PV forecast change",
    simConsumption: "Consumption forecast change",
    simBatteries: "Batteries (all together)",
    simCapacity: "Usable capacity",
    simMinSoc: "Minimum state of charge",
    simMaxSoc: "Maximum state of charge",
    simChargePower: "Charge power",
    simDischargePower: "Discharge power",
    simMetricsToday: "Key figures today (from now on)",
    simMetricsTomorrow: "Key figures tomorrow",
    simCurrent: "Current settings",
    simSimulated: "Simulation",
    mExport: "Feed-in",
    mImport: "Grid import",
    mMaxImport: "Highest import",
    mMaxExport: "Highest feed-in",
    mCurtailed: "Curtailed",
    mSocEnd: "State of charge at midnight",
    exportCompare: "Feed-in (current settings)",
    socCompare: "State of charge (current settings)",
    status: "Status",
    expectedExport: "Expected export today",
    exportDetail: "{so_far} so far · {rest} kWh to come",
    tileHints: {
      feed_in_limit:
        "With grid friendly charging the batteries only charge with the surplus above this grid export. SLEMS recalculates it continuously from the PV and consumption forecasts so that the batteries are still full by the evening (buffer included): they absorb the midday peak instead of being full in the morning. \"off\": grid friendly charging is switched off. \"none – charge at once\": the expected surplus is not enough, the batteries charge at once.",
    },
    peakShavingNote:
      "Below the threshold only {usable} above the minimum state of charge ({min}) are left for peaks, about {kwh} kWh.",
    settingHints: {
      night_reserve_auto:
        "Learns the reserve from the mornings: every day SLEMS measures how much energy the house needed from battery or grid between the moment PV should have taken over (end of the night discharge as planned) and the moment it really did (PV covering the consumption for 15 minutes), in % of the day's forecast consumption. Needs 14 measured mornings; the measurement also runs while the night discharge is off.",
      night_reserve_coverage:
        "Share of the mornings the learned reserve covers: 90 % = enough on 9 of 10 mornings. Above 100 % the largest measured gap times this value, e.g. 110 % = 10 % more than the worst morning so far. Lower values feed in more at night.",
      grid_friendly_buffer_auto:
        "Learns the buffer from the recorded PV forecasts: of the days with less PV than forecast, the shortfall not exceeded on 80 % of them, applied to the PV still expected today. Needs 14 recorded days.",
      charge_secured_buffer_auto:
        "Learns the safety buffer (charge secured and night discharge target) from the PV forecast being too high and the consumption forecast being too low, each the error not exceeded on 80 % of such days, applied to the forecasts of the rest of the day or the next 24 hours.",
      grid_targets_auto:
        "Learns both grid surplus targets from how far the grid power swings towards import while the batteries control it: the target keeps the grid on the export side 90 % of the time (20–1000 W). Needs some controlling in operating mode active first.",
      timing_auto:
        "Derives the control interval (0.8 × the report interval of the smart meter) and the averaging window (3 × the report interval) from the learned smart meter interval.",
      regular_full_charge:
        "A battery not full for longer than the interval (or never full since SLEMS records it) is charged first until it was full once, one battery at a time; it may exceed its maximum SoC for that. When discharging it is spared while the other batteries have more than 50 %. Once full it rests 90 s so the cell delta at the top is measured. LFP batteries recalibrate their state of charge only when full.",
      full_charge_interval:
        "Days after the last full charge from which a battery is charged first.",
      learn_capacity:
        "SLEMS always learns the usable capacity from charge and discharge legs of at least 20 % state of charge (DC energy / change of the state of charge; legs with a jump of the state of charge are discarded). On: the learned capacity is used for planning once three legs were measured. Off: the configured capacity applies.",
      consumer_learning:
        "SLEMS always learns along in operating mode active: the power while on (power controlled consumers: the highest power, measured while commanded at 90 % of the maximum power or more) and whether the own thermostat switches the consumer off while it is commanded. On: it plans with the measured power (on/off consumers) or caps the maximum power at the learned highest power (power controlled consumers) and treats a consumer whose thermostat cycles like \"thermostat cycles by itself\". Off: the configured values apply.",
      feed_in_cap:
        "Keeps the export at the grid connection point below PV peak power × limit. From the PV and consumption forecasts SLEMS plans how much energy above the limit the batteries must absorb, keeps that space free (night discharge, otherwise feeding in battery energy before the peak, as late as possible and never above the limit) and warns if it does not work out. Takes precedence over grid friendly charging, night discharge and battery priority.",
      pv_peak_power: "Peak power of the PV system the limit refers to.",
      feed_in_cap_limit: "Share of the PV peak power that may be fed in at most, e.g. 60 %.",
      feed_in_cap_buffer:
        "Extra space on top of the forecast energy above the limit, in % of it, against a too low PV forecast. Negative values plan with less.",
      target_type:
        "Runtime: time the consumer draws power. Enabled time: time SLEMS has it switched on, for devices with their own control (a dehumidifier with a hygrostat). Energy: kWh. Temperature: minimum and target temperature of its storage (temperature sensors). Counted from one deadline to the next; met from the surplus first. A temperature target applies from midnight to the deadline: after the deadline the consumer stays off until midnight, also with surplus.",
      target_earliest_enabled:
        "Not switched on before the earliest start, also with surplus (e.g. a dehumidifier only from 10:00). Counted until the deadline; the latest start is never before it.",
      target_sensor:
        "Which temperature the minimum and target temperature apply to: the mean of both sensors, or one of them (e.g. the upper sensor for the hot water at the tap). Sensor 1 is the temperature sensor of the storage, sensor 2 the second one in the consumer's configuration. The latest start is estimated with the energy per degree learned for the chosen sensor (the mean's until it is learned).",
      target_source:
        "What may cover the rest if the surplus is not enough by the deadline. With the batteries (and the grid) the consumer runs from the latest start on regardless of the surplus. Only surplus: the target may be missed (notification).",
      target_priority:
        "The consumer gets the surplus before the batteries if the forecast surplus until the deadline is short for the rest of its target plus filling the batteries.",
      target_deadline: "End of the daily period, e.g. 22:00; also across midnight (06:00 = until the next morning).",
      cap_mode:
        "Supporting: gets no other surplus while the feed-in cap is on, only the surplus above the limit the batteries cannot absorb. If the forecast shows that a peak does not fit into the batteries, it runs from the start of the peak so its power is used for the whole peak. Normal: surplus as without the feed-in cap; above the limit it takes what the batteries and the supporting consumers cannot, before it is curtailed. Never: surplus as without the feed-in cap, never the surplus above the limit.",
      feed_in_cap_auto_buffer:
        "Uses the recorded PV forecast errors instead of the fixed buffer: of the days with more PV than forecast, the underestimation not exceeded on 80 % of them raises the PV forecast. Needs 14 recorded days; until then the fixed buffer applies.",
      peak_shaving_grid_limit:
        "Below the state of charge threshold, consumption up to this power comes from the grid; the batteries only cover what exceeds it.",
      peak_shaving_auto:
        "Calculates the import limit so that the usable energy (above the minimum state of charge, minus the safety reserve) lasts until PV refills the batteries. Based on the consumption peaks of the last days; the fixed limit is then not used.",
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
      "The battery leaves the normal control: it charges into the top voltage range (from the PV surplus, at least 95 W; without surplus the other batteries cover it), stands by for a measurement, discharges a little and repeats until the cell delta is in the normal range (below 190 mV) or has not fallen for 6 hours, at most 24 h. The BMS equalises only a few mV per day. Its discharge is fed into the grid unless the other batteries charge anyway.",
    balancingNotNeeded: "The last measurement ({delta} mV) is in the normal range; cell balancing is hardly needed.",
    startBalancingConfirm: "Start",
    balancingNeedsActive: "Cell balancing can only be started in operating mode active.",
    balancingNeedsEnabled: "Cell balancing needs an enabled battery.",
    balancingNeedsCommunication: "Cell balancing cannot be started while the communication is paused.",
    lastBalancing: "Last cell balancing",
    balancingResults: {
      done: "completed",
      cancelled: "cancelled",
      timeout: "stopped: final discharge not finished",
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
      feedInCap: "Feed-in cap",
      gridFriendly: "Grid friendly charging",
      night: "Night discharge",
      peak: "Import peak shaving",
      rotation: "Several batteries",
      fullCharge: "Regular full charge",
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
    dayChartHint: "Mittlere Leistung je halbe Stunde (links), Gesamt-Ladezustand (rechts)",
    pvForecast: "PV-Prognose",
    pvActual: "PV gemessen",
    consumptionForecast: "Verbrauchsprognose",
    consumerForecast: "Verbraucher (geplant)",
    consumptionActual: "Verbrauch gemessen",
    plannedCharge: "Geplantes Laden",
    actualCharge: "Laden gemessen",
    exportForecast: "Einspeisung (Prognose)",
    exportActual: "Einspeisung gemessen",
    socForecast: "Ladezustand-Prognose",
    socActual: "Ladezustand gemessen",
    showTable: "Tabelle anzeigen",
    showChart: "Diagramm anzeigen",
    hour: "Stunde",
    now: "jetzt",
    noData: "Noch keine Prognose verfügbar",
    soc: "Ladezustand",
    planned: "Geplant",
    storedEnergy: "Gespeichert",
    menu: "Menü",
    enableBattery: "Aktivieren",
    disableBattery: "Deaktivieren",
    pauseCommunication: "Kommunikation pausieren (Firmware-Update)",
    resumeCommunication: "Kommunikation fortsetzen",
    resume: "Fortsetzen",
    paused: "Kommunikation pausiert",
    pausedManual: "Kommunikation pausiert bis {time} (z. B. für ein Firmware-Update).",
    pausedFirmware: "Firmware-Update erkannt: Kommunikation pausiert bis {time}.",
    pauseTitle: "Kommunikation mit {name} pausieren?",
    pauseText: "SLEMS gibt die Batterie an ihre eigene Logik zurück und liest und sendet {min} Minuten lang nichts, z. B. während eines Firmware-Updates. Danach verbindet es sich von selbst wieder; du kannst auch früher fortsetzen.",
    pauseConfirm: "Pausieren",
    details: "Details",
    deviceView: "Gerät in Home Assistant öffnen",
    close: "Schließen",
    noDetails: "Keine Details verfügbar.",
    model: "Modell",
    deviceName: "Gerätename",
    emsVersion: "EMS-Firmware",
    vmsVersion: "VMS-Firmware",
    bmsVersion: "BMS-Firmware",
    commFirmware: "Kommunikationsmodul",
    macAddress: "MAC-Adresse",
    capacity: "Kapazität",
    totalCharged: "Gesamt geladen",
    totalDischarged: "Gesamt entladen",
    lastFullCharge: "Letzte Vollladung",
    fullChargeDue: "Vollladung fällig",
    fullChargeDueHint: "Länger als das eingestellte Intervall nicht voll: wird zuerst geladen, bis sie einmal voll war (siehe Einstellungen, regelmäßige Vollladung).",
    fullChargeOverdue: "Lange nicht voll geladen",
    fullChargeOverdueText: "{name} war zuletzt vor {days} Tagen voll. Fehlt die PV, lädt SLEMS sie bei der nächsten Gelegenheit zuerst.",
    topDeltaMeasured: "Zell-Delta zuletzt gemessen",
    responseTime: "Reaktionszeit",
    responseNotLearned: "noch nicht gelernt (alle Batterien: {value})",
    automatic: "automatisch",
    learned: "gelernt",
    learnedWaiting: "Noch zu wenig Daten zum Lernen; dieser Wert gilt.",
    cycles: "Ladezyklen",
    forecastAccuracy: "Prognosegüte",
    forecastAccuracyHint: "Verbrauch: für die letzten 14 Tage nachgerechnet; PV: gespeicherte Prognosen mit der Erzeugung verglichen",
    consumptionForecastTitle: "Verbrauch",
    pvForecastTitle: "PV",
    accuracy: "Treffsicherheit",
    lastDays: "letzte {n} Tage",
    collectingData: "sammelt noch Daten ({n}/{min} Tage)",
    tendency: "Tendenz",
    tooHigh: "zu hoch",
    tooLow: "zu niedrig",
    balanced: "ausgeglichen",
    hourlyCourse: "Tagesverlauf",
    hourlyDeviation: "{pct} Abweichung pro Stunde",
    dataBasis: "Datenbasis",
    consumptionBasis: "{days} Tage Verbrauch",
    heatPumpBasis: ", {days} Tage Wärmepumpe mit Temperatur",
    pvBasis: "{days} Tage aufgezeichnet",
    tomorrow: "Morgen (erwartet)",
    storedOf: "{stored} von {capacity} kWh",
    powerGridSide: "Leistung (netzseitig)",
    setPoint: "Vorgabe SLEMS",
    efficiency: "Gesamtwirkungsgrad",
    state: "Status",
    temperature: "Temperatur",
    enabled: "Aktiviert",
    measured: "Gemessen",
    blocked: "gesperrt",
    saturated: "gesättigt",
    resting: "Thermostat-Pause",
    controlOff: "Steuerung aus",
    onlyWithControl: "Wirkt nur bei aktiver Steuerung.",
    capSection: "Einspeisebegrenzung",
    capRole: "Einsatz",
    targetSection: "Tagesziel",
    targetKind: "Art",
    targetHours: "Stunden",
    targetEnergy: "Energie",
    targetSensor: "Fühler",
    targetEarliestOn: "Frühester Beginn",
    targetEarliest: "Ab",
    targetWaiting: "wartet bis {time}",
    showSettings: "Einstellungen anzeigen",
    hideSettings: "Einstellungen ausblenden",
    targetMin: "Mindesttemperatur",
    targetMax: "Zieltemperatur",
    targetDeadline: "Bis",
    targetSource: "Quelle",
    targetPriority: "Vor der Batterie, wenn knapp",
    targetProgress: "Heute",
    targetUntil: "bis {time}",
    targetLatest: "erzwungen ab {time}",
    targetDone: "erreicht",
    targetEnergy: "noch ca. {energy}",
    targetEnergyLearning: "Energie wird noch gelernt",
    targetForced: "läuft erzwungen",
    targetBoost: "vor der Batterie",
    targetMinShort: "min.",
    targetGoalTemp: "Ziel",
    targetBoostChip: "Vorrang",
    targetForcedChip: "erzwungen",
    storageCapacity: "Speicherreserve",
    storageLearning: "lernt noch ({runs}/3 Heizläufe, {marks}/2 Taktbeginne)",
    storageUntilCycling: "{energy} bis zum Takten",
    storageUntilFull: "{cycling} bis zum Takten, {full} bis voll",
    controlActive: "Steuerung aktiv",
    responseTime: "Reaktionszeit an/aus (eigener Sensor)",
    gridResponseTime: "Reaktionszeit an/aus am Zähler",
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
    batteryLimits: "Grenzen: {name}",
    feedInLimitReasons: {
      disabled: "aus",
      no_forecast: "keine Prognose",
      not_enough_surplus: "keine – sofort laden",
    },
    exportBelowTarget:
      "Die maximale Einspeisung beim Entladen ({limit}) liegt unter dem Ziel-Netzüberschuss beim Entladen ({target}): Die Batterien regeln auf {limit}.",
    feedInCap: "Einspeisebegrenzung",
    capAbsorb: "{absorb} aufnehmen",
    capExport: "davor {export} einspeisen (bis {time})",
    capNoPeak: "keine Spitze über {limit}",
    capProblems: {
      battery_too_small: "Einspeisebegrenzung: Batterien zu klein",
      too_late: "Einspeisebegrenzung: zu wenig Zeit für Platz",
      charge_power_too_low: "Einspeisebegrenzung: Ladeleistung zu gering",
      limit_exceeded: "Einspeisung über der Grenze",
    },
    capProblemTexts: {
      battery_too_small: "Die Batterien können die prognostizierte Energie über der Grenze nicht aufnehmen ({space} nötig); der Rest wird abgeregelt.",
      too_late: "Es bleibt nicht genug Zeit oder Leistung, um vor der Spitze {export} einzuspeisen; ein Teil des Überschusses wird abgeregelt.",
      charge_power_too_low: "Die Batterien können nicht schnell genug laden; etwa {curtailed} würden abgeregelt. Ein Verbraucher mit dem Einsatz „Unterstützend“ oder „Normal“ bei der Einspeisebegrenzung kann ihn aufnehmen.",
      limit_exceeded: "Die Einspeisung liegt seit mehr als 5 Minuten über der Grenze.",
    },
    capNote: "Grenze {limit}. Puffer in Verwendung: {buffer}{source}, mindestens {min} je Spitze.",
    capBufferShort: "Einspeisebegrenzung: Prognose im Pufferbereich",
    capBufferShortText:
      "Die Prognose bewegt sich innerhalb der Pufferzone: Die erwartete Energie über der Grenze passt in die Batterien, nur der volle Sicherheitspuffer nicht.",
    capMissing: "Derzeit nicht in der Planung: {batteries}.",
    capTakeover: "Einspeisebegrenzung: Verbraucher übernehmen den Rest",
    capTakeoverText: "{energy} passen nicht in die Batterien und gehen voraussichtlich statt Abregeln an: {consumers}.",
    batteryMissing: {
      balancing: "Zellausgleich",
      paused: "Kommunikation pausiert",
      disabled: "deaktiviert",
      not_responding: "reagiert nicht",
      unreadable: "nicht lesbar",
    },
    capBufferAuto: " (gelernt aus {days} Tagen)",
    capBufferWaiting: " (fest; automatisch ab 14 aufgezeichneten Tagen, bisher {days})",
    capLine: "PV-Grenze der Einspeisebegrenzung",
    capExcess: "PV über der Grenze",
    capCurtailed: "Abgeregelt",
    capLostReasons: { full: "Batterien voll", charge_power: "Ladeleistung zu gering" },
    simulation: "Simulation",
    simIntro:
      "Probiere andere Einstellungen aus: Diagramm und Kennzahlen zeigen, wie heute und morgen aussehen würden, gerechnet ab dem aktuellen Ladezustand mit den aktuellen Prognosen. Nichts davon wird gespeichert oder von SLEMS verwendet; grau gepunktet: der Plan mit den aktuellen Einstellungen. Die Tagesziele der Verbraucher gehen wie in der echten Planung ein; sonst werden von SLEMS gesteuerte Verbraucher nicht simuliert.",
    simToday: "Simulation: heute",
    simTomorrow: "Simulation: morgen (erwartet)",
    simReset: "Auf aktuelle Einstellungen zurücksetzen",
    simAfternoon:
      "Es ist nach 12 Uhr: Die Simulation ändert nur noch den Rest des Tages ab jetzt; die Werte davor sind gemessen und nicht simuliert. Für einen ganzen simulierten Tag „Morgen“ wählen.",
    simShowTomorrow: "Morgen anzeigen",
    simForecast: "Prognose",
    simPv: "PV-Prognose ändern",
    simConsumption: "Verbrauchsprognose ändern",
    simBatteries: "Batterien (alle zusammen)",
    simCapacity: "Nutzbare Kapazität",
    simMinSoc: "Minimaler Ladezustand",
    simMaxSoc: "Maximaler Ladezustand",
    simChargePower: "Ladeleistung",
    simDischargePower: "Entladeleistung",
    simMetricsToday: "Kennzahlen heute (ab jetzt)",
    simMetricsTomorrow: "Kennzahlen morgen",
    simCurrent: "Aktuelle Einstellungen",
    simSimulated: "Simulation",
    mExport: "Einspeisung",
    mImport: "Netzbezug",
    mMaxImport: "Höchster Bezug",
    mMaxExport: "Höchste Einspeisung",
    mCurtailed: "Abgeregelt",
    mSocEnd: "Ladezustand um Mitternacht",
    exportCompare: "Einspeisung (aktuelle Einstellungen)",
    socCompare: "Ladezustand (aktuelle Einstellungen)",
    status: "Status",
    expectedExport: "Erwartete Einspeisung heute",
    exportDetail: "bisher {so_far} · noch {rest} kWh",
    tileHints: {
      feed_in_limit:
        "Beim netzdienlichen Laden laden die Batterien nur mit dem Überschuss oberhalb dieser Einspeisung. SLEMS berechnet sie laufend aus PV- und Verbrauchsprognose so, dass die Batterien bis zum Abend trotzdem voll werden (Puffer eingerechnet): Sie fangen die Mittagsspitze ab, statt schon am Vormittag voll zu sein. „aus“: netzdienliches Laden ist ausgeschaltet. „keine – sofort laden“: Der erwartete Überschuss reicht nicht, die Batterien laden sofort.",
    },
    peakShavingNote:
      "Unter der Schwelle bleiben nur {usable} über dem minimalen Ladezustand ({min}) für Spitzen, etwa {kwh} kWh.",
    settingHints: {
      night_reserve_auto:
        "Lernt die Reserve aus den Morgen: Jeden Tag misst SLEMS, wie viel Energie das Haus zwischen dem Zeitpunkt, an dem die PV hätte übernehmen sollen (Ende der Nachtentladung laut Plan), und dem, an dem sie es wirklich tat (PV deckt den Verbrauch 15 Minuten lang), aus Batterie oder Netz brauchte, in % des prognostizierten Tagesverbrauchs. Braucht 14 gemessene Morgen; gemessen wird auch, wenn die Nachtentladung aus ist.",
      night_reserve_coverage:
        "Anteil der Morgen, für den die gelernte Reserve reicht: 90 % = an 9 von 10 Morgen. Über 100 % die größte gemessene Lücke mal diesem Wert, z. B. 110 % = 10 % mehr als der bisher schlechteste Morgen. Kleinere Werte speisen nachts mehr ein.",
      grid_friendly_buffer_auto:
        "Lernt den Puffer aus den aufgezeichneten PV-Prognosen: von den Tagen mit weniger PV als prognostiziert die Abweichung, die an 80 % davon nicht überschritten wurde, angewendet auf die heute noch erwartete PV. Braucht 14 aufgezeichnete Tage.",
      charge_secured_buffer_auto:
        "Lernt den Sicherheitspuffer (gesicherte Ladung und Ziel der Nachtentladung) aus zu hoher PV- und zu niedriger Verbrauchsprognose, jeweils die Abweichung, die an 80 % solcher Tage nicht überschritten wurde, angewendet auf die Prognosen des restlichen Tages bzw. der nächsten 24 Stunden.",
      grid_targets_auto:
        "Lernt beide Ziel-Netzüberschüsse daraus, wie weit die Netzleistung Richtung Bezug schwankt, während die Batterien regeln: Das Ziel hält das Netz 90 % der Zeit auf der Einspeiseseite (20–1000 W). Braucht zuerst etwas Regelbetrieb im Modus Aktiv.",
      timing_auto:
        "Leitet Regelintervall (0,8 × Meldeintervall des Smart Meters) und Mittelungsfenster (3 × Meldeintervall) aus dem gelernten Meldeintervall ab.",
      regular_full_charge:
        "Eine Batterie, die länger als das Intervall nicht voll war (oder seit der Aufzeichnung durch SLEMS noch nie), wird zuerst geladen, bis sie einmal voll war, immer nur eine Batterie; dafür darf sie ihren maximalen Ladezustand überschreiten. Beim Entladen wird sie geschont, solange die anderen Batterien über 50 % haben. Ist sie voll, ruht sie 90 s, damit das Zell-Delta am oberen Ladeende gemessen wird. LFP-Batterien kalibrieren ihren Ladezustand nur bei einer Vollladung.",
      full_charge_interval:
        "Tage nach der letzten Vollladung, ab denen eine Batterie zuerst geladen wird.",
      learn_capacity:
        "SLEMS lernt die nutzbare Kapazität immer aus Lade- und Entladevorgängen über mindestens 20 % Ladezustand (DC-Energie / Änderung des Ladezustands; Vorgänge mit einem Sprung des Ladezustands werden verworfen). Ein: Die gelernte Kapazität wird zur Planung verwendet, sobald drei Vorgänge gemessen sind. Aus: Es gilt die eingestellte Kapazität.",
      consumer_learning:
        "SLEMS lernt im Betriebsmodus Aktiv immer mit: die Leistung im eingeschalteten Zustand (leistungsgeregelte Verbraucher: die Höchstleistung, gemessen bei einer Vorgabe ab 90 % der maximalen Leistung) und ob der eigene Thermostat den Verbraucher trotz Vorgabe abschaltet. Ein: Es plant mit der gemessenen Leistung (Ein/Aus-Verbraucher) bzw. begrenzt die maximale Leistung auf die gelernte Höchstleistung (leistungsgeregelte Verbraucher) und behandelt einen selbst taktenden Thermostat wie „Thermostat taktet selbst“. Aus: Es gelten die eingestellten Werte.",
      feed_in_cap:
        "Hält die Einspeisung am Netzanschlusspunkt unter PV-Leistung × Grenze. Aus PV- und Verbrauchsprognose plant SLEMS, wie viel Energie über der Grenze die Batterien aufnehmen müssen, hält dafür Platz frei (Nachtentladung, sonst Einspeisen von Batterieenergie vor der Spitze, möglichst spät und nie über der Grenze) und warnt, wenn es sich nicht ausgeht. Hat Vorrang vor netzdienlichem Laden, Nachtentladung und Batterievorrang.",
      pv_peak_power: "Spitzenleistung der PV-Anlage, auf die sich die Grenze bezieht.",
      feed_in_cap_limit: "Anteil der PV-Spitzenleistung, der höchstens eingespeist werden darf, z. B. 60 %.",
      feed_in_cap_buffer:
        "Zusätzlicher Platz zur prognostizierten Energie über der Grenze, in % davon, gegen eine zu niedrige PV-Prognose. Negative Werte planen mit weniger.",
      target_type:
        "Laufzeit: Zeit, in der der Verbraucher Leistung zieht. Freigabezeit: Zeit, in der SLEMS ihn eingeschaltet hat, für Geräte mit eigener Regelung (Luftentfeuchter mit Hygrostat). Energie: kWh. Temperatur: Mindest- und Zieltemperatur seines Speichers (Temperaturfühler). Gezählt von Frist zu Frist; zuerst aus dem Überschuss. Ein Temperaturziel gilt von Mitternacht bis zur Frist: Nach der Frist bleibt der Verbraucher bis Mitternacht aus, auch mit Überschuss.",
      target_earliest_enabled:
        "Vor dem frühesten Beginn wird der Verbraucher nicht eingeschaltet, auch nicht mit Überschuss (z. B. ein Luftentfeuchter erst ab 10:00). Gezählt bis zur Frist; die späteste Startzeit liegt nie davor.",
      target_sensor:
        "Für welche Temperatur Mindest- und Zieltemperatur gelten: das Mittel beider Fühler oder einer davon (z. B. der obere Fühler für das Warmwasser am Hahn). Fühler 1 ist der Temperaturfühler des Speichers, Fühler 2 der zweite in der Konfiguration des Verbrauchers. Die späteste Startzeit wird mit der für den gewählten Fühler gelernten Energie pro Grad geschätzt (bis sie gelernt ist, mit der des Mittelwerts).",
      target_source:
        "Was den Rest decken darf, wenn der Überschuss bis zur Frist nicht reicht. Mit Batterie (und Netz) läuft der Verbraucher ab der spätesten Startzeit unabhängig vom Überschuss. Nur Überschuss: Das Ziel kann verfehlt werden (Benachrichtigung).",
      target_priority:
        "Der Verbraucher bekommt den Überschuss vor den Batterien, wenn der prognostizierte Überschuss bis zur Frist für den Rest seines Ziels plus das Füllen der Batterien knapp ist.",
      target_deadline: "Ende des Tageszeitraums, z. B. 22:00; auch über Mitternacht (06:00 = bis zum nächsten Morgen).",
      cap_mode:
        "Unterstützend: bekommt bei aktiver Einspeisebegrenzung keinen sonstigen Überschuss, nur den Überschuss über der Grenze, den die Batterien nicht aufnehmen können. Zeigt die Prognose, dass eine Spitze nicht in die Batterien passt, läuft er ab Beginn der Spitze, damit seine Leistung über die ganze Spitze genutzt wird. Normal: Überschuss wie ohne Einspeisebegrenzung; über der Grenze nimmt er, was Batterien und unterstützende Verbraucher nicht schaffen, bevor abgeregelt wird. Nie: Überschuss wie ohne Einspeisebegrenzung, nie den Überschuss über der Grenze.",
      feed_in_cap_auto_buffer:
        "Verwendet statt des festen Puffers die aufgezeichneten Abweichungen der PV-Prognose: Von den Tagen mit mehr PV als prognostiziert hebt die Unterschätzung, die an 80 % davon nicht überschritten wurde, die PV-Prognose an. Braucht 14 aufgezeichnete Tage; bis dahin gilt der feste Puffer.",
      peak_shaving_grid_limit:
        "Unterhalb der Ladezustand-Schwelle kommt Verbrauch bis zu dieser Leistung aus dem Netz; die Batterien decken nur, was darüber hinausgeht.",
      peak_shaving_auto:
        "Berechnet die Bezugsgrenze so, dass die nutzbare Energie (über dem minimalen Ladezustand, abzüglich Sicherheitsreserve) reicht, bis PV die Batterien wieder füllt. Grundlage sind die Verbrauchsspitzen der letzten Tage; die feste Grenze wird dann nicht verwendet.",
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
      "Die Batterie verlässt die normale Steuerung: Sie lädt bis in den oberen Spannungsbereich (aus dem PV-Überschuss, mindestens mit 95 W; ohne Überschuss gleichen die anderen Batterien aus), ist für eine Messung im Standby, entlädt etwas und wiederholt das, bis das Zell-Delta im normalen Bereich liegt (unter 190 mV) oder 6 Stunden lang nicht mehr gesunken ist, höchstens 24 h. Das BMS gleicht nur wenige mV pro Tag aus. Ihre Entladung wird eingespeist, außer die anderen Batterien laden ohnehin.",
    balancingNotNeeded: "Die letzte Messung ({delta} mV) liegt im normalen Bereich; ein Zellausgleich ist kaum nötig.",
    startBalancingConfirm: "Starten",
    balancingNeedsActive: "Der Zellausgleich kann nur im Betriebsmodus „Aktiv“ gestartet werden.",
    balancingNeedsEnabled: "Der Zellausgleich braucht eine aktivierte Batterie.",
    balancingNeedsCommunication: "Der Zellausgleich kann nicht gestartet werden, solange die Kommunikation pausiert ist.",
    lastBalancing: "Letzter Zellausgleich",
    balancingResults: {
      done: "abgeschlossen",
      cancelled: "abgebrochen",
      timeout: "beendet: Abschlussentladung nicht fertig",
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
      feedInCap: "Einspeisebegrenzung",
      gridFriendly: "Netzdienliches Laden",
      night: "Nachtentladung",
      peak: "Bezugsspitzen abfangen",
      rotation: "Mehrere Batterien",
      fullCharge: "Regelmäßige Vollladung",
    },
  },
};

// Settings tab: limits of every battery (translation keys of its entities).
const BATTERY_SETTINGS = ["min_soc", "max_soc", "max_charge_limit", "max_discharge_limit", "learn_capacity", "learned_capacity"];

// Settings with a learned value: number key -> [switch key, attribute of the
// sensor "learned_values" (see learning.py)].
const LEARNED_SETTINGS = {
  grid_friendly_buffer: ["grid_friendly_buffer_auto", "grid_friendly_buffer_kwh"],
  charge_secured_buffer: ["charge_secured_buffer_auto", "charge_secured_buffer_kwh"],
  charge_grid_target: ["grid_targets_auto", "charge_grid_target_w"],
  discharge_grid_target: ["grid_targets_auto", "discharge_grid_target_w"],
  control_interval: ["timing_auto", "control_interval_s"],
  surplus_average_window: ["timing_auto", "surplus_average_window_s"],
  night_reserve: ["night_reserve_auto", "night_reserve_pct"],
};

// Settings tab: translation keys of the system entities per group.
// Settings shown only while the switch they belong to is on.
const SETTING_SHOWN_WITH = {
  night_reserve_coverage: "night_reserve_auto",
  full_charge_interval: "regular_full_charge",
};

// A battery not full for longer than this is named in the overview.
const FULL_CHARGE_NOTE_DAYS = 14;

const SETTING_GROUPS = [
  ["mode", ["operating_mode", "vacation"]],
  [
    "control",
    [
      "auto_gain",
      "control_gain",
      "timing_auto",
      "control_interval",
      "surplus_average_window",
      // Learned values (read only).
      "control_gain_current",
      "meter_interval",
      "battery_response_time",
    ],
  ],
  [
    "priority",
    [
      "battery_priority_soc",
      "battery_share_when_secured",
      "charge_secured_buffer_auto",
      "charge_secured_buffer",
      "grid_targets_auto",
      "charge_grid_target",
      "discharge_grid_target",
      "discharge_max_grid_export",
    ],
  ],
  [
    "feedInCap",
    [
      "feed_in_cap",
      "pv_peak_power",
      "feed_in_cap_limit",
      "feed_in_cap_buffer",
      "feed_in_cap_auto_buffer",
    ],
  ],
  ["gridFriendly", ["grid_friendly_charging", "grid_friendly_buffer_auto", "grid_friendly_buffer"]],
  ["night", ["night_discharge", "night_reserve_auto", "night_reserve", "night_reserve_coverage"]],
  [
    "peak",
    [
      "peak_shaving",
      // Read only with the automatic limit (shows the calculated value).
      "peak_shaving_grid_limit",
      "peak_shaving_soc_threshold",
      "peak_shaving_auto",
      "peak_shaving_reserve",
    ],
  ],
  ["temperature", ["temperature_limit", "temperature_high", "temperature_band", "temperature_floor", "temperature_low"]],
  ["fullCharge", ["regular_full_charge", "full_charge_interval"]],
  [
    "rotation",
    ["rotation_soc_threshold", "rotation_min_interval", "rotation_ramp_rate", "rotation_ramp_max"],
  ],
];

/** Mean of two values, null if one is missing. */
const midpoint = (a, b) =>
  a === null || a === undefined || b === null || b === undefined ? null : (a + b) / 2;

/** "HH:MM" of the start of a half hour slot (0 = 00:00, 48 = 24:00). */
const slotTime = (slot) => `${String(Math.floor(slot / 2)).padStart(2, "0")}:${slot % 2 ? "30" : "00"}`;

/** Consecutive rows with a value for ``key``; a missing value starts a new run. */
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

/** One polyline per run of points; a single point becomes a dot. ``dash``: true or a dash pattern. */
function polylines(pointRuns, color, dash) {
  return pointRuns
    .map((points) =>
      points.length === 1
        ? `<circle cx="${points[0][0]}" cy="${points[0][1]}" r="2.5" fill="${color}"/>`
        : `<polyline points="${points.map((p) => p.join(",")).join(" ")}" fill="none" stroke="${color}"
          stroke-width="2" stroke-linejoin="round" stroke-linecap="round" ${dash ? `stroke-dasharray="${dash === true ? "5 4" : dash}"` : ""}/>`
    )
    .join("");
}

// Comparison series of the simulation (plan with the current settings): dotted.
const COMPARE_DASH = "1 4";

// localStorage keys of the day chart series hidden via the legend and of the
// series hidden by default that were switched on.
// Top cell delta below which balancing is hardly needed (green status limit).
const BALANCING_NORMAL_MV = 200;
const HIDDEN_SERIES_KEY = "slems-hidden-series";
// localStorage key of the consumer cards shown with all their settings.
const EXPANDED_CONSUMERS_KEY = "slems-expanded-consumers";
const SHOWN_SERIES_KEY = "slems-shown-series";
// Series hidden until switched on in the legend.
const DEFAULT_HIDDEN_SERIES = ["capExcess"];

// Energy the feed-in cap forecasts to be curtailed (status colour "critical", not a series colour).
const CURTAILED_COLOR = "#d03b3b";

// Below this SoC threshold the peak shaving card warns about the little energy left.
const PEAK_SHAVING_NOTE_PCT = 20;

// Smallest width of a battery box in the energy flow before they are stacked.
const MIN_FLOW_BOX_W = 130;

// SLEMS icon (copy of assets/icon.svg) in the crossing of the energy flow lines.
const ICON_URL = new URL("slems-icon.svg", import.meta.url).href;

// The first tile is the combined status (see _statusTile).
// "expected_export" is computed in the panel (see _exportTile).
const OVERVIEW_TILES = [
  "allocation_strategy",
  "battery_soc_total",
  "battery_energy_total",
  "feed_in_limit",
  "expected_surplus_energy",
  "expected_export",
  "pv_forecast_today",
  "consumption_forecast_today",
  "pv_forecast_tomorrow",
  "consumption_forecast_tomorrow",
];

/**
 * Expected grid export (mean W) of a day plan row: the export of the
 * projection, without the part above the feed-in cap (curtailed).
 */
function planExportW(row) {
  if (row?.grid_w === null || row?.grid_w === undefined) return null;
  return Math.min(
    Math.max(0, -row.grid_w),
    row.cap_line_wh === undefined ? Infinity : row.cap_line_wh - (row.consumption_wh ?? 0)
  );
}

/** Selector that finds a control again after its section was drawn anew. */
function focusSelector(element) {
  const attributes = ["data-action", "data-entity", "data-sim", "data-device", "data-series", "data-tab", "data-key"];
  const parts = attributes
    .filter((name) => element.hasAttribute(name))
    .map((name) => `[${name}="${CSS.escape(element.getAttribute(name))}"]`);
  return parts.length ? element.tagName.toLowerCase() + parts.join("") : null;
}

const escapeHtml = (value) =>
  String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

class SlemsPanel extends HTMLElement {
  constructor() {
    super();
    this._tab = "overview";
    this._showTable = false;
    // Simulation tab: form values, last result (see _simulate).
    this._sim = { form: null, result: null, serial: 0, timer: null, requested: false };
    // Series of the day chart switched off in its legend (kept in the browser).
    this._hiddenSeries = new Set(DEFAULT_HIDDEN_SERIES);
    try {
      const shown = JSON.parse(localStorage.getItem(SHOWN_SERIES_KEY) || "[]");
      const hidden = JSON.parse(localStorage.getItem(HIDDEN_SERIES_KEY) || "[]");
      this._hiddenSeries = new Set([...DEFAULT_HIDDEN_SERIES.filter((k) => !shown.includes(k)), ...hidden]);
    } catch (err) {
      // Storage not available: the default series shown.
    }
    // Consumer cards shown with all their settings (kept in the browser).
    this._expandedConsumers = new Set();
    try {
      this._expandedConsumers = new Set(JSON.parse(localStorage.getItem(EXPANDED_CONSUMERS_KEY) || "[]"));
    } catch (err) {
      // Storage not available: all cards collapsed.
    }
    this._chartDay = "today";
    // Settings whose explanation is shown (translation keys).
    this._openHints = new Set();
    this._stats = { pv: {}, house: {}, soc: {}, charge: {}, export: {} };
    this._statsFetched = 0;
    this._sections = {};
    this._renderQueued = false;
  }

  set hass(hass) {
    this._hass = hass;
    // Hyphenation of long words in narrow tiles follows the language.
    this.lang = hass?.locale?.language || hass?.language || "en";
    // Entity names in the language of the frontend, not of the server.
    if (hass?.loadBackendTranslation && this._entityLanguage !== this.lang) {
      this._entityLanguage = this.lang;
      hass.loadBackendTranslation("entity", DOMAIN).then(() => {
        this._sections = {};
        this._queueRender();
      });
    }
    if (hass && this._renamed === undefined) {
      // Entities renamed by the user keep their name (only the full registry knows).
      this._renamed = new Set();
      hass
        .callWS({ type: "config/entity_registry/list" })
        .then((entries) => {
          this._renamed = new Set(entries.filter((e) => e.platform === DOMAIN && e.name).map((e) => e.entity_id));
          this._sections = {};
          this._queueRender();
        })
        .catch(() => {});
    }
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

  /** How well the consumption and PV forecasts matched (backtest / recorded days). */
  _renderAccuracy() {
    const t = this._t;
    const kwh = (v) =>
      `${new Intl.NumberFormat(this._hass?.locale?.language || "en", { maximumFractionDigits: 1 }).format(v)} kWh`;
    const block = (key, title, basis) => {
      const st = this._state(key);
      if (!st) return "";
      const a = st.attributes;
      const accuracy = this._number(st);
      const days = a.evaluated_days ?? 0;
      // A few days say little: an error of 200 % on day 3 is no accuracy yet.
      const collecting = accuracy === null || days < MIN_ACCURACY_DAYS;
      const rows = [];
      if (collecting) {
        rows.push([
          t.accuracy,
          `<span class="muted">${t.collectingData.replace("{n}", days).replace("{min}", MIN_ACCURACY_DAYS)}</span>`,
        ]);
      } else {
        rows.push([t.accuracy, `${this._percent(accuracy)} <span class="muted">(${t.lastDays.replace("{n}", a.evaluated_days)})</span>`]);
        if (a.bias_pct !== null && a.bias_pct !== undefined) {
          const direction = a.bias_pct > 0 ? t.tooHigh : t.tooLow;
          rows.push([t.tendency, Math.abs(a.bias_pct) < 1 ? t.balanced : `${direction} (${this._percent(Math.abs(a.bias_pct))})`]);
        }
        if (a.hourly_error_pct !== null && a.hourly_error_pct !== undefined) {
          rows.push([t.hourlyCourse, t.hourlyDeviation.replace("{pct}", this._percent(a.hourly_error_pct))]);
        }
      }
      rows.push([t.dataBasis, basis(a)]);
      if (a.tomorrow_forecast_kwh !== null && a.tomorrow_forecast_kwh !== undefined) {
        const error = collecting ? null : a.tomorrow_expected_error_kwh;
        rows.push([
          t.tomorrow,
          `${kwh(a.tomorrow_forecast_kwh)}${error !== null && error !== undefined ? ` ± ${kwh(error)}` : ""}`,
        ]);
      }
      return `<div><h3>${title}</h3><dl>${rows.map(([l, v]) => this._row(l, v, st.entity_id)).join("")}</dl></div>`;
    };
    const consumption = block("consumption_forecast_accuracy", t.consumptionForecastTitle, (a) =>
      t.consumptionBasis.replace("{days}", a.history_days) +
      (a.heat_pump_days ? t.heatPumpBasis.replace("{days}", a.heat_pump_days) : "")
    );
    const pv = block("pv_forecast_accuracy", t.pvForecastTitle, (a) =>
      t.pvBasis.replace("{days}", a.recorded_days)
    );
    this._setSection(
      "accuracy",
      consumption || pv
        ? `<h2>${t.forecastAccuracy}</h2><span class="hint">${t.forecastAccuracyHint}</span><div class="accuracy">${consumption}${pv}</div>`
        : ""
    );
  }

  /**
   * Operating mode and problems in one tile: problems first (the most
   * important one, "+n" for further ones), otherwise the operating mode.
   */
  _statusTile() {
    const t = this._t;
    const problems = [];
    if (this._state("control_status")?.state === "grid_stale") problems.push(t.gridStale);
    for (const battery of this._config.batteries || []) {
      if (this._state("communication_paused", battery.device_id)?.state === "on") continue;
      if (this._state("battery_soc", battery.device_id)?.state === "unavailable") {
        problems.push(`${battery.name}: ${t.unreadable}`);
      } else if (this._state("not_responding", battery.device_id)?.state === "on") {
        problems.push(`${battery.name}: ${t.notResponding}`);
      }
    }
    for (const key of this._capProblems()) problems.push(t.capProblems[key]);
    const mode = this._state("operating_mode");
    const value = problems.length
      ? `<span class="value problem-value"><ha-icon icon="mdi:alert-circle"></ha-icon>${escapeHtml(problems[0])}${
          problems.length > 1 ? ` (+${problems.length - 1})` : ""
        }</span>`
      : `<span class="value">${escapeHtml(mode ? this._format(mode) : "–")}</span>`;
    const moreInfo = mode ? ` data-more-info="${mode.entity_id}"` : "";
    return `<div class="tile${problems.length ? " wide" : ""}"${moreInfo}><span class="label">${t.status}</span>${value}</div>`;
  }

  /** Sensor of the feed-in cap while it is switched on, otherwise null. */
  _capState() {
    if (this._state("feed_in_cap")?.state !== "on") return null;
    return this._state("feed_in_cap_energy") || null;
  }

  _capProblems() {
    const a = this._capState()?.attributes || {};
    return [...(a.problems || []), ...(a.limit_exceeded ? ["limit_exceeded"] : [])];
  }

  /** Time of day of an ISO timestamp. */
  _time(iso) {
    if (!iso) return "–";
    return new Date(iso).toLocaleTimeString(this._hass?.locale?.language, { hour: "2-digit", minute: "2-digit" });
  }

  /** Time of an ISO timestamp; with the weekday when it is not today. */
  _clock(iso) {
    if (!iso) return "–";
    const date = new Date(iso);
    const time = this._time(iso);
    if (date.toDateString() === new Date().toDateString()) return time;
    return `${date.toLocaleDateString(this._hass?.locale?.language, { weekday: "short" })} ${time}`;
  }

  /** Overview tile of the feed-in cap: energy to absorb and export before the peak. */
  _capTile() {
    const t = this._t;
    const energy = this._capState();
    if (!energy) return "";
    const a = energy.attributes || {};
    let label = t.feedInCap;
    let value;
    if (!a.peak_start) {
      value = t.capNoPeak.replace("{limit}", this._watts(a.limit_w));
    } else {
      label += ` ${this._clock(a.peak_start)}–${this._time(a.peak_end)}`;
      const parts = [t.capAbsorb.replace("{absorb}", this._kwh((this._number(energy) || 0) * 1000))];
      if (a.export_needed_kwh >= 0.05) {
        parts.push(
          t.capExport
            .replace("{export}", this._kwh(a.export_needed_kwh * 1000))
            .replace("{time}", this._clock(a.export_until))
        );
      }
      value = parts.join(" · ");
    }
    return `<div class="tile wide" data-more-info="${energy.entity_id}"><span class="label">${escapeHtml(label)}</span>
      <span class="value">${escapeHtml(value)}</span></div>`;
  }

  /** Batteries not full for longer than FULL_CHARGE_NOTE_DAYS (a note, not a problem). */
  _fullChargeNotes() {
    const t = this._t;
    return (this._config.batteries || [])
      .map((battery) => {
        const moment = new Date(this._state("last_full_charge", battery.device_id)?.state ?? "");
        if (Number.isNaN(moment.getTime())) return "";
        const days = Math.floor((Date.now() - moment.getTime()) / 86400000);
        if (days <= FULL_CHARGE_NOTE_DAYS) return "";
        const text = t.fullChargeOverdueText.replace("{name}", battery.name).replace("{days}", days);
        return `<div class="info-box"><ha-icon icon="mdi:battery-alert-variant-outline"></ha-icon><span><b>${escapeHtml(t.fullChargeOverdue)}</b> – ${escapeHtml(text)}</span></div>`;
      })
      .join("");
  }

  /** Problem notes of the feed-in cap for the overview (and the buffer note). */
  _capNotes() {
    const t = this._t;
    const a = this._capState()?.attributes || {};
    // Batteries left out of the planning explain a lack of room.
    const missing = (a.missing_batteries || []).length
      ? ` ${t.capMissing.replace(
          "{batteries}",
          a.missing_batteries.map((b) => `${b.name} (${t.batteryMissing[b.reason] || b.reason})`).join(", ")
        )}`
      : "";
    const problems = this._capProblems()
      .map((key) => {
        const text = t.capProblemTexts[key]
          .replace("{space}", this._kwh((a.required_space_kwh || 0) * 1000))
          .replace("{export}", this._kwh((a.export_needed_kwh || 0) * 1000))
          .replace("{curtailed}", this._kwh((a.curtailed_kwh || 0) * 1000));
        return `<div class="problem"><ha-icon icon="mdi:alert-circle"></ha-icon><span><b>${escapeHtml(t.capProblems[key])}</b> – ${escapeHtml(text + (key === "limit_exceeded" ? "" : missing))}</span></div>`;
      })
      .join("");
    const note = a.buffer_short
      ? `<div class="info-box"><ha-icon icon="mdi:information-outline"></ha-icon><span><b>${escapeHtml(t.capBufferShort)}</b> – ${escapeHtml(t.capBufferShortText + missing)}</span></div>`
      : "";
    const takeover = (a.takeover_kwh || 0) > 0.1 && !this._capProblems().length
      ? `<div class="info-box"><ha-icon icon="mdi:information-outline"></ha-icon><span><b>${escapeHtml(t.capTakeover)}</b> – ${escapeHtml(
          t.capTakeoverText
            .replace("{energy}", this._kwh(a.takeover_kwh * 1000))
            .replace("{consumers}", (a.takeover_consumers || []).join(", "))
        )}</span></div>`
      : "";
    return problems + note + takeover;
  }

  /** Limit and buffer in use below the feed-in cap settings. */
  _capNote() {
    const t = this._t;
    const energy = this._state("feed_in_cap_energy");
    if (!energy) return "";
    const a = energy.attributes || {};
    const pct = a.buffer_pct ?? this._number(this._state("feed_in_cap_buffer"));
    const buffer = pct === null || pct === undefined ? "–" : `${pct > 0 ? "+" : ""}${this._percent(pct)}`;
    let source = "";
    if (a.buffer_source === "auto") source = t.capBufferAuto.replace("{days}", a.buffer_days);
    else if (this._state("feed_in_cap_auto_buffer")?.state === "on") source = t.capBufferWaiting.replace("{days}", a.buffer_days ?? 0);
    const text = t.capNote
      .replace("{limit}", this._watts(a.limit_w))
      .replace("{buffer}", buffer)
      .replace("{source}", source)
      .replace("{min}", this._kwh((a.min_buffer_kwh ?? 0) * 1000));
    return `<p class="setting-note"><ha-icon icon="mdi:information-outline"></ha-icon>${escapeHtml(text)}</p>`;
  }

  /** Value of an overview tile; the feed-in limit explains why it has none. */
  _tileValue(stateObj) {
    const stored = this._storedOf(stateObj);
    if (stored) return stored;
    const reason = stateObj.attributes?.reason;
    const noLimit = this._t.feedInLimitReasons;
    if (this._number(stateObj) === null && reason && noLimit[reason] && this._entityId("feed_in_limit") === stateObj.entity_id) {
      return noLimit[reason];
    }
    return this._format(stateObj);
  }

  /** ⋮ menu of a battery card: enable, pause communication, cell balancing, details. */
  _batteryMenu(battery) {
    const t = this._t;
    const s = (key) => this._state(key, battery.device_id);
    const open = this._openMenu === battery.device_id;
    const button = `<button class="menu-button" data-action="battery-menu" data-device="${battery.device_id}" aria-label="${t.menu}" aria-expanded="${open}"><ha-icon icon="mdi:dots-vertical"></ha-icon></button>`;
    if (!open) return `<div class="menu-wrap">${button}</div>`;
    const name = escapeHtml(battery.name);
    const item = (action, label, attrs = "") => `<button class="menu-item" data-action="${action}"${attrs}>${label}</button>`;
    const items = [];
    const enabled = s("battery_enabled");
    if (enabled) {
      items.push(
        enabled.state === "off"
          ? item("menu-enable", t.enableBattery, ` data-entity="${enabled.entity_id}"`)
          : item("menu-disable", t.disableBattery, ` data-entity="${enabled.entity_id}" data-name="${name}"`)
      );
    }
    const pause = s("communication_paused");
    if (pause) {
      items.push(
        pause.state === "on"
          ? item("menu-resume", t.resumeCommunication, ` data-entity="${pause.entity_id}"`)
          : item("menu-pause", t.pauseCommunication, ` data-entity="${pause.entity_id}" data-name="${name}"`)
      );
    }
    const balancing = s("cell_balancing");
    if (balancing) {
      const blocked = this._balancingBlocked(battery.device_id);
      items.push(
        balancing.state === "on"
          ? item("balancing-off", t.cancelBalancing, ` data-entity="${balancing.entity_id}"`)
          : item("balancing-on", t.startBalancing, ` data-entity="${balancing.entity_id}" data-name="${name}" data-delta="${escapeHtml(s("top_cell_delta")?.state ?? "")}"${blocked ? ` disabled title="${escapeHtml(blocked)}"` : ""}`)
      );
    }
    items.push(item("menu-details", t.details, ` data-device="${battery.device_id}"`));
    items.push(item("menu-device", t.deviceView, ` data-device="${battery.device_id}"`));
    return `<div class="menu-wrap">${button}<div class="menu" role="menu">${items.join("")}</div></div>`;
  }

  /** Why a cell balancing run cannot be started right now (null if it can). */
  _balancingBlocked(deviceId) {
    const t = this._t;
    if (this._state("battery_enabled", deviceId)?.state === "off") return t.balancingNeedsEnabled;
    if (this._state("communication_paused", deviceId)?.state === "on") return t.balancingNeedsCommunication;
    if (this._state("operating_mode")?.state !== "active") return t.balancingNeedsActive;
    return null;
  }

  /** Enabled items of the ⋮ menu of a battery. */
  _menuItems(deviceId) {
    const toggle = this.shadowRoot.querySelector(`[data-action='battery-menu'][data-device="${deviceId}"]`);
    return [...(toggle?.closest(".menu-wrap")?.querySelectorAll(".menu-item:not([disabled])") || [])];
  }

  /** Keyboard in the ⋮ menu: arrows move, Escape closes it and returns to its button. */
  _onMenuKey(event) {
    const deviceId = this._openMenu;
    if (!deviceId) return;
    if (event.key === "Escape") {
      event.preventDefault();
      this._openMenu = null;
      this._render();
      this.shadowRoot.querySelector(`[data-action='battery-menu'][data-device="${deviceId}"]`)?.focus();
      return;
    }
    if (event.key !== "ArrowDown" && event.key !== "ArrowUp") return;
    const items = this._menuItems(deviceId);
    if (!items.length) return;
    event.preventDefault();
    const index = items.indexOf(this.shadowRoot.activeElement);
    const step = event.key === "ArrowDown" ? 1 : -1;
    items[(index + step + items.length) % items.length].focus();
  }

  /** Handles the ⋮ menu; returns true if the click was consumed. */
  _onMenuClick(event) {
    const t = this._t;
    const toggle = event.target.closest("[data-action='battery-menu']");
    if (toggle) {
      this._openMenu = this._openMenu === toggle.dataset.device ? null : toggle.dataset.device;
      this._render();
      // The card is drawn anew: move the focus into the opened menu.
      if (this._openMenu) this._menuItems(this._openMenu)[0]?.focus();
      return true;
    }
    const item = event.target.closest(".menu-item, [data-action='menu-resume']");
    const wasOpen = this._openMenu;
    if (wasOpen && !event.target.closest(".menu-wrap")) {
      this._openMenu = null;
      this._render();
    }
    if (!item) return false;
    this._openMenu = null;
    const entityId = item.dataset.entity;
    switch (item.dataset.action) {
      case "menu-enable":
        this._hass.callService("switch", "turn_on", { entity_id: entityId });
        break;
      case "menu-disable":
        this._confirm(
          t.disableBatteryTitle.replace("{name}", item.dataset.name),
          t.disableBatteryText,
          t.disableBatteryConfirm,
          () => this._hass.callService("switch", "turn_off", { entity_id: entityId })
        );
        break;
      case "menu-pause":
        this._confirm(
          t.pauseTitle.replace("{name}", item.dataset.name),
          t.pauseText.replace("{min}", this._number(this._state("communication_pause")) ?? 20),
          t.pauseConfirm,
          () => this._hass.callService("switch", "turn_on", { entity_id: entityId }),
          false
        );
        break;
      case "menu-resume":
        this._hass.callService("switch", "turn_off", { entity_id: entityId });
        break;
      case "menu-details":
        this._detailsDevice = item.dataset.device;
        this._renderDetails();
        this.shadowRoot.getElementById("details").showModal();
        break;
      case "menu-device":
        // Navigation inside the Home Assistant frontend without a reload.
        history.pushState(null, "", `/config/devices/device/${item.dataset.device}`);
        window.dispatchEvent(new CustomEvent("location-changed", { detail: { replace: false } }));
        return true;
      default:
        // balancing-on / balancing-off: handled by the caller.
        this._render();
        return false;
    }
    this._render();
    return true;
  }

  /** Details dialog of a battery: firmware, device data, counters. */
  _renderDetails() {
    const deviceId = this._detailsDevice;
    const dialog = this.shadowRoot?.getElementById("details");
    if (!deviceId || !dialog) return;
    const t = this._t;
    const battery = (this._config.batteries || []).find((b) => b.device_id === deviceId);
    const s = (key) => this._state(key, deviceId);
    const firmware = s("firmware");
    const info = firmware?.attributes || {};
    const device = this._hass.devices?.[deviceId];
    const stored = s("stored_energy");
    const response = this._state("battery_response_time");
    const seconds = (v) =>
      `${new Intl.NumberFormat(this._hass.locale?.language || "en", { maximumFractionDigits: 1, minimumFractionDigits: 1 }).format(v)} s`;
    const ownResponse = response?.attributes?.per_battery?.[battery?.id];
    const joint = this._number(response);
    const rows = [
      [t.model, device?.model],
      [t.deviceName, info.device_name],
      [t.emsVersion, info.ems_version, firmware?.entity_id],
      [t.vmsVersion, info.vms_version, firmware?.entity_id],
      [t.bmsVersion, info.bms_version, firmware?.entity_id],
      [t.commFirmware, info.comm_module_firmware, firmware?.entity_id],
      [t.macAddress, info.mac_address],
      [
        t.capacity,
        stored?.attributes?.capacity_kwh !== undefined
          ? `${new Intl.NumberFormat(this._hass.locale?.language || "en", { maximumFractionDigits: 2 }).format(stored.attributes.capacity_kwh)} kWh`
          : undefined,
        stored?.entity_id,
      ],
      [t.cycles, s("cycle_count") && this._format(s("cycle_count")), s("cycle_count")?.entity_id],
      [t.totalCharged, s("total_charging_energy") && this._format(s("total_charging_energy")), s("total_charging_energy")?.entity_id],
      [t.totalDischarged, s("total_discharging_energy") && this._format(s("total_discharging_energy")), s("total_discharging_energy")?.entity_id],
      [t.lastFullCharge, this._moment(s("last_full_charge")?.state), s("last_full_charge")?.entity_id],
      [t.topDeltaMeasured, this._moment(s("top_cell_delta")?.attributes?.measured_at), s("top_cell_delta")?.entity_id],
      [
        t.responseTime,
        ownResponse !== undefined
          ? seconds(ownResponse)
          : joint !== null
            ? t.responseNotLearned.replace("{value}", seconds(joint))
            : undefined,
        response?.entity_id,
      ],
    ].filter(([, value]) => value !== undefined && value !== null && value !== "");
    this.shadowRoot.getElementById("details-title").textContent = `${t.details}: ${battery?.name ?? ""}`;
    this.shadowRoot.getElementById("details-close").textContent = t.close;
    this.shadowRoot.getElementById("details-body").innerHTML = rows.length
      ? rows.map(([label, value, entityId]) => this._row(label, escapeHtml(String(value)), entityId)).join("")
      : `<dd class="muted">${t.noDetails}</dd>`;
  }

  /** Energy the storage of a consumer can still take, or how far the learning is. */
  _storageText(storage) {
    const t = this._t;
    const a = storage.attributes || {};
    if (a.until_cycling_kwh === null || a.until_cycling_kwh === undefined) {
      return t.storageLearning.replace("{runs}", `${a.runs ?? 0}`).replace("{marks}", `${a.cycling_marks ?? 0}`);
    }
    const cycling = this._kwh(a.until_cycling_kwh * 1000);
    if (a.full_temperature_c === null || a.full_temperature_c === undefined) {
      return t.storageUntilCycling.replace("{energy}", cycling);
    }
    return t.storageUntilFull.replace("{cycling}", cycling).replace("{full}", this._kwh(a.until_full_kwh * 1000));
  }

  /** "28.09.2026, 14:32 (vor 2 Tagen)" for an ISO time; undefined without a valid one. */
  _moment(iso) {
    const moment = iso ? new Date(iso) : null;
    if (!moment || Number.isNaN(moment.getTime())) return undefined;
    const language = this._hass?.locale?.language || "en";
    const absolute = new Intl.DateTimeFormat(language, { dateStyle: "medium", timeStyle: "short" }).format(moment);
    const seconds = (moment.getTime() - Date.now()) / 1000;
    const [value, unit] =
      Math.abs(seconds) < 3600
        ? [Math.round(seconds / 60), "minute"]
        : Math.abs(seconds) < 86400
          ? [Math.round(seconds / 3600), "hour"]
          : [Math.round(seconds / 86400), "day"];
    const relative = new Intl.RelativeTimeFormat(language, { numeric: "auto" }).format(value, unit);
    return `${absolute} (${relative})`;
  }

  /** A dt/dd pair; with an entity both open its more-info dialog on click. */
  _row(label, valueHtml, entityId) {
    const moreInfo = entityId ? ` data-more-info="${entityId}"` : "";
    return `<dt${moreInfo}>${label}</dt><dd${moreInfo}>${valueHtml}</dd>`;
  }

  /** "2,09 von 5,12 kWh" for a stored energy sensor with its capacity. */
  _storedOf(stateObj) {
    const stored = this._number(stateObj);
    const capacity = stateObj?.attributes?.capacity_kwh;
    if (stored === null || capacity === null || capacity === undefined) return null;
    const format = (v) =>
      new Intl.NumberFormat(this._hass?.locale?.language || "en", { maximumFractionDigits: 2, minimumFractionDigits: 2 }).format(v);
    return this._t.storedOf.replace("{stored}", format(stored)).replace("{capacity}", format(capacity));
  }

  /**
   * Battery power, +charge / -discharge: grid side if reported (the AC power
   * sensor uses +discharge like the Home Assistant energy dashboard), else
   * the battery power.
   */
  _batteryPower(deviceId) {
    const ac = this._number(this._state("ac_power", deviceId));
    return ac !== null ? -ac : this._number(this._state("battery_power", deviceId));
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
    // Not renamed by the user: the translated name in the frontend language.
    if (entry?.platform === DOMAIN && entry.translation_key && !this._renamed?.has(stateObj.entity_id)) {
      const domain = stateObj.entity_id.split(".")[0];
      const translated = this._hass.localize?.(`component.${DOMAIN}.entity.${domain}.${entry.translation_key}.name`);
      if (translated) return translated;
    }
    const device = entry?.device_id ? this._hass.devices?.[entry.device_id] : undefined;
    let name = stateObj.attributes.friendly_name || stateObj.entity_id;
    const prefix = device?.name_by_user || device?.name;
    if (prefix && name.startsWith(prefix + " ")) name = name.slice(prefix.length + 1);
    return name;
  }

  /** Energy of an hour (Wh, equal to the mean power in W) in kWh. */
  _kwh(wh) {
    const language = this._hass?.locale?.language || "en";
    return `${new Intl.NumberFormat(language, { maximumFractionDigits: 2 }).format(wh / 1000)} kWh`;
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
    if (!element || this._sections[name] === html) return;
    // The focused control and a value being typed survive the new drawing.
    const active = this.shadowRoot.activeElement;
    const key = active && element.contains(active) ? focusSelector(active) : null;
    const typed =
      key && active.matches("input:not([type=checkbox]), select") && active.value !== active.getAttribute("value")
        ? active.value
        : null;
    element.innerHTML = html;
    this._sections[name] = html;
    const again = key ? element.querySelector(key) : null;
    if (again) {
      if (typed !== null && active.tagName === "INPUT") again.value = typed;
      again.focus();
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
      </dialog>
      <dialog id="details">
        <h2 id="details-title"></h2>
        <dl id="details-body"></dl>
        <div class="dialog-buttons"><button class="link" data-answer="close" id="details-close"></button></div>
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
    const details = this.shadowRoot.getElementById("details");
    details.addEventListener("click", (event) => {
      if (event.target.closest("[data-answer]") || event.target === details) {
        details.close();
        return;
      }
      const moreInfo = event.target.closest("[data-more-info]");
      if (moreInfo) this._moreInfo(moreInfo.dataset.moreInfo);
    });
    details.addEventListener("close", () => {
      this._detailsDevice = null;
    });
    this.shadowRoot.getElementById("tabs").addEventListener("click", (event) => {
      const tab = event.target.closest("button")?.dataset.tab;
      if (tab) {
        if (tab === "simulation" && this._tab !== "simulation") {
          // Starts with the real settings on every visit.
          this._sim = { form: null, result: null, serial: this._sim.serial + 1, timer: null, requested: false };
        }
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
    content.addEventListener("keydown", (event) => this._onMenuKey(event));
    content.addEventListener("change", (event) => this._onChange(event));
    // The mouse wheel over a focused number field would change and save its
    // value while scrolling the page; the field loses the focus instead.
    content.addEventListener(
      "wheel",
      (event) => {
        if (event.target.matches?.('input[type="number"]') && event.target === this.shadowRoot.activeElement) {
          event.target.blur();
        }
      },
      { passive: true }
    );
    content.addEventListener("pointermove", (event) => this._onChartHover(event));
    // Touch: a tap (or a horizontal drag) on the chart shows the hour and keeps
    // it; a tap elsewhere hides it. The mouse hides it when leaving the chart.
    content.addEventListener("pointerdown", (event) => {
      if (event.pointerType === "mouse") return;
      if (event.target.closest?.("svg.chart")) this._onChartHover(event);
      else this._hideTooltip();
    });
    content.addEventListener(
      "pointerleave",
      (event) => {
        if (event.pointerType === "mouse" && event.target.matches?.("svg.chart")) this._hideTooltip();
      },
      true
    );
  }

  _render() {
    if (!this._hass || !this.shadowRoot) return;
    this._renderDetails();
    const menu = this.shadowRoot.getElementById("menu");
    menu.hass = this._hass;
    menu.narrow = this._narrow;
    const t = this._t;
    const tabs = ["overview", "batteries", "consumers", "simulation", "settings"];
    this._setSection(
      "tabs",
      tabs
        .map((tab) => `<button data-tab="${tab}" class="${tab === this._tab ? "active" : ""}">${t[tab]}</button>`)
        .join("")
    );
    this.toggleAttribute("dark", Boolean(this._hass.themes?.darkMode));
    // The active tab stays visible in the sideways scrolling tab row (phones);
    // only the row scrolls, never the page.
    if (this._shownTab !== this._tab) {
      this._shownTab = this._tab;
      const nav = this.shadowRoot.getElementById("tabs");
      const active = nav.querySelector("button.active");
      if (active && nav.scrollWidth > nav.clientWidth) {
        const left = active.getBoundingClientRect().left - nav.getBoundingClientRect().left + nav.scrollLeft;
        nav.scrollLeft = left - (nav.clientWidth - active.offsetWidth) / 2;
      }
    }
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
    else if (this._tab === "simulation") this._renderSimulation();
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
        <section class="card"><div id="daychart"></div></section>
        <section class="card"><div id="accuracy"></div></section>`;
    }
    if (this._tab === "simulation") {
      return `
        <div class="sim-layout">
          <div class="sim-main">
            <section class="card"><div id="simnote"></div><div id="daychart"></div></section>
            <section class="card"><div id="simmetrics"></div></section>
          </div>
          <section class="card sim-side"><div id="simintro"></div><div id="simcontrols"></div></section>
        </div>`;
    }
    return `<div id="${this._tab}-list" class="grid cards"></div>`;
  }

  // --- overview ------------------------------------------------------------------

  _renderOverview() {
    const t = this._t;
    this._setSection(
      "banner",
      (this._state("control_status")?.state === "grid_stale"
        ? `<div class="problem"><ha-icon icon="mdi:alert-circle"></ha-icon><span><b>${t.gridStale}</b> – ${t.gridStaleText}</span></div>`
        : "") + this._capNotes() + this._fullChargeNotes()
    );
    this._renderFlow();
    this._setSection(
      "tiles",
      this._statusTile() +
      this._capTile() +
      OVERVIEW_TILES.map((key) => [key, this._state(key)])
        .filter(([key, s]) => s || key === "expected_export")
        .map(([key, s]) => {
          if (key === "expected_export") return this._exportTile();
          const hint = t.tileHints[key];
          const info = hint
            ? `<button class="info" data-action="toggle-hint" data-key="${key}" title="${escapeHtml(hint)}" aria-label="${escapeHtml(hint)}"><ha-icon icon="mdi:information-outline"></ha-icon></button>`
            : "";
          const open = hint && this._openHints.has(key) ? `<span class="setting-hint">${escapeHtml(hint)}</span>` : "";
          return `<div class="tile" data-more-info="${s.entity_id}"><span class="label">${escapeHtml(this._name(s))}${info}</span>
                  <span class="value">${escapeHtml(this._tileValue(s))}</span>${open}</div>`;
        })
        .join("")
    );
    this._fetchStats(false);
    this._renderDayChart();
    this._renderAccuracy();
  }

  /** Grid export today: measured until now plus the export the plan still expects. */
  _exportTile() {
    const t = this._t;
    const limit = this._state("feed_in_limit");
    const plan = limit?.attributes?.day_plan || [];
    const now = Date.now();
    let restWh = null;
    for (const row of plan) {
      const start = new Date(row.start).getTime();
      const end = start + 3600e3;
      const exportW = planExportW(row);
      if (end <= now || exportW === null) continue;
      // The current hour: the plan holds the mean of its remaining part.
      restWh = (restWh ?? 0) + exportW * (start < now ? (end - now) / 3600e3 : 1);
    }
    if (restWh === null) return "";
    const soFar = limit.attributes.exported_today_kwh;
    const language = this._hass?.locale?.language || "en";
    const kwh = (value) => new Intl.NumberFormat(language, { maximumFractionDigits: 1, minimumFractionDigits: 1 }).format(value);
    const total = (soFar ?? 0) + restWh / 1000;
    const detail = t.exportDetail
      .replace("{so_far}", soFar === null || soFar === undefined ? "–" : kwh(soFar))
      .replace("{rest}", kwh(restWh / 1000));
    return `<div class="tile" data-more-info="${limit.entity_id}"><span class="label">${escapeHtml(t.expectedExport)}</span>
            <span class="value">${escapeHtml(kwh(total))} kWh</span><span class="sub">${escapeHtml(detail)}</span></div>`;
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
      { id: "pv", role: "pv", icon: "mdi:solar-power-variant", title: t.pv, power: num("pv_power"), entity: this._entityId("pv_power") },
      {
        id: "grid",
        role: "grid",
        icon: "mdi:transmission-tower",
        title: t.grid,
        power: grid,
        detail: grid === null ? "" : grid > 10 ? t.import : grid < -10 ? t.export : "",
        entity: this._entityId("grid_power"),
      },
      { id: "house", role: "house", icon: "mdi:home-lightning-bolt", title: t.house, power: num("house_power"), entity: this._entityId("house_power") },
    ];
    for (const battery of this._config.batteries || []) {
      // Grid side (AC) power if the driver reports it, +charge / -discharge.
      const power = this._batteryPower(battery.device_id);
      const enabled = this._state("battery_enabled", battery.device_id)?.state !== "off";
      const balancing = this._state("cell_balancing", battery.device_id)?.state === "on";
      const notResponding = this._state("not_responding", battery.device_id)?.state === "on";
      const paused = this._state("communication_paused", battery.device_id)?.state === "on";
      const unreadable = !paused && this._state("battery_soc", battery.device_id)?.state === "unavailable";
      nodes.push({
        id: `battery-${battery.id}`,
        role: "battery",
        icon: "mdi:home-battery",
        title: battery.name,
        power,
        soc: num("battery_soc", battery.device_id),
        entity: this._entityId("ac_power", battery.device_id) || this._entityId("battery_power", battery.device_id),
        disabled: !enabled || balancing || notResponding || unreadable || paused,
        detail: paused
          ? t.paused
          : unreadable
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
      if (consumer.show_in_flow === false) continue;
      nodes.push({
        id: `consumer-${consumer.id}`,
        role: "consumer",
        icon: consumer.type === "heat_pump" ? "mdi:heat-pump" : consumer.type === "heating_rod" ? "mdi:water-boiler" : "mdi:power-plug",
        title: consumer.name,
        power: this._powerW(this._hass.states[consumer.power_entity]),
        entity: consumer.power_entity,
        // Temperature of its storage (the sensor chosen for the daily target).
        detail: this._consumerTemperature(consumer),
      });
    }
    return nodes;
  }

  /** "52 °C" for a consumer with temperature sensors, "" otherwise. */
  _consumerTemperature(consumer) {
    const value = this._state("planned_power", consumer.device_id)?.attributes?.temperature_c;
    if (value === null || value === undefined) return "";
    const language = this._hass?.locale?.language || "en";
    return `${new Intl.NumberFormat(language, { maximumFractionDigits: 1 }).format(value)} °C`;
  }

  _renderFlow() {
    const container = this.shadowRoot.getElementById("flow");
    if (!container) return;
    const nodes = this._flowNodes();
    const key = nodes.map((n) => `${n.id}:${n.title}:${n.entity}`).join("|") + this._t.pv;
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
    const moreInfo = (entityId) => (entityId ? ` data-more-info="${entityId}"` : "");
    const box = (n) => `
      <div class="fbox ${n.role}" data-node="${n.id}"${moreInfo(n.entity)}>
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
    const batteryId = this._entityId("battery_power_total");
    const gridId = this._entityId("grid_power");
    const ids = [pvId, houseId, socId, batteryId, gridId].filter(Boolean);
    if (!ids.length) return;
    const start = new Date();
    start.setHours(0, 0, 0, 0);
    try {
      const result = await this._hass.callWS({
        type: "recorder/statistics_during_period",
        start_time: start.toISOString(),
        end_time: new Date().toISOString(),
        statistic_ids: ids,
        period: "5minute",
        types: ["mean"],
        units: { power: "W" },
      });
      // 5 minute means into half hours (slot 0 = 00:00–00:30); ``map`` per sample.
      const bySlot = (rows, map = (v) => v) => {
        const sums = {};
        for (const row of rows || []) {
          if (row.mean === null || row.mean === undefined) continue;
          const start = new Date(row.start);
          const slot = start.getHours() * 2 + Math.floor(start.getMinutes() / 30);
          const entry = (sums[slot] ||= [0, 0]);
          entry[0] += map(row.mean);
          entry[1] += 1;
        }
        return Object.fromEntries(Object.entries(sums).map(([slot, [sum, n]]) => [slot, sum / n]));
      };
      this._stats = {
        pv: bySlot(result[pvId]),
        house: bySlot(result[houseId]),
        soc: bySlot(result[socId]),
        // Battery power is +charge / -discharge; only the charging part.
        charge: bySlot(result[batteryId], (v) => Math.max(0, v)),
        // Grid power is +import / -export; only the export.
        export: bySlot(result[gridId], (v) => Math.max(0, -v)),
      };
      this._sections.daychart = undefined;
      this._queueRender();
    } catch (err) {
      // Statistics are optional for the chart; keep the forecast visible.
      console.debug("SLEMS: statistics not available", err);
    }
  }

  /** Day plans of the real planning: {today, tomorrow} (rows of the backend). */
  _realPlans() {
    const attributes = this._state("feed_in_limit")?.attributes || {};
    return { today: attributes.day_plan || [], tomorrow: attributes.day_plan_tomorrow || [] };
  }

  /**
   * Chart rows of ``plans`` for the selected day; ``compare`` (other plans)
   * adds their state of charge and feed-in as comparison series.
   */
  _chartRows(plans = this._realPlans(), compare = null) {
    const today = this._chartDay === "today";
    const plan = (today ? plans.today : plans.tomorrow) || [];
    const comparePlan = compare ? (today ? compare.today : compare.tomorrow) || [] : [];
    const exportOf = planExportW;
    // Half hours; all values are mean powers (W). The plan rows are hourly
    // (Wh per hour = mean W); PV and the feed-in cap also come per half hour.
    const perHalf = (wh) => (wh === null || wh === undefined ? null : wh * 2);
    return plan.flatMap((row, hour) =>
      [0, 1].map((half) => {
        const slot = hour * 2 + half;
        return {
          slot,
          hour: hour + half / 2,
          pvForecast: row.pv_half_w?.[half] ?? row.pv_wh,
          // Forecasts with one value per hour are drawn at the middle of the hour.
          pvHourly: !row.pv_half_w || row.pv_half_w[0] === row.pv_half_w[1],
          consumptionForecast: row.consumption_wh,
          // Part of it: planned load of the consumers' daily targets.
          consumerForecast: row.consumer_wh || null,
          plannedCharge: row.planned_charge_w,
          // With the feed-in cap the inverter curtails the export above the limit.
          exportForecast: exportOf(row),
          socCompare: !compare
            ? null
            : half
              ? comparePlan[hour]?.soc_pct ?? null
              : midpoint(comparePlan[hour - 1]?.soc_pct, comparePlan[hour]?.soc_pct),
          exportCompare: compare ? exportOf(comparePlan[hour]) : null,
          // Projected total state of charge at the end of the half hour: the
          // projection is hourly, the first half is interpolated (tooltip and
          // table; the line uses the hour ends).
          socForecast: half ? row.soc_pct : midpoint(plan[hour - 1]?.soc_pct, row.soc_pct),
          // Feed-in cap: PV level above which is capped, power above it and the curtailed part.
          capLine: row.cap_line_wh ?? null,
          capExcess: row.cap_excess_half_wh ? perHalf(row.cap_excess_half_wh[half]) : null,
          // Lost above the limit as the projection expects it (batteries full or
          // charging too slowly), split over the half hours like the excess.
          capCurtailed: (() => {
            const lost = row.cap_lost_wh;
            if (lost === undefined || lost === null) {
              return row.cap_curtailed_half_wh ? perHalf(row.cap_curtailed_half_wh[half]) : null;
            }
            const halves = row.cap_excess_half_wh || [];
            const total = (halves[0] || 0) + (halves[1] || 0);
            return perHalf(total > 0 ? (lost * (halves[half] || 0)) / total : lost / 2);
          })(),
          capLostReason: row.cap_lost_reason ?? null,
          // Measured values exist for today only.
          pvActual: today ? this._stats.pv[slot] ?? null : null,
          consumptionActual: today ? this._stats.house[slot] ?? null : null,
          socActual: today ? this._stats.soc[slot] ?? null : null,
          actualCharge: today ? this._stats.charge[slot] ?? null : null,
          exportActual: today ? this._stats.export[slot] ?? null : null,
        };
      })
    );
  }

  /** Where the projected state of charge line starts: [hour of day, %]. */
  _socStart(todayPlan = this._realPlans().today) {
    if (this._chartDay === "today") {
      const now = new Date();
      const soc = this._number(this._state("battery_soc_total"));
      return soc === null ? null : [now.getHours() + now.getMinutes() / 60, soc];
    }
    const last = todayPlan[todayPlan.length - 1]?.soc_pct;
    return last === null || last === undefined ? null : [0, last];
  }

  /**
   * The day chart in section "daychart"; the simulation passes its rows,
   * titles and the plan of today (start of tomorrow's state of charge).
   */
  _renderDayChart(options = {}) {
    const t = this._t;
    const rows = options.rows || this._chartRows();
    this._socStartPlan = options.todayPlan || this._realPlans().today;
    const day = this._chartDay;
    const titles = options.titles || [t.dayChart, t.dayChartTomorrow];
    const header = `
      <div class="chart-head">
        <div><h2>${day === "today" ? titles[0] : titles[1]}</h2><span class="hint">${t.dayChartHint}</span></div>
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
    this._showTooltip();
  }

  _legend(rows) {
    const t = this._t;
    const has = (key) => rows.some((r) => r[key] !== null && r[key] !== undefined && r[key] !== 0);
    // A click on an entry shows or hides the series.
    const item = (key, color, label, style) => {
      if (!has(key)) return "";
      const hidden = this._hiddenSeries.has(key);
      return `<button class="legend-item${hidden ? " off" : ""}" data-action="toggle-series" data-series="${key}" aria-pressed="${!hidden}"><svg width="22" height="10">${
        style === "bar"
          ? `<rect x="4" y="0" width="14" height="10" rx="2" fill="${color}"/>`
          : `<line x1="1" y1="5" x2="21" y2="5" stroke="${color}" stroke-width="2" stroke-linecap="round" ${
              style === "dash" ? 'stroke-dasharray="4 3"' : style === "dot" ? `stroke-dasharray="${COMPARE_DASH}"` : ""
            }/>`
      }</svg>${label}</button>`;
    };
    const c = this._colors;
    return `<div class="legend">
      ${item("pvForecast", c.pv, t.pvForecast, "dash")}${item("pvActual", c.pv, t.pvActual, "solid")}
      ${item("consumptionForecast", c.house, t.consumptionForecast, "dash")}${item("consumptionActual", c.house, t.consumptionActual, "solid")}
      ${item("consumerForecast", c.consumer, t.consumerForecast, "dash")}
      ${item("exportForecast", c.grid, t.exportForecast, "dash")}${item("exportActual", c.grid, t.exportActual, "solid")}
      ${item("plannedCharge", `${c.battery}66`, t.plannedCharge, "bar")}${item("actualCharge", c.battery, t.actualCharge, "bar")}
      ${item("socForecast", c.battery, t.socForecast, "dash")}${item("socActual", c.battery, t.socActual, "solid")}
      ${item("exportCompare", c.muted, t.exportCompare, "dot")}${item("socCompare", c.muted, t.socCompare, "dot")}
      ${item("capLine", c.muted, t.capLine, "dash")}${item("capExcess", `${c.pv}73`, t.capExcess, "bar")}
      ${item("capCurtailed", CURTAILED_COLOR, t.capCurtailed, "bar")}</div>`;
  }

  _chartSvg(rows) {
    const c = this._colors;
    // Draw in real pixels so text keeps its size on narrow screens.
    const available = this.shadowRoot.getElementById("daychart")?.clientWidth || 720;
    const width = Math.max(280, Math.round(available));
    const height = width < 500 ? 220 : 260;
    // The total state of charge is drawn on top with its own scale (0–100 %) on the right.
    const hasSoc = rows.some((r) => r.socForecast !== null && r.socForecast !== undefined) ||
      rows.some((r) => r.socCompare !== null && r.socCompare !== undefined) ||
      rows.some((r) => r.socActual !== null && r.socActual !== undefined);
    const pad = { left: 52, right: hasSoc ? 46 : 22, top: 10, bottom: 26 };
    const plotW = width - pad.left - pad.right;
    const plotH = height - pad.top - pad.bottom;
    const ys = (value) => pad.top + plotH - (Math.max(0, Math.min(100, value)) / 100) * plotH;
    const plotBottom = pad.top + plotH;
    const shown = (key) => !this._hiddenSeries.has(key);
    // The scale follows the series shown.
    const scaled = [
      "pvForecast", "consumptionForecast", "consumerForecast", "plannedCharge", "actualCharge", "pvActual", "consumptionActual",
      "exportForecast", "exportActual", "exportCompare",
    ].filter(shown);
    const values = rows.flatMap((r) => scaled.map((key) => r[key]));
    // The PV limit of the feed-in cap is always drawn completely (hide it in
    // the legend to get a smaller scale), the energy above it on top.
    if (shown("capLine")) for (const r of rows) if (r.capLine !== null && r.capLine !== undefined) values.push(r.capLine);
    if (shown("capExcess") || shown("capCurtailed")) {
      for (const r of rows) if (r.capExcess > 0) values.push(r.capLine + r.capExcess);
    }
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
      const start = this._socStart(this._socStartPlan);
      // Forecast points at the end of each hour (second half hour rows).
      const hourEnds = rows.filter((r) => r.slot % 2 === 1);
      const forecast = runs(hourEnds, "socForecast").map((run) => run.map((r) => [x(r.hour + 0.5), ys(r.socForecast)]));
      if (start && forecast.length) forecast[0].unshift([x(start[0]), ys(start[1])]);
      const actual = runs(rows, "socActual").map((run) => run.map((r) => [x(r.hour + 0.25), ys(r.socActual)]));
      // The comparison first, so the simulation is drawn on top.
      if (shown("socCompare")) {
        const compare = runs(hourEnds, "socCompare").map((run) => run.map((r) => [x(r.hour + 0.5), ys(r.socCompare)]));
        socLines.push(polylines(compare, c.muted, COMPARE_DASH));
      }
      if (shown("socForecast")) socLines.push(polylines(forecast, c.battery, true));
      if (shown("socActual")) socLines.push(polylines(actual, c.battery, false));
    }

    const gridLines = [];
    for (let v = 0; v <= top; v += step) {
      gridLines.push(`<line x1="${pad.left}" x2="${width - pad.right}" y1="${y(v)}" y2="${y(v)}" stroke="${v === 0 ? c.axis : c.grid_line}" stroke-width="1"/>
        <text x="${pad.left - 6}" y="${y(v) + 4}" text-anchor="end" class="tick">${escapeHtml(this._watts(v))}</text>`);
    }
    const labelled = width < 500 ? [0, 6, 12, 18, 24] : [0, 3, 6, 9, 12, 15, 18, 21, 24];
    const hourTicks = labelled
      .map((h) => `<text x="${x(h)}" y="${height - 8}" text-anchor="middle" class="tick">${String(h).padStart(2, "0")}:00</text>`)
      .join("");
    // A faint vertical line per hour (solid where labelled) and a mark on the time axis.
    const hourLines = Array.from({ length: 25 }, (_, h) => {
      const solid = labelled.includes(h);
      return `<line x1="${x(h)}" x2="${x(h)}" y1="${pad.top}" y2="${plotBottom}" stroke="${c.grid_line}" stroke-width="1"${
        solid ? "" : ' stroke-dasharray="2 4"'
      }/><line x1="${x(h)}" x2="${x(h)}" y1="${plotBottom}" y2="${plotBottom + 4}" stroke="${c.axis}" stroke-width="1"/>`;
    }).join("");
    const slot = plotW / 48;
    // Per half hour the planned charging (light) on the left, the measured one on the right.
    const gap = slot > 8 ? 1 : 0.5;
    const barW = Math.max(1, slot / 2 - gap);
    const bar = (r, key, offset, color) => {
      const h = Math.max(1, plotH - (y(r[key]) - pad.top));
      return `<path d="${roundedTopBar(x(r.hour) + offset, y(r[key]), barW, h)}" fill="${color}"/>`;
    };
    const bars = rows
      .map((r) =>
        (r.plannedCharge > 0 && shown("plannedCharge") ? bar(r, "plannedCharge", gap / 2, `${c.battery}66`) : "") +
        (r.actualCharge > 0 && shown("actualCharge") ? bar(r, "actualCharge", slot / 2 + gap / 2, c.battery) : "")
      )
      .join("");
    // Hourly values (both halves equal) get one point in the middle of the hour
    // instead of two, so the line has no steps.
    const hourlyKeys = {
      consumptionForecast: () => true,
      consumerForecast: () => true,
      capLine: () => true,
      exportForecast: () => true,
      exportCompare: () => true,
      pvForecast: (r) => r.pvHourly,
    };
    const path = (key, color, dash) => {
      if (!shown(key)) return "";
      const hourly = hourlyKeys[key] || (() => false);
      const points = rows.filter((r) => !hourly(r) || r.slot % 2 === 0);
      return polylines(
        runs(points, key).map((run) => run.map((r) => [x(r.hour + (hourly(r) ? 0.5 : 0.25)), y(r[key])])),
        color,
        dash
      );
    };
    // Energy above the feed-in limit as a bar on the limit line, the curtailed part on top.
    const capBars = rows
      .filter((r) => r.capExcess > 0 && (shown("capExcess") || shown("capCurtailed")))
      .map((r) => {
        const capW = Math.max(1, slot - 2 * gap);
        const base = y(r.capLine);
        const top = y(r.capLine + r.capExcess);
        const curtailed = r.capCurtailed > 0 ? y(r.capLine + r.capExcess - r.capCurtailed) : top;
        return `${shown("capExcess") ? `<rect x="${x(r.hour) + gap}" y="${top}" width="${capW}" height="${Math.max(1, base - top)}" fill="${c.pv}" fill-opacity="0.45"/>` : ""}${
          curtailed > top && shown("capCurtailed") ? `<rect x="${x(r.hour) + gap}" y="${top}" width="${capW}" height="${curtailed - top}" fill="${CURTAILED_COLOR}"/>` : ""
        }`;
      })
      .join("");
    const capLine = !shown("capLine") ? "" : polylines(
      runs(
        rows.filter((r) => r.slot % 2 === 0),
        "capLine"
      ).map((run) => run.map((r) => [x(r.hour + 0.5), y(r.capLine)])),
      c.muted,
      true
    );
    const now = new Date();
    const nowHour = now.getHours() + now.getMinutes() / 60;
    const showNow = this._chartDay === "today";
    return `
      <div class="chart-wrap"><svg viewBox="0 0 ${width} ${height}" width="${width}" height="${height}" class="chart" role="img" aria-label="${this._t.dayChart}">
        ${hourLines}${gridLines.join("")}${hourTicks}${bars}${capBars}${capLine}
        ${path("pvForecast", c.pv, true)}${path("pvActual", c.pv, false)}
        ${path("consumptionForecast", c.house, true)}${path("consumptionActual", c.house, false)}
        ${path("consumerForecast", c.consumer, true)}
        ${path("exportCompare", c.muted, COMPARE_DASH)}${path("exportForecast", c.grid, true)}${path("exportActual", c.grid, false)}
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
    const cap = rows.some((r) => r.capLine !== null && r.capLine !== undefined);
    return `<div class="table-wrap"><table>
      <thead><tr><th>${t.hour}</th><th>${t.pvForecast}</th><th>${t.pvActual}</th><th>${t.consumptionForecast}</th>
      <th>${t.consumptionActual}</th><th>${t.consumerForecast}</th><th>${t.exportForecast}</th><th>${t.exportActual}</th><th>${t.plannedCharge}</th><th>${t.actualCharge}</th><th>${t.socForecast}</th><th>${t.socActual}</th>
      ${cap ? `<th>${t.capLine}</th><th>${t.capExcess}</th><th>${t.capCurtailed}</th>` : ""}</tr></thead>
      <tbody>${rows
        .map(
          (r) => `<tr><td>${slotTime(r.slot)}</td><td>${cell(r.pvForecast)}</td><td>${cell(r.pvActual)}</td>
          <td>${cell(r.consumptionForecast)}</td><td>${cell(r.consumptionActual)}</td><td>${cell(r.consumerForecast)}</td><td>${cell(r.exportForecast)}</td><td>${cell(r.exportActual)}</td><td>${cell(r.plannedCharge)}</td><td>${cell(r.actualCharge)}</td>
          <td>${percent(r.socForecast)}</td><td>${percent(r.socActual)}</td>${
            cap ? `<td>${cell(r.capLine)}</td><td>${cell(r.capExcess)}</td><td>${cell(r.capCurtailed)}</td>` : ""
          }</tr>`
        )
        .join("")}</tbody></table></div>`;
  }

  _onChartHover(event) {
    const svg = event.target.closest?.("svg.chart");
    if (!svg || !this._chartData || !this._chartGeometry) return;
    const { pad, plotW, width } = this._chartGeometry;
    const rect = svg.getBoundingClientRect();
    const viewX = ((event.clientX - rect.left) / rect.width) * width;
    // Kept, so the tooltip survives a redraw of the chart with new data.
    this._tooltipAt = { slot: Math.floor(((viewX - pad.left) / plotW) * 48), clientY: event.clientY };
    this._showTooltip();
  }

  _showTooltip() {
    const svg = this.shadowRoot.querySelector("svg.chart");
    if (!svg || !this._tooltipAt || !this._chartData || !this._chartGeometry) return;
    const { pad, plotW, width } = this._chartGeometry;
    const rect = svg.getBoundingClientRect();
    const { slot, clientY } = this._tooltipAt;
    const row = this._chartData[slot];
    const tooltip = this.shadowRoot.getElementById("tooltip");
    const crosshair = this.shadowRoot.getElementById("crosshair");
    if (!row || !tooltip) {
      this._hideTooltip();
      return;
    }
    const cx = pad.left + ((slot + 0.5) / 48) * plotW;
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
    // Series hidden in the legend are left out.
    const v = (key) => (this._hiddenSeries.has(key) ? null : row[key]);
    tooltip.innerHTML = `<div class="tt-title">${slotTime(slot)}–${slotTime(slot + 1)}</div>
      ${entry(c.pv, t.pvForecast, v("pvForecast"))}${entry(c.pv, t.pvActual, v("pvActual"))}
      ${entry(c.house, t.consumptionForecast, v("consumptionForecast"))}${entry(c.house, t.consumptionActual, v("consumptionActual"))}
      ${entry(c.consumer, t.consumerForecast, v("consumerForecast"))}
      ${entry(c.grid, t.exportForecast, v("exportForecast"))}${entry(c.grid, t.exportActual, v("exportActual"))}
      ${entry(c.battery, t.plannedCharge, v("plannedCharge"))}${entry(c.battery, t.actualCharge, v("actualCharge"))}
      ${percentEntry(c.battery, t.socForecast, v("socForecast"))}${percentEntry(c.battery, t.socActual, v("socActual"))}
      ${entry(c.muted, t.exportCompare, v("exportCompare"))}${percentEntry(c.muted, t.socCompare, v("socCompare"))}
      ${v("capExcess") > 0 ? entry(c.pv, t.capExcess, v("capExcess")) : ""}${
        v("capCurtailed") > 0
          ? entry(
              CURTAILED_COLOR,
              row.capLostReason ? `${t.capCurtailed} (${t.capLostReasons[row.capLostReason]})` : t.capCurtailed,
              v("capCurtailed")
            )
          : ""
      }`;
    tooltip.hidden = false;
    // Position relative to the chart card, next to the cursor.
    const card = this.shadowRoot.getElementById("daychart").getBoundingClientRect();
    const left = (cx / width) * rect.width + (rect.left - card.left);
    const flip = left > card.width * 0.6;
    const top = Math.min(clientY - card.top + 12, card.height - tooltip.offsetHeight - 8);
    tooltip.style.left = `${flip ? left - tooltip.offsetWidth - 12 : left + 12}px`;
    tooltip.style.top = `${Math.max(0, top)}px`;
  }

  _hideTooltip() {
    this._tooltipAt = null;
    const tooltip = this.shadowRoot?.getElementById("tooltip");
    if (tooltip) tooltip.hidden = true;
    this.shadowRoot?.getElementById("crosshair")?.setAttribute("visibility", "hidden");
  }

  // --- simulation ------------------------------------------------------------------
  //
  // Day plans with other settings, calculated by SLEMS (slems/simulate) from
  // the current state of charge and forecasts. Nothing is stored or used by
  // the control; the values start with the real settings on every visit.

  async _simulate() {
    if (!this._hass) return;
    const sim = this._sim;
    const request = { type: `${DOMAIN}/simulate` };
    if (sim.form) Object.assign(request, JSON.parse(JSON.stringify(sim.form)));
    const serial = ++sim.serial;
    try {
      const result = await this._hass.callWS(request);
      if (serial !== sim.serial) return;
      sim.result = result;
      if (!sim.form) sim.form = this._simBaseForm(result.base);
    } catch (err) {
      console.error("SLEMS: simulation failed", err);
      sim.result = { available: false };
    }
    this._sections.daychart = undefined;
    this._sections.simcontrols = undefined;
    this._queueRender();
  }

  _simBaseForm(base) {
    return {
      settings: { ...(base?.settings || {}) },
      battery: { ...(base?.battery || {}) },
      pv_pct: 0,
      consumption_pct: 0,
    };
  }

  _simSchedule() {
    clearTimeout(this._sim.timer);
    this._sim.timer = setTimeout(() => this._simulate(), 250);
  }

  _renderSimulation() {
    const t = this._t;
    const sim = this._sim;
    if (!sim.result) {
      if (!sim.requested) {
        sim.requested = true;
        this._simulate();
      }
      this._setSection("daychart", `<p class="empty">…</p>`);
      return;
    }
    this._setSection("simintro", `<p class="hint">${escapeHtml(t.simIntro)}</p>`);
    this._setSection("simcontrols", this._simControls());
    if (!sim.result.available) {
      this._setSection("daychart", `<p class="empty">${t.noData}</p>`);
      this._setSection("simmetrics", "");
      return;
    }
    // In the afternoon most of today is already measured, not simulated.
    const afternoon = this._chartDay === "today" && new Date().getHours() >= 12;
    this._setSection(
      "simnote",
      afternoon
        ? `<div class="info-box sim-note"><ha-icon icon="mdi:information-outline"></ha-icon><span>${escapeHtml(t.simAfternoon)}</span>
            <button class="link" data-action="day-tomorrow">${escapeHtml(t.simShowTomorrow)}</button></div>`
        : ""
    );
    const plans = { today: sim.result.day_plan, tomorrow: sim.result.day_plan_tomorrow };
    this._fetchStats(false);
    this._renderDayChart({
      rows: this._chartRows(plans, this._realPlans()),
      titles: [t.simToday, t.simTomorrow],
      todayPlan: plans.today,
    });
    this._setSection("simmetrics", this._simMetrics());
  }

  _simMetrics() {
    const t = this._t;
    const day = this._chartDay;
    const real = this._sim.result.real_metrics?.[day];
    const simulated = this._sim.result.metrics?.[day];
    if (!real || !simulated) return "";
    const kwh = (v) => this._kwh((v ?? 0) * 1000);
    const watts = (v) => this._watts(v ?? 0);
    const percent = (v) => (v === null || v === undefined ? "–" : this._percent(v));
    const rows = [
      [t.mExport, "export_kwh", kwh],
      [t.mImport, "import_kwh", kwh],
      [t.mMaxImport, "max_import_w", watts],
      [t.mMaxExport, "max_export_w", watts],
      [t.mCurtailed, "curtailed_kwh", kwh],
      [t.mSocEnd, "soc_end_pct", percent],
    ];
    return `<h3>${escapeHtml(day === "today" ? t.simMetricsToday : t.simMetricsTomorrow)}</h3>
      <div class="table-wrap"><table class="sim-metrics">
      <thead><tr><th></th><th>${escapeHtml(t.simCurrent)}</th><th>${escapeHtml(t.simSimulated)}</th></tr></thead>
      <tbody>${rows
        .map(([label, key, format]) => {
          const changed = real[key] !== simulated[key];
          return `<tr><td>${escapeHtml(label)}</td><td>${escapeHtml(format(real[key]))}</td>
            <td class="${changed ? "changed" : ""}">${escapeHtml(format(simulated[key]))}</td></tr>`;
        })
        .join("")}</tbody></table></div>`;
  }

  /** Input for a simulation value; entity keys take name, unit and limits from the real setting. */
  _simField(path, { entity, label, unit, min, max, step, disabled = false } = {}) {
    const t = this._t;
    const [group, key] = path.includes(".") ? path.split(".") : [null, path];
    const form = this._sim.form;
    const value = group ? form[group]?.[key] : form[key];
    const stateObj = entity ? this._state(entity) : null;
    const a = stateObj?.attributes || {};
    const name = label ?? (stateObj ? this._name(stateObj) : key);
    const hint = entity ? t.settingHints[entity] : null;
    const info = hint
      ? `<button class="info" data-action="toggle-hint" data-key="${entity}" title="${escapeHtml(hint)}" aria-label="${escapeHtml(hint)}"><ha-icon icon="mdi:information-outline"></ha-icon></button>${
          this._openHints.has(entity) ? `<span class="setting-hint">${escapeHtml(hint)}</span>` : ""
        }`
      : "";
    if (typeof value === "boolean") {
      return `<div class="setting"><span>${escapeHtml(name)}${info}</span><label class="switch">
        <input type="checkbox" data-sim="${path}" data-kind="sim-bool" aria-label="${escapeHtml(name)}" ${value ? "checked" : ""}${disabled ? " disabled" : ""}><span></span></label></div>`;
    }
    const stepValue = step ?? a.step ?? 1;
    const decimals = String(stepValue).split(".")[1]?.length ?? 0;
    const shown = Number.isFinite(value) ? Number(value).toFixed(decimals) : "";
    return `<div class="setting"><span>${escapeHtml(name)}${info}</span><span class="number">
      <input type="number" data-sim="${path}" data-kind="sim-number" aria-label="${escapeHtml(name)}" value="${escapeHtml(shown)}"
        min="${min ?? a.min ?? ""}" max="${max ?? a.max ?? ""}" step="${stepValue}"${disabled ? " disabled" : ""}><span class="unit">${escapeHtml(unit ?? a.unit_of_measurement ?? "")}</span></span></div>`;
  }

  _simControls() {
    const t = this._t;
    const s = this._sim.form?.settings || {};
    const field = (path, options) => this._simField(path, options);
    const setting = (key, entity, options = {}) => field(`settings.${key}`, { entity, ...options });
    const group = (title, body) => `<div class="sim-group"><h3>${escapeHtml(title)}</h3><div class="settings">${body}</div></div>`;
    const option = (title, switchKey, switchEntity, children) =>
      group(title, setting(switchKey, switchEntity) + (s[switchKey] ? children : ""));
    return `
      <div class="sim-actions"><button class="link" data-action="sim-reset">${escapeHtml(t.simReset)}</button></div>
      ${group(
        t.simForecast,
        field("pv_pct", { label: t.simPv, unit: "%", min: -90, max: 100, step: 5 }) +
          field("consumption_pct", { label: t.simConsumption, unit: "%", min: -90, max: 200, step: 5 })
      )}
      ${group(
        t.simBatteries,
        field("battery.capacity_kwh", { label: t.simCapacity, unit: "kWh", min: 0.5, max: 500, step: 0.1 }) +
          field("battery.min_soc_pct", { label: t.simMinSoc, unit: "%", min: 0, max: 100, step: 1 }) +
          field("battery.max_soc_pct", { label: t.simMaxSoc, unit: "%", min: 0, max: 100, step: 1 }) +
          field("battery.max_charge_w", { label: t.simChargePower, unit: "W", min: 0, max: 100000, step: 100 }) +
          field("battery.max_discharge_w", { label: t.simDischargePower, unit: "W", min: 0, max: 100000, step: 100 })
      )}
      ${group(
        t.groups.priority,
        setting("charge_secured_buffer_kwh", "charge_secured_buffer") +
          setting("discharge_max_grid_export_w", "discharge_max_grid_export", { max: 100000 })
      )}
      ${option(t.groups.gridFriendly, "grid_friendly_charging", "grid_friendly_charging",
        setting("grid_friendly_buffer_kwh", "grid_friendly_buffer"))}
      ${option(t.groups.night, "night_discharge", "night_discharge", setting("night_reserve_pct", "night_reserve"))}
      ${option(t.groups.peak, "peak_shaving", "peak_shaving",
        setting("peak_shaving_auto", "peak_shaving_auto") +
          setting("peak_shaving_grid_limit_w", "peak_shaving_grid_limit", { disabled: s.peak_shaving_auto }) +
          setting("peak_shaving_soc_threshold_pct", "peak_shaving_soc_threshold", { min: 0 }) +
          (s.peak_shaving_auto ? setting("peak_shaving_reserve_pct", "peak_shaving_reserve") : ""))}
      ${option(t.groups.feedInCap, "feed_in_cap", "feed_in_cap",
        setting("pv_peak_power_kwp", "pv_peak_power") +
          setting("feed_in_cap_limit_pct", "feed_in_cap_limit") +
          setting("feed_in_cap_auto_buffer", "feed_in_cap_auto_buffer") +
          setting("feed_in_cap_buffer_pct", "feed_in_cap_buffer", { disabled: s.feed_in_cap_auto_buffer }))}`;
  }

  _onSimChange(target) {
    const path = target.dataset.sim;
    const [group, key] = path.includes(".") ? path.split(".") : [null, path];
    const form = this._sim.form;
    let value;
    if (target.dataset.kind === "sim-bool") value = target.checked;
    else {
      value = parseFloat(target.value);
      if (!Number.isFinite(value)) return;
    }
    if (group) form[group][key] = value;
    else form[key] = value;
    this._sections.simcontrols = undefined;
    this._render();
    this._simSchedule();
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
          const direction = (value) => {
            if (value === null || value === undefined) return undefined;
            const text = value > 10 ? t.charging : value < -10 ? t.discharging : t.idle;
            return { text: `${this._watts(Math.abs(value))} (${text})` };
          };
          const rows = [
            [t.storedEnergy, s("stored_energy") && { text: this._storedOf(s("stored_energy")) ?? this._format(s("stored_energy")) }, s("stored_energy")?.entity_id],
            [t.powerGridSide, direction(this._batteryPower(b.device_id)), (s("ac_power") || s("battery_power"))?.entity_id],
            [t.setPoint, direction(this._number(s("planned_power"))), s("planned_power")?.entity_id],
            [t.efficiency, s("round_trip_efficiency"), s("round_trip_efficiency")?.entity_id],
            [t.state, s("inverter_state"), s("inverter_state")?.entity_id],
            [t.temperature, s("internal_temperature"), s("internal_temperature")?.entity_id],
          ].filter(([, st]) => st);
          let problem = "";
          const pause = s("communication_paused");
          if (pause?.state === "on") {
            const until = pause.attributes.paused_until
              ? new Date(pause.attributes.paused_until).toLocaleTimeString(this._hass.locale?.language, { hour: "2-digit", minute: "2-digit" })
              : "–";
            const text = (pause.attributes.reason === "firmware_update" ? t.pausedFirmware : t.pausedManual).replace("{time}", until);
            problem = `<div class="info-box"><ha-icon icon="mdi:pause-circle-outline"></ha-icon><span>${escapeHtml(text)}</span>
              <button class="link" data-action="menu-resume" data-entity="${pause.entity_id}">${t.resume}</button></div>`;
          } else if (s("battery_soc")?.state === "unavailable") {
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
              <div class="chips">${enabled?.state === "off" ? `<span class="chip">${t.disabled}</span>` : ""}${
                s("last_full_charge")?.attributes?.preferred ? `<span class="chip" title="${escapeHtml(t.fullChargeDueHint)}">${t.fullChargeDue}</span>` : ""
              }${this._batteryMenu(b)}</div></div>
            ${problem}
            <div class="soc"><div class="soc-bar"><div style="width:${soc ?? 0}%"></div></div>
              <span>${soc === null ? "–" : Math.round(soc) + " %"}</span></div>
            <dl>${rows.map(([label, st, entityId]) => this._row(label, escapeHtml(st.text ?? this._format(st)), entityId)).join("")}</dl>
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
    const blocked = this._balancingBlocked(battery.device_id);
    const disabled = blocked ? ` disabled title="${escapeHtml(blocked)}"` : "";
    const hint = blocked ? `<span class="muted balancing-hint">${blocked}</span>` : "";
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
          ${hint}<button class="primary" data-action="balancing-on" data-entity="${switchState.entity_id}" data-name="${escapeHtml(battery.name)}" data-delta="${escapeHtml(top?.state ?? "")}"${disabled}>${t.startBalancing}</button>
        </div>`;
    }
    const result = t.balancingResults[phase?.attributes?.last_result];
    const lastRun = !balancing && result ? `<dt>${t.lastBalancing}</dt><dd>${escapeHtml(result)}</dd>` : "";
    return `<div class="cells">
        <dl>
          ${this._row(`${t.cellDelta}<span class="sub">${t.cellDeltaHint}</span>`, escapeHtml(this._format(live)), live?.entity_id)}
          ${this._row(t.topCellDelta, topValue, top?.entity_id)}
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
          // Only while the feed-in cap is on.
          const capMode = this._state("cap_mode", c.device_id);
          const learning = this._state("consumer_learning", c.device_id);
          const learnedPower =
            this._state("learned_power", c.device_id) || this._state("learned_max_power", c.device_id);
          const settings = [learning, learning?.state === "on" ? learnedPower : null].filter(Boolean);
          const capOff = control?.state === "off";
          const storage = this._state("storage_capacity", c.device_id);
          const capSection = capMode && this._capState()
            ? `<div class="settings card-setting"><h3 class="card-subheading${capOff ? " disabled" : ""}">${t.capSection}</h3>${this._control(
                capMode, { label: t.capRole, disabledNote: capOff ? t.onlyWithControl : null }
              )}${storage ? `<dl>${this._row(t.storageCapacity, escapeHtml(this._storageText(storage)), storage.entity_id)}</dl>` : ""}</div>`
            : "";
          const chips = [
            !c.controllable ? t.notControlled : "",
            control?.state === "off" ? t.controlOff : "",
            attrs.blocked ? t.blocked : "",
            attrs.saturated ? t.saturated : "",
            attrs.resting ? t.resting : "",
            attrs.target_mode === "boost" ? t.targetBoostChip : "",
            attrs.target_mode === "forced" ? t.targetForcedChip : "",
          ]
            .filter(Boolean)
            .map((chip) => `<span class="chip">${chip}</span>`)
            .join("");
          const seconds = (v) =>
            v === null || v === undefined
              ? "–"
              : `${new Intl.NumberFormat(this._hass?.locale?.language || "en", { maximumFractionDigits: 1 }).format(v)} s`;
          // Switching on (with the start delay of the device) / off.
          const response = `${seconds(attrs.response_on_s)} / ${seconds(attrs.response_off_s)}`;
          const gridResponse = `${seconds(attrs.grid_response_on_s)} / ${seconds(attrs.grid_response_off_s)}`;
          // Collapsed: the important values; expanded: all settings too.
          const expanded = this._expandedConsumers.has(c.id);
          const targetType = this._state("target_type", c.device_id)?.state;
          const progress =
            c.controllable && targetType && targetType !== "none"
              ? this._row(t.targetProgress, escapeHtml(this._targetText(attrs, this._state("target_source", c.device_id)?.state)), planned?.entity_id)
              : "";
          const details = expanded
            ? `<dl>
              ${c.controllable ? this._row(t.responseTime, response, planned?.entity_id) : ""}
              ${c.controllable ? this._row(t.gridResponseTime, gridResponse, planned?.entity_id) : ""}
            </dl>${
              settings.length ? `<div class="settings card-setting">${settings.map((st) => this._control(st)).join("")}</div>` : ""
            }${capSection}${c.controllable ? this._targetSection(c) : ""}`
            : "";
          const toggle = c.controllable
            ? `<button class="link card-toggle" data-action="toggle-consumer" data-id="${c.id}" aria-expanded="${expanded}">
                <ha-icon icon="${expanded ? "mdi:chevron-up" : "mdi:chevron-down"}"></ha-icon>${expanded ? t.hideSettings : t.showSettings}</button>`
            : "";
          return `<section class="card">
            <div class="card-head"><h2>${escapeHtml(c.name)}</h2><div class="chips">${chips}${control ? this._toggle(control, t.controlActive) : ""}</div></div>
            <dl>
              ${this._row(t.measured, escapeHtml(this._format(measured)), c.power_entity)}
              ${planned ? this._row(t.planned, escapeHtml(this._format(planned)), planned.entity_id) : ""}
              ${progress}
            </dl>${details}${toggle}</section>`;
        })
        .join("")
    );
  }

  /** Settings of a consumer's daily target (only the ones of its kind). */
  _targetSection(c) {
    const t = this._t;
    const s = (key) => this._state(key, c.device_id);
    const kind = s("target_type");
    if (!kind) return "";
    const type = kind.state;
    const controls = [[kind, t.targetKind]];
    if (type === "runtime" || type === "enabled") controls.push([s("target_hours"), t.targetHours]);
    if (type === "energy") controls.push([s("target_energy"), t.targetEnergy]);
    if (type === "temperature") {
      controls.push(
        [s("target_sensor"), t.targetSensor],
        [s("target_min_temperature"), t.targetMin],
        [s("target_max_temperature"), t.targetMax]
      );
    }
    if (type !== "none" && type !== "temperature") {
      const earliest = s("target_earliest_enabled");
      controls.push([earliest, t.targetEarliestOn]);
      if (earliest?.state === "on") controls.push([s("target_earliest"), t.targetEarliest]);
    }
    if (type !== "none") controls.push([s("target_deadline"), t.targetDeadline], [s("target_source"), t.targetSource]);
    if (type !== "none" && type !== "temperature") controls.push([s("target_priority"), t.targetPriority]);
    return `<div class="settings card-setting"><h3 class="card-subheading">${t.targetSection}</h3>${controls
      .filter(([st]) => st)
      .map(([st, label]) => this._control(st, { label }))
      .join("")}</div>`;
  }

  /** "2,5 / 4 h · bis 22:00 · erzwungen ab 19:30" or "43 °C · min. 40 °C · Ziel 55 °C". */
  _targetText(attrs, source) {
    const t = this._t;
    const language = this._hass?.locale?.language || "en";
    const number = (v) => new Intl.NumberFormat(language, { maximumFractionDigits: 1 }).format(v);
    const clock = (iso) =>
      iso ? new Date(iso).toLocaleTimeString(language, { hour: "2-digit", minute: "2-digit" }) : "–";
    const mode = attrs.target_mode;
    const parts = [];
    if (attrs.target_type === "temperature") {
      const temperature = attrs.target_temperature_c;
      parts.push(temperature === null || temperature === undefined ? "– °C" : `${number(temperature)} °C`);
      parts.push(`${t.targetMinShort} ${number(attrs.target_goal)} °C`, `${t.targetGoalTemp} ${number(attrs.target_max_temperature_c)} °C`);
    } else {
      parts.push(`${number(attrs.target_got ?? 0)} / ${number(attrs.target_goal)} ${attrs.target_unit}`);
    }
    parts.push(t.targetUntil.replace("{time}", clock(attrs.target_deadline)));
    const energy = attrs.target_energy_wh;
    if (mode !== "done" && energy > 0) parts.push(t.targetEnergy.replace("{energy}", `${number(energy / 1000)} kWh`));
    else if (mode !== "done" && attrs.target_type === "temperature" && (energy === null || energy === undefined)) {
      parts.push(t.targetEnergyLearning);
    }
    if (mode === "done") parts.push(t.targetDone);
    else if (mode === "waiting") parts.push(t.targetWaiting.replace("{time}", clock(attrs.target_earliest)));
    else if (mode === "forced") parts.push(t.targetForced);
    else if (source !== "surplus" && attrs.target_latest_start) parts.push(t.targetLatest.replace("{time}", clock(attrs.target_latest_start)));
    if (mode === "boost") parts.push(t.targetBoost);
    return parts.join(" · ");
  }

  // --- settings ----------------------------------------------------------------

  _renderSettings() {
    const t = this._t;
    this._setSection(
      "settings-list",
      SETTING_GROUPS.map(([group, keys]) => {
        const rows = keys
          .filter((key) => !SETTING_SHOWN_WITH[key] || this._state(SETTING_SHOWN_WITH[key])?.state === "on")
          .map((key) => this._state(key))
          .filter(Boolean);
        if (!rows.length) return "";
        return `<section class="card"><h2>${t.groups[group]}</h2>
          <div class="settings">${rows.map((s) => this._control(s)).join("")}</div>${
            group === "priority"
              ? this._exportNote()
              : group === "peak"
                ? this._peakNote()
                : group === "feedInCap"
                  ? this._capNote()
                  : ""
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

  /** Warning when little energy is left above the minimum SoC for peak shaving. */
  _peakNote() {
    const threshold = this._number(this._state("peak_shaving_soc_threshold"));
    const limit = this._state("peak_shaving_limit");
    const minSoc = limit?.attributes?.min_soc_pct;
    const capacity = limit?.attributes?.capacity_kwh;
    if (threshold === null || minSoc === undefined || threshold >= PEAK_SHAVING_NOTE_PCT) return "";
    const usable = Math.max(0, threshold - minSoc);
    const language = this._hass?.locale?.language || "en";
    const kwh = capacity ? new Intl.NumberFormat(language, { maximumFractionDigits: 1 }).format((usable / 100) * capacity) : "–";
    const text = this._t.peakShavingNote
      .replace("{usable}", this._percent(usable))
      .replace("{min}", this._percent(minSoc))
      .replace("{kwh}", kwh);
    return `<p class="setting-note"><ha-icon icon="mdi:information-outline"></ha-icon>${escapeHtml(text)}</p>`;
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
      <input type="checkbox" data-entity="${stateObj.entity_id}" data-kind="switch" aria-label="${escapeHtml(label)}"${confirm} ${stateObj.state === "on" ? "checked" : ""}>
      <span></span></label>`;
  }

  // label: instead of the entity name; disabledNote: the control is shown
  // greyed out with this note.
  _control(stateObj, { label = null, disabledNote = null } = {}) {
    const domain = stateObj.entity_id.split(".")[0];
    const key = this._hass.entities?.[stateObj.entity_id]?.translation_key;
    const hint = this._t.settingHints[key];
    // Title for the mouse, a click on the icon opens the text (touch screens).
    const title = label ?? this._name(stateObj);
    let name = hint
      ? `${escapeHtml(title)}<button class="info" data-action="toggle-hint" data-key="${key}" title="${escapeHtml(hint)}" aria-label="${escapeHtml(hint)}"><ha-icon icon="mdi:information-outline"></ha-icon></button>${
          this._openHints.has(key) ? `<span class="setting-hint">${escapeHtml(hint)}</span>` : ""
        }`
      : escapeHtml(title);
    if (domain === "switch") {
      return `<div class="setting"><span>${name}</span>${this._toggle(stateObj, title)}</div>`;
    }
    const learned = LEARNED_SETTINGS[key];
    if (learned && this._state(learned[0])?.state === "on") {
      const value = this._state("learned_values")?.attributes?.[learned[1]];
      const unit = stateObj.attributes.unit_of_measurement || "";
      if (value !== null && value !== undefined) {
        const language = this._hass?.locale?.language || "en";
        const text = `${new Intl.NumberFormat(language, { maximumFractionDigits: 2 }).format(value)} ${unit}`.trim();
        return `<div class="setting"><span>${name}</span><span class="readonly">${escapeHtml(text)} (${this._t.learned})</span></div>`;
      }
      // Not enough data yet: the set value applies and stays editable.
      name += `<span class="setting-hint">${escapeHtml(this._t.learnedWaiting)}</span>`;
    }
    if (key === "peak_shaving_grid_limit" && this._state("peak_shaving_auto")?.state === "on") {
      const effective = this._state("peak_shaving_limit");
      return `<div class="setting"><span>${name}</span><span class="readonly">${escapeHtml(
        effective ? this._format(effective) : "–"
      )} (${this._t.automatic})</span></div>`;
    }
    if (domain === "time") {
      const value = (stateObj.state || "").slice(0, 5);
      return `<div class="setting"><span>${name}</span><input type="time" data-entity="${stateObj.entity_id}" data-kind="time" aria-label="${escapeHtml(title)}" value="${escapeHtml(value)}"></div>`;
    }
    if (domain === "sensor") {
      return `<div class="setting"><span>${name}</span><span class="readonly">${escapeHtml(this._format(stateObj))}</span></div>`;
    }
    if (domain === "select") {
      const options = stateObj.attributes.options || [];
      const label = (option) =>
        this._hass.formatEntityState ? this._hass.formatEntityState(stateObj, option) : option;
      if (disabledNote) name += `<span class="setting-hint">${escapeHtml(disabledNote)}</span>`;
      return `<div class="setting${disabledNote ? " disabled" : ""}"><span>${name}</span><select data-entity="${stateObj.entity_id}" data-kind="select" aria-label="${escapeHtml(title)}"${disabledNote ? " disabled" : ""}>
        ${options.map((o) => `<option value="${escapeHtml(o)}" ${o === stateObj.state ? "selected" : ""}>${escapeHtml(label(o))}</option>`).join("")}
        </select></div>`;
    }
    const a = stateObj.attributes;
    // As many decimals as the step has (7000, 12, 0.5).
    const decimals = String(a.step ?? 1).split(".")[1]?.length ?? 0;
    const numeric = parseFloat(stateObj.state);
    const value = Number.isFinite(numeric) ? numeric.toFixed(decimals) : stateObj.state;
    return `<div class="setting"><span>${name}</span><span class="number">
      <input type="number" data-entity="${stateObj.entity_id}" data-kind="number" aria-label="${escapeHtml(title)}" value="${escapeHtml(value)}"
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

  _moreInfo(entityId) {
    this.dispatchEvent(
      new CustomEvent("hass-more-info", { detail: { entityId }, bubbles: true, composed: true })
    );
  }

  _onClick(event) {
    if (this._onMenuClick(event)) return;
    // Values open the more-info dialog of their entity (not inside controls).
    const moreInfo = event.target.closest("[data-more-info]");
    if (moreInfo && !event.target.closest("button, input, select, label.switch, a")) {
      this._moreInfo(moreInfo.dataset.moreInfo);
      return;
    }
    const balancingButton = event.target.closest("[data-action='balancing-on'], [data-action='balancing-off']");
    if (balancingButton) {
      const entityId = balancingButton.dataset.entity;
      if (balancingButton.dataset.action === "balancing-off") {
        this._hass.callService("switch", "turn_off", { entity_id: entityId });
      } else {
        const t = this._t;
        // The last top measurement in the normal range: balancing is hardly needed.
        const delta = parseFloat(balancingButton.dataset.delta);
        const notNeeded =
          Number.isFinite(delta) && delta < BALANCING_NORMAL_MV
            ? `${t.balancingNotNeeded.replace("{delta}", Math.round(delta))} `
            : "";
        this._confirm(
          t.startBalancingTitle.replace("{name}", balancingButton.dataset.name),
          notNeeded + t.startBalancingText,
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
    if (event.target.closest("[data-action='sim-reset']")) {
      this._sim.form = this._simBaseForm(this._sim.result?.base);
      this._sections.simcontrols = undefined;
      this._render();
      this._simSchedule();
      return;
    }
    const consumerToggle = event.target.closest("[data-action='toggle-consumer']");
    if (consumerToggle) {
      const id = consumerToggle.dataset.id;
      if (!this._expandedConsumers.delete(id)) this._expandedConsumers.add(id);
      try {
        localStorage.setItem(EXPANDED_CONSUMERS_KEY, JSON.stringify([...this._expandedConsumers]));
      } catch (err) {
        // Not stored: the choice lasts until the page is reloaded.
      }
      this._render();
      return;
    }
    const seriesButton = event.target.closest("[data-action='toggle-series']");
    if (seriesButton) {
      const key = seriesButton.dataset.series;
      if (!this._hiddenSeries.delete(key)) this._hiddenSeries.add(key);
      const hidden = [...this._hiddenSeries];
      try {
        localStorage.setItem(HIDDEN_SERIES_KEY, JSON.stringify(hidden));
        localStorage.setItem(
          SHOWN_SERIES_KEY,
          JSON.stringify(DEFAULT_HIDDEN_SERIES.filter((k) => !hidden.includes(k)))
        );
      } catch (err) {
        // Not stored: the choice lasts until the page is reloaded.
      }
      this._sections.daychart = undefined;
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
    if (target.dataset?.sim) {
      this._onSimChange(target);
      return;
    }
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
    } else if (kind === "time") {
      if (target.value) this._hass.callService("time", "set_value", { entity_id: entityId, time: `${target.value}:00` });
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
  /* Accent for text: the theme colour itself is too light on light backgrounds. */
  :host { --slems-accent: color-mix(in srgb, var(--primary-color) 62%, black); }
  :host([dark]) { --slems-accent: var(--primary-color); }
  :host { display: block; min-height: 100%; background: var(--primary-background-color); color: var(--primary-text-color);
    font-family: var(--ha-font-family-body, system-ui, -apple-system, "Segoe UI", sans-serif); }
  .page { max-width: 1280px; margin: 0 auto; padding: 0 16px 24px; }
  header { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; min-height: 56px; }
  h1 { font-size: 20px; font-weight: 500; margin: 0 16px 0 0; }
  nav { display: flex; gap: 4px; flex-wrap: wrap; }
  nav button { background: none; border: none; color: var(--secondary-text-color); font: inherit; padding: 8px 12px;
    border-radius: 8px; cursor: pointer; }
  /* Phones: the tabs stay in one row and scroll sideways. */
  @media (max-width: 600px) {
    nav { flex: 1 1 100%; flex-wrap: nowrap; overflow-x: auto; scrollbar-width: none; margin: 0 -16px; padding: 0 16px 4px; }
    nav::-webkit-scrollbar { display: none; }
    nav button { flex: none; white-space: nowrap; }
  }
  nav button.active { color: var(--slems-accent); background: color-mix(in srgb, var(--primary-color) 12%, transparent);
    box-shadow: inset 0 -2px 0 var(--primary-color); }
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
  .tile { min-width: 0; overflow-wrap: break-word; hyphens: auto; }
  .tile.wide { grid-column: 1 / -1; }
  .sim-layout { display: grid; grid-template-columns: minmax(0, 2fr) minmax(300px, 1fr); gap: 16px; align-items: start; }
  .sim-main { display: flex; flex-direction: column; gap: 16px; min-width: 0; }
  .sim-side h3, #simmetrics h3 { margin: 16px 0 8px; font-size: 14px; }
  .sim-group:first-child h3 { margin-top: 8px; }
  .sim-actions { display: flex; justify-content: flex-end; margin-top: 8px; }
  .sim-metrics td.changed { font-weight: 600; }
  .sim-note { margin: 0 0 12px; }
  .sim-metrics th { white-space: normal; }
  @media (max-width: 500px) {
    .sim-metrics th, .sim-metrics td { padding: 4px; font-size: 12px; white-space: normal; }
  }
  /* One column: the inputs first, chart and key figures below. */
  @media (max-width: 900px) { .sim-layout { grid-template-columns: 1fr; } .sim-side { order: -1; } }
  .card-setting { margin-top: 10px; padding-top: 10px; border-top: 1px solid var(--divider-color); }
  .tile .label { font-size: 12px; }
  .menu-wrap { position: relative; }
  .menu-button { background: none; border: none; cursor: pointer; color: var(--secondary-text-color); padding: 4px; line-height: 0;
    border-radius: 50%; }
  .menu-button:hover { background: color-mix(in srgb, var(--primary-text-color) 8%, transparent); }
  .menu { position: absolute; right: 0; top: 100%; z-index: 5; min-width: 240px; padding: 4px 0; border-radius: 8px;
    background: var(--card-background-color); box-shadow: 0 4px 16px rgba(0,0,0,0.3); border: 1px solid var(--divider-color); }
  .menu-item { display: block; width: 100%; text-align: left; background: none; border: none; font: inherit; font-size: 14px;
    color: var(--primary-text-color); padding: 10px 16px; cursor: pointer; }
  .menu-item:hover:not(:disabled) { background: color-mix(in srgb, var(--primary-color) 10%, transparent); }
  .menu-item:disabled { color: var(--secondary-text-color); cursor: not-allowed; }
  .info-box { display: flex; gap: 8px; align-items: center; margin: 4px 0 12px; padding: 8px 10px; border-radius: 8px; font-size: 13px;
    border: 1px solid var(--divider-color); background: color-mix(in srgb, var(--primary-color) 8%, transparent); }
  .info-box ha-icon { color: var(--primary-color); --mdc-icon-size: 18px; flex: none; }
  .info-box button { margin-left: auto; }
  #details dl { margin: 0 0 16px; min-width: 300px; }
  [data-more-info] { cursor: pointer; }
  .tile[data-more-info]:hover .value, dd[data-more-info]:hover, dt[data-more-info]:hover + dd { color: var(--slems-accent); }
  .fbox[data-more-info]:hover { border-color: var(--accent); }
  .accuracy { display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 8px 32px; margin-top: 12px; }
  .accuracy h3 { margin: 0 0 4px; font-size: 14px; font-weight: 500; }
  .tile .problem-value { display: flex; align-items: center; gap: 6px; color: var(--error-color, #d03b3b); }
  .tile .problem-value ha-icon { --mdc-icon-size: 20px; flex: none; }
  .setting .readonly { font-variant-numeric: tabular-nums; color: var(--secondary-text-color); white-space: nowrap; }
  .tile .value { font-size: 18px; }
  .tile .sub { font-size: 12px; color: var(--secondary-text-color); }
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
  /* Up to two lines, so longer consumer names stay readable in narrow boxes. */
  .fbox-title { font-size: 12px; line-height: 1.25; color: var(--secondary-text-color); overflow: hidden;
    display: -webkit-box; -webkit-box-orient: vertical; -webkit-line-clamp: 2; overflow-wrap: anywhere; }
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
  button.link { background: none; border: none; color: var(--slems-accent); cursor: pointer; font: inherit; padding: 4px; }
  .chart-actions { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; justify-content: flex-end; }
  .segmented { display: inline-flex; border: 1px solid var(--divider-color); border-radius: 8px; overflow: hidden; }
  .segmented button { background: none; border: none; font: inherit; font-size: 13px; padding: 4px 12px; cursor: pointer;
    color: var(--secondary-text-color); }
  .segmented button + button { border-left: 1px solid var(--divider-color); }
  .segmented button.active { color: var(--slems-accent); background: color-mix(in srgb, var(--primary-color) 12%, transparent);
    box-shadow: inset 0 -2px 0 var(--primary-color); }
  .legend { display: flex; flex-wrap: wrap; gap: 4px 16px; margin: 12px 0 4px; font-size: 12px; color: var(--primary-text-color); }
  .legend-item { display: inline-flex; align-items: center; gap: 6px; background: none; border: none; padding: 2px 0;
    font: inherit; color: inherit; cursor: pointer; }
  .legend-item.off { opacity: 0.4; text-decoration: line-through; }
  .chart-wrap { position: relative; }
  .chart { display: block; max-width: 100%; }
  .chart .tick { font-size: 11px; fill: var(--secondary-text-color); font-variant-numeric: tabular-nums; }
  #daychart { position: relative; }
  svg.chart { touch-action: pan-y; }
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
  .tile button.info, .setting button.info { background: none; border: none; padding: 0 0 0 4px; cursor: pointer; color: var(--secondary-text-color);
    vertical-align: middle; line-height: 0; }
  .tile button.info ha-icon, .setting button.info ha-icon { --mdc-icon-size: 16px; }
  .setting-note { display: flex; gap: 6px; align-items: flex-start; margin: 12px 0 0; font-size: 12px; line-height: 1.35;
    color: var(--secondary-text-color); }
  .setting-note ha-icon { --mdc-icon-size: 16px; flex: none; color: var(--warning-color, #fab219); }
  .setting-hint { display: block; margin-top: 4px; font-size: 12px; color: var(--secondary-text-color); line-height: 1.35; }
  .setting input[type=number] { width: 90px; font: inherit; padding: 4px 6px; border-radius: 6px; text-align: right;
    border: 1px solid var(--divider-color); background: var(--card-background-color); color: var(--primary-text-color); }
  .setting input[type=time] { font: inherit; padding: 4px 6px; border-radius: 6px; border: 1px solid var(--divider-color);
    background: var(--card-background-color); color: var(--primary-text-color); color-scheme: light dark; }
  .setting select { font: inherit; padding: 4px 6px; border-radius: 6px; border: 1px solid var(--divider-color);
    background: var(--card-background-color); color: var(--primary-text-color); max-width: 60%; }
  .card-setting .setting > span:first-child { min-width: 0; }
  .card-toggle { display: flex; align-items: center; gap: 4px; margin: 8px 0 -6px auto; font-size: 13px; }
  .card-toggle ha-icon { --mdc-icon-size: 18px; }
  .card-subheading { margin: 0 0 6px; font-size: 14px; font-weight: 500; color: var(--primary-text-color); }
  .card-subheading.disabled { color: var(--secondary-text-color); }
  .setting.disabled > span:first-child { color: var(--secondary-text-color); }
  .setting select:disabled { opacity: 0.5; }
  .card-setting select { min-width: 0; max-width: 55%; font-size: 14px; padding: 3px 4px; }
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
