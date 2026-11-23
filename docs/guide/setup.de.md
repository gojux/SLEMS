# Installation und Einrichtung

[Zurück zur README](../../README.de.md#schnellstart) · [English](setup.md)

<!-- toc -->

- [Voraussetzungen](#voraussetzungen)
- [Installation](#installation)
  - [Marktplatz oder HACS (benutzerdefiniertes Repository)](#marktplatz-oder-hacs-benutzerdefiniertes-repository)
  - [Manuell](#manuell)
- [Einrichtung](#einrichtung)
- [Smart Meter per Modbus (optional)](#smart-meter-per-modbus-optional)
- [PV-Prognose](#pv-prognose)
- [Wetter (optional)](#wetter-optional)
- [Verbrauchsprognose](#verbrauchsprognose)
- [Sprache](#sprache)

<!-- /toc -->

## Voraussetzungen

- Home Assistant 2026.10 oder neuer; ab 2026.11 den eingebauten Marktplatz,
  davor HACS.
- Einen Smart Meter in Home Assistant, der die Netzleistung (Bezug und
  Einspeisung) laufend meldet, z. B. ein Shelly Pro 3EM. Je öfter er meldet,
  desto genauer regelt SLEMS (z. B. jede Sekunde).
- Optional: eine PV-Prognose (z. B. Forecast.Solar), Batterien und
  Verbraucher, die SLEMS steuern soll.

## Installation

### Marktplatz oder HACS (benutzerdefiniertes Repository)

Home Assistant 2026.11 hat HACS durch den eingebauten **Marktplatz** ersetzt
(**Einstellungen → Marktplatz**); bestehende HACS-Installationen ziehen von
selbst um.

[![Öffne deine Home-Assistant-Instanz und das Repository im Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=gojux&repository=SLEMS&category=integration)

Der Button öffnet SLEMS im Marktplatz (bzw. HACS) deiner Home-Assistant-Instanz
und fügt das benutzerdefinierte Repository hinzu; danach weiter mit Schritt 2.
Oder von Hand:

1. Marktplatz: **Einstellungen → Marktplatz**, das benutzerdefinierte
   Repository `https://github.com/gojux/SLEMS` hinzufügen, Typ
   **Integration**. Benutzerdefinierte Repositories brauchen eine Verbindung
   des Marktplatzes mit GitHub; er fragt danach.

   HACS (bis Home Assistant 2026.10): HACS → ⋮ → **Benutzerdefinierte
   Repositories** → `https://github.com/gojux/SLEMS` eintragen, Typ
   **Integration**.
2. **SLEMS** installieren und Home Assistant neu starten.

### Manuell

`custom_components/slems` in den Ordner `custom_components` deiner
Home-Assistant-Konfiguration kopieren und Home Assistant neu starten.

## Einrichtung

1. **Einstellungen → Geräte & Dienste → Integration hinzufügen → SLEMS**.
2. Die Geräte deines Smart Meters und deiner PV-Anlagen auswählen (optional,
   vorausgewählt aus dem Energie-Dashboard von Home Assistant; die am besten
   passenden Geräte stehen oben, z. B. ein Shelly Pro 3EM oder der Zähler
   eines Wechselrichters). SLEMS schlägt ihre Sensoren vor und ergänzt, was
   das Energie-Dashboard schon kennt, auch das Vorzeichen seiner
   Netzleistung, die PV-Prognose und das Wetter. Die Netzleistungs-Entität
   prüfen (positiv = Bezug, negativ = Einspeisung; das Formular zeigt ihren
   aktuellen Wert; **Vorzeichen der Netzleistung umkehren** aktivieren, wenn dein Zähler es
   umgekehrt meldet), optional PV-Leistung, PV-Prognose und Wetter. Mehrere
   PV-Anlagen (z. B. Dach und Balkon) werden addiert.
3. Auf der Seite der SLEMS-Integration für jede Batterie **Batterie
   hinzufügen** und für jeden Verbraucher, der gemessen oder gesteuert werden
   soll, **Verbraucher hinzufügen** wählen (siehe
   [Batterien](batteries.de.md) und [Verbraucher](consumers.de.md)).
4. SLEMS läuft zuerst im Betriebsmodus **Nur beobachten**: Im Dashboard siehst
   du Prognose und Plan, gesteuert wird noch nichts. Zum Regeln andere
   Steuerungen derselben Batterien und Verbraucher abschalten und den
   Betriebsmodus auf **Aktiv** stellen (siehe
   [Betrieb und Regelung](control.de.md)).

Um die Systemeinstellungen später zu ändern, das Zahnrad des SLEMS-Eintrags
auswählen: Ein Menü bietet **Geräte neu erkennen** (die geführten Schritte der
Ersteinrichtung) oder einen Bereich, **Netz**, **PV & Prognose** oder
**Verbrauch & Kalender**. Jeder Bereich wird für sich gespeichert, die übrigen
Einstellungen bleiben.

**Phasen** (optional): Misst dein Smart Meter jede Phase, kannst du in den
SLEMS-Einstellungen seine **Netzleistung L1**, **L2** und **L3** auswählen.
SLEMS lernt dann, an welcher Phase jedes Gerät hängt, und zeigt sie an; auf
die Regelung hat das noch keinen Einfluss (siehe
[Phase von Batterien und Verbrauchern](control.de.md#phase-von-batterien-und-verbrauchern)).

## Smart Meter per Modbus (optional)

Die Entität des Smart Meters wird einmal pro Abfragedurchlauf ihrer
Integration aktualisiert, oft nur etwa einmal pro Sekunde, und zeigt dann nur
einen Teil der Zählerwerte. Mit **Smart Meter zusätzlich per Modbus lesen**
liest SLEMS die Netzleistung direkt von einem SunSpec-Zähler alle
0,5 Sekunden (0,2 bis 5 s). Die Regelung
sieht jeden Wert früher; am meisten bringt das bei schnellen Batterien.

- SLEMS nutzt die geteilte Modbus-Verbindung von Home Assistant. Solange die
  Integration des Wechselrichters dessen einzige Modbus-TCP-Verbindung selbst
  belegt, muss ein Modbus-Proxy eingerichtet werden, z. B. die App [Modbus
  Proxy](https://github.com/TCzerny/ha-modbusproxy). SLEMS verlangt von der
  geteilten Verbindung ein Timeout von 1 Sekunde; braucht eine andere
  Integration am selben Gerät länger, gilt das längere. Antwortet der Zähler
  nicht, verbindet SLEMS sofort neu; andere Integrationen am selben Gerät
  merken das als kurzes Neuverbinden.
- SLEMS findet die Zähler in der SunSpec-Modellkette; bei mehreren Zählern
  (z. B. Einspeisung/Bezug und Verbrauch) wählst du den am Netzanschlusspunkt,
  die aktuelle Leistung jedes Zählers hilft beim Erkennen.
- Das Vorzeichen findet SLEMS selbst, indem es einige Werte mit der
  Netzleistungs-Entität vergleicht; dafür braucht es etwas Bezug oder
  Einspeisung (mindestens 100 W). Sonst lässt es sich von Hand wählen.
- Die Entität bleibt Pflicht: Sie ist die Historie, die Grundlage der Prognosen
  und die Rückfallebene. Solange kein Modbus-Wert neuer als 3 Intervalle
  (mindestens 5 Sekunden) ist, nutzt SLEMS die Entität; nach 5 Minuten ohne
  Modbus erscheint ein Reparaturhinweis. Der Sensor **Aktualisierungsintervall
  Smart Meter** zeigt die Quelle im Attribut `source`.
- Beim Wechsel der Quelle beginnen die gelernten Reaktionszeiten der
  Batterien neu, weil sie die Verzögerung der alten Quelle enthalten.

## PV-Prognose

Auswählbar ist jede Integration, die eine Solarprognose für das
Energie-Dashboard von Home Assistant liefert, z. B. Forecast.Solar oder
Solcast. Mehrere Einträge (z. B. ein Forecast.Solar-Eintrag je Dachfläche)
werden addiert. SLEMS verwendet die feinste Auflösung, die der Anbieter
liefert (z. B. 15 oder 30 Minuten). Forecast.Solar kennzeichnet jede Periode
mit ihrem Ende; SLEMS berücksichtigt das.

**Empfehlung: [Helios Forecast](https://github.com/ReikanYsora/Helios-Forecast)**
(Open Source, ohne Konto und API-Schlüssel, lokal gerechnet mit Wetterdaten von
Open-Meteo). Es lernt aus der Erzeugung der eigenen PV-Anlage, auch
Abschattung durch Bäume oder Berge, Verschmutzung und eine um ein paar Grad
abweichende Ausrichtung, und nutzt dafür gleich die vorhandenen
Langzeitstatistiken des Energiesensors. Es liefert eine Prognose für das
Energie-Dashboard in 15-Minuten-Schritten und ist in SLEMS direkt auswählbar.

## Wetter (optional)

Die Wetter-Entität verbessert die Verbrauchsprognose, besonders bei einer
Wärmepumpe. Am besten passt die Prognose des nationalen Wetterdienstes:

| Land | Empfehlung | Warum |
|---|---|---|
| Deutschland | [DWD Weather](https://github.com/FL550/dwd_weather) (HACS) | Prognose des Deutschen Wetterdienstes für eine Station in deiner Nähe, stündlich |
| Österreich | [GeoSphere Austria Plus](https://github.com/coding-pagro/GeoSphere-Austria-Plus) oder [GeoSphere Austria Next](https://github.com/slettmayer/ha-geosphere-next) (HACS) | hochaufgelöstes AROME-Modell von GeoSphere Austria, gut für Alpentäler; die eingebaute Integration *GeoSphere Austria* liefert nur Messwerte, keine Prognose |
| Schweiz | [Swiss Weather](https://github.com/izacus/hass-swissweather) (HACS) | Lokalprognose von MeteoSchweiz für deine Postleitzahl, stündlich |
| Liechtenstein | [Swiss Weather](https://github.com/izacus/hass-swissweather) (HACS) | MeteoSchweiz deckt auch Liechtenstein ab |
| Überall | **Open-Meteo** (eingebaute Integration) | ohne Konto, kombiniert mehrere Wettermodelle; gute Wahl ohne eigene Integration |

**Met.no**, die Voreinstellung von Home Assistant, funktioniert überall, ist
aber im alpinen Gelände weniger detailliert.

## Verbrauchsprognose

SLEMS lernt den Verbrauch deines Hauses aus den Langzeitstatistiken von Home
Assistant und prognostiziert heute und morgen stundenweise: Wärmepumpen über
die Außentemperatur, Feiertage wie Wochenenden. Steigt der Verbrauch plötzlich
(z. B. zu Beginn der Heizsaison), hebt SLEMS die Prognose sofort an. Die Karte
**Prognosegüte** zeigt, wie gut Verbrauchs- und PV-Prognose treffen. Beim
Umstieg von einer anderen Batterie-Integration deren Hausverbrauchssensor als
**Historie Hausverbrauch** wählen, dann lernt SLEMS sofort aus der ganzen
Historie.

Details: [Verbrauchsprognose](consumption-forecast.de.md).

## Sprache

SLEMS gibt es auf Deutsch und Englisch. Home Assistant verwendet dafür zwei
verschiedene Spracheinstellungen:

- **Namen der Entitäten** (z. B. **Hausverbrauch**, **Betriebsmodus**, auch die
  Kennzahlen im Dashboard) folgen der **Serversprache** unter **Einstellungen →
  System → Allgemein**. Sie werden beim Laden der Integration gesetzt; nach
  einer Änderung SLEMS neu laden.
- Dialoge, Menüs, Zustände (z. B. **Nur beobachten**) und die Texte des
  Dashboards folgen der Sprache im **Benutzerprofil**.

Bleiben Namen nach einem Update von SLEMS in der falschen Sprache, Home
Assistant neu starten (Übersetzungen werden nur beim Start gelesen) und den
Browser ohne Cache neu laden. Entitäts-IDs wie `sensor.slems_house_consumption`
behalten die Sprache, in der sie angelegt wurden; nur die angezeigten Namen
ändern sich.
