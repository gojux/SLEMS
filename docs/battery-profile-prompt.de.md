# Aus dem Handbuch einer Batterie mit einer KI ein SLEMS-Profil erstellen

[English](battery-profile-prompt.md)

Eine KI (ChatGPT, Claude und Mistral haben sich in Probeläufen bewährt)
kann die Modbus-Registerliste einer Batterie oder eines
Hybrid-Wechselrichters lesen und daraus ein Batterieprofil für SLEMS
erstellen. Füge das Ergebnis in SLEMS unter
**Einstellungen → Geräte & Dienste → SLEMS → Batterie hinzufügen** mit dem
Modell **Batterie aus einem Modbus-Profil (experimentell)** ein. Den ganzen
Weg vom Handbuch bis zur geregelten Batterie beschreibt die README unter
[Batterie aus einem Modbus-Profil](../README.de.md#batterie-aus-einem-modbus-profil-experimentell).

So geht's:

1. Besorg dir die Modbus-Registerliste deiner Batterie (Handbuch,
   „Modbus-Protokoll“ oder „Register Map“ des Herstellers, als PDF oder
   Text) und notiere Modell und Firmware-Version. Viele Hersteller
   veröffentlichen keine: Dann häng nichts an, und die KI sucht online
   mehrere Quellen (Integrationen für Home Assistant oder ioBroker, evcc,
   andere Open-Source-Projekte) und vergleicht sie. Dafür braucht die KI
   eine Websuche.
2. Kopiere den Prompt unten in einen neuen Chat, füll die ersten Zeilen aus
   und häng die Registerliste an, falls du eine hast.
3. Prüfe die Antwort: Zeilen mit `# unsicher`, die Zeilen `# Frage` oben und
   vor allem jedes Register unter `control` gegen das Handbuch oder die
   Quellen oben (Adresse, Einheit, Bereich).
4. Füge das YAML in SLEMS ein (die ganze Antwort geht auch, wenn sie nur
   einen Codeblock enthält). SLEMS liest jedes Register einmal und zeigt
   die Werte, danach was es schreiben würde, bevor etwas geschrieben wird.
5. Zeigt SLEMS einen Fehler oder sieht ein gelesener Wert falsch aus
   (markiert oder anders als in der App der Batterie), gib die
   Fehlermeldung oder die gelesenen Werte im selben Chat an die KI zurück
   und füge ihr korrigiertes Profil ein.

Ein Profil von einer KI ist ein Entwurf. Eine KI kann eine Tabelle falsch
lesen oder Adressen falsch zählen. Ein Wert im falschen Register kann eine
Einstellung der Batterie ändern; die Prüfungen von SLEMS fangen die meisten
dieser Fehler ab, aber nicht alle. Vergleiche die Schreibregister mit dem
Handbuch, bevor du die Batterie speicherst. Ein Profil aus Quellen im Netz
ist unsicherer als eines aus dem Handbuch: Öffne die Quellen, die oben
genannt sind, und prüfe die Schreibregister dort.

## Prompt

````text
Batterie: <Hersteller und Modell>
Firmware: <Firmware-Version, falls bekannt>

Lies die angehängte Modbus-Registerliste und gib ein Batterieprofil für SLEMS
im Format "slems_battery_profile", Version 1, aus. Ist keine Registerliste
angehängt, such online nach den Registern dieses Modells: beim Hersteller,
in Integrationen für Home Assistant oder ioBroker, bei evcc, in anderen
Open-Source-Projekten; nimm mehrere unabhängige Quellen und vergleiche sie.
Gib nur das YAML in einem Codeblock aus, ohne weitere Erklärungen. Schreib
die Kommentare in der Sprache dieser Nachricht; die Schlüssel bleiben wie
angegeben. Setz Texte (name, manufacturer, notes) in doppelte
Anführungszeichen.

Gib kein Profil aus, sondern sag in einem Satz, warum sich die Batterie so
nicht steuern lässt, wenn einer dieser Fälle zutrifft:
- Es gibt kein Register, über das SLEMS die Leistung vorgibt: weder je
  eines für die Lade- und die Entladeleistung noch eines für eine Leistung
  mit Vorzeichen noch eines für eine Leistung ohne Vorzeichen, deren
  Richtung ein Befehl setzt.
- Das Register der Leistungsvorgabe selbst wird dauerhaft gespeichert
  (Flash, EEPROM). Dass die Batterie andere Register dauerhaft speichert
  (z. B. ihre Höchstleistung), ist kein Grund; solche Einstellungen
  schreibt das Profil nicht (siehe Regeln).
- Kein Register für die Leistungsvorgabe erfüllt die Regel für Quellen aus
  dem Netz (zwei unabhängige Quellen, siehe Regeln).
Sonst sind auch unsichere Quellen kein Grund: Gib das Profil aus und
markiere jeden unsicheren Wert mit "# unsicher: <Grund>". Gibst du kein
Profil aus, nenne die Quellen, die du gefunden hast.

Format (alle Schlüssel, die es gibt; andere Schlüssel sind nicht erlaubt):

slems_battery_profile: 1
id: <Hersteller und Modell in Kleinbuchstaben, Ziffern und _, z. B. acme_x5>
name: "<Hersteller und Modell>"
manufacturer: "<Hersteller>"                  # optional
revision: 1
notes: "<Firmware, Schnittstelle>"            # optional
connection:                                   # optional, Standardwerte wie gezeigt
  port: 502
  unit_id: 1
  message_spacing_s: 0.0                      # kleinster Abstand zwischen zwei Anfragen in s
  timeout_s: 3.0
limits:
  max_charge_w: <höchste Ladeleistung in W, ganze Zahl>
  max_discharge_w: <höchste Entladeleistung in W, ganze Zahl>
  min_power_w: <kleinste Leistung, die die Batterie annimmt, in W>   # optional
timing:                                       # optional, siehe Regeln
  start_delay_s: <s, z. B. 2.5>               # von einer Vorgabe, bis die Leistung sichtbar ist
  start_extra_s: <s, z. B. 2.5>               # zusätzlich aus dem Standby
  reversal_extra_s: <s, z. B. 2.5>            # zusätzlich bei einem Richtungswechsel
  min_command_interval_s: <s, z. B. 2.5>      # kleinster Abstand zwischen zwei Vorgaben
own_pv:                                       # nur für eine Batterie mit eigenen PV-Modulen
  max_output_w: <höchste Ausgangsleistung ins Haus in W>
  ac_charge: true                             # kann aus dem Netz laden
  passes_pv_through: false                    # gibt ihre PV voll ins Haus weiter
read:                                         # Werte, die SLEMS liest, siehe Regeln
  soc_pct:         {address: ..., type: ..., scale: ...}  # Ladezustand in %, Pflicht
  battery_power_w: {..., positive: charge|discharge}      # Leistung an der Batterie (DC) in W
  ac_power_w:      {..., positive: input|output}          # Leistung am AC-Anschluss zu Haus und Netz in W (ohne Notstrom-Ausgang)
  pv_power_w:      {...}                      # nur eigene PV: Leistung der eigenen PV-Module in W
  charged_kwh:     {...}                      # Ladezähler über die Lebensdauer in kWh
  discharged_kwh:  {...}                      # Entladezähler über die Lebensdauer in kWh
  voltage_v:       {...}                      # Batteriespannung in V
  temperature_c:   {...}                      # Batterietemperatur in °C (Zellen oder BMS; gibt es keine, die des Geräts)
  max_cell_v:      {...}                      # höchste Zellspannung in V
  min_cell_v:      {...}                      # niedrigste Zellspannung in V
  cycles:          {...}                      # Vollzyklen
  state:                                      # Zustand der Batterie
    address: ...
    type: uint16                              # oder int16, uint32, int32
    values: {<Rohwert>: <sleep|standby|charge|discharge|backup|firmware_update|bypass>}
device_info:                                  # nur lesend, frei gewählte Namen in Kleinbuchstaben
  <name>: {address: ..., type: ...}
control:
  type: split                                 # split, signed oder magnitude, siehe Regeln
  positive: charge|discharge                  # nur signed: Richtung eines positiven Werts
  setpoint: battery                           # nur eigene PV: battery oder connection
  keepalive_s: <s, z. B. 30.0>                # spätestens nach dieser Zeit alle Schritte erneut schreiben, auch ohne Änderung
  confirm: {delay_s: 0.2, tolerance_w: 100, tolerance_pct: 0.0}   # optional: Toleranzen beim Rücklesen der Schritte mit confirm: true
  apply:                                      # geschrieben zum Laden und Entladen, in dieser Reihenfolge
    - {address: ..., type: ..., value: ..., at_rest: [min, max], confirm: true}
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
  scale       Wert = Rohwert × scale (z. B. 0.1 für 0,1 %); Standard 1; nicht bei probe (Bereich roh)
  word_order  big (Standard) oder little: Reihenfolge der zwei Register bei 32 Bit
  input       true für Input-Register (Funktionscode 04); Standard Holding (03)
  positive    nur Leistungen mit Vorzeichen, siehe oben
  fallback    nur soc_pct: ein zweites Register, falls das erste nicht lesbar ist
Statt eines Registers mit positive dürfen battery_power_w und ac_power_w ein
Paar zweier Register ohne Vorzeichen sein, eines je Richtung:
  battery_power_w: {charge: {address: ..., type: ...}, discharge: {address: ..., type: ...}}
  ac_power_w:      {input: {address: ..., type: ...}, output: {address: ..., type: ...}}
Eine Leistung darf die Summe mehrerer Register sein: eine Liste von Registern
(z. B. die PV-Eingänge mehrerer MPPTs), auch je Seite eines Paars.

Schlüssel eines Schritts unter control.apply:
  address, word_order wie oben
  type                 Pflicht: uint16 für ein Register, uint32, int32 oder float32 für zwei (kein char)
  value                ein fester Rohwert oder ein Platzhalter:
                         "{charge_w}" und "{discharge_w}" (split), "{power_w}" (signed, magnitude): Leistung in W
                         "{mode}" mit modes: {idle: ..., charge: ..., discharge: ...} (Pflicht bei magnitude)
  scale                nur Leistungs-Platzhalter: eine Rohwert-Einheit sind scale W (z. B. 10 für 10-W-Einheiten, 1000 für kW)
  at_rest: [min, max]  Bereich des Rohwerts, solange die Batterie ruht
  at_rest_values: [..] statt at_rest: die einzigen Rohwerte in Ruhe (z. B. Kennwerte)
  confirm: true        SLEMS liest das Register nach dem Schreiben zurück
  persistent: true     die Batterie speichert das Register dauerhaft (Flash, EEPROM)
  max_changes_per_day  nur persistent: erlaubte Änderungen in 24 h (Standard 100)
Schritte unter release haben nur address, value (eine feste Zahl) und confirm;
type und word_order übernimmt SLEMS vom Schritt in apply.

Beispiel einer erfundenen Batterie (übernimm keine Adresse und keinen Wert daraus):

```yaml
# Quelle: „Example battery Modbus protocol V1.2“, Tabelle 5, Seiten 8-11.
# Adressen: Holding-Register ab 40001 nummeriert (Adresse = Nummer - 40001),
# Input-Register ab 30001 nummeriert (Adresse = Nummer - 30001).
# Frage: Seite 5: „Modbus control“ muss einmal in der App eingeschaltet werden; ist es das?
# Frage: Das Handbuch sagt nicht, ob 40001-40007 im Flash gespeichert werden.
slems_battery_profile: 1
id: example_battery
name: "Example battery 5 kWh"
manufacturer: "Example"
revision: 1
notes: "Firmware 1.2, Modbus TCP über den LAN-Anschluss."

connection:
  port: 502
  unit_id: 1
  message_spacing_s: 0.1    # Seite 4: mindestens 100 ms zwischen zwei Anfragen
  timeout_s: 3.0

limits:
  max_charge_w: 2500
  max_discharge_w: 2500

read:
  soc_pct:        {address: 100, type: uint16, input: true, scale: 0.1}          # 30101, 0,1 %
  ac_power_w:     {address: 110, type: int32, input: true, positive: output}     # 30111-30112, W, „positiv: Entladen“ (Seite 9)
  voltage_v:      {address: 120, type: uint16, input: true, scale: 0.01}         # 30121, 0,01 V
  charged_kwh:    {address: 130, type: uint32, input: true, scale: 0.1}          # 30131-30132, 0,1 kWh
  discharged_kwh: {address: 132, type: uint32, input: true, scale: 0.1}          # 30133-30134, 0,1 kWh
  temperature_c:  {address: 140, type: int16, input: true, scale: 0.1}           # 30141, 0,1 °C, Batterie
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
  keepalive_s: 60.0         # die Hälfte des Timeouts in 40007
  apply:
    # 40001 „Remote control“ (Seite 10): 0xA5A5 = an, 0x5A5A = aus
    - {address: 0, type: uint16, value: 0xA5A5, at_rest_values: [0xA5A5, 0x5A5A], confirm: true}
    # 40007 „Remote time-out“ (Seite 10): s, 0-3600; danach ohne Schreiben zurück in den Automatikbetrieb
    - {address: 6, type: uint16, value: 120, at_rest: [0, 3600], confirm: true}
    # 40005-40006 „Discharge power“ (Seite 10): U32, höheres Wort zuerst, in 10 W, 0-250
    - {address: 4, type: uint32, value: "{discharge_w}", scale: 10, at_rest: [0, 250], confirm: true}
    # 40003-40004 „Charge power“ (Seite 10): U32, höheres Wort zuerst, in 10 W, 0-250
    - {address: 2, type: uint32, value: "{charge_w}", scale: 10, at_rest: [0, 250], confirm: true}
    # 40002 „Force mode“ (Seite 10): 0 = keiner, 1 = Laden, 2 = Entladen
    - {address: 1, type: uint16, value: "{mode}", modes: {idle: 0, charge: 1, discharge: 2}, at_rest: [0, 2], confirm: true}
  release:
    auto:
      - {address: 4, value: 0}
      - {address: 2, value: 0}
      - {address: 1, value: 0, confirm: true}
      - {address: 0, value: 0x5A5A, confirm: true}
    standby:
      - {address: 4, value: 0}
      - {address: 2, value: 0}
      - {address: 1, value: 0, confirm: true}

probe: {address: 100, type: uint16, input: true, range: [0, 1000]}
```

Regeln:
- Nimm jedes Register aus der angehängten Liste oder, ohne Liste, aus den
  gefundenen Quellen; „das Handbuch“ in diesen Regeln meint sie. Fehlt in
  der angehängten Liste ein Register, das das Profil braucht, such es online
  wie ohne Liste und schreib oben, welche Register nicht aus der Liste
  stammen. Ebenso eine Angabe, die die Liste nicht nennt (z. B. das
  Vorzeichen einer Leistung oder die Höchstleistung): Such sie online und
  nenne die Quelle im Kommentar. Erfinde keine Register, Adressen,
  Faktoren oder Bereiche. Ist ein Wert unsicher, schreib ans Zeilenende den
  Kommentar "# unsicher: <Grund>".
- Quellen aus dem Netz: Nenne jede oben im Profil mit Link (und Datei).
  Schreib in den Kommentar jedes Registers, welche Quellen es nennen. Nimm
  ein Schreibregister nur, wenn mindestens zwei unabhängige Quellen bei
  Adresse, Werten und Bereich übereinstimmen (eine Kopie oder ein Fork
  einer anderen Quelle ist nicht unabhängig); sonst lass es weg. Weichen
  die Quellen bei einem Leseregister ab, schreib "# unsicher: Quellen
  weichen ab". Ein Dokument des Herstellers aus dem Netz (z. B. ein
  Modbus-Protokoll oder eine Technical Note) zählt als Handbuch.
