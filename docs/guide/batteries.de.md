# Batterien

[Zurück zur README](../../README.de.md#batterien) · [English](batteries.md)

## Marstek Venus E 3.0

Host/IP, Port (502) und Modbus Unit-ID. Die Batterie akzeptiert nur **eine**
Modbus-TCP-Verbindung. Lass nie zwei Integrationen (z. B. SLEMS und
Omnibattery) gleichzeitig mit derselben Batterie sprechen. Ihr Sensor
**AC-Leistung** ist beim Entladen positiv und beim Laden negativ, wie es das
Energie-Dashboard von Home Assistant für die Batterieleistung erwartet.

### Fernsteuerung

Die Venus folgt Vorgaben nur, solange ihre Fernsteuerung an ist; die
Registerliste von Marstek nennt sie **RS485 control mode**. Trotz des Namens
braucht sie kein RS485-Kabel: SLEMS steuert die Venus über das LAN (Modbus
TCP) und schaltet die Fernsteuerung selbst ein und aus; einzustellen ist
nichts.

### Suche

Beim Hinzufügen einer Venus sucht SLEMS zuerst im Netz von Home Assistant nach
Batterien (TCP-Port 502, dann ein Probe-Lesen des Ladezustands; wenige
Sekunden) und bietet die gefundenen an; bereits hinzugefügte fehlen in der
Liste. Eine Batterie, deren einzige Modbus-Verbindung eine andere Integration
hält, kann nicht gefunden werden.

### IP-Adresse finden

Modbus TCP gibt es nur am **LAN-Anschluss** der Batterie (mit Netzwerkkabel
verbinden), nicht über WLAN. Die Marstek-App zeigt die LAN-IP-Adresse nicht
an; sie steht im Router / Internet-Gateway / DHCP-Server (Liste der
verbundenen Geräte). Vergib der Batterie dort eine feste Adresse
(DHCP-Reservierung), damit sie sich später nicht ändert. Alternativ lässt sich
das Netz nach Geräten mit offenem Modbus-Port durchsuchen, z. B. mit
[nmap](https://nmap.org) (Netz an deines anpassen):

```bash
nmap -p 502 --open 192.168.0.0/24
```

Jede Adresse mit `502/tcp open` ist ein Modbus-TCP-Gerät (mit `sudo` im
selben Netz zeigt nmap auch die MAC-Adresse, das hilft bei mehreren
Batterien). Beim Hinzufügen prüft SLEMS die Verbindung, indem es den
Ladezustand liest; andere Integrationen, die die Batterie verwenden, vorher
stoppen.

## Vorhandene Home-Assistant-Entitäten

Für jede Batterie, die eine andere Integration in Home Assistant einbindet;
die Steuerung ist experimentell. Zuerst das Gerät der Batterie wählen: SLEMS
schlägt seine Entitäten vor (Ladezustand, Leistung, Sollwerte, Modus, optional
Temperatur, Zellspannungen und Energiezähler); bitte prüfen und korrigieren.
Ohne Gerät wählst du alles selbst. Die Batterie eines Hybrid-Wechselrichters,
den du als PV-Anlage eingerichtet hast, oder eine Batterie aus dem
Energie-Dashboard steht oben und ist vorausgewählt (dann auch als Modell);
Ladezustand, Energiezähler und Leistung mit ihrem Vorzeichen aus dem Dashboard
werden übernommen.

- **Nur lesen**: SLEMS sendet nie Befehle, z. B. um SLEMS im
  Betriebsmodus **Nur beobachten** parallel zu einer bestehenden
  Batterie-Integration laufen zu lassen.
- **Sollwert**: eine Number-Entität mit der Leistung mit Vorzeichen (+Laden /
  −Entladen, Vorzeichen umkehrbar).
- **Getrennte Lade- und Entladeleistung**: je eine Number, optional eine
  Modus-Auswahl (Laden / Entladen / Standby / Automatik, die Optionen werden
  im nächsten Schritt zugeordnet).
- **Skript**: ein eigenes Skript erhält die Leistung als Variable `power_w`
  (W, +Laden / −Entladen, 0 = Standby); optional ein Freigabe-Skript. Damit
  lassen sich auch Batterien steuern, die über Aktionen (Dienste) bedient
  werden. SLEMS wartet, bis das Skript fertig ist (höchstens 10 Sekunden),
  damit Fehler auffallen; es sollte daher kurz sein (ohne Wartezeiten).
- **Ausgang**: eine Number mit dem Ausgang ins Haus, für eine Batterie mit
  **Eigene PV-Module an der Batterie** (siehe unten).

### Eigene PV-Module an der Batterie

Für eine Batterie, deren PV-Module nur über den Ausgang der Batterie ins Haus
liefern, z. B. ein Balkonspeicher (Marstek B2500, Zendure SolarFlow, Anker
Solarbank) oder ein Hybrid-Wechselrichter. Wähle die Leistung der Module, den
Ausgang ins Haus und den höchsten Ausgang (z. B. 800 W). SLEMS zählt die
Module als PV und die Batterieleistung als eigene PV + Eingang − Ausgang,
damit die Bilanz stimmt; trag die Module oder den Ausgang nicht zusätzlich als
PV-Anlage ein. SLEMS weist darauf hin und bietet an, sie dort zu entfernen;
auch die Systemeinstellungen nehmen sie bei **PV-Leistung** nicht an. Mit der
Steuerung **Ausgang** setzt SLEMS Ausgang = eigene PV − Ladeleistung und
schreibt ihn neu, wenn sich die eigene PV ändert. Ohne Eingang aus dem Netz
lädt eine solche Batterie höchstens, was ihre Module liefern (nachts keines),
und entlädt höchstens den höchsten Ausgang abzüglich ihrer eigenen PV: bei 600
W PV und 800 W Ausgang bleiben 200 W. PV über dem höchsten Ausgang lädt sie in
jedem Fall, außer sie ist voll. **Erlaubte Ladeleistung** und **Erlaubte
Entladeleistung** zeigen dann den Grund `pv`. Ihre Sensoren **Leistung eigene
PV**, **Ausgangsleistung** und mit Eingang **Eingangsleistung** zeigen die
Werte. Mit dem Zustand bei Freigabe **Standby** bleiben Eingang und Ausgang
bei 0 W, die Batterie lädt dann nur aus ihren Modulen. Nimm Entitäten für
Ausgang und Eingang, die das Gerät nicht dauerhaft speichert: häufige Vorgaben
verschleißen seinen Flash-Speicher.

Ein Gerät mit AC-Eingang (z. B. Zendure SolarFlow mit **inputLimit** und
**acMode**) bekommt den Sensor **Eingang aus dem Netz** und mit der Steuerung
**Ausgang** die Number für den Eingang und optional eine Modus-Auswahl
(Laden: Eingang, Entladen: Ausgang). Es lädt dann über die eigene PV hinaus
aus dem Netz (Eingang = Ladeleistung − eigene PV, Ausgang 0) und nimmt am
Laden aus dem Netz teil. Ohne Eingang bleibt es dort außen vor.

**Reicht die PV bei voller Batterie durch**: schalte es ein, wenn das Gerät
bei voller Batterie seine PV unabhängig von der Vorgabe des Ausgangs ins Haus
liefert. Sonst regelt das Gerät seine Module ab, sobald die Batterie weniger
nimmt als PV − Ausgang (voll, am Ende der Ladung, durch die Temperatur
begrenzt), und der PV-Sensor zeigt nur noch den Rest. SLEMS hebt den Ausgang
dann in Schritten an (50 W, verdoppelt, solange die PV folgt), um die PV
wiederzufinden, und hält den Ausgang über dem, was die Batterie nimmt. Gewollt
regelt nur die **Einspeisebegrenzung** eine solche Batterie ab: Können weder
die Batterien noch die Verbraucher den Überschuss über der Grenze nehmen,
senken die Batterien mit eigener PV ihren Ausgang, bis die Einspeisung an der
Grenze liegt; das geht nur mit der Steuerung **Ausgang**. Der Sensor **Abgeregelte eigene PV** zeigt, wie viel sie
abregeln.

**PV-Prognose der Module** (optional): der Prognose-Eintrag der Module der
Batterie (z. B. eine Forecast.Solar-Fläche des Balkons). Er zählt zur
PV-Prognose der Anlage, auch wenn er in den Systemeinstellungen nicht
ausgewählt ist. In der Prognose des Ladezustands, der Einspeisebegrenzung, dem
Preisplan, der Nachtentladung (Wiederaufladen am nächsten Tag), der
automatischen Grenze der Spitzenkappung und der Simulation lädt die Batterie
höchstens diese Prognose und nur aus ihr;
ohne den Eintrag lädt sie dort nicht, und ihre eigene PV bleibt aus der
PV-Korrektur (Ist / Prognose) heraus.

Optional wird ein Schalter oder eine Auswahl für die **Fernsteuerung** (z. B.
**Manuelle Batteriesteuerung** bei Omnibattery) vor der ersten Vorgabe
eingeschaltet und bei der Freigabe an die **Automatik** wieder aus, damit die
Batterie wieder selbst regelt; leer lassen, wenn du sie selbst schaltest.
Zum Steuern ist der Sensor der
Batterieleistung Pflicht (mit eigenen PV-Modulen nimmt SLEMS stattdessen
eigene PV + Eingang − Ausgang). **Mindestabstand zwischen Befehlen** passt für
Integrationen mit begrenzter Befehlsrate (oft über eine Cloud), **Vorgabe
wiederholen alle** für Batterien, die ohne neue Befehle auf ihre eigene Logik
zurückfallen. Die eigene Regelung der Batterie (z. B. ihre Nulleinspeisung)
in ihrer Integration ausschalten, sonst regeln beide gleichzeitig.

### Reaktionszeit

Von der Vorgabe, bis der Smart Meter sie zeigt, addiert sich der Weg: die
Integration, die den Befehl schreibt (oft in ihrem eigenen Abfragetakt, über
eine Cloud auch mit Ratenbegrenzung), die Batterie selbst und das
Aktualisierungsintervall des Smart Meters. SLEMS lernt die Reaktionszeit je
Batterie (**Details** der Batterie) und getrennt davon die längeren Zeiten
eines Starts aus dem Standby und eines Richtungswechsels (Laden ↔ Entladen),
mit denen die Regelung bei solchen Schritten rechnet: Ein Richtungswechsel
stoppt sofort und startet erst danach die andere Richtung. Gemessen an einer
Anlage mit derselben Marstek Venus E 3.0 und dem Smart Meter per Modbus: etwa
0,5–0,9 s direkt per Modbus, etwa 1,8 s bis zur neuen Richtung eines
Richtungswechsels, etwa 2,7 s aus dem Standby (der Wechselrichter tut etwa 2 s
lang nichts, egal wie lange er stand), etwa 4 s über Omnibattery (Entitäten);
Batterien über eine Cloud sind meist noch langsamer. Die gelernte Zeit ist der
Median der letzten Messungen; eine Messung zählt nicht, wenn sich der Smart
Meter bewegt hat, bevor die Batterie reagieren konnte (siehe [Reaktionszeit
und Phase messen](#reaktionszeit-und-phase-messen)), oder viel weiter als der
Schritt (ein anderes Gerät hat im selben Moment geschaltet), und Befehle, die
noch unterwegs sind, werden eingerechnet, damit die vielen Korrekturen der
Regelung hintereinander die Zeit nicht zu kurz machen. Ein Start kurz nach
einem Stopp zählt nicht als Start. Eine langsamere Batterie bedeutet mehr
Netzaustausch nach Laständerungen und eine gröbere Regelung; zusammen mit
schnelleren Batterien übernehmen diese die Änderungen zuerst (siehe
[Aufteilung auf die Batterien](#aufteilung-auf-die-batterien)). Mit
Zellspannungen nutzt SLEMS sanftes Laden nahe voll, das Zell-Delta und den
Zellausgleich wie bei einer Venus (LFP-Zellen). Diese Steuerung ist mit
simulierten Entitäten und mit einer Marstek Venus E 3.0 über Omnibattery
getestet; Erfahrungen mit anderen Geräten bitte als
[Issue](https://github.com/gojux/SLEMS/issues) melden.

### Direkt oder über eine andere Integration?

Wo SLEMS eine Batterie direkt unterstützt (Marstek Venus E 3.0 per Modbus),
ist das die bessere Wahl: Sie reagiert schneller, es gibt weniger
Zwischenschritte, und SLEMS bekommt mehr Daten (Zellspannungen, Zähler,
Firmware, OTA-Zustand). Der Weg über eine andere Integration ist sinnvoll,
wenn die Batterie nicht direkt unterstützt wird, wenn diese Integration die
einzige Modbus-Verbindung der Batterie behalten soll oder wenn kein
Netzwerkkabel hinreicht. Der Unterschied zeigt sich vor allem bei schnellen
Laständerungen (Kochen, Wasserkocher).

### Ein weiteres Batteriemodell?

Lässt es sich über Modbus TCP steuern, versuch eine [Batterie aus einem
Modbus-Profil](battery-modbus-profile.de.md). Soll SLEMS ein Modell direkt
unterstützen, bitte ein [Issue](https://github.com/gojux/SLEMS/issues) anlegen
mit Hersteller und Modell, Schnittstelle (Modbus TCP/RTU, lokale API, nur
Cloud), Registerliste oder API-Doku, falls vorhanden, und der
Home-Assistant-Integration, die es dafür gibt.

### Beispiel: eine Batterie über Omnibattery

Für ein Batteriemodell, das SLEMS nicht direkt unterstützt, kann
[Omnibattery](https://github.com/ffunes/Omnibattery) die Kommunikation und
SLEMS die Regelung übernehmen:

1. In Omnibattery für die Batterie **Manuelle Batteriesteuerung** einschalten,
   damit ihre eigene Regelung stoppt.
2. In SLEMS die Batterie als **Vorhandene Home-Assistant-Entitäten** hinzufügen
   und das Omnibattery-Gerät wählen. SLEMS schlägt die Entitäten vor
   (Ladezustand, Leistung, Lade- und Entladeleistung, Betriebsmodus,
   Zellspannungen, Zähler); diese prüfen.
3. Automationen, die die Batterie steuern, ausschalten, damit nur SLEMS
   Vorgaben sendet.

## Batterie aus einem Modbus-Profil (experimentell)

Eine Batterie, die sich über Modbus TCP steuern lässt, beschrieben durch ein
Profil, das mit einem KI-Prompt aus ihrem Handbuch erstellt wird; siehe
[Batterie aus einem Modbus-Profil](battery-modbus-profile.de.md).

## Zustand bei Freigabe

In welchem Zustand SLEMS die Batterie lässt, wenn es die Steuerung beendet, z.
B. im Betriebsmodus **Aus**, ohne Werte des Smart Meters oder wenn die
Batterie aus SLEMS entfernt wird. **Automatik** (Standard): die eigene Logik
der Batterie übernimmt wieder (z. B. ihre Nulleinspeisung); bei einer Batterie
aus Entitäten braucht das eine Fernsteuerungs-Entität, eine Modus-Option für
Automatik oder ein Freigabe-Skript. **Standby**: die Batterie bleibt bei 0 W
stehen, bis etwas anderes sie übernimmt; eine Venus bleibt dafür in der
Fernsteuerung.

## Aufteilung auf die Batterien

Bei mehreren Batterien entscheidet SLEMS, wie viele und welche laufen: Bei
kleiner Leistung ist meist eine einzelne Batterie effizienter, bei großer das
Aufteilen. Beim Entladen läuft die Batterie mit dem höchsten Ladezustand, beim
Laden die mit dem niedrigsten. Entfernt sich die laufende Batterie um mehr als
die **Schwelle Batteriewechsel** (Standard 5 %) von der besten inaktiven, wird
gewechselt, höchstens einmal pro **Mindestabstand Batteriewechsel** (Standard 15
min) und mit sanftem Übergang: Die Leistung wandert mit der **Rampe
Batteriewechsel** (Standard 100 W/s), aber nie länger als die **Maximale
Übergangszeit Batteriewechsel** (Standard 30 s). Die Umwandlungsverluste je
Leistungsbereich lernt SLEMS aus AC- und DC-Leistung der Batterie.

Reagieren die Batterien deutlich unterschiedlich schnell (gelernte
Reaktionszeiten, die langsamste mindestens doppelt und 3 s langsamer als die
schnellste, z. B. eine Venus über Modbus neben einer Batterie über eine
Cloud-Integration), übernimmt die schnellste Änderungen der Gesamtleistung
zuerst; danach wandert die Leistung mit der Reaktionszeit der langsameren zur
effizienten Aufteilung. Keine Batterie arbeitet gegen die Richtung der
Gesamtleistung.

Jede Batterie hat einen Schalter **Aktiviert**. Eine deaktivierte Batterie wird
weiter gemessen (ihre Leistung gehört zur Energiebilanz), aber weder
eingeplant noch gesteuert und zählt nicht zum Gesamt-Ladezustand. Entlädt sie
im Modus **Aktiv** gerade, übernehmen die anderen Batterien innerhalb von 5
Sekunden, bevor sie an ihre eigene Logik zurückgegeben wird.

## Verschleißkosten der Batterie

Optional in der Batterie-Konfiguration: Anschaffungspreis und Zyklen laut
Hersteller. Daraus berechnet SLEMS die Verschleißkosten je kWh, die
eingespeichert und wieder abgegeben wird: Preis ÷ (Zyklen × nutzbare
Kapazität), z. B. 1200 € ÷ (6000 × 5 kWh) = 4 ct/kWh. Preisbewusste Aktionen
mit zusätzlichem Zyklus (Laden aus dem Netz) finden nur statt, wenn sie mehr
einbringen. Ein Preis von 0 bedeutet keine Verschleißkosten: Feldmessungen an
Heimspeichern zeigen, dass sie vor allem mit der Zeit, der Temperatur und dem
Ladezustand altern und oft ihr Lebensende erreichen, bevor ihre Zyklen
aufgebraucht sind. Ohne Angaben nimmt SLEMS einen niedrigen Schätzwert von
1 ct/kWh an; die **Details** der Batterie weisen dann darauf hin. Der Sensor
**Verschleißkosten** zeigt den Wert.

## Wirkungsgrad der Batterie

Gesamtwirkungsgrad (AC zu AC), je Batterie aus
einer von drei Quellen:

- **Batteriezähler** (empfohlen für die Marstek Venus E 3.0 und für Batterien
  aus Entitäten mit Energiezählern): aus den Gesamtzählern der Batterie für
  Laden und Entladen und ihrem Ladezustand: (entladen + gespeichert) /
  geladen. Die Batterie zählt selbst, schnell und über ihre ganze
  Betriebszeit; der Wert ist daher sofort genau und stabil. Einzige Annahme
  ist eine leere Batterie zu Beginn der Zähler; ihr Einfluss verschwindet nach
  wenigen Zyklen.
- **Gelernt**: SLEMS summiert die gemessene Batterieleistung selbst (alle 5
  Sekunden) ab dem Start von SLEMS. Das zählt erst nach etwa drei vollen
  Ladezyklen (bis dahin gilt der Startwert), und kurze Leistungsspitzen
  zwischen zwei Abfragen gehen verloren. Empfohlen für Batterien ohne eigene
  Zähler.
- **Manuell**: ein fester Wert, z. B. aus dem Datenblatt.

Der Wirkungsgrad wird überall dort verwendet, wo Energie umgerechnet wird: ob
der PV-Überschuss die Batterien füllt (gesicherte Ladung), beim netzdienlichen
Laden, bei der Nachtentladung und in der Ladezustands-Prognose. Er entscheidet
**nicht**, wie viele Batterien laufen: Dafür lernt SLEMS je Batterie eine
eigene Verlustkurve aus dem Unterschied von AC- und DC-Leistung bei jeder
Leistung (fester Verlust eines laufenden Wechselrichters plus mit der Leistung
steigende Verluste). Daraus berechnet die Aufteilung die Anzahl Batterien mit
dem geringsten Gesamtverlust: bei kleiner Leistung eine Batterie, oberhalb des
Break-even-Punkts mehrere (siehe **Aufteilung auf die Batterien**). Das funktioniert mit
jeder Wirkungsgrad-Quelle, aber nur für Batterien, die AC- und DC-Leistung
melden (Marstek Venus E 3.0).

## Umstieg von einer anderen Batterie-Integration

Übernimmt SLEMS eine Batterie von einer anderen Integration (z. B.
Omnibattery), lässt sich der Verlauf der alten Sensoren (z. B. geladene und
entladene Energie im Energie-Dashboard) mit [HA Merge Sensor
History](https://github.com/mayerwin/HA-Merge-Sensor-History) auf die
SLEMS-Sensoren übertragen. Das Werkzeug kopiert Zustände und
Langzeitstatistiken, sodass die Summen im Energie-Dashboard weiterlaufen.

- Vorher ein vollständiges Backup von Home Assistant machen; das Werkzeug
  schreibt direkt in die Datenbank.
- Das Vorzeichen von Leistungssensoren vergleichen: Die **AC-Leistung** von
  SLEMS ist beim Entladen positiv.
- Danach im Energie-Dashboard die alten Sensoren durch die SLEMS-Sensoren
  ersetzen und die alten Entitäten löschen, wenn alles stimmt.

## Zell-Delta und aktiver Zellausgleich

Für Batterien, die ihre Zellspannungen melden (Marstek Venus E 3.0), zeigt
SLEMS das **Zell-Delta** (höchste minus niedrigste Zellspannung). Bei LFP-Zellen
ist der Live-Wert nur nahe der Vollladung aussagekräftig: In der Mitte ist die
Spannungskurve so flach, dass ungleiche Zellen fast dieselbe Spannung zeigen.
SLEMS erfasst daher das **Zell-Delta am oberen Ladeende**: nachdem die höchste
Zelle 3,60 V erreicht oder das BMS die Ladung bei 100 % beendet hat und die
Batterie danach 60 Sekunden im Standby war (einschließlich der etwa 13 W, die
eine Venus selbst braucht). Gemessen wird einmal pro Ladung: Steht die
Batterie voll, entspannen sich die Zellen, und das Delta sinkt weiter, ohne
dass der Ausgleich besser wird; die nächste Messung folgt daher erst, nachdem
die Batterie aus dem oberen Bereich entladen wurde (höchste Zelle unter 3,49
V), auch nach einem Neustart von SLEMS. Die Kurve ist dort steil;
Marstek-Zellen zeigen ab Werk typischerweise etwa 180 mV, das ist normal.
Status: unter 200 mV gut, unter 230 mV leichtes, unter 250 mV mittleres, sonst
starkes Ungleichgewicht. Ab 230 mV empfiehlt das Dashboard den aktiven
Zellausgleich.

Der aktive Zellausgleich (Schalter **Aktiver Zellausgleich** oder die
Schaltfläche im Dashboard) folgt dem Ausgleichs-Blueprint von Omnibattery. Er
lässt sich nur im Betriebsmodus **Aktiv** starten:

1. Entlädt die Batterie gerade, übergibt sie zuerst sanft (wie beim
   Deaktivieren). Danach verlässt sie die normale Planung; die anderen
   Batterien übernehmen.
2. Sie lädt, bis die höchste Zelle 3,49 V erreicht, mit dem PV-Überschuss (vor
   den anderen Batterien), mindestens mit 95 W. Reicht der Überschuss nicht,
   entladen die anderen Batterien, um die 95 W auszugleichen.
3. Sie lädt mit 95 W, bis die höchste Zelle 3,60 V erreicht, ist 60 Sekunden
   im Standby und misst das Zell-Delta.
4. Sonst entlädt sie mit 200 W bis zur Wiederholspannung (3,49 V) und
   wiederholt ab Schritt 3. Verweigert das BMS das Laden (weniger als 30 W
   statt 95 W, z. B. weil es bei etwa 3,55 V voll meldet), misst sie nicht
   (das Delta unter 3,60 V ist kleiner und nicht vergleichbar), entlädt bis zur
   Wiederholspannung (3,49 V oder 10 mV unter der Spannung, bei der es
   verweigert hat) und versucht es erneut.
5. Sie entlädt mit 200 W bis 3,48 V und endet, wenn das Delta im normalen
   Bereich liegt (höchstens 190 mV), wenn es 6 Stunden lang nicht um 2 mV
   gesunken ist oder nach 24 Stunden.

Das BMS gleicht die hohen Zellen passiv aus, nur wenige mV pro Tag; ein Lauf
senkt ein großes Delta schrittweise, erreicht aber nicht die 0 mV eines
Laborladegeräts. Liegt die letzte Messung schon im normalen Bereich, weist das
Dashboard vor dem Start darauf hin.

Die Entladung der ausgleichenden Batterie wird eingespeist; die anderen
Batterien speichern sie nicht (sie laden nur weiter, wenn sie ohnehin laden).
Ihre erwartete Ladung fließt in den erwarteten PV-Überschuss und das
netzdienliche Laden ein. Mit einem Fehler endet er, wenn die Batterie nicht
gelesen werden kann oder die Abschlussentladung 2 Stunden nach den 24 Stunden
noch nicht fertig ist; außerhalb des Betriebsmodus
**Aktiv** pausiert er, und nach einem Neustart von Home Assistant läuft er
weiter. Zum Ende wird die Batterie an ihre eigene Logik zurückgegeben und
kehrt danach in die normale Planung zurück. Der Sensor **Phase Zellausgleich**
zeigt die Phase und das Ergebnis des letzten Laufs.

## Selbsttest einer Batterie

Der Selbsttest prüft, ob eine gesteuerte Batterie tut, was SLEMS vorgibt, und
ob ihre Werte stimmen. Er ist vor allem für Batterien über Home-Assistant-
Entitäten gedacht, bei denen Vorzeichen, Einheiten und Verzögerungen der
Integration oft anders sind als erwartet. Beim Hinzufügen einer Batterie ist
**Nach dem Speichern einen Selbsttest machen** vorausgewählt; er startet, sobald
SLEMS im Betriebsmodus **Aktiv** ist. Jederzeit lässt er sich im Batterie-Menü
(⋮ → **Selbsttest starten**) oder mit dem Schalter **Selbsttest** starten.

Ablauf, zusammen etwa 5–10 Minuten:

1. Entlädt die Batterie gerade, übergibt sie zuerst sanft.
2. 60 Sekunden Ruhe (0 W): der Bezugswert.
3. Laden mit einem Viertel der kleineren Höchstleistung, mindestens 300 W,
   höchstens 800 W, bis Smart Meter und Batterie mindestens die Hälfte davon
   zeigen, dann noch 45 Sekunden und bis sich der Ladezähler bewegt hat
   (höchstens 4 Minuten).
4. 60 Sekunden Ruhe, dann Entladen wie beim Laden.
5. Rückgabe an die eigene Logik der Batterie direkt aus dem Entladen (oder dem
   letzten Schritt), bis zu 60 Sekunden am Smart Meter beobachtet, während die
   anderen weiter halten; danach normale Planung.

Darf die Batterie gerade nicht laden (z. B. voll) oder nicht entladen (leer),
fällt dieser Schritt aus. Unter 20 % oder über 90 % (mindestens 5
Prozentpunkte innerhalb des Ladezustandsfensters der Batterie) hält sich eine
Batterie womöglich selbst zurück (z. B. eine Entladegrenze in ihrer App). Dann
schafft der Test Platz: Unter 20 % lädt er nach dem Ladeschritt weiter bis
20 %, über 90 % entlädt er zuerst und weiter bis 90 % (mit bis zu 800 W,
mindestens der Testleistung), danach folgt die andere
Richtung. Dabei wird nichts gemessen, und die anderen Batterien und die
Verbraucher laufen normal (die anderen Batterien decken das Laden womöglich
ab). Ist das Ziel nach 30 Minuten nicht erreicht, fällt die andere Richtung aus
und die Prüfung **Platz für beide Richtungen** ist unklar. Während der Messung halten die anderen Batterien und
die Verbraucher ihre letzten Vorgaben, damit der Smart Meter nur die Wirkung der
getesteten Batterie zeigt; es kann also etwas Energie aus dem Netz kommen oder
eingespeist werden. SLEMS rechnet die gemessene Leistung der anderen Batterien
zusätzlich heraus.

Geprüft wird:

- **Wirkung am Smart Meter**: Bewegt er sich um mindestens die Hälfte der
  Vorgabe in die richtige Richtung? Bewegt er sich nicht, kommt der Befehl
  nicht an (falsche Entität oder falsches Register, Fernsteuerung nicht
  freigegeben, Batterie nicht hinter dem Smart Meter); in die Gegenrichtung:
  die Vorgabe ist invertiert; um mehr als das Doppelte: die Vorgabe hat einen
  falschen Faktor. Läuft der Smart Meter 10 Sekunden lang in die falsche
  Richtung oder mehr als doppelt so weit (und bestätigt die Leistung der
  Batterie das), endet der Test sofort, und SLEMS gibt die Batterie frei,
  statt mit einer falschen Vorgabe weiterzulaufen.
- **Vorzeichen und Höhe der Batterieleistung**: Ändert sich die gemeldete
  Leistung wie am Smart Meter? Erkennt ein umgekehrtes Vorzeichen und einen
  falschen Faktor (z. B. kW statt W); eine kleinere Abweichung deutet auf die
  DC- statt der AC-Leistung.
- **Reaktionszeit** bis zur Wirkung am Smart Meter (über 60 Sekunden: träge).
- **Energiezähler**: der Ladezähler steigt beim Laden, der Entladezähler beim
  Entladen, nicht der andere und nicht um ein Vielfaches. Bewegen sich grobe
  Zähler im kurzen Test nicht, gilt das nicht als Fehler.
- **Ladezustand**: zwischen 0 und 100 %, ohne Sprünge, nicht gegen die
  Richtung.
- **Rückgabe** an die eigene Logik: Die Batterie bestätigt sie, und der Smart
  Meter zeigt sie. Mit dem Zustand bei Freigabe **Standby** gehen Batterie und
  Smart Meter zurück auf die Ruhe; bleibt die Batterie bei der Testleistung,
  wird sie noch ferngesteuert (Fehler). Mit **Automatik** verlässt die Batterie
  die Testleistung; will ihre eigene Logik zufällig ungefähr dieselbe
  Leistung, ist die Prüfung unklar.

Misst der Smart Meter jede Phase, lernt der Test auch die
[Phase](../../README.de.md#phase-von-batterien-und-verbrauchern) der Batterie: die Phase, an der sich die Schritte
zeigen.

Schwankt der Hausverbrauch während des Tests stark, sind die Prüfungen am Smart
Meter **unklar** statt fehlerhaft; dann den Test zu einer ruhigeren Zeit
wiederholen. Genauso bei einer fast vollen oder leeren Batterie, die nur noch
wenig laden oder entladen kann: dann den Test bei mittlerem Ladezustand
wiederholen. Ein Test, der nichts Falsches gefunden, aber nicht alles geprüft
hat, endet **teilweise unklar** und erscheint als Hinweis, nicht als Problem.
Das Ergebnis erscheint als Benachrichtigung, auf der Batteriekarte (bei
Unklarheiten, Auffälligkeiten oder Fehlern) und mit einer Erklärung je Prüfung unter
⋮ → **Details** → **Prüfergebnisse anzeigen**; der Sensor **Selbsttest Ergebnis** hält es fest. Abgebrochen
wird er außerhalb des Betriebsmodus **Aktiv**, bei pausierter Kommunikation,
wenn der Smart Meter nicht meldet oder die Batterie 30 Sekunden nicht lesbar
ist; ein Neustart von Home Assistant beendet ihn ebenfalls. Es läuft immer nur
ein Selbsttest, nicht während eines Zellausgleichs.

## Reaktionszeit und Phase messen

Eine Messung der Reaktionszeit zählt nicht, wenn sich der Smart Meter bewegt
hat, bevor die Batterie überhaupt reagieren konnte: Dann hat ein anderes Gerät
das Netz bewegt. Diese früheste Reaktion (die **Totzeit**) misst SLEMS je
Batterie mit einem kurzen Probeschritt:

1. SLEMS wartet, bis das Netz ruhig ist: Der Hausverbrauch ohne Batterien
   schwankt 10 Sekunden lang um weniger als 50 W (Standardabweichung), seit 3
   Sekunden hat sich keine Batterievorgabe geändert und seit einer Minute ging
   kein Befehl an einen Verbraucher. Die Batterie muss laufen (laden oder
   entladen).
2. Die anderen Batterien und die Verbraucher halten ihre Vorgaben (wie beim
   Selbsttest). Nach einer Sekunde Bezugswert bekommt die Batterie für etwa 3
   Sekunden 500 W mehr oder weniger (mindestens 300 W, innerhalb ihrer
   erlaubten Leistung), danach wieder ihre Vorgabe, 3 Sekunden beobachtet.
   Bei Überschuss lädt sie mehr bzw. entlädt weniger, bei Bezug umgekehrt; ist
   sie nahe voll oder leer oder an ihrer Leistungsgrenze, geht der Schritt in
   die andere Richtung. Die Richtung der Batterie wechselt dabei nie, sie läuft
   mit mindestens 100 W weiter. Am Netz kostet das etwa 0,4 Wh je Versuch.
3. Drei Versuche mit 10 Sekunden Pause. Hin- und Rückschritt sind je eine
   Messung; ausgewertet wird jede Meldung des Smart Meters. Die Totzeit einer
   Messung ist die Zeit der letzten Meldung, die sich noch nicht um 10 % des
   Schritts bewegt hat (eine Untergrenze, weil der Zähler nur etwa alle halbe
   Sekunde meldet). Unklar ist eine Messung, wenn das Netz vorher unruhig war,
   sich in die Gegenrichtung oder weiter als das 1,5-Fache bewegt hat oder 60 %
   des Schritts nicht erreicht hat.

Die Totzeit ist der Median der klaren Messungen. Bewegt sich der Smart Meter in
einer späteren Messung früher um die Hälfte des Schritts, zählt diese Messung
nicht (bei Start und Richtungswechsel kommt die Zusatzzeit des Treibers dazu).
Die Zeiten bis 60 % gehen als Messungen in die Reaktionszeit der Batterie ein.
Ohne Probeschritt gilt wie bisher die Vorgabe des Treibers (Marstek Venus
0,45 s) bzw. die Hälfte der gelernten Reaktionszeit. Meldet der Smart Meter
seine Phasen so schnell wie die Gesamtleistung, zeigt der Schritt auch die
[Phase](../../README.de.md#phase-von-batterien-und-verbrauchern) der Batterie.

SLEMS misst von selbst, sobald das Netz ruhig ist:

- bei einer neuen Batterie (auch nach dem Update auf diese Version),
- nach einer Änderung von Verbindung, Profil, Entitäten oder Ansteuerungsart
  der Batterie, ihrer Firmware, des Smart Meters (Quelle, Entität,
  Phasen-Entitäten, Firmware seines Geräts, Version einer benutzerdefinierten
  Integration) oder bei einer Batterie über Entitäten der steuernden
  Integration; dann verwirft SLEMS die bisher gelernten Reaktionszeiten der
  Batterie (Updates von Home Assistant zählen nicht),
- wenn mehr als die Hälfte der letzten 20 Messungen zu früh war,
- spätestens 90 Tage nach dem letzten Probeschritt.

Von Hand startet ihn ⋮ → **Reaktionszeit und Phase messen** oder die
gleichnamige Schaltfläche der Batterie; auch er wartet, bis das Netz ruhig ist.
Nicht gemessen wird außerhalb des Betriebsmodus **Aktiv**, während eines
Selbsttests oder Zellausgleichs, bei aktiver Einspeisebegrenzung, wenn das BMS
die Batterie begrenzt, wenn der Smart Meter nicht meldet und bei Batterien, die
Vorgaben nur seltener als jede Sekunde annehmen. Ist das Netz 15 Minuten lang
nicht ruhig oder bleibt eine Messung unklar, versucht SLEMS es nach einer Stunde
wieder. Das Ergebnis steht unter ⋮ → **Details** (**Totzeit**) und in der
Diagnose.

Hinweise im Log (Stufe Info, je Hinweis höchstens einmal am Tag; sie starten
keinen Probeschritt): Der Smart Meter meldet einen Tag lang um mehr als die
Hälfte schneller oder langsamer als beim letzten Probeschritt; die gelernte
Reaktionszeit weicht um mehr als 30 % von der des Probeschritts ab oder streut
stark; die Batterie hat innerhalb eines Tages zweimal ihre Vorgabe nicht
geliefert. Dann kann ein Probeschritt von Hand helfen.

## Firmware-Updates und Batterie-Menü

Während eines Firmware-Updates einer Marstek-Batterie darf keinerlei
Modbus-Kommunikation laufen. Das Menü (⋮) einer Batteriekarte bietet
**Kommunikation pausieren (Firmware-Update)**: SLEMS gibt die Batterie an ihre
eigene Logik zurück, trennt die Verbindung und liest und sendet für die **Dauer
Kommunikationspause** (Standard 20 Minuten; Schalter **Kommunikation pausiert**)
nichts. Danach verbindet es sich von selbst wieder; **Fortsetzen** beendet die
Pause früher, **Pause verlängern** hängt an eine laufende Pause noch einmal die
Pausendauer an (ebenso das erneute Einschalten von **Kommunikation pausiert**,
z. B. in einer Automation). Solange wird die Batterie wie eine deaktivierte behandelt.
Meldet die Batterie selbst ein laufendes Firmware-Update (Zustand
**OTA-Update**), pausiert SLEMS automatisch; das fällt erst bei der nächsten
Abfrage auf, daher vor einem Update besser manuell pausieren.

Im selben Menü lässt sich die Batterie aktivieren oder deaktivieren, der
Zellausgleich starten oder abbrechen, und **Details** zeigt Modell, Gerätename,
Firmware-Versionen (EMS, VMS, BMS, Kommunikationsmodul; Sensor **Firmware**),
MAC-Adresse, Kapazität, Ladezyklen, die insgesamt geladene und entladene
Energie, die letzte Vollladung, das aktuelle Zell-Delta, wann das Zell-Delta am
oberen Ladeende zuletzt gemessen wurde und die Phase (siehe **Phasen**).
**Gerät in Home Assistant öffnen** führt zur Geräteseite der Batterie mit allen
ihren Entitäten.

Die letzte Vollladung (Sensor **Letzte Vollladung**) ist der Zeitpunkt, an dem
die Batterie zuletzt oben angekommen ist: höchste Zelle auf der
Ladeschlussspannung, der Ladezustand, den das BMS bei voll meldet, oder das
BMS beendet die Ladung schon vorher (siehe Ladezustandsfenster oben), auch
wenn sie
gleich danach wieder entladen wurde. Eine neue Vollladung zählt, wenn die
Batterie den oberen Bereich verlassen hat (höchste Zelle unter 3,40 V, ohne
Zellspannungen unter 97 %). LFP-Batterien kalibrieren ihren Ladezustand nur
bei einer Vollladung neu, sie sollte daher regelmäßig vorkommen.

**Regelmäßige Vollladung** (gleichnamige Einstellungsgruppe, standardmäßig
an): Eine Batterie, die länger als **Vollladung spätestens alle** (Standard 7
Tage) nicht voll war oder seit der Aufzeichnung durch SLEMS noch nie, wird
zuerst geladen, bis sie einmal voll war. Immer nur eine Batterie, die mit der
ältesten letzten Vollladung (noch nie voll zuerst, dann nach Namen). Die
Aufteilung zwischen Batterien und Verbrauchern ändert sich nicht; die Batterie
bekommt die Ladeleistung nur vor den anderen Batterien. Dafür darf sie ihren
maximalen Ladezustand einmal überschreiten, danach gilt die Grenze wieder.
Wird sie mangels PV nicht voll, bleibt sie an den nächsten Tagen zuerst dran.
Beim Entladen wird sie geschont, solange alle anderen Batterien über 50 %
haben und die Leistung liefern können; so startet sie am nächsten Tag höher.
Ist sie voll, ruht sie 90 Sekunden, damit das Zell-Delta am oberen Ladeende
gemessen wird. Ihre Karte zeigt solange **Vollladung fällig**; nach 14 Tagen
ohne Vollladung nennt die Übersicht die Batterie.

## Grenzen und Schutz der Batterien

Jede steuerbare Batterie hat diese Einstellungen (Dashboard: **Einstellungen**):

- **Minimaler Ladezustand** (Standard 12 %) und **Maximaler Ladezustand**
  (Standard 100 %): SLEMS entlädt nicht bei oder unter dem Minimum und lädt
  nicht bei oder über dem Maximum (bei 100 % beendet das BMS die Ladung). Nach
  Erreichen einer Grenze wird die Batterie erst 2 % davon entfernt wieder
  genutzt, weil der Ladezustand nach einer Last wieder etwas steigt. Das
  Maximum gilt als „voll“ für das netzdienliche Laden, die gesicherte Ladung
  und die Prognose; das Minimum ist das niedrigste Ziel der Nachtentladung.
  Der aktive Zellausgleich ignoriert diese Grenzen (er braucht das obere
  Ladeende).

  Nahe am Minimum kann das BMS das Entladen früher beenden als eingestellt,
  z. B. wenn eine schwache Zelle unter hoher Last einbricht. Liefert eine
  Batterie unter 20 % bei drei Abfragen in Folge nichts, obwohl SLEMS sie
  entladen lässt, begrenzt SLEMS ihr Entladen zuerst auf die halbe Leistung
  (mindestens 300 W), weil das BMS bei geringerer Last später abschaltet;
  liefert sie auch damit nichts, sperrt SLEMS ihr Entladen. Beides (Grund
  `bms` bei **Erlaubte Entladeleistung**) gilt, bis sie geladen wurde oder
  ihr Ladezustand um 2 % gestiegen ist; die anderen Batterien und das Netz
  übernehmen.

  Oben beendet das BMS die Ladung, sobald eine Zelle voll ist; bei ungleich
  geladenen Zellen kann das deutlich unter 100 % sein (manche Firmware behält
  dann den Ladezustand bei, z. B. 88 %). Nimmt eine Batterie, deren höchste
  Zelle bei 3,45 V oder mehr steht (ohne Zellspannungen: ab 98 %), bei drei
  Abfragen nichts auf, obwohl SLEMS sie laden lässt, gilt sie als voll: SLEMS
  sperrt ihr Laden (Grund `bms` bei **Erlaubte Ladeleistung**, ein Hinweis im
  Dashboard), bis sie entladen hat oder ihre höchste Zelle unter 3,40 V
  gefallen ist (ohne Zellspannungen: ihr Ladezustand um 2 % gesunken ist).
  Eine Batterie, die alle paar Sekunden zwischen Laden und Standby wechselt,
  gilt erst nach drei Abfragen in Folge mit Ladeleistung wieder als ladend.
  Ein solches frühes Ende kommt oft von einem großen Zell-Delta; es wird in
  diesem Moment gemessen (siehe Zell-Delta).
  Unabhängig
  davon rechnet die Regelung eine Batterie, die ihrer Vorgabe messbar nicht
  folgt, mit ihrer gemessenen Leistung, damit Hausverbrauch und
  Verbrauchsprognose keine Leistung enthalten, die nicht fließt.
- **Grenze Ladeleistung** und **Grenze Entladeleistung** (Standard: das Maximum
  der Batterie), z. B. 800 W für ein Steckergerät.
- **Mindestleistung**: kein Laden oder Entladen mit so kleiner Leistung, dass
  es sich nicht lohnt. Unter dem festen Verlust des Wechselrichters (Venus
  etwa 35–48 W) entlädt das Laden die Batterie sogar.
  - **Automatisch, energetisch**: Laden erst, wenn dabei etwas gespeichert
    wird; Entladen ohne Schwelle (es liefert immer, nur mit schlechterem
    Wirkungsgrad).
  - **Automatisch, wirtschaftlich** (Standard): Laden mit P lohnt, wenn die
    gespeicherte Energie mehr wert ist als ihre Einspeisung: Wirkungsgrad
    beim Laden mit P × Bezugspreis × typischer Wirkungsgrad beim Entladen >
    Einspeisevergütung (Venus mit 19,8 / 7 ct: etwa 60–70 W). Ist die Ladung
    gesichert (die Batterie wird ohnehin voll), lohnt kleine Ladeleistung erst
    ab dem typischen Wirkungsgrad: später mit mehr Leistung zu laden ist
    besser. Entladen mit P lohnt, wenn Bezugspreis × Wirkungsgrad beim
    Entladen mit P > Einspeisevergütung / typischer Wirkungsgrad beim Laden
    (Venus: schon ab etwa 25 W, praktisch keine Grenze). Ohne Tarif oder mit
    einer Vergütung von 0 oder weniger: wie energetisch.
  - **Manuell**: die **Mindestleistung** in W für beide Richtungen.
  - **Aus**.

  Beide Automatiken brauchen die Verluste unter 300 W, die SLEMS je Richtung
  in Stufen von 50 W ab 40 W lernt (in der Diagnose unter `min_power`); bis
  dahin gilt keine Schwelle. Mit **Kein negativer Ziel-Netzüberschuss** gibt
  es keine Entladeschwelle. Eine Batterie startet erst ab der Schwelle, läuft
  bis 25 W darunter weiter (nie unter dem festen Verlust; liegt die Schwelle
  dort, beginnt sie 25 W darüber) und mindestens ihre gelernte Startzeit. Die
  Lücke spart Starts, wenn der Überschuss um die Schwelle schwankt (in einem
  Debug-Log 16 %, eine breitere kaum mehr). Solange die Mindestleistung die
  Batterien zurückhält, zeigt die **Strategie** **Unter Mindestleistung**; der
  kleine Überschuss wird eingespeist, ein kleines Defizit bezogen. Nicht
  zurückgehalten wird bei Einspeisebegrenzung, Netzladen, Preis halten,
  Einspeisen aus den Batterien, Nachtentladung, Spitzenabfang, Vollladung,
  Pflichtladung, Selbsttest und Probeschritt.

Die **Ladebegrenzung nach Temperatur** (standardmäßig aus, nach Omnibattery)
begrenzt die Ladeleistung nach der Batterietemperatur: Über der **Temperatur
für Ladeabregelung** (40 °C) sinkt sie linear über den **Bereich der
Ladeabregelung** (10 °C) bis auf die **Ladeleistung bei hoher Temperatur** (40
%); bei oder unter der **Mindesttemperatur zum Laden** (0 °C) wird nicht
geladen, innerhalb von 5 °C darüber steigt die Leistung wieder auf voll. Die
Venus meldet ihre Innentemperatur, nicht die Zelltemperatur; das BMS behält
seinen eigenen Schutz. Die Sensoren **Erlaubte Ladeleistung** und **Erlaubte
Entladeleistung** zeigen die aktuelle Grenze und ihren Grund.

Kurz vor voll lädt SLEMS sanft (immer, nach Omnibattery): Sobald die höchste
Zelle 3,48 V erreicht, lädt eine Batterie mit höchstens 200 W, bis die Zelle
wieder unter 3,44 V fällt. Bei kleinem Strom gleicht das BMS die Zellen passiv
aus, bevor die höchste Zelle die Ladung beendet; so werden die Batterien
wirklich voll, mit kleinerem Zell-Delta, und die Zellen sehen weniger
Spannungsspitze und Wärme. Das betrifft nur das letzte ein bis zwei Prozent;
der Überschuss geht in der Zeit an die anderen Batterien oder die Verbraucher.
Der Sensor **Erlaubte Ladeleistung** hat den Grund `top`; Batterien ohne
Zellspannungen werden nicht begrenzt.

Im Betriebsmodus **Aktiv** prüft SLEMS wie Omnibattery, ob jede Batterie die
vorgegebene Leistung liefert. Liefert eine Batterie bei einer Vorgabe von
mindestens 100 W (nach 30 s in dieser Richtung) dreimal hintereinander weniger
als 10 % davon, werden zuerst alle Steuerregister neu geschrieben; hilft das
nicht, wird sie für 5 Minuten ausgeschlossen (wie eine deaktivierte Batterie,
an ihre eigene Logik übergeben, die anderen übernehmen) und danach wieder
versucht. Eine Batterie am oberen Ladeende, die nicht mehr lädt, oder eine
Batterie bei höchstens 20 %, die nicht mehr entlädt, zählt nicht (das BMS
schützt sie, siehe Ladezustandsfenster). Die
Venus liest außerdem nach jedem vollständigen Schreiben (erster Befehl und
alle 60 s) ihre Steuerregister zurück; ein nicht bestätigter Befehl zählt
ebenfalls. Der Binärsensor **Reagiert nicht** und das Dashboard zeigen eine
ausgeschlossene Batterie.
