<p align="center"><img src="assets/logo.png" alt="SLEMS" height="96"></p>

[English](README.md) | **Deutsch**

# SLEMS

SLEMS ist eine Home-Assistant-Integration, die Heimspeicher und regelbare
Verbraucher steuert. Ziel ist ein netzdienlicher Betrieb der Batterien,
z. B. durch Verschieben des Ladezeitpunkts, damit die tägliche
Einspeisespitze abgefangen wird. Grundlage dafür sind Prognosen für Verbrauch
und PV-Ertrag.

Der Name setzt sich aus *Slug* (unserem Haustier) und *EMS*
(Energiemanagementsystem) zusammen.

> **Status: frühe Entwicklung.** SLEMS liest aktuell Batterien und Messwerte
> und stellt sie als Entities bereit. Prognose, Planung, Verbrauchersteuerung
> und Dashboard sind noch nicht umgesetzt (siehe [Roadmap](#roadmap)).

## Funktionen

| Funktion | Status |
|---|---|
| Beliebig viele Batterien, jederzeit hinzufügen, bearbeiten, entfernen | ✅ |
| Marstek Venus E 3.0 über Modbus TCP | ✅ Lesen, Steuerung vorbereitet |
| Nur lesende Batterie aus vorhandenen Entities (z. B. solange Omnibattery steuert) | ✅ |
| Smart Meter, PV-Leistung, PV-Prognose und Wetter frei wählbar | ✅ |
| Betriebsmodus *Aus / Simulation (nur lesend) / Aktiv* | ✅ Entity, Logik folgt |
| Verbrauchsprognose (Historie + Wetter) | geplant |
| Netzdienliche Ladeplanung (Spitzen abfangen) | geplant |
| Verbrauchersteuerung (Schalter / Leistungsvorgabe, externe Sperre) | geplant |
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
3. Auf der Seite der SLEMS-Integration für jede Batterie **Batterie hinzufügen** wählen.

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

Die Entity `select.slems_operating_mode` schaltet zwischen:

- **Aus**: Es wird nichts geplant oder gesendet.
- **Simulation**: Prognosen und Pläne werden berechnet und angezeigt, aber
  keine Befehle an Batterien oder Verbraucher gesendet. Das ist die Voreinstellung.
- **Aktiv**: Pläne werden ausgeführt.

## Roadmap

1. Verbrauchermodell (Ein/Aus und leistungsgeregelt, Prioritäten, externe Sperre)
2. Verbrauchsprognose aus Historie und Wetter
3. Planer (netzdienliches Laden, Spitzen abfangen) und Echtzeit-Regler
4. Dashboard: Energiefluss-Schema, Tagesdiagramm mit Prognose und Plan

## Entwicklung

Siehe [developers.md](developers.md).

## Lizenz

GPL-3.0, siehe [LICENSE](LICENSE). Register-Map und Ansteuerung der Marstek
Venus basieren auf [Omnibattery](https://github.com/ffunes/Omnibattery) (GPL-3.0).