- Adressen sind die Protokolladresse, die im Modbus-Frame steht (0-65535;
  das erste Register hat die Adresse 0). Handbücher nennen oft stattdessen
  eine Registernummer, die ab 1 zählt (Modicon-Konvention: Das erste
  Register heißt 1, 40001 oder 30001). Prüf, welcher Fall zutrifft:
  - Protokolladresse, nichts abziehen: Das Handbuch sagt "protocol
    address", "PDU address", "offset", "0-based" oder "zero-based", die
    Tabelle beginnt bei 0 (oder 40000, 30000), oder die Adressen sind
    hexadezimal (z. B. 0xE004).
  - Registernummer ab 1: Das Handbuch sagt "register number", "PLC
    address", "Modicon", "1-based" oder "address = register - 1", oder die
    Tabelle beginnt bei 1. Zieh 1 ab.
  - Registernummer mit Präfix (40001 oder 400001 für Holding-, 30001 oder
    300001 für Input-Register, "4x"/"3x reference"): Zieh das Präfix ab
    (aus 40001 wird 0, aus 40101 wird 100).
  "address" oder "register address" allein belegen keinen der Fälle:
  Handbücher nutzen sie für beides. Hat die Tabelle eine Spalte für die
  Nummer und eine für die Adresse, nimm die Adresse. Schreib Adressen wie
  die Quelle: Eine hexadezimale Adresse bleibt hexadezimal. Schreib oben
  ins Profil, wie das Handbuch zählt, welche Wörter oder welche Tabelle das
  zeigen und was du abgezogen hast. Fehlen die Hinweise oder widersprechen
  sie sich, schreib oben "# Frage: ...".
  Quellen aus dem Netz schreiben entweder die Nummer des Handbuchs oder
  die Protokolladresse (z. B. Adresse 13049 in einer Integration für
  Register 13050 des Handbuchs): Schreib oben zu jeder Quelle, welche sie
  schreibt, und zieh nur von Nummern des Handbuchs ab.
