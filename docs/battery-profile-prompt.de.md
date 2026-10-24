# Aus dem Handbuch einer Batterie mit einer KI ein SLEMS-Profil erstellen

[English](battery-profile-prompt.md)

Eine KI (z. B. Claude oder ChatGPT) kann die Modbus-Registerliste einer
Batterie oder eines Hybrid-Wechselrichters lesen und daraus ein
Batterieprofil für SLEMS erstellen. Füge das Ergebnis in SLEMS unter
**Einstellungen → Geräte & Dienste → SLEMS → Batterie hinzufügen** mit dem
Modell **Batterie aus einem Modbus-Profil (experimentell)** ein. Den ganzen Weg vom Handbuch bis zur geregelten Batterie beschreibt die
README unter
[Batterie aus einem Modbus-Profil](../README.de.md#batterie-aus-einem-modbus-profil-experimentell).

So geht's:

1. Besorg dir die Modbus-Registerliste deiner Batterie (Handbuch,
   „Modbus-Protokoll“ oder „Register Map“ des Herstellers, als PDF oder
   Text) und notiere Modell und Firmware-Version.
2. Kopiere den Prompt unten in einen neuen Chat, füll die ersten Zeilen aus
   und häng die Registerliste an.
3. Prüfe die Antwort: Zeilen mit `# unsicher`, die Zeilen `# Frage` oben und
   vor allem jedes Register unter `control` gegen das Handbuch (Adresse,
   Einheit, Bereich).
4. Füge das YAML in SLEMS ein. SLEMS liest jedes Register einmal und zeigt
   die Werte, danach was es schreiben würde, bevor etwas geschrieben wird.

Eine KI kann eine Tabelle falsch lesen oder Adressen falsch zählen. Ein Wert
im falschen Register kann eine Einstellung der Batterie ändern; die
Prüfungen von SLEMS fangen die meisten dieser Fehler ab, aber nicht alle.
Vergleiche die Schreibregister mit dem Handbuch, bevor du die Batterie
speicherst.

## Prompt

````text
Batterie: <Hersteller und Modell>
Firmware: <Firmware-Version, falls bekannt>

Lies die angehängte Modbus-Registerliste und gib ein Batterieprofil für SLEMS
im Format "slems_battery_profile", Version 1, aus. Gib nur das YAML in einem
Codeblock aus, ohne weitere Erklärungen. Schreib die Kommentare in der
Sprache dieser Nachricht; die Schlüssel bleiben wie angegeben.

Hat die Registerliste kein Register für die Lade- und Entladeleistung (oder
eine Leistung mit Vorzeichen), oder speichert die Batterie laut Handbuch die
Leistungsvorgabe dauerhaft (Flash, EEPROM), gib kein Profil aus: sag in
einem Satz, warum sich die Batterie so nicht steuern lässt.

Format (alle Schlüssel, die es gibt; andere Schlüssel sind nicht erlaubt):

slems_battery_profile: 1
id: <Kleinbuchstaben, Ziffern und _, z. B. acme_x5; nicht marstek_venus_e3>
name: <Hersteller und Modell>
manufacturer: <Hersteller>                    # optional
revision: 1
notes: <Firmware, Schnittstelle>              # optional
connection:                                   # optional, Standardwerte wie gezeigt
  port: 502
  unit_id: 1
  message_spacing_s: 0                        # kleinster Abstand zwischen zwei Anfragen in s
  timeout_s: 3
limits:
  max_charge_w: <höchste Ladeleistung in W, ganze Zahl>
  max_discharge_w: <höchste Entladeleistung in W, ganze Zahl>
  min_power_w: <kleinste Leistung, die die Batterie annimmt, in W>   # optional
timing:                                       # optional, siehe Regeln
  start_delay_s: <s>                          # von einer Vorgabe, bis die Leistung sichtbar ist
  start_extra_s: <s>                          # zusätzlich aus dem Standby
  reversal_extra_s: <s>                       # zusätzlich bei einem Richtungswechsel
  min_command_interval_s: <s>                 # kleinster Abstand zwischen zwei Vorgaben
own_pv:                                       # nur für eine Batterie mit eigenen PV-Modulen
  max_output_w: <höchste Ausgangsleistung ins Haus in W>
  ac_charge: true                             # kann aus dem Netz laden
  passes_pv_through: false                    # gibt ihre PV voll ins Haus weiter
read:                                         # Werte, die SLEMS liest; soc_pct ist Pflicht
  soc_pct:        {address: ..., type: ..., scale: ...}   # Ladezustand in %
  power_dc_w:     {..., positive: charge|discharge}       # Batterieleistung in W
  power_ac_w:     {..., positive: charge|discharge}       # AC-Leistung in W
  pv_w:           {...}                       # nur eigene PV: PV-Leistung in W
  output_w:       {...}                       # nur eigene PV: Ausgang ins Haus in W
  input_w:        {...}                       # nur eigene PV: Eingang aus dem Netz in W
  connection_w:   {..., positive: input|output}  # nur eigene PV: Leistung am AC-Anschluss mit Vorzeichen
  charged_kwh:    {...}                       # Ladezähler über die Lebensdauer in kWh
  discharged_kwh: {...}                       # Entladezähler über die Lebensdauer in kWh
  voltage_v:      {...}                       # Batteriespannung in V
  temperature_c:  {...}                       # Temperatur in °C
  max_cell_v:     {...}                       # höchste Zellspannung in V
  min_cell_v:     {...}                       # niedrigste Zellspannung in V
  cycles:         {...}                       # Vollzyklen
  state:                                      # Zustand der Batterie
    address: ...
    type: uint16
    values: {<Rohwert>: <sleep|standby|charge|discharge|backup|firmware_update|bypass>}
device_info:                                  # nur lesend, frei gewählte Namen in Kleinbuchstaben
  <name>: {address: ..., type: ...}
control:
  type: split                                 # split: Register für Laden und Entladen; signed: ein Register
  positive: charge|discharge                  # nur signed: Richtung eines positiven Werts
  setpoint: battery                           # nur eigene PV: battery oder connection
  keepalive_s: <s>                            # nach dieser Zeit alles erneut schreiben
  confirm: {delay_s: 0.2, tolerance_w: 100, tolerance_pct: 0}   # optional
  apply:                                      # geschrieben zum Laden und Entladen, in dieser Reihenfolge
    - {address: ..., value: ..., at_rest: [min, max], confirm: true}
  release:                                    # geschrieben, wenn SLEMS die Batterie freigibt
    auto:                                     # ihre eigene Logik übernimmt wieder
      - {address: ..., value: ...}
    standby:                                  # sie bleibt bei 0 W im Standby
      - {address: ..., value: ...}
probe: {address: ..., type: ..., range: [min, max]}   # gelesen, um die Verbindung zu prüfen

Schlüssel eines Registers (read, device_info, probe):
  address     Protokolladresse 0-65535 (siehe Regeln)
  type        uint16, int16, uint32, int32, float32 oder char (Text, mit count)
  count       nur char: Anzahl der Register
  scale       Wert = Rohwert × scale (z. B. 0.1 für 0,1 %); Standard 1
  word_order  big (Standard) oder little: Reihenfolge der zwei Register bei 32 Bit
  input       true für Input-Register (Funktionscode 04); Standard Holding (03)
  positive    nur Leistungen mit Vorzeichen, siehe oben
  fallback    nur soc_pct: ein zweites Register, falls das erste nicht lesbar ist
Eine Leistung darf die Summe mehrerer Register sein: eine Liste von Registern
(z. B. die PV-Eingänge mehrerer MPPTs).

Schlüssel eines Schritts unter control.apply:
  address, type (kein char), word_order wie oben
  value                ein fester Rohwert oder ein Platzhalter:
                         "{charge_w}" und "{discharge_w}" (split), "{power_w}" (signed): Leistung in W
                         "{mode}" mit modes: {idle: ..., charge: ..., discharge: ...}
  scale                nur Leistungs-Platzhalter: eine Rohwert-Einheit sind scale W (z. B. 10 für 10-W-Schritte, 1000 für kW)
  at_rest: [min, max]  Bereich des Rohwerts, solange die Batterie ruht
  at_rest_values: [..] statt at_rest: die einzigen Rohwerte in Ruhe (z. B. Kennwerte)
  confirm: true        SLEMS liest das Register nach dem Schreiben zurück
  persistent: true     die Batterie speichert das Register dauerhaft (Flash, EEPROM)
  max_changes_per_day  nur persistent: erlaubte Änderungen in 24 h (Standard 100)
Schritte unter release haben nur address, value (eine feste Zahl) und confirm.

Beispiel einer erfundenen Batterie (übernimm keine Adresse und keinen Wert daraus):

```yaml
# Quelle: „Example battery Modbus protocol V1.2“, Tabelle 5, Seiten 8-11.
# Adressen: Holding-Register ab 40001 nummeriert (Adresse = Nummer - 40001),
# Input-Register ab 30001 nummeriert (Adresse = Nummer - 30001).
# Frage: Das Handbuch sagt nicht, ob 40001-40004 im Flash gespeichert werden.
slems_battery_profile: 1
id: example_battery
name: Example battery 5 kWh
manufacturer: Example
revision: 1
notes: Firmware 1.2, Modbus TCP über den LAN-Anschluss.

connection:
  port: 502
  unit_id: 1
  message_spacing_s: 0.1    # Seite 4: mindestens 100 ms zwischen zwei Anfragen
  timeout_s: 3

limits:
  max_charge_w: 2500
  max_discharge_w: 2500

read:
  soc_pct:        {address: 100, type: uint16, input: true, scale: 0.1}          # 30101, 0,1 %
  power_ac_w:     {address: 110, type: int32, input: true, positive: discharge}  # 30111-30112, W, „positiv: Entladen“ (Seite 9)
  voltage_v:      {address: 120, type: uint16, input: true, scale: 0.01}         # 30121, 0,01 V
  charged_kwh:    {address: 130, type: uint32, input: true, scale: 0.1}          # 30131-30132, 0,1 kWh
  discharged_kwh: {address: 132, type: uint32, input: true, scale: 0.1}          # 30133-30134, 0,1 kWh
  temperature_c:  {address: 140, type: int16, input: true, scale: 0.1}           # 30141, 0,1 °C
  state:                                                                         # 30151 (Seite 9)
    address: 150
    type: uint16
    input: true
    values: {0: standby, 1: charge, 2: discharge, 3: firmware_update}

device_info:
  serial_number:    {address: 200, type: char, count: 8, input: true}            # 30201-30208
  firmware_version: {address: 210, type: uint16, input: true}                    # 30211

control:
  type: split
  keepalive_s: 30           # Seite 10: nach 60 s ohne Schreiben zurück in den Automatikbetrieb
  apply:
    # 40001 „Remote control“ (Seite 10): 0 = aus, 1 = an
    - {address: 0, value: 1, at_rest: [0, 1], confirm: true}
    # 40004 „Discharge power“ (Seite 10): in 10 W, 0-250
    - {address: 3, value: "{discharge_w}", scale: 10, at_rest: [0, 250], confirm: true}
    # 40003 „Charge power“ (Seite 10): in 10 W, 0-250
    - {address: 2, value: "{charge_w}", scale: 10, at_rest: [0, 250], confirm: true}
    # 40002 „Force mode“ (Seite 10): 0 = keiner, 1 = Laden, 2 = Entladen
    - {address: 1, value: "{mode}", modes: {idle: 0, charge: 1, discharge: 2}, at_rest: [0, 2], confirm: true}
  release:
    auto:
      - {address: 3, value: 0}
      - {address: 2, value: 0}
      - {address: 1, value: 0, confirm: true}
      - {address: 0, value: 0, confirm: true}
    standby:
      - {address: 3, value: 0}
      - {address: 2, value: 0}
      - {address: 1, value: 0, confirm: true}

probe: {address: 100, type: uint16, input: true, range: [0, 1000]}
```

Regeln:
- Nimm jedes Register aus der angehängten Liste. Erfinde keine Register,
  Adressen, Faktoren oder Bereiche. Ist ein Wert unsicher, schreib ans
  Zeilenende den Kommentar "# unsicher: <Grund>".
- Adressen sind die Protokolladresse, die im Modbus-Frame steht (0-65535).
  Handbücher nummerieren Register oft stattdessen: Mit einem Präfix (40001,
  400001 für Holding-, 30001 für Input-Register) zieh es ab; ab 1
  nummeriert, zieh 1 ab; hexadezimale Adressen dürfen als 0x1A2B stehen.
  Schreib oben ins Profil, wie das Handbuch zählt und was du abgezogen hast.
  Ist das im Handbuch nicht eindeutig, schreib oben "# Frage: ...".
- Register mit Funktionscode 04 (Input-Register, oft 3xxxx) bekommen
  input: true. Register, die SLEMS schreibt, sind immer Holding-Register.
- Nenne für jedes Leistungsregister Einheit und Vorzeichen aus dem Handbuch
  in einem Kommentar (z. B. „positiv: Entladen“). positive ist Pflicht bei
  power_dc_w, power_ac_w und connection_w; steht das Vorzeichen nicht im
  Handbuch, schreib "# unsicher: Vorzeichen".
- scale ist der Faktor vom Rohwert zu %, W, kWh, V oder °C (0.1 für 0,1 %,
  1000 für kW in W). Unter control ist es umgekehrt: eine Rohwert-Einheit
  sind scale W (10 für 10-W-Schritte).
- Nenne über jedem Schritt unter control.apply in einem Kommentar: Nummer
  und Name des Registers im Handbuch, Seite oder Tabelle und seine
  Bedeutung.
- Jeder Schritt unter control.apply braucht at_rest (oder at_rest_values)
  aus dem Handbuch: den Bereich des Rohwerts, solange die Batterie ruht.
  Darin müssen 0, die Höchstleistungen als Rohwerte (max_charge_w / scale),
  feste Werte, alle Werte von modes und jeder Wert der Freigabe liegen.
- Lass ein Schreibregister ohne eindeutigen Eintrag im Handbuch weg; rate
  nie. Schreib nur Register, die SLEMS zum Laden, Entladen und Freigeben
  braucht; keine Einstellungen, Grenzen, Schutz- oder Reset-Register.
- Register, die das Handbuch als Einstellung, Parameter, EEPROM oder Flash
  beschreibt, bekommen persistent: true. Nie ein Register mit einem
  Leistungs-Platzhalter. Sagt das Handbuch dazu bei einem Schreibregister
  nichts, schreib oben "# Frage: Wird <Register> dauerhaft gespeichert?".
- Reihenfolge in apply: zuerst die Fernsteuerung einschalten, dann die
  Leistungen, dann den Modus, damit eine neue Richtung mit gültigen Werten
  beginnt.
- release: gib auto und/oder standby an, je nachdem, was das Handbuch
  unterstützt. Sie schreiben nur Register aus apply, mit festen Zahlen:
  Leistungen 0, Modus idle, bei auto die Fernsteuerung aus. Lass die
  Freigabe nie weg.
- confirm: true an jedem Schreibregister, das sich laut Handbuch
  zurücklesen lässt.
- keepalive_s: Nennt das Handbuch eine Zeit, nach der die Batterie in ihre
  eigene Logik zurückfällt, einen Wert deutlich darunter (z. B. die
  Hälfte); sonst weglassen.
- message_spacing_s und timeout_s nur, wenn das Handbuch einen kleinsten
  Abstand zwischen Anfragen oder eine Antwortzeit nennt; sagt es nichts,
  schreib oben "# Frage: kleinster Abstand zwischen Anfragen?".
- timing: weglassen (SLEMS lernt die Reaktionszeiten), außer das Handbuch
  nennt eine Verzögerung der Vorgaben oder einen kleinsten Abstand zwischen
  ihnen.
- device_info: die Firmware-Versionen und die Seriennummer, falls
  vorhanden: Daran erkennt SLEMS ein Firmware-Update.
- probe: das Register des Ladezustands mit seinem Rohbereich (z. B.
  [0, 1000] bei 0,1 %).
- state: nur die oben genannten Zustände; Rohwerte, die zu keinem passen,
  weglassen.
- own_pv nur für eine Batterie mit eigenen PV-Modulen: dann pv_w, und
  output_w (mit input_w, wenn sie aus dem Netz lädt) oder connection_w.
- SLEMS erreicht die Batterie nur über Modbus TCP. Beschreibt das Handbuch
  nur RS485 (Modbus RTU), schreib oben "# Frage: Modbus-TCP-Adapter?".
````

## Profil als mitgeliefertes Profil vorschlagen

Ein Profil, das sich bewährt, kann ein mitgeliefertes Profil für alle mit
derselben Batterie werden. Melde es als
[Issue](https://github.com/gojux/SLEMS/issues) mit:

- dem Profil (in SLEMS: **Batterie bearbeiten** → **Profil als YAML
  anzeigen**),
- Hersteller, Modell und Firmware-Versionen (in der Diagnose von SLEMS),
- dem Ergebnis seines Probebetriebs (die Benachrichtigung des Selbsttests
  oder die Diagnose),
- der Quelle der Registerliste (ein Link oder Name und Version des
  Handbuchs).

Ein Profil wird mitgeliefert, sobald es bei mindestens einer, besser zwei
Batterien den Probebetrieb bestanden hat, seine Schreibregister gegen das
Handbuch geprüft sind und es einen Test im Repository hat.
