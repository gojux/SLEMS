<p align="center"><img src="assets/logo.png" alt="SLEMS" height="96"></p>

[English](README.md) | **Deutsch**

# SLEMS

SLEMS ist eine Home-Assistant-Integration, die Heimspeicher und regelbare
Verbraucher steuert. Ziel ist ein netzdienlicher Betrieb der Batterien,
z. B. durch Verschieben des Ladezeitpunkts, damit die tägliche
Einspeisespitze abgefangen wird. Grundlage dafür sind Prognosen für Verbrauch
und PV-Ertrag.

Der Name setzt sich aus *Slug* und *EMS* (Energiemanagementsystem) zusammen.

> **Status: frühe Entwicklung.** Prognose, Planung und Regelung sind
> umgesetzt, aber noch nicht an einer echten Batterie getestet. Das Dashboard
> fehlt noch (siehe [Roadmap](#roadmap)).

## Funktionen

| Funktion | Status |
|---|---|
| Beliebig viele Batterien, jederzeit hinzufügen, bearbeiten, entfernen | ✅ |
| Marstek Venus E 3.0 über Modbus TCP | ✅ (Steuerung noch nicht am echten Gerät getestet) |
| Nur lesende Batterie aus vorhandenen Entities (z. B. solange Omnibattery steuert) | ✅ |
| Smart Meter, PV-Leistung und Wetter frei wählbar | ✅ |
| PV-Prognose aus beliebiger Solarprognose-Integration (Forecast.Solar, Solcast, …) | ✅ |
| Verbraucher mit eigenen Leistungs-/Energiesensoren, im oder außerhalb des Smart Meters | ✅ Konfiguration |
| Wärmepumpe als Verbrauchertyp (wetterabhängige Prognose) | ✅ Konfiguration |
| Betriebsmodus *Aus / Simulation (nur lesend) / Aktiv* | ✅ |
| Urlaubsschalter | ✅ |
| Bezugsspitzen abfangen bei niedrigem Ladezustand (manuell aktivierbar) | ✅ |
| Verbrauchsprognose heute/morgen (Historie + Wetter, Wärmepumpe temperaturabhängig) | ✅ |
| Netzdienliches Laden: PV-Einspeisespitzen abfangen | geplant |
| Verteilung des Überschusses auf Batterien und Verbraucher (Priorität, Aufteilung, Mindestlaufzeit/-pause) | ✅ |
| Wirkungsgrad der Batterie (Batteriezähler, gelernt oder manuell) | ✅ |
| Gemittelter Netzüberschuss (0–300 s) | ✅ |
| Ziel-Netzüberschuss beim Laden/Entladen, maximale Einspeisung beim Entladen | ✅ |
| Nachtentladung bis zu einer prognosebasierten Reserve | ✅ |
| Aufteilung auf Batterien nach Wirkungsgrad, Wechsel mit sanftem Übergang | ✅ |
| Echtzeit-Regelung von Batterien und Verbrauchern (Betriebsmodus *Aktiv*) | ✅ |
| Dashboard mit Energiefluss und Tagesprognose | geplant |

## Installation

### HACS (benutzerdefiniertes Repository)

1. HACS → ⋮ → *Benutzerdefinierte Repositories* → URL dieses Repositories eintragen, Typ *Integration*.
2. *SLEMS* installieren und Home Assistant neu starten.

### Manuell

`custom_components/slems` in den Ordner `custom_components` deiner
Home-Assistant-Konfiguration kopieren und Home Assistant neu starten.

Voraussetzung: Home Assistant 2026.9 oder neuer.

## Konfiguration

1. *Einstellungen → Geräte & Dienste → Integration hinzufügen → SLEMS*.
2. Die Netzleistungs-Entity deines Smart Meters wählen (positiv = Bezug,
   negativ = Einspeisung; *Vorzeichen umkehren* aktivieren, wenn dein Zähler
   es umgekehrt meldet), optional PV-Leistung, PV-Prognose und Wetter.
3. Auf der Seite der SLEMS-Integration für jede Batterie **Batterie hinzufügen**
   und für jeden Verbraucher, der gemessen oder gesteuert werden soll,
   **Verbraucher hinzufügen** wählen.

### PV-Prognose

Auswählbar ist jede Integration, die eine Solarprognose für das
Energie-Dashboard von Home Assistant liefert, z. B. Forecast.Solar oder
Solcast. Mehrere Einträge (z. B. ein Forecast.Solar-Eintrag je Dachfläche)
werden addiert.

### Wetter (optional)

Die Wetter-Entity verbessert die Verbrauchsprognose, besonders bei einer
Wärmepumpe. Empfehlungen für Vorarlberg bzw. den Alpenraum:

- **GeoSphere Austria AROME**: hochaufgelöstes Modell des österreichischen
  Wetterdienstes, gut geeignet für Alpentäler. In Home Assistant über
  benutzerdefinierte Integrationen wie
  [GeoSphere Austria Plus](https://github.com/coding-pagro/GeoSphere-Austria-Plus) oder
  [GeoSphere Austria Next](https://github.com/slettmayer/ha-geosphere-next) (HACS).
  Die eingebaute Integration *GeoSphere Austria* liefert nur Stationsmesswerte,
  keine Prognose.
- **Open-Meteo** (eingebaute Integration): ohne Konto nutzbar, kombiniert
  mehrere Wettermodelle.
- **Met.no** (Standard in Home Assistant): funktioniert überall, im
  alpinen Gelände weniger detailliert.

### Verbrauchsprognose

SLEMS lernt den Verbrauch aus den Langzeitstatistiken von Home Assistant und
prognostiziert heute und morgen stundenweise. Jüngere Tage zählen mehr als
ältere, Änderungen werden so innerhalb weniger Tage übernommen. Wärmepumpen
werden über die Außentemperatur des Tages prognostiziert, ein warmer Tag in der
Heizsaison ergibt also sofort weniger Heizenergie. Zwei optionale Einstellungen
helfen beim Start:

- **Außentemperatur**: ein Temperatursensor mit Historie. Ohne ihn zeichnet
  SLEMS die Temperatur der Wetter-Entity selbst auf; das Wärmepumpenmodell
  nutzt die Temperatur dann nach etwa einer Woche.
- **Historie Hausverbrauch**: ein Leistungssensor des Hausverbrauchs mit
  vorhandener Historie (z. B. aus einer anderen Batterie-Integration), für die
  Zeit, bevor SLEMS eigene Werte aufgezeichnet hat.

Empfohlene Einrichtung beim Umstieg von einer anderen Batterie-Integration:

1. Den Hausverbrauchssensor dieser Integration als *Historie Hausverbrauch*
   wählen. SLEMS lernt dann sofort aus der gesamten Historie. Ohne ihn leitet
   SLEMS die Historie nur aus Netz und PV ab; die Batterieleistung vor SLEMS
   ist unbekannt, Laden und Entladen würden den gelernten Verbrauch verfälschen.
2. Falls vorhanden, einen Außentemperatursensor mit Historie wählen. Sonst
   nutzt die Wärmepumpenprognose den Durchschnitt der letzten Tage, bis SLEMS
   etwa eine Woche Temperaturen aufgezeichnet hat.
3. Feiertage werden derzeit wie Werktage behandelt.

### Verbraucher

Jeder Verbraucher braucht einen eigenen Leistungs- **und** Energiesensor.

- **Im Smart Meter enthalten**: aktivieren, wenn der Verbraucher hinter dem
  Smart Meter hängt (sein Verbrauch ist bereits in der Netzleistung enthalten).
  Für Verbraucher an einer separaten Versorgung deaktivieren.
- **Typ**: *Wärmepumpe* (Heizung und Warmwasser, wetterabhängige Prognose),
  *Heizpatrone* (z. B. Warmwasser im Sommer) oder *Sonstiges*. Wärmepumpe und
  Heizpatrone dürfen gleichzeitig laufen.
- **Steuerung**: keine (nur Messung), Ein/Aus über einen Schalter oder eine
  Leistungsvorgabe über eine Number-Entity in W. Für gesteuerte Verbraucher
  kann eine Entity für eine externe Sperre gewählt werden (solange sie
  eingeschaltet ist, steuert SLEMS den Verbraucher nicht), dazu eine
  Priorität (1 = höchste) und optional eine Mindestlaufzeit und Mindestpause.

Wirkungsgrad der Batterie: je Batterie aus den eigenen Lade-/Entladezählern
der Batterie, aus der gemessenen Leistung gelernt oder manuell vorgegeben. Er
fließt in die Entscheidung ein, ob der PV-Überschuss die Batterien füllt.

### Batterien

- **Marstek Venus E 3.0**: Host/IP, Port (502) und Modbus Unit-ID. Die
  Batterie akzeptiert nur **eine** Modbus-TCP-Verbindung. Lass nie zwei
  Integrationen (z. B. SLEMS und Omnibattery) gleichzeitig mit derselben
  Batterie sprechen.
- **Vorhandene Home-Assistant-Entities (nur lesend)**: Ladezustand und
  Leistung einer Batterie, die von etwas anderem gesteuert wird. SLEMS sendet
  an eine solche Batterie nie Befehle. So kann SLEMS im Simulationsmodus
  parallel zu einer bestehenden Batterie-Integration laufen.

Bei mehreren Batterien entscheidet SLEMS, wie viele und welche laufen: Bei
kleiner Leistung ist meist eine einzelne Batterie effizienter, bei großer das
Aufteilen. Beim Entladen läuft die Batterie mit dem höchsten Ladezustand, beim
Laden die mit dem niedrigsten. Entfernt sich die laufende Batterie um mehr als
die *Schwelle Batteriewechsel* (Standard 5 %) von der besten inaktiven, wird
gewechselt, höchstens einmal pro *Mindestabstand Batteriewechsel* (Standard
15 min) und mit sanftem Übergang über die *Übergangszeit Batteriewechsel*
(Standard 30 s). Die Umwandlungsverluste je Leistungsbereich lernt SLEMS aus
AC- und DC-Leistung der Batterie.

Jede Batterie hat einen Schalter *Aktiviert*. Eine deaktivierte Batterie wird
weiter gemessen (ihre Leistung gehört zur Energiebilanz), aber weder
eingeplant noch gesteuert und zählt nicht zum Gesamt-Ladezustand.

### Betriebsmodus

Die Entity *SLEMS Betriebsmodus* schaltet zwischen:

- **Aus**: Es wird nichts geplant oder gesendet.
- **Simulation**: Prognosen und Pläne werden berechnet und angezeigt, aber
  keine Befehle an Batterien oder Verbraucher gesendet. Das ist die Voreinstellung.
- **Aktiv**: Pläne werden ausgeführt: SLEMS sendet Sollwerte an die
  Batterien und schaltet bzw. stellt die Verbraucher. Es reagiert auf jede
  Änderung des Smart Meters. Die Entity *Regelstatus* zeigt, ob die Regelung
  aktiv ist oder pausiert, weil der Smart Meter 60 s lang nichts gemeldet hat
  (die Batterien folgen dann ihrer eigenen Logik, bis der Zähler wieder meldet).

Vor dem Umschalten auf *Aktiv* die *Beruhigungszeit Regelung* einstellen
(Standard 5 s): die Zeit zwischen einem Befehl und dem Moment, in dem der
Smart Meter die Wirkung zeigt, etwa das Aktualisierungsintervall des Zählers
plus rund eine Sekunde. Schwankt die Netzleistung hin und her, erhöhen.

### Weitere Einstellungen (Entities)

- *Urlaub* (Schalter): Der Haushalt ist abwesend; manuell oder per Automation schalten.
- *Batterievorrang unter Ladezustand* (Standard 30 %), *Sicherheitspuffer
  gesicherte Ladung* (Standard 1 kWh) und *Batterieanteil bei gesicherter
  Ladung* (Standard 75 %): Die Batterien bekommen den gesamten Überschuss, bis
  ihre Ladung gesichert ist, d. h. der Ladezustand über der Schwelle liegt und
  der erwartete PV-Überschuss des Tages die Energie bis zur Vollladung plus
  Sicherheitspuffer deckt. Danach wird aufgeteilt, der Anteil der Verbraucher geht nach
  Priorität. Was eine Seite nicht nutzen kann, bekommt die andere.
- *Ziel-Netzüberschuss beim Laden* (0–5000 W, Standard 100 W): Die Batterien
  laden nur aus dem Überschuss oberhalb dieses Werts.
- *Ziel-Netzüberschuss beim Entladen* (−1000…+1000 W, Standard 50 W;
  positiv = Einspeisung, negativ = Bezug): der Netzwert, auf den die
  entladenden Batterien regeln. Zwischen den beiden Zielwerten ruhen die Batterien.
- *Maximale Einspeisung beim Entladen* (0 bis Summe der maximalen
  Entladeleistung aller Batterien, Standard 5000 W): Das Entladen verursacht
  nie mehr Einspeisung als diesen Wert.
- *Nachtentladung* (Schalter, standardmäßig aus) und *Reserve Nachtentladung*
  (Standard 25 % des prognostizierten Verbrauchs von morgen): Über Nacht
  entladen die Batterien gleichmäßig bis zur Reserve, bis die PV-Erzeugung den
  Verbrauch wieder übersteigt; der Ziel-Netzüberschuss beim Entladen wird dabei
  ignoriert (die maximale Einspeisung gilt weiter). Reicht die PV-Prognose für
  morgen nicht, um die Batterien von der Reserve aus wieder zu füllen, bleibt
  eine höhere Reserve. Benötigt die Verbrauchsprognose.
- *Mittelungsfenster Überschuss* (0–300 s, Standard 5 s, 0 = aus): Die
  Netzleistung wird gemittelt; es gilt der ungünstigere Wert aus Mittelwert
  und aktuellem Wert, damit die Regelung bei schwankender PV nicht überschießt.
- *Bezugsspitzen abfangen* (Schalter): standardmäßig aus. Wenn aktiviert und
  der Gesamt-Ladezustand auf oder unter der *Ladezustand-Schwelle für
  Spitzenabfang* liegt, entladen die Batterien nur noch, um den Netzbezug
  unter der *Bezugsgrenze für Spitzenabfang* zu halten.

## Roadmap

1. Tests am echten System (siehe offene Punkte in developers.md)
2. Dashboard: Energiefluss-Schema, Tagesdiagramm mit Prognose und Plan

## Entwicklung

Siehe [developers.md](developers.md).

## Lizenz

GPL-3.0, siehe [LICENSE](LICENSE). Register-Map und Ansteuerung der Marstek
Venus basieren auf [Omnibattery](https://github.com/ffunes/Omnibattery) (GPL-3.0).