- Register, für die das Handbuch Funktionscode 04 (Input-Register) nennt,
  bekommen input: true. Eine Adresse wie 3xxxx allein belegt das nicht:
  Manche Geräte lesen alle Register mit 03. Register, die SLEMS schreibt,
  sind immer Holding-Register.
  Steuert das Handbuch die Batterie über Coils (Funktionscode 05 oder 15),
  schreib oben "# Frage: Steuerung über Coils - das Format kann keine Coils
  schreiben".
- Jeder Schritt unter control.apply braucht type, auch uint16: Ein Wert
  über zwei Register (32 Bit) ist uint32, int32 oder float32, mit
  word_order, wenn das niedrige Wort zuerst kommt. Das Format kennt keine
  64-Bit-Typen: Lass solche Register weg (z. B. 64-Bit-Zähler).
- Nenne für jedes Leistungsregister Einheit und Vorzeichen aus dem Handbuch
  in einem Kommentar (z. B. „positiv: Entladen“). battery_power_w braucht
  positive: charge oder discharge, ac_power_w positive: input (aus dem Netz
  in die Batterie) oder output (ins Haus); steht das Vorzeichen nicht im
  Handbuch, schreib "# unsicher: Vorzeichen". Eine Leistung kann im
  Handbuch auf drei Arten stehen:
  - ein Register mit Vorzeichen (z. B. int16, -1500 = Laden, +1500 =
    Entladen): Gib es als ein Register an; positive nennt die Richtung, in
    der der Wert positiv ist (hier positive: discharge);
  - zwei Register ohne Vorzeichen, eines je Richtung (z. B. "Charge
    power" und "Discharge power", beide 0 oder größer): Gib beide als Paar
    an (siehe oben);
  - ein Register ohne Vorzeichen, dessen Richtung nur ein anderes Register
    zeigt (z. B. ein Statusbit "charging"): Das kann SLEMS nicht lesen.
    Nimm ein anderes Register derselben Leistung mit Vorzeichen oder lass
    den Schlüssel weg.
- read braucht battery_power_w oder ac_power_w oder beide. ac_power_w ist
  die Leistung am AC-Anschluss zu Haus und Netz, nie ein Notstrom- oder
  EPS-Ausgang.
- control.type: split für ein Register zum Laden und eines zum Entladen,
  signed für ein Register mit Vorzeichen (mit positive), magnitude für ein
  Register ohne Vorzeichen, dessen Richtung ein Befehls- oder
  Modus-Register setzt (dann "{power_w}" und "{mode}"). Gib nie einem
  Register zwei Platzhalter.
- scale ist der Faktor vom Rohwert zum Wert in %, W, kWh, V oder °C:
  Wert = Rohwert × scale (0.1 für 0,1 %, 10 für 10-W-Einheiten, 1000 für
  kW). Unter control gilt derselbe Faktor: SLEMS schreibt Rohwert =
  Leistung in W / scale (bei scale 10 wird aus 2500 W der Rohwert 250).
  Nennt das Handbuch einen Gain (Wert = Rohwert / Gain), ist scale die
  Einheit in W, %, … geteilt durch den Gain: kW mit Gain 1000 ergibt
  scale 1 (Rohwert in W), % mit Gain 10 ergibt scale 0.1.
- Nenne über jedem Schritt unter control.apply in einem Kommentar: Nummer
  und Name des Registers im Handbuch, Seite oder Tabelle und seine
  Bedeutung.
- Jeder Schritt unter control.apply braucht at_rest (oder at_rest_values)
  aus dem Handbuch: den Bereich des Rohwerts, solange die Batterie ruht.
  Darin müssen jeder feste Wert, alle Werte von modes und jeder Wert der
  Freigabe liegen, bei einem Leistungsschritt auch 0 und die
  Höchstleistungen als Rohwerte (max_charge_w / scale).
  Ein Register mit Kennwerten (z. B. 0xA5A5 = an, 0x5A5A = aus) bekommt
  at_rest_values mit genau diesen Werten, nie einen Bereich; ein Timeout
  oder eine Dauer bekommt seinen Bereich aus dem Handbuch.
- Lass ein Schreibregister ohne eindeutigen Eintrag im Handbuch weg; rate
  nie. Schreib nur Register, die SLEMS zum Laden, Entladen und Freigeben
  braucht; keine Einstellungen, Grenzen, Schutz- oder Reset-Register.
- Einstellungen, die das Handbuch einmalig vor der Fernsteuerung verlangt
  (z. B. Fernsteuerung freischalten, Laden aus dem Netz erlauben, ein
  Standardmodus), werden nicht geschrieben: Nenne jede oben als
  "# Frage: ...". Braucht die Freigabe auto eine solche Einstellung (z. B.
  einen Speichermodus, der auf Fernsteuerung und zurück geschaltet wird),
  schreib sie in apply und release mit persistent: true.
- Register, die das Handbuch als Einstellung, Parameter, EEPROM oder Flash
  beschreibt, bekommen persistent: true. Nie ein Register mit einem
  Leistungs-Platzhalter. Sagt das Handbuch dazu bei einem Schreibregister
  nichts, schreib oben "# Frage: Wird <Register> dauerhaft gespeichert?".
- Reihenfolge in apply: zuerst die Fernsteuerung einschalten und das
  Timeout-Register (Watchdog, Heartbeat, Fernsteuerungs-Timeout; siehe
  keepalive_s) setzen, dann die Leistungen, dann den Modus. So beginnt
  eine neue Richtung mit gültigen Werten, und die Batterie fällt bei
  einem Ausfall von SLEMS schon zurück.
- release: Gib auto und/oder standby an, je nachdem, was das Handbuch
  unterstützt und was hält, ohne dass SLEMS erneut schreibt; mindestens
  einen der beiden. Fällt die Batterie nach einem Timeout auf einen
  eingestellten Modus zurück, lass diesen Zustand weg oder schreib
  "# Frage: ...". Ein Zustand, der nur bis zum Ende eines Timeouts aus
  apply hält (z. B. eines Heartbeats oder der Dauer einer Zwangsladung),
  hält nicht: Lass ihn weg. Eine Freigabe schreibt nur Register, die auch
  in apply stehen, und nur feste Zahlen:
  - Leistungsregister auf 0. Nutzt die eigene Logik der Batterie diese
    Register als Obergrenze, schreib stattdessen die Höchstleistung,
    sonst bliebe sie auf 0 W begrenzt.
  - das Modusregister auf den Wert für idle.
  - bei auto die Fernsteuerung aus.
- confirm: true an jedem Schreibregister, das sich laut Handbuch
  zurücklesen lässt.
- min_power_w nur, wenn das Handbuch eine kleinste Leistung nennt; nie
  schätzen.
- keepalive_s: Nennt das Handbuch eine Zeit, nach der die Batterie ohne
  neue Vorgabe in ihre eigene Logik zurückfällt (Timeout, Watchdog,
  Heartbeat), setz keepalive_s deutlich darunter (z. B. die Hälfte):
  SLEMS schreibt dann spätestens nach dieser Zeit alle Schritte erneut,
  auch wenn sich nichts ändert, und die Batterie fällt nicht zurück.
  Nennt das Handbuch keine solche Zeit, lass keepalive_s weg; SLEMS
  schreibt dann alle 60 s alle Schritte erneut. Ist dieses Timeout (oder
  die Dauer einer Zwangsladung) ein Register, schreib es als festen Wert
  unter apply, so kurz wie möglich: etwa zwei- bis viermal keepalive_s.
  Hat nur ein Modus der Batterie ein solches Timeout (z. B. einen
  Heartbeat), nimm diesen Modus, wenn die Quellen belegen, dass die
  Steuerregister darin wirken. Gibt es keines, schreib oben "# Frage: Kein
  Timeout – fällt SLEMS aus, gilt die letzte Vorgabe weiter".
- message_spacing_s und timeout_s nur, wenn das Handbuch einen kleinsten
  Abstand zwischen Anfragen oder eine Antwortzeit für Modbus TCP nennt;
  Angaben nur für RS485 (Modbus RTU) gelten nicht. Sagt es nichts,
  schreib oben "# Frage: kleinster Abstand zwischen Anfragen?".
- timing: weglassen (SLEMS lernt die Reaktionszeiten), außer das Handbuch
  nennt eine Verzögerung der Vorgaben oder einen kleinsten Abstand zwischen
  ihnen.
- device_info: die Firmware-Versionen und die Seriennummer, falls
  vorhanden: Daran erkennt SLEMS ein Firmware-Update. Bei einem
  Hybrid-Wechselrichter die Firmware von Wechselrichter und Batterie.
- probe: das Register des Ladezustands mit seinem Rohbereich (z. B.
  [0, 1000] bei 0,1 %).
- state: nur die oben genannten Zustände; Rohwerte, die zu keinem passen,
  weglassen. Ein Zustand über zwei Register ist uint32 oder int32.
- own_pv nur, wenn PV-Module direkt an dieses Gerät angeschlossen sind
  (nicht für eine PV-Anlage mit eigenem Wechselrichter). Dann braucht read
  zwei Leistungen: pv_power_w, die Leistung dieser PV-Module, und
  ac_power_w, die Leistung am AC-Anschluss des Geräts. Batterie und PV
  teilen sich einen Wechselrichter; SLEMS rechnet die Batterieleistung aus
  beiden zurück. Gibt die Quelle die AC-Leistung als zwei Register ohne
  Vorzeichen an und fehlt das für den Eingang (aus dem Netz), geht das nur,
  wenn das Gerät nicht aus dem Netz laden kann: dann ac_charge: false.
  Ein Hybrid-Wechselrichter mit PV-Eingängen hat eigene PV, wenn seine
  PV-Leistung als Register mit festem Faktor lesbar ist (nicht nur mit
  einem eigenen Skalierungsregister wie bei SunSpec): dann own_pv mit
  setpoint: battery, passes_pv_through: true und max_output_w gleich der
  AC-Nennleistung des Wechselrichters. Sonst kein own_pv; schreib dann oben
  "# Frage: PV dieses Wechselrichters in SLEMS als PV-Anlage eintragen
  (Erzeugung der Module, nicht die AC-Leistung des Wechselrichters)".
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
  Handbuchs, oder die Quellen aus dem Netz).

Ein Profil wird mitgeliefert, sobald es bei mindestens einer, besser zwei
Batterien den Probebetrieb bestanden hat, seine Schreibregister gegen das
Handbuch oder mehrere Quellen geprüft sind und es einen Test im Repository
hat.
