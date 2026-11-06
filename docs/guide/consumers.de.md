# Verbraucher

[Zurück zur README](../../README.de.md#verbraucher) · [English](consumers.md)

<!-- toc -->

- [Einstellungen](#einstellungen)
- [Freigabe für eine eigene Automation](#freigabe-für-eine-eigene-automation)
  - [Wärmepumpe mit Freigabe (Warmwasser)](#wärmepumpe-mit-freigabe-warmwasser)
- [Weitere Einstellungen und Verhalten](#weitere-einstellungen-und-verhalten)
- [Stromvorgabe](#stromvorgabe)
- [Auto an der Wallbox](#auto-an-der-wallbox)
- [Temperaturfühler des Speichers](#temperaturfühler-des-speichers)
- [Batterie-Unterstützung](#batterie-unterstützung)
- [Einsatz in der Einspeisebegrenzung](#einsatz-in-der-einspeisebegrenzung)
- [Tagesziel](#tagesziel)

<!-- /toc -->

Jeder Verbraucher braucht einen eigenen Leistungs- **und** Energiesensor.

Welche Verbraucher anlegen: die, die SLEMS steuert (Heizstab, Wallbox,
Luftentfeuchter, …), und Wärmepumpen (ihr Verbrauch wird wetterabhängig
prognostiziert). Andere Geräte nur zum Messen anzulegen, z. B. Waschmaschine
oder Geschirrspüler, verbessert die Planung derzeit nicht: Ihr Verbrauch ist
bereits im Hausverbrauch enthalten, und dessen Prognose (ein Profil je
Wochentag und Stunde) enthält ihren durchschnittlichen Verbrauch. Sie
bekommen nur eine eigene Karte und ein Feld im Energiefluss.

## Einstellungen

- **Gerät** (erster Schritt, optional): das Gerät des Verbrauchers in Home
  Assistant, z. B. die Wärmepumpe, der Heizstab oder die Wallbox. Die am
  besten passenden Geräte stehen oben (nach ihrer Integration und den
  Entitäten, die sie haben); Geräte, deren Entitäten SLEMS schon verwendet,
  fehlen in der Liste. SLEMS schlägt daraus die Entitäten der
  nächsten Schritte vor: Leistung und Energie (bei einer Wärmepumpe die
  elektrische Leistung, nicht die Heizleistung), die Steuerung und ihre
  Steuer-Entität mit ihrem Bereich, die Start/Stopp-Entität und die aktiven
  Phasen einer Wallbox, den Betriebsmodus und den Speicher einer Wärmepumpe,
  die Speicherfühler eines Heizstabs und seinen Water Heater als externe
  Sperre. Du prüfst und korrigierst sie. **Messgerät**: optional, wenn ein
  anderes Gerät den Verbraucher misst (z. B. eine smarte Steckdose).
- **Heizt einen Warmwasserspeicher** (Wärmepumpe): nur dann fragt SLEMS ihren
  Betriebsmodus (bei **Freigabe**) und die Temperatur des Speichers ab.
- **Im Smart Meter enthalten**: aktivieren, wenn der Verbraucher hinter dem
  Smart Meter hängt (sein Verbrauch ist bereits in der Netzleistung
  enthalten). Für Verbraucher an einer separaten Versorgung deaktivieren.
- **Im Energiefluss anzeigen** (Standard: an): aus blendet die Box des
  Verbrauchers im Energiefluss des Dashboards aus; sein Verbrauch zählt weiter
  im Haus, und die Verbraucherkarte zeigt ihn weiterhin.
- **Typ**: **Wärmepumpe** (Heizung und Warmwasser, wetterabhängige Prognose),
  **Heizpatrone** (z. B. Warmwasser im Sommer), **Wallbox** (Elektroauto) oder
  **Sonstiges**. Wärmepumpe und Heizpatrone dürfen gleichzeitig laufen. Eine
  Wallbox gehört nie zur Prognose des Hausverbrauchs (auch nur gemessen); ihre
  Vorgaben sind Stromvorgabe, 6–16 A, 3 Phasen, 5 Minuten Mindestlaufzeit und
  Mindestpause und Batterie-Unterstützung **Automatisch**.
- **Steuerung**: keine (nur Messung), Ein/Aus über einen Schalter, eine
  Leistungsvorgabe über eine Number-Entität in W, eine
  [Stromvorgabe](#stromvorgabe) in A oder eine **Freigabe** für eine eigene
  Automation. Für gesteuerte Verbraucher
  kann eine Entität für eine externe Sperre gewählt werden (SLEMS steuert den
  Verbraucher nicht, solange ein Schalter oder Binärsensor eingeschaltet ist
  oder ein Water Heater in der Betriebsart **off** steht), dazu eine Priorität
  (1 = höchste) und optional eine Mindestlaufzeit und Mindestpause. Bei
  Ein/Aus-Verbrauchern reicht für die Leistung im eingeschalteten Zustand eine
  grobe Schätzung, wenn **Gelernte Werte verwenden** an ist; lieber zu niedrig
  als zu hoch, weil SLEMS nur lernt, während es den Verbraucher betreibt.

## Freigabe für eine eigene Automation

Für Geräte, die eine eigene Automation startet, weil sie mehr wissen muss als
SLEMS (z. B. eine Waschmaschine mit Startbefehl, oder die Warmwasserladung
einer Wärmepumpe über SG Ready nur, wenn ihr Kompressor gerade stabil läuft).
SLEMS schaltet selbst nichts, plant den Verbraucher aber wie einen
Ein/Aus-Verbraucher mit der eingestellten Leistung und stellt bereit:

- **Freigabe** (Binärsensor): an, solange SLEMS den Verbraucher einschalten
  würde (genug Überschuss, mit **Takten vermeiden** für einen ganzen Lauf,
  oder ab dem spätesten Start seines Tagesziels).
- **Erzwungener Lauf** (Binärsensor): an, solange die Freigabe ab dem
  spätesten Start des Tagesziels gilt: dann sofort starten, auch ohne
  Überschuss.
- **Spätester Start** (Sensor, Zeitpunkt): wann das Tagesziel spätestens
  starten muss; leer, wenn es erreicht ist.

Auf der Verbraucherkarte unter **Freigabe**:
- **Freigabedauer** (Minuten, 0 = aus): Einmal geöffnet, bleibt die Freigabe
  mindestens so lange offen, auch ohne Überschuss, bis das Tagesziel
  erreicht oder seine Frist vorbei ist. Für eine Automation, die auf den
  richtigen Moment wartet, z. B. einen Kompressorlauf der Wärmepumpe.
  Mit der Quelle **Nur Überschuss** gilt das nur, solange der Verbraucher
  läuft: Bis dahin schließt die Freigabe ohne Überschuss und öffnet bei neuem
  Überschuss wieder. Anders als die Mindestlaufzeit zählt sie nicht für
  **Takten vermeiden**: SLEMS plant nur die Energie, die der Verbraucher
  wirklich aufnimmt.
- **Vor dem erzwungenen Start** (mit einem Tagesziel, dessen Quelle über
  den Überschuss hinausgeht): Die **Freigabedauer** liegt vor dem spätesten Start.
  SLEMS gibt den Verbraucher ab spätestem Start − Freigabedauer frei, auch ohne
  Überschuss, aber nicht erzwungen; **Erzwungener Lauf** erst ab dem
  spätesten Start. Die Karte zeigt, wann die Freigabe öffnet („Freigabe
  12:20–14:20 · erzwungen ab 14:20“).
- **Überschuss-Fenster** (mit Tagesziel): Aus PV- und Verbrauchsprognose
  plant SLEMS das Fenster, so lang wie die **Freigabedauer**, mindestens wie
  der Lauf (vom frühesten Beginn bis zum erzwungenen Lauf, die noch nötige
  Energie mit der gelernten Leistung). Weil die Automation jederzeit im
  Fenster starten kann, zählen alle Startzeiten darin: Gewählt wird das
  Fenster, bei dem am wenigsten Energie aus Batterien oder Netz nötig ist,
  bei Gleichstand das mit dem meisten Überschuss. Ein Lauf endet mit dem
  Fenster, reicht aber bis zur Mindestlaufzeit darüber hinaus, eine
  Warmwasserladung bis zu ihrem Ende. **Nach Überschuss**: freigegeben,
  sobald genug Überschuss da ist, dann für die **Freigabedauer** und
  anschließend solange er reicht, ohne geplantes Fenster. **Nach Prognose**:
  Karte (Zeile **Freigabe geplant**, mit der Quelle **Nur Überschuss**
  **Überschuss-Fenster**) und Tagesdiagramm zeigen es, und es wird
  zusätzlich für das geplante Fenster freigegeben, auch wenn der Überschuss
  kurz fehlt, damit die Automation auf ihren Moment warten kann; dann kann ein
  Teil aus Batterien oder Netz kommen. Mit der Quelle **Nur Überschuss** wird
  das Fenster nur angezeigt. Einmal offen,
  bleibt das Fenster wie geplant, bis es schließt.

Startet die Automation das Gerät noch nicht, zieht es nichts: SLEMS lässt
die Freigabe dann an und gibt den Überschuss solange den Batterien (wie bei
einem gesättigten Verbraucher). Mit der Quelle **Nur Überschuss** schließt sie
dabei, sobald der Überschuss nicht mehr reicht.

Die **Mindestlaufzeit** einer Freigabe zählt ab dem Start des Geräts (ab 50 W),
nicht ab dem Öffnen. Bis zum Start bleibt die Freigabe auch ohne Überschuss bis
zu 10 Minuten offen, höchstens so lange wie die Mindestlaufzeit: So nimmt eine
kurze Wolke sie nicht zurück, bevor die Automation starten konnte oder der
Leistungsmesser den Start zeigt.

### Wärmepumpe mit Freigabe (Warmwasser)

Mit dem Typ **Wärmepumpe** gilt
die Freigabe ihrer Warmwasserladung. Dazu gehören der **Betriebsmodus**
(z. B. ihr Sensor **Status**) und im nächsten Schritt die Zustände, die
Warmwasser bedeuten; vorausgewählt ist, was nach Warmwasser klingt (z. B.
`hot_water`, `dhw`, „Warmwasser“). Nur in diesen Zuständen zählt ihre
gemessene Leistung als gesteuert; das Heizen bleibt eine ungesteuerte Last
und ihre Prognose unverändert. Die Leistung im eingeschalteten Zustand ist
ein Startwert: Mit **Gelernte Werte verwenden** lernt SLEMS sie im
Warmwasserbetrieb, sie folgt so der Leistungszahl. Mit den Fühlern des
Boilers als Temperaturfühler eignet sich ein Tagesziel **Temperatur**
(z. B. Minimum 42 °C, Ziel 50 °C, Frist 16:00). Die Entitäten des
Tagesziels, der Freigabe und der Steuerung tragen „Warmwasser“ im Namen
(z. B. **Warmwasser-Freigabe**, **Erzwungene Warmwasserladung**,
**Spätester Warmwasser-Start**,
**Warmwasser-Steuerung aktiv**); die Verbraucherkarte zeigt den Schalter der
Steuerung im eigenen Abschnitt **Warmwasser-Steuerung** statt im Menü (⋮). Ein Tagesziel
**Energie** passt hier nicht, weil der Energiezähler das Heizen enthält.
Die [Anleitung zum Warmwasser einer Wärmepumpe](../heat-pump-hot-water.de.md)
zeigt eine ausführliche Automation, die auf einen guten Moment für den
Kompressor wartet. Kurzes Beispiel:

```yaml
triggers:
  - trigger: state
    entity_id: binary_sensor.waermepumpe_warmwasser_freigabe
    to: "on"
  - trigger: state
    entity_id: binary_sensor.waermepumpe_erzwungene_warmwasserladung
    to: "on"
  - trigger: state
    entity_id: binary_sensor.heat_pump_running  # deine eigene Bedingung
    to: "on"
conditions:
  - condition: state
    entity_id: binary_sensor.waermepumpe_warmwasser_freigabe
    state: "on"
  - or:
      - condition: state
        entity_id: binary_sensor.waermepumpe_erzwungene_warmwasserladung
        state: "on"
      - condition: state
        entity_id: binary_sensor.heat_pump_running
        state: "on"
actions:
  - action: number.set_value
    target:
      entity_id: number.heat_pump_smart_grid_offset
    data:
      value: 38
```

## Weitere Einstellungen und Verhalten

- **Takten vermeiden** (z. B. ein Gerät mit Kompressor): SLEMS schaltet den
  Verbraucher nur ein, wenn der prognostizierte Überschuss für einen ganzen
  Lauf reicht (seine Mindestlaufzeit, ohne eine 15 Minuten, oder was sein
  Tagesziel noch braucht, wenn weniger), und lässt ihn bei Einbrüchen des
  Überschusses bis zu 5 Minuten weiterlaufen (Batterien oder Netz
  überbrücken). Erzwungene Läufe eines Tagesziels bleiben unverändert. Dazu
  eine Mindestlaufzeit und Mindestpause setzen.
- **Eigener Thermostat** (keine Einstellung, beobachtet): Ein Verbraucher, der
  trotz Vorgabe nichts abnimmt, gilt 15 Minuten als gesättigt und behält seine
  letzte Vorgabe (bei **Geplant** als „gehalten“ angezeigt); sobald er wieder die
  Hälfte dieser Vorgabe abnimmt, steuert SLEMS ihn sofort wieder, ebenso wenn
  sein Tagesziel erreicht ist oder auf seinen Zeitraum wartet (nach seiner
  Mindestlaufzeit): Dann wird er ausgeschaltet, eine Freigabe endet. Schaltet ihn
  sein eigener Thermostat während der Ansteuerung aus und wieder ein (z. B. ein
  Heizstab, der am Heizelement misst: zwei Pausen von 30 s bis 10 min innerhalb
  von 30 Tagen), steuert SLEMS ihn stattdessen weiter: In einer Pause (keine
  Leistung für 2 Reaktionszeiten, 10–60 s) bekommen die Batterien seine nicht
  genutzte Leistung, und sobald er wieder abnimmt, bekommt er sie zurück
  (angezeigt als **Thermostat-Pause**).
- **Ausschalten und Herunterregeln** (keine Einstellung): Was ein Verbraucher
  über seine Vorgabe hinaus abnimmt, bis das Ausschalten wirkt, zählt als
  Hausverbrauch; die Batterien decken es nach seiner
  **Batterie-Unterstützung**, bis er wirklich weniger abnimmt.
- **Läuft eigenständig** (keine Einstellung, beobachtet): Ein Verbraucher, den
  SLEMS ausgeschaltet hat (0 W, aus oder Freigabe geschlossen), der aber
  weiter mindestens 100 W abnimmt, läuft eigenständig, z. B. eine von Hand
  gestartete Warmwasser-Ladung, die eine geschlossene Freigabe nicht beendet.
  Nach 10 s und dem 1,5-Fachen seiner Reaktionszeit beim Ausschalten (am
  Zähler gelernt, sonst am eigenen Sensor, sonst 10 s) plant SLEMS ihn wie
  einen nicht gesteuerten Verbraucher, bis er wieder weniger abnimmt; dann
  steuert SLEMS ihn wieder.
- **Steuerung aktiv** (Schalter je steuerbarem Verbraucher, im Dashboard über
  ⋮ → **Steuerung einschalten** oder **Steuerung ausschalten**): aus bedeutet, SLEMS misst den Verbraucher nur. Beim
  Ausschalten im Betriebsmodus **Aktiv** setzt SLEMS ihn einmal auf 0 W (bzw.
  aus); danach lässt SLEMS ihn in Ruhe und plant ihn wie eine ungesteuerte
  Last: Sein Tagesziel pausiert und nichts von ihm geht in die Prognose ein
  (die Karte zeigt **Steuerung aus**); läuft er von selbst, sieht die Regelung
  das live.

## Stromvorgabe

Für Wallboxen und evcc-Ladepunkte: SLEMS plant in Watt und sendet ganze
Ampere, abgerundet, damit die Ladung im geplanten Rahmen bleibt.

- **Mindest- / Höchststrom** (Standard 6 / 16 A): unter dem Mindeststrom wird
  der Verbraucher gestoppt. Sein Leistungsbereich ist Strom × Spannung ×
  Phasen (3 Phasen: 4,1–11 kW, 1 Phase: 1,4–3,7 kW); ein Ampere sind 230 W je
  Phase.
- **Phasen** fest (1 oder 3) oder eine **Entität der aktiven Phasen** (1 oder
  3, z. B. einer Wallbox oder eines evcc-Ladepunkts, der selbst umschaltet);
  der Leistungsbereich folgt ihr. SLEMS schaltet die Phasen nicht selbst um.
- **Spannung** (Standard 230 V je Phase).
- **Start/Stopp-Entität** (optional): ein Schalter oder eine Auswahl mit den
  Optionen für ein und aus (im nächsten Schritt abgefragt), z. B. der
  Modus eines evcc-Ladepunkts (**now** / **off**). Ohne sie stoppt SLEMS,
  indem es den kleinsten Strom setzt, den die Steuer-Entität zulässt (0 A,
  wenn erlaubt).
- Die Steuer-Entität ist eine Number-Entität in A oder eine Auswahl mit
  Ampere-Optionen.
- Mit evcc ([ha-evcc](https://github.com/marq24/ha-evcc)): Steuer-Entität ist
  der **Max. Ladestrom** des Ladepunkts (eine Auswahl), Phasen-Entität seine
  aktiven Phasen. Schritt für Schritt mit Screenshots:
  [Wallbox mit evcc anbinden](../wallbox-evcc.de.md). Ohne
  Start/Stopp-Entität entscheidet evcc selbst über Start und Stopp (z. B. im
  PV-Modus), und SLEMS begrenzt nur den Strom; mit dem Lademodus als
  Start/Stopp-Entität steuert SLEMS das Laden vollständig, dann entscheiden
  seine Batterie-Unterstützung und Prognosen. Die Karte zeigt die geplante
  Leistung mit Ampere und Phasen, z. B. **4.140 W (6 A, 3 Phasen)**.

## Auto an der Wallbox

Optional, nur beim Verbrauchertyp **Wallbox**, im Schritt **Steuerung**. Mit
evcc oder einer go-e schlägt SLEMS die Entitäten des Geräts vor.

- **Ladestand des Fahrzeugs**: Sensor mit dem Ladestand des Autos in %. Die
  Wallbox selbst kennt ihn beim AC-Laden nicht; er kommt aus evcc (der
  **Fahrzeug Ladestand** eines Ladepunkts; evcc weiß, welches Auto
  angesteckt ist) oder aus der Integration des Autos.
- **Akkukapazität des Fahrzeugs** (kWh): nötig für ein Tagesziel
  **Ladestand**.
- **Auto angesteckt**: ein Binärsensor (an = angesteckt, z. B. **Verbunden**
  eines evcc-Ladepunkts) oder der Fahrzeugstatus der Wallbox, z. B. einer
  go-e. Beim Fahrzeugstatus fragt der nächste Schritt, welche Zustände
  „angesteckt“ bedeuten, auch wenn das Auto gerade nicht lädt (wartet,
  fertig); vorausgewählt ist, was danach klingt, fehlende Zustände lassen
  sich eintippen. Ohne Auto steuert SLEMS die Wallbox nicht und plant nichts
  für sie ein: kein erzwungener Lauf eines Tagesziels, kein Anteil am
  Überschuss in der Prognose. Ist das Auto wieder angesteckt, gilt alles wie
  vorher.
- Die Box der Wallbox im Energiefluss zeigt mit einem Auto-Symbol den
  Ladestand, ohne Ladestand-Sensor **angesteckt**; ohne Auto ist sie
  ausgegraut und zeigt **nicht angesteckt**. Die Karte der Wallbox zeigt
  beides in der Zeile **Auto**.
- **Sofort laden** (Schalter jeder gesteuerten Wallbox, im Dashboard
  **Sofort laden starten** im Menü (⋮) ihrer Karte oder ihrer Box im
  Energiefluss; mit Ladestand-Sensor fragt der Dialog nach **Bis**): lädt
  mit voller Leistung, unabhängig vom Überschuss und vom Tagesziel. Die
  Batterien helfen nach der [Batterie-Unterstützung](#batterie-unterstützung)
  der Wallbox mit, den Rest liefert das Netz. Der Schalter geht von selbst
  aus, wenn das Auto abgesteckt wird oder den Ladestand **Sofort laden bis**
  erreicht (Standard 100 %, nur mit Ladestand-Sensor); eingeschaltet, bevor
  ein Auto angesteckt ist, wartet er darauf. Die Planung rechnet mit der
  Ladung bis zu diesem Ladestand, wenn Ladestand und Akkukapazität bekannt
  sind. Die Box im Energiefluss zeigt dann **lädt sofort**.

## Temperaturfühler des Speichers

Optional, bis zu zwei, z. B. der Fühler eines Boilers am Heizstab und einer
weiter oben. Ein Water Heater (z. B. das Warmwasser einer Wärmepumpe) zählt
mit seiner Ist-Temperatur.

- SLEMS lernt aus dem Mittel der Fühler (und für jeden Fühler einzeln), wie
  viel Energie der Speicher je Grad aufnimmt, ab welcher Temperatur der
  Thermostat zu takten beginnt, wie hoch die mittlere Leistung beim Takten ist
  und wann er voll ist. Welcher Fühler den Thermostat schaltet und wo sie
  sitzen, spielt keine Rolle. Werden andere Fühler ausgewählt, lernt SLEMS
  neu.
- Ab drei Heizläufen und zwei Taktbeginnen zeigt die Verbraucherkarte, wie
  viel der Speicher noch aufnehmen kann, und die Einspeisebegrenzung plant den
  Verbraucher nur mit 80 % davon ein.
- Seine Box im Energiefluss zeigt mit einem Boiler-Symbol die Temperatur des
  für das Tagesziel gewählten Fühlers (standardmäßig den Mittelwert).

## Batterie-Unterstützung

Auswahl je Verbraucher hinter dem Smart Meter (auch nur gemessene), auf seiner
Karte unter **Einstellungen anzeigen**: wie weit die Batterien ihn decken
dürfen, wenn kein PV-Überschuss da ist.

- **Immer** (Standard): wie jede andere Last.
- **Nie**: Seine Leistung kommt immer aus dem Netz; die Batterien decken nur
  den Rest des Hauses.
- **Automatisch**: Die Batterien decken ihn nur mit der Energie, die sie übrig
  haben, dem **Spielraum**: die niedrigste gespeicherte Energie bis zur nächsten
  Ladung aus PV (aus der Prognose von Hausverbrauch und PV, ohne
  Nachtentladung) minus minimalem Ladezustand, **Morgenreserve** und
  Sicherheitspuffer; mit Bezugsspitzen-Kappung auch deren SoC-Schwelle. „Bis
  zur nächsten Ladung“ heißt nachts bis zum kommenden Morgen, während eines
  Überschusses bis zum Morgen nach der kommenden Nacht, an einem Tag, dessen
  prognostizierter PV-Überschuss die Batterien vom minimalen bis zum
  maximalen Ladezustand füllt (etwas Überschuss an einem trüben Tag zählt
  nicht, der Spielraum schaut dann bis zum nächsten solchen Tag). Der Spielraum wird
  laufend aus dem aktuellen Ladezustand neu berechnet und schrumpft, solange
  die Batterien den Verbraucher decken; ist er aufgebraucht, läuft der
  Verbraucher aus dem Netz, bis wieder 0,2 kWh Spielraum da sind. Mehrere
  Verbraucher auf Automatisch teilen sich den Spielraum. Energie, die die
  Nachtentladung einspeisen würde, dürfen sie stattdessen nutzen.

Der PV-Überschuss ist nicht betroffen, die Bezugsspitzen-Kappung deckt Spitzen
weiter, und ein Zwangslauf eines Tagesziels mit der Quelle **Batterie** darf die
Batterien nutzen. Die Zwangsläufe von Tageszielen mit der Quelle **Netz**
folgen der Einstellung auch im Prognose-Diagramm (bei Automatisch bis zur
nächsten Ladung aus PV; spätere werden wie **Immer** geplant); nur gemessene
Verbraucher stecken in der Verbrauchsprognose, bei ihnen wirkt die Einstellung
nur in der Regelung. Die Karte zeigt die Einstellung mit dem Spielraum und ob
die Batterien den Verbraucher gerade decken; läuft er deshalb aus dem Netz,
trägt seine Box im Energiefluss das Abzeichen **Netz**. Der Spielraum ist auch
das Attribut `support_budget_kwh` von **Gespeicherte Energie gesamt**.

Nützlich für Lasten, die abends die Batterien leeren würden: Sauna,
Durchlauferhitzer, Heizstab mit Quelle **Netz**, später eine Wallbox.

## Einsatz in der Einspeisebegrenzung

Auswahl je gesteuertem Verbraucher, auf seiner Karte im Dashboard unter
**Einspeisebegrenzung**, solange sie an ist. Innerhalb eines Einsatzes
entscheidet die Priorität. Wirkt nur, solange **Steuerung aktiv** an ist (sonst
ausgegraut). Siehe **Einspeisebegrenzung**.

- **Unterstützend**: bekommt bei aktiver Einspeisebegrenzung keinen sonstigen
  Überschuss, nur den Überschuss über der Grenze, den die Batterien nicht
  aufnehmen können; zeigt die Prognose, dass eine Spitze nicht in die
  Batterien passt, läuft er ab Beginn der Spitze, damit seine Leistung über
  die ganze Spitze genutzt wird.
- **Normal** (Standard): Überschuss wie ohne Einspeisebegrenzung; über der
  Grenze nimmt er, was Batterien und unterstützende Verbraucher nicht
  schaffen, bevor abgeregelt wird.
- **Nie**: Überschuss wie ohne Einspeisebegrenzung, nie den Überschuss über der
  Grenze.

## Tagesziel

Je gesteuertem Verbraucher, auf seiner Karte unter **Tagesziel**.

- **Art**: **Laufzeit** (Zeit, in der er Leistung zieht), **Freigabezeit** (Zeit,
  in der SLEMS ihn eingeschaltet hat, für Geräte mit eigener Regelung wie
  einen Luftentfeuchter mit Hygrostat), **Energie** (kWh), **Temperatur** (mit
  Temperaturfühlern: Mindest- und Zieltemperatur seines Speichers; mit zwei
  Fühlern wählt **Fühler** den Mittelwert, Fühler 1 oder Fühler 2, in der
  Reihenfolge der Konfiguration des Verbrauchers) oder **Ladestand** (eine
  Wallbox mit dem Ladestand und der Akkukapazität des Autos, siehe
  [Auto an der Wallbox](#auto-an-der-wallbox)).
- **Zeitraum**: gezählt von Frist (**Bis**, Standard 22:00, auch über
  Mitternacht) zu Frist und zuerst aus dem Überschuss erfüllt.
- **Frühester Beginn** (optional): Vorher schaltet
  SLEMS den Verbraucher nicht ein, auch nicht mit Überschuss (z. B. ein
  Luftentfeuchter erst ab 10:00); die späteste Startzeit liegt nie davor.
  Passen die Stunden (oder die Energie bei voller Leistung) nicht zwischen
  frühesten Beginn und Frist, zeigen die Einstellungen des Tagesziels eine
  Warnung.
- **Keine Leistung**: Nimmt ein Verbraucher mit Tagesziel an 3 Tagen in Folge
  keine Leistung auf, obwohl SLEMS ihn eingeschaltet hat (mindestens 30
  Minuten am Tag, weniger, wenn das Ziel oder sein Zeitfenster kürzer ist),
  zeigen seine Karte und sein Feld im Energiefluss einen Hinweis
  (ausgeschaltet, Sicherung oder defekt?), bis er wieder Leistung aufnimmt.
- **Quelle** bestimmt, was den Rest rechtzeitig decken darf:
  - **Nur Überschuss** (Standard; das Ziel kann verfehlt werden, dann meldet es
    eine Benachrichtigung – nicht, wenn SLEMS den Verbraucher in der Periode
    zeitweise nicht steuern konnte: Betriebsmodus nicht **Aktiv** oder seine
    **Steuerung aktiv** ausgeschaltet – oder wenn er keine Leistung aufnahm, weil
    sein eigener Thermostat zufrieden war (gesättigt oder ruhend); war er
    extern gesperrt oder kein Auto angesteckt, nennt die Benachrichtigung, wie
    lange),
  - **Überschuss + Batterie** (ab der spätesten Startzeit läuft der Verbraucher
    unabhängig vom Überschuss, solange die Batterien liefern können, ein
    leistungsgeregelter höchstens mit ihrer Entladeleistung),
  - **Überschuss + Batterie + Netz**.

  Die späteste Startzeit ist die Frist minus Restzeit × 1,2 minus 10 Minuten.
- **Vor der Batterie, wenn knapp**: Der Verbraucher bekommt den Überschuss vor
  den Batterien, wenn der prognostizierte Überschuss bis zur Frist für den
  Rest seines Ziels plus das Füllen der Batterien knapp ist.
- **Temperatur**: Unter der Mindesttemperatur bekommt er den Überschuss immer
  vor den Batterien (ab der spätesten Startzeit erzwungen, geschätzt mit der
  gelernten Energie pro Grad, sonst 2 Stunden vor der Frist); ab der
  Zieltemperatur ist er für den Rest des Tages aus; wird später am Tag eine
  höhere Zieltemperatur eingestellt, heizt er weiter. Ein Temperaturziel gilt
  für den Kalendertag: Nach der Frist bleibt der Verbraucher bis Mitternacht
  aus (**wartet bis 00:00**), auch mit Überschuss; mit einem frühesten Beginn
  bis dahin (z. B. Warmwasser über eine Wärmepumpe erst ab 11:00, wenn die
  Luft wärmer ist und Überschuss da ist). Fällt die Temperatur wieder
  unter das Minimum (z. B. nach Warmwasserentnahme), ist das Ziel wieder
  offen: bis zum Minimum mit Vorrang, danach mit dem Überschuss bis zum Ziel.
  Wird ein anderer Fühler gewählt, gilt das Ziel wieder als offen.
  SLEMS lernt, wie der Speicher je Stunde des Tages abkühlt, während er nicht
  geheizt wird (Wärmeverluste und die üblichen Zapfungen, etwa die letzten
  zwei Wochen). Kennt es 12 Stunden des Tages, sagt es die Temperatur zur
  Frist voraus: Fällt der Speicher bis dahin unter das Minimum, plant es den
  erzwungenen Lauf (und eine Freigabe davor) für diese Temperatur, auch
  solange er noch darüber liegt und vor dem frühesten Beginn. Die Zeile
  **Plan** zeigt die Vorhersage, z. B. **ohne Heizen um 15:30 ca. 37 °C**.
- **Minimum spätestens wieder nach** (Temperaturziel, optional, z. B. 3 h;
  0: aus): ein Sicherheitsnetz für die Zeit, die das Ziel nicht abdeckt,
  etwa Warmwasser nach dem Duschen am Abend. Fällt die Temperatur unter das
  Minimum und ist die Frist weiter weg als diese Zeit (nach der Frist, vor
  dem frühesten Beginn oder früh im Zeitraum), setzt SLEMS ein Ersatzziel:
  das Minimum bis dahin wieder (z. B. um 20:00 darunter gefallen → Minimum
  bis 23:00, auch über Mitternacht). Es wird wie eine Frist erfüllt: zuerst
  aus dem Überschuss mit Vorrang, mit der Preissteuerung im günstigsten
  Netzfenster, am Ende erzwungen, nach der **Quelle** (mit **Nur Überschuss**
  ohne erzwungenen Lauf). Es endet, sobald das Minimum erreicht ist; danach
  wartet der Verbraucher wieder wie gewohnt. Wird das Minimum nicht
  rechtzeitig erreicht, bleibt er erzwungen, und eine Benachrichtigung
  **Minimum von … nicht rechtzeitig erreicht** meldet das einmal (nicht, wenn
  SLEMS den Verbraucher in der Zeit nicht steuern konnte oder sein eigener
  Thermostat ablehnte; eine externe Sperre nennt sie mit ihrer Dauer). Das
  Tagesziel selbst (erreicht, verfehlt, Zieltemperatur) bleibt davon
  unberührt. Solange es gilt, zeigt die Karte den Chip **Sicherheitsnetz** und
  **Minimum bis 23:00** statt der Frist, die Zeile **Plan** Freigabe und
  erzwungenen Lauf dafür, und die Bahn unter dem Tagesdiagramm das Ersatzziel
  gestrichelt umrandet mit einem gestrichelten Strich an seinem Ende.
- **Ladestand**: Ziel-Ladestand des Autos bis zur Frist (Standard 80 %). Die
  noch nötige Energie ist der fehlende Ladestand × Akkukapazität, mit 10 %
  Ladeverlusten (z. B. 40 % von 60 kWh: ca. 26,7 kWh aus dem Netz); daraus
  folgt die späteste Startzeit wie bei einem Energieziel. Ohne bekannten
  Ladestand lädt die Wallbox nur mit Überschuss. Ist der Ziel-Ladestand
  erreicht, bleibt die Wallbox bis zur Frist aus (nach ihrer
  Mindestlaufzeit), auch wenn der Ladestand danach etwas sinkt; ein höherer
  Ziel-Ladestand gilt wieder als offen. Die Karte zeigt z. B. **62 / 80 % · bis
  07:00**, ohne Auto zusätzlich **nicht angesteckt**.
- **Anzeige**: Die Karte zeigt den Fortschritt, z. B. **1,5 / 4 h** (wartet das
  Ziel auf den nächsten Tag, heißt die Zeile **Morgen** statt **Heute**), die **Details**
  ausführlich, z. B. **1,5 / 4 h · bis 22:00 · noch ca. 2,3 kWh**, in der Zeile
  **Plan**, wann SLEMS den Rest einplant,
  z. B. **Freigabe 12:20–14:20 · erzwungen ab 14:20 · dauert ca. 1 h 5 min**,
  und die Chips **Vorrang** oder
  **erzwungen**, solange sie gelten; die Box des Verbrauchers im Energiefluss
  trägt dasselbe Abzeichen, und die Kachel Strategie nennt ihn (**Vorrang vor
  den Batterien: …**), weil die Batterien dann nur bekommen, was übrig bleibt. Die noch benötigte Energie ist bei einem
  Energieziel genau; bei Laufzeit oder Freigabezeit ist es die Restzeit mit
  voller Leistung (weniger, wenn der eigene Thermostat früher abschaltet); bei
  einem Temperaturziel der Weg bis zur Zieltemperatur mit der gelernten
  Energie pro Grad. Darin fehlen Warmwasserzapfungen und Wärmeverluste, daher
  gilt mindestens der mittlere Tagesverbrauch des Verbrauchers an seinen
  letzten 7 Tagen mit Verbrauch (mindestens 2) abzüglich dessen, was er in der
  Periode schon bekommen hat (**geschätzt aus den letzten Tagen**); diese
  Schätzung gilt auch, solange die Energie pro Grad noch nicht gelernt ist,
  ohne solche Tage **Energie wird noch gelernt**.
- **Planung**: Mit einer Quelle über den Überschuss hinaus rechnet die Planung
  den erzwungenen Lauf ab der spätesten Startzeit als zusätzlichen Verbrauch
  ein, so als deckte der Überschuss nichts mehr; der geplante Lauf schrumpft,
  sobald der Überschuss das Ziel füllt. Den Rest des Ziels erwartet die
  Planung aus dem Überschuss: Stunde für Stunde nimmt der Verbraucher, was nach
  dem Laden der Batterien übrig bleibt, in der Reihenfolge der Priorität, ab
  jetzt (oder dem frühesten Beginn) bis zur Frist und höchstens mit seiner
  Leistung; die erwartete Einspeisung sinkt entsprechend. Das Ziel der
  folgenden Periode plant SLEMS genauso ein (ein Temperaturziel mit dem
  Tagesverbrauch der letzten Tage), es erscheint also auch unter **Morgen**. Von
  einem gesteuerten Verbraucher ohne Tagesziel erwartet die Planung seinen
  mittleren Tagesverbrauch der letzten Tage aus dem Überschuss (heute den Rest
  davon). Beides erscheint im
  Tagesdiagramm als **Verbraucher (geplant)** und geht in Verbrauchsprognose,
  Ladezustandsprognose und Nachtentladung ein. Unter dem Tagesdiagramm zeigt
  **Tagesziele** je Verbraucher mit Tagesziel eine Bahn: seinen Zeitraum
  (blass), die Freigabe (das geplante Fenster und die Freigabe vor dem
  erzwungenen Lauf, als ein Abschnitt, wo sie sich überlappen), das geplante
  Fenster in der PV-Farbe umrandet, den erzwungenen Lauf und die Frist
  (Strich); eine Legende darunter nennt, was zu sehen ist. Während eine
  Warmwasserladung läuft, bleibt die Freigabe, in der sie begonnen hat; die
  noch kommende Freigabe und der erzwungene Lauf weichen der Ladung
  (schraffiert, bis etwa zur Zieltemperatur); bricht die Ladung früher ab,
  etwa für ein Abtauen, erscheinen sie wieder. Ein Verbraucher mit dem Einsatz
  **Unterstützend** bleibt beim Überschuss-Teil außen vor, solange die
  Einspeisebegrenzung an ist: Sie plant ihn mit dem Überschuss über der
  Grenze ein.
