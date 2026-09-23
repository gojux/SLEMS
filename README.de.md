<p align="center"><img src="assets/logo.png" alt="SLEMS" height="96"></p>

[English](README.md) | **Deutsch**

# SLEMS

SLEMS ist eine Home-Assistant-Integration, die Heimspeicher und regelbare
Verbraucher steuert. Ziel ist ein netzdienlicher Betrieb der Batterien,
z. B. durch Verschieben des Ladezeitpunkts, damit die tägliche
Einspeisespitze abgefangen wird. Grundlage dafür sind Prognosen für Verbrauch
und PV-Ertrag.

Der Name setzt sich aus *Slug* (unserer Haustier-Nacktschnecke) und *EMS*
(Energiemanagementsystem) zusammen; die Schnecke im Logo trägt einen Akku
statt eines Hauses.

> **Status: frühe Entwicklung.** SLEMS liest aktuell Batterien und Messwerte
> und stellt sie als Entities bereit. Prognose, Planung, Verbrauchersteuerung
> und Dashboard sind noch nicht umgesetzt (siehe [Roadmap](#roadmap)).

## Funktionen

| Funktion | Status |
|---|---|
| Beliebig viele Batterien, jederzeit hinzufügen, bearbeiten, entfernen | ✅ |
| Marstek Venus E 3.0 über Modbus TCP | ✅ Lesen, Steuerung vorbereitet |
| Nur lesende Batterie aus vorhandenen Entities (z. B. solange Omnibattery steuert) | ✅ |
| Smart Meter, PV-Leistung und Wetter frei wählbar | ✅ |
| PV-Prognose aus beliebiger Solarprognose-Integration (Forecast.Solar, Solcast, …) | ✅ |
| Verbraucher mit eigenen Leistungs-/Energiesensoren, im oder außerhalb des Smart Meters | ✅ Konfiguration |
| Wärmepumpe als Verbrauchertyp (wetterabhängige Prognose) | ✅ Konfiguration |
| Betriebsmodus *Aus / Simulation (nur lesend) / Aktiv* | ✅ Entity, Logik folgt |
| Urlaubsschalter | ✅ Entity, Logik folgt |
| Bezugsspitzen abfangen bei niedrigem Ladezustand (manuell aktivierbar) | ✅ Entities, Logik folgt |
| Verbrauchsprognose (Historie + Wetter) | geplant |
| Netzdienliches Laden: PV-Einspeisespitzen abfangen | geplant |
| Verbrauchersteuerung (Schalter / Leistungsvorgabe, externe Sperre, Priorität) | geplant |
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
  Priorität (1 = höchste).

### Batterien

- **Marstek Venus E 3.0**: Host/IP, Port (502) und Modbus Unit-ID. Die
  Batterie akzeptiert nur **eine** Modbus-TCP-Verbindung. Lass nie zwei
  Integrationen (z. B. SLEMS und Omnibattery) gleichzeitig mit derselben
  Batterie sprechen.
- **Vorhandene Home-Assistant-Entities (nur lesend)**: Ladezustand und
  Leistung einer Batterie, die von etwas anderem gesteuert wird. SLEMS sendet
  an eine solche Batterie nie Befehle. So kann SLEMS im Simulationsmodus
  parallel zu einer bestehenden Batterie-Integration laufen.

### Betriebsmodus

Die Entity *SLEMS Betriebsmodus* schaltet zwischen:

- **Aus**: Es wird nichts geplant oder gesendet.
- **Simulation**: Prognosen und Pläne werden berechnet und angezeigt, aber
  keine Befehle an Batterien oder Verbraucher gesendet. Das ist die Voreinstellung.
- **Aktiv**: Pläne werden ausgeführt.

### Weitere Einstellungen (Entities)

- *Urlaub* (Schalter): Der Haushalt ist abwesend; manuell oder per Automation schalten.
- *Bezugsspitzen abfangen* (Schalter): standardmäßig aus. Wenn aktiviert und
  der Gesamt-Ladezustand auf oder unter der *Ladezustand-Schwelle für
  Spitzenabfang* liegt, entladen die Batterien nur noch, um den Netzbezug
  unter der *Bezugsgrenze für Spitzenabfang* zu halten.

## Roadmap

1. Verbrauchermodell: Details der Steuerung (Mindestlaufzeiten, Hysterese, …)
2. Verbrauchsprognose aus Historie und Wetter
3. Planer (netzdienliches Laden, Spitzen abfangen) und Echtzeit-Regler
4. Dashboard: Energiefluss-Schema, Tagesdiagramm mit Prognose und Plan

## Entwicklung

Siehe [developers.md](developers.md).

## Lizenz

GPL-3.0, siehe [LICENSE](LICENSE). Register-Map und Ansteuerung der Marstek
Venus basieren auf [Omnibattery](https://github.com/ffunes/Omnibattery) (GPL-3.0).
