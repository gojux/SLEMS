<p align="center"><img src="assets/logo.png" alt="SLEMS" height="96"></p>

[English](README.md) | **Deutsch**

# SLEMS

SLEMS ist eine Home-Assistant-Integration, die Heimspeicher und regelbare
Verbraucher steuert. Ziel ist ein netzdienlicher Betrieb der Batterien, z. B.
durch Verschieben des Ladezeitpunkts, damit die tägliche Einspeisespitze
abgefangen wird. Grundlage dafür sind Prognosen für Verbrauch und PV-Ertrag.

Der Name setzt sich aus *Slug* (unsere „Lieblingshaustiere“ und ein lustiges
Wort für eine ganze Reihe [sehr interessanter
Tiere](https://en.wikipedia.org/wiki/Sea_slug)) und *EMS*
(Energiemanagementsystem) zusammen.

![SLEMS-Übersicht: Energiefluss, Kennzahlen und das Diagramm mit Prognose und Plan des Tages](docs/images/dashboard_overview_de.png)

> **Status: Beta.** SLEMS läuft schon längere Zeit problemlos im
> Produktivbetrieb; neue Funktionen und Verbesserungen kommen weiterhin häufig
> dazu. Im Betriebsmodus **Aktiv** sendet es Vorgaben an
> Batterien und schaltet Verbraucher. Die Nutzung erfolgt auf eigene
> Verantwortung: Prüfe die Grenzen deiner Batterien und die Absicherung deiner
> Stromkreise. Fehler bitte als [Issue](https://github.com/gojux/SLEMS/issues)
> mit den Diagnosedaten melden (siehe [Schnellstart](#schnellstart)).

## Passt SLEMS zu deiner Anlage?

| Batterie | Unterstützung |
|---|---|
| AC-Batterie (eigener Wechselrichter, an der Steckdose oder im Verteiler) | Marstek Venus E 3.0 direkt über Modbus TCP; andere Modelle über ein [Modbus-Profil](docs/guide/battery-modbus-profile.de.md) oder die Entitäten ihrer Home-Assistant-Integration (Steuerung experimentell) |
| Balkonspeicher mit PV-Modulen (z. B. Marstek B2500, Zendure SolarFlow, Anker Solarbank) | über die Entitäten seiner Integration mit **Eigene PV-Module an der Batterie** (experimentell, Tester und Feedback willkommen) |
| Hybrid-Wechselrichter (PV und Batterie an einem Wechselrichter) | seine Batterie über die Entitäten seiner Integration (experimentell, Tester und Feedback willkommen) |
| Keine Batterie | PV-Überschuss nur in Verbraucher ([ohne Batterien](#ohne-batterien)) |

Wenn du SLEMS mit einer anderen Batterie nutzen möchtest, erstelle bitte ein
[Issue](https://github.com/gojux/SLEMS/issues) mit dem Modell, der Anbindung
(Modbus, lokale API, Home-Assistant-Integration) und ob du testen könntest.

| Tarif-Vorlagen | |
|---|---|
| Österreich | 23 Stromtarife und 2 PV-Einspeisetarife von 11 Lieferanten (Burgenland Energie, Energie AG Oberösterreich, Energie Steiermark, EVN, IKB, illwerke vkw, Kelag, LINZ AG, Salzburg AG, TIWAG, Wien Energie), ein allgemeiner dynamischer Tarif, die Netzentgelte aller 14 Netzbereiche, die Bundesabgaben |
| Deutschland | ein allgemeiner dynamischer Tarif, die EEG-Einspeisevergütung (bis 10 kWp, 8 Inbetriebnahme-Zeiträume), die Netzentgelte von 19 Netzbetreibern, die bundesweiten Abgaben |
| Schweiz | der Tarif deiner Gemeinde aus den offenen Daten der ElCom |
| Alle Länder | jeder Tarif, für den es keine Vorlage gibt (auch in Österreich, Deutschland und der Schweiz): wie auf der Rechnung eingegeben oder als YAML-Datei, die eine KI mit dem mitgelieferten Prompt aus deiner Rechnung erstellt (siehe [Tarife](#tarife-optional)) |

## Inhalt

- [Passt SLEMS zu deiner Anlage?](#passt-slems-zu-deiner-anlage)
- [Schnellstart](#schnellstart)
- [Warum SLEMS?](#warum-slems)
- [Funktionen](#funktionen)
- [Installation](#installation)
  - [HACS (benutzerdefiniertes Repository)](#hacs-benutzerdefiniertes-repository)
  - [Manuell](#manuell)
- [Einrichtung](#einrichtung)
  - [Smart Meter per Modbus (optional)](#smart-meter-per-modbus-optional)
  - [Tarife (optional)](#tarife-optional)
  - [PV-Prognose](#pv-prognose)
  - [Wetter (optional)](#wetter-optional)
  - [Verbrauchsprognose](#verbrauchsprognose)
- [Ohne Batterien](#ohne-batterien)
- [Batterien](#batterien)
- [Verbraucher](#verbraucher)
  - [Stromvorgabe](#stromvorgabe)
  - [Auto an der Wallbox](#auto-an-der-wallbox)
  - [Temperaturfühler des Speichers](#temperaturfühler-des-speichers)
  - [Batterie-Unterstützung](#batterie-unterstützung)
  - [Einsatz in der Einspeisebegrenzung](#einsatz-in-der-einspeisebegrenzung)
  - [Tagesziel](#tagesziel)
- [Dashboard](#dashboard)
  - [Übersicht](#übersicht)
  - [Reiter Batterien](#reiter-batterien)
  - [Reiter Verbraucher](#reiter-verbraucher)
  - [Simulation](#simulation)
  - [Reiter Einstellungen](#reiter-einstellungen)
  - [Bedienung des Dashboards](#bedienung-des-dashboards)
- [Betrieb und Regelung](#betrieb-und-regelung)
  - [Betriebsmodus](#betriebsmodus)
  - [Schlechtwetter-Modus](#schlechtwetter-modus)
  - [So funktioniert die Regelung](#so-funktioniert-die-regelung)
  - [Welche Option wann?](#welche-option-wann)
  - [Netzdienliches Laden](#netzdienliches-laden)
  - [Einspeisebegrenzung](#einspeisebegrenzung)
  - [Bezugsspitzen abfangen und Leistungspreis](#bezugsspitzen-abfangen-und-leistungspreis)
  - [Gelernte Werte](#gelernte-werte)
  - [Weitere Einstellungen (Entitäten)](#weitere-einstellungen-entitäten)
  - [Probleme und Benachrichtigungen](#probleme-und-benachrichtigungen)
- [Sprache](#sprache)
- [Roadmap](#roadmap)
- [Entwicklung](#entwicklung)
- [Lizenz](#lizenz)

## Schnellstart

Was du brauchst:

- Home Assistant 2026.9 oder neuer und HACS.
- Einen Smart Meter in Home Assistant, der die Netzleistung (Bezug und
  Einspeisung) laufend meldet, z. B. ein Shelly Pro 3EM. Je öfter er meldet,
  desto genauer regelt SLEMS (z. B. jede Sekunde).
- Optional: eine PV-Prognose (z. B. Forecast.Solar), Batterien und
  Verbraucher, die SLEMS steuern soll.

In fünf Schritten:

1. SLEMS über HACS installieren und Home Assistant neu starten
   ([Installation](#installation)).
2. Die Integration hinzufügen, Smart Meter und PV-Anlagen auswählen und das
   Vorzeichen der Netzleistung prüfen ([Einrichtung](#einrichtung)).
3. Batterien und Verbraucher hinzufügen ([Batterien](#batterien),
   [Verbraucher](#verbraucher)). Die meisten Batterien erlauben nur eine
   Modbus-Verbindung: Trenne sie vorher von anderen Integrationen.
4. SLEMS vorübergehend im Betriebsmodus **Nur beobachten** laufen lassen (die
   Voreinstellung), so fallen offensichtliche Probleme früh auf. Im Dashboard
   siehst du Prognose und Plan; gesteuert wird noch nichts.
5. Andere Steuerungen derselben Batterien und Verbraucher (Automationen,
   andere Integrationen) abschalten und den [Betriebsmodus](#betriebsmodus)
   auf **Aktiv** stellen. Neue Batterien machen zuerst ihren
   [Selbsttest](docs/guide/batteries.de.md#selbsttest-einer-batterie) (etwa 5–10 Minuten), danach regelt
   SLEMS.

Die Voreinstellungen passen für den Anfang; Verluste, Reaktionszeiten und
Prognose-Puffer lernt SLEMS selbst (siehe [Gelernte Werte](#gelernte-werte)).
Hinweise zu Problemen stehen unter
[Probleme und Benachrichtigungen](#probleme-und-benachrichtigungen). Für eine
Fehlermeldung die Diagnosedaten anhängen: **Einstellungen → Geräte & Dienste →
SLEMS → ⋮ → Diagnosedaten herunterladen**. Adressen und Seriennummern der
Batterien sind darin geschwärzt.

## Warum SLEMS?

Die meisten Speichersteuerungen reagieren auf den Moment: Sie halten die
Netzleistung bei null und laden, sobald Überschuss da ist. Die Batterie ist
dann am späten Vormittag voll, und die Mittagsspitze geht trotzdem ins Netz.
SLEMS plant voraus und regelt genau:

- **Netzdienlich statt um 10 Uhr voll.** Aus PV- und Verbrauchsprognose
  berechnet SLEMS eine Einspeisegrenze und lädt die Batterien mit dem
  Überschuss darüber. So fangen sie die Einspeisespitze ab und sind am Abend
  trotzdem voll. Die Grenze wird laufend neu berechnet und sinkt von selbst,
  wenn der Tag schlechter wird als vorhergesagt.
- **Eigene Verbrauchsprognose.** Gelernt aus den Langzeitstatistiken deines
  Hauses, mit Wetter und eigenem Wärmepumpen-Modell; Urlaubsmodus,
  Nachtentladung bis zu einer prognosebasierten Reserve und eine Prognose des
  Ladezustands für heute und morgen.
- **Batterien und Verbraucher in einem Plan.** Der Überschuss wird auf
  Batterien und steuerbare Verbraucher (Heizstab, Wärmepumpe, …) verteilt, mit
  Prioritäten, Batterievorrang bis zur gesicherten Ladung, Mindestlaufzeiten,
  externer Sperre und Thermostat-Pausen. Tagesziele (Laufzeit, Energie oder
  Temperatur bis zu einer Uhrzeit) erfüllt SLEMS möglichst aus dem Überschuss
  und erst wenn nötig aus Batterie oder Netz; je Verbraucher legst du fest, ob
  die Batterien ihn versorgen dürfen.
- **Mehrere Batterien im effizienten Arbeitspunkt.** SLEMS lernt die
  Umwandlungsverluste jeder Batterie, betreibt nur so viele Batterien wie
  sinnvoll und wechselt zwischen ihnen mit sanftem Übergang.
- **Einspeisebegrenzung.** Darfst du nur einen Teil der PV-Leistung einspeisen
  (z. B. 60 % oder gar nichts), speichert SLEMS die Energie über der Grenze und
  schafft vorher rechtzeitig Platz in den Batterien, statt PV abzuregeln.
- **Bezugsspitzen wirtschaftlich abfangen.** Bei leistungsbezogenen
  Netzentgelten (Leistungspreis für die höchste Viertelstunde des Monats)
  wählt SLEMS Bezugsgrenze und Reserve mit den niedrigsten erwarteten Kosten,
  hält Energie in den Batterien für die Spitzen zurück, lässt erzwungene
  Verbraucher zurücktreten und lädt vor den Spitzen unter der Grenze nach.
- **Preise im Blick.** Tarife wie auf der Rechnung, mit Vorlagen für
  Österreich, Deutschland und die Schweiz; Börsenpreise je Viertelstunde nur
  mit deiner Zustimmung, geschätzt, bis die Preise des Folgetags erscheinen.
  Die preisbewusste Steuerung hebt die gespeicherte Energie für teure Stunden
  auf und kann optional günstig aus dem Netz laden oder teuer einspeisen; ein
  monatlicher Tarifvergleich zeigt, was ein anderer Tarif gekostet hätte.
- **Genaue Regelung ohne Schwingen.** Ereignisgesteuert bei jeder Meldung des
  Smart Meters; die gelernte Reaktionszeit der Batterien (auch eines Starts
  aus dem Standby und eines Richtungswechsels) wird ausgeglichen,
  die Regelverstärkung passt sich selbst an.
- **Batterieschonung und Sicherheit.** Ladezustandsfenster, Leistungsgrenzen
  (z. B. 800 W), Ladebegrenzung nach Temperatur, Überwachung des Zell-Deltas
  mit aktivem Zellausgleich, Erkennung nicht reagierender Batterien,
  Reparatur-Einträge und Benachrichtigungen.
- **Regelmäßige Vollladung ohne verschenkten Überschuss.** LFP-Batterien
  kalibrieren ihren Ladezustand nur bei einer Vollladung neu. SLEMS lädt die
  Batterie mit der ältesten letzten Vollladung zuerst, immer nur eine und nur
  mit dem Überschuss, der ohnehin gespeichert würde, und schont sie beim
  Entladen, damit sie am nächsten Tag höher startet. Es erkennt auch eine
  Vollladung, die das BMS knapp unter 100 % beendet, und misst dabei das
  Zell-Delta am oberen Ladeende.
- **Lernt statt zu fragen.** Neben der Verbrauchsprognose lernt SLEMS die
  Umwandlungsverluste und die Reaktionszeit der Batterien, das
  Meldeintervall des Smart Meters, die Regelverstärkung und die
  Verbrauchsspitzen; auf Wunsch
  auch die Prognose-Puffer, die Ziel-Netzüberschüsse, die Regelzeiten, die
  nutzbare Kapazität jeder Batterie sowie Leistung und Thermostatverhalten von
  Verbrauchern (siehe **Gelernte Werte**). Jeder gelernte Wert lässt sich wieder
  auf einen festen umstellen.
- **Transparent und lokal.** Ein Dashboard mit Energiefluss, Prognose-Diagramm
  und allen Einstellungen. Regelung, Prognosen und Lernen laufen lokal in Home
  Assistant, ohne Cloud-Konto. Ins Internet geht SLEMS nur für Börsenpreise
  und Monatsmarktwerte (nur mit deiner Zustimmung), für Schweizer Tarife aus
  den offenen Daten der ElCom und einmal für den Vorschlag der
  Feiertagsregion (Nominatim, siehe
  [Verbrauchsprognose](#verbrauchsprognose)). Im Betriebsmodus **Nur beobachten** siehst du, was SLEMS tun würde, bevor es
  etwas steuert, auch neben einer bestehenden Batterie-Integration.
- **Erst ausprobieren, dann ändern.** Die Simulation im Dashboard berechnet
  den Tagesplan mit anderen Einstellungen (netzdienliches Laden,
  Nachtentladung, Einspeisebegrenzung, Grenzen der Batterien, Prognosen ±%)
  und vergleicht Einspeisung, Netzbezug und Ladezustand mit dem aktuellen
  Plan, ohne etwas zu ändern.
- **Auch ohne Batterien.** SLEMS kann den PV-Überschuss auch allein in
  Verbraucher lenken (Heizstab, Wallbox, Luftentfeuchter, …), mit Tageszielen,
  Tarifen und Prognosen (siehe [Ohne Batterien](#ohne-batterien)).

**Andere Batterien und Elektroautos:** Batterien, die SLEMS noch nicht direkt
oder über ein [Modbus-Profil](docs/guide/battery-modbus-profile.de.md)
unterstützt, lassen sich trotzdem steuern, wenn es für sie eine Integration in
Home Assistant gibt: SLEMS steuert sie dann über deren Entitäten
(experimentell). Eine Wallbox regelt SLEMS mit dem Überschuss, mit **Sofort
laden** und mit einem Tagesziel nach dem Ladezustand des Autos. evcc ist auf
das Laden von E-Autos spezialisiert und ergänzt SLEMS gut (siehe
[evcc-Anleitung](docs/wallbox-evcc.de.md)).

## Funktionen

| Einrichtung | Status |
|---|---|
| Beliebig viele Batterien, jederzeit hinzufügen, bearbeiten, entfernen | ✅ |
| Smart Meter, PV-Leistung und Wetter frei wählbar | ✅ |
| Einrichtung aus den Geräten von Smart Meter und PV-Anlagen und aus dem Energie-Dashboard; mehrere PV-Anlagen addiert | ✅ |
| Smart Meter zusätzlich direkt per Modbus lesen (SunSpec), mit dem Sensor als Rückfallebene | ✅ |
| Betriebsmodus **Aus / Nur beobachten / Aktiv** | ✅ |
| Urlaubsschalter und Schlechtwetter-Modus | ✅ |
| Reparaturhinweise, Benachrichtigungen und Diagnosedaten | ✅ |

| Prognosen | Status |
|---|---|
| PV-Prognose aus beliebiger Solarprognose-Integration (Forecast.Solar, Solcast, …) | ✅ |
| Verbrauchsprognose heute/morgen (Historie + Wetter, Wärmepumpe temperaturabhängig, Feiertage des eigenen Bundeslands oder Kantons), nach einem plötzlichen Anstieg sofort angehoben | ✅ |
| Korrektur der PV-Prognose im Tagesverlauf und Prognosegüte von PV und Verbrauch | ✅ |

| Batterien | Status |
|---|---|
| Marstek Venus E 3.0 über Modbus TCP | ✅ |
| Batterie aus vorhandenen Entitäten: nur lesend oder gesteuert über Sollwert, Lade-/Entladeleistung oder Skript, Entitäten aus dem Gerät vorgeschlagen | ✅ (Steuerung experimentell) |
| Batterie aus einem Modbus-Profil: ein eigenes Profil mit einem KI-Prompt aus dem Handbuch erstellt, gelesene Werte und Schreibvorschau, bevor etwas geschrieben wird, Probebetrieb mit dem Selbsttest, bevor sie geregelt wird, beobachtet nach einem Firmware-Update; mitgeliefert (ungeprüft): Marstek Venus E Gen 2 | ✅ (experimentell) |
| Wirkungsgrad der Batterie (Batteriezähler, gelernt oder manuell), gelernte nutzbare Kapazität | ✅ |
| Verschleißkosten je Batterie (aus Kaufpreis und Zyklen) in der Planung | ✅ |
| Batteriemenü: Kommunikation für Firmware-Updates pausieren, Batterie deaktivieren, Gerät öffnen | ✅ |
| Aufteilung auf Batterien nach Wirkungsgrad, die schnellste Batterie übernimmt rasche Änderungen zuerst, Wechsel mit sanftem Übergang | ✅ |
| Grenzen je Batterie: minimaler/maximaler Ladezustand, Grenze Lade-/Entladeleistung (z. B. 800 W), Ladebegrenzung nach Temperatur, vorzeitiger Entladestopp des BMS nahe dem Minimum erkannt | ✅ |
| Erkennung von Batterien, die die vorgegebene Leistung nicht liefern, Bestätigung der Vorgaben | ✅ |
| Zell-Delta und aktiver Zellausgleich (Marstek Venus E 3.0) | ✅ |
| Selbsttest einer Batterie: Wirkung am Smart Meter, Vorzeichen und Einheit der Leistung, Energiezähler, Ladezustand, Reaktionszeit, Freigabe am Smart Meter geprüft, sofortiger Abbruch bei falscher Richtung | ✅ |
| Totzeit und Phase je Batterie mit einem kurzen Probeschritt gemessen (bei einer neuen Batterie, nach Änderungen an Batterie, Firmware, Smart Meter oder Integration, alle 90 Tage, von Hand) | ✅ |
| Regelmäßige Vollladung zur SoC-Kalibrierung der LFP-Zellen (immer eine Batterie, aus dem Überschuss, beim Entladen geschont) | ✅ |

| Verbraucher | Status |
|---|---|
| Verbraucher mit eigenen Leistungs-/Energiesensoren, im oder außerhalb des Smart Meters | ✅ |
| Verbraucher aus seinem Gerät einrichten: Geräte nach Eignung sortiert (z. B. Wärmepumpe, Heizstab, Wallbox), Entitäten, Steuerung und ihr Bereich vorgeschlagen | ✅ |
| Phase von Verbrauchern aus den Phasen des Smart Meters gelernt (beginnend mit dem Verlauf), von Batterien im Selbsttest, Wechsel gemeldet, neu lernen; Energiezähler, die nicht über die Phasen saldieren, erkannt | ✅ (Regelung je Phase noch nicht) |
| Wärmepumpe als Verbrauchertyp (wetterabhängige Prognose), optional Freigabe ihrer Warmwasserladung (Betriebsmodus, gelernte Warmwasserleistung, [Anleitung](docs/heat-pump-hot-water.de.md)) | ✅ |
| Steuerung von Verbrauchern: Ein/Aus, Leistungsvorgabe oder Freigabe für eine eigene Automation (mit spätestem Start), Priorität, Mindestlaufzeit/-pause, Takten vermeiden, externe Sperre, Steuerung je Verbraucher abschaltbar | ✅ |
| Verbraucher mit eigenem Thermostat: Pausen erkannt, ihre Leistung geht solange an die Batterien | ✅ |
| Temperaturfühler eines Speichers: gelernte Energie pro Grad, noch aufnehmbare Energie, gelernte Abkühlung je Stunde | ✅ |
| Einsatz eines Verbrauchers in der Einspeisebegrenzung (unterstützend, normal, nie) | ✅ |
| Tagesziele: Laufzeit, Freigabezeit, Energie, Temperatur oder Ladestand des Autos, mit frühestem Start, Frist und Quelle (Überschuss, Batterie, Netz) | ✅ |
| Batterie-Unterstützung je Verbraucher (immer, automatisch mit der Energie, die die Batterien übrig haben, nie) | ✅ |
| Wallbox / evcc-Ladepunkt: Stromvorgabe in A mit Phasen, Start/Stopp ([Anleitung](docs/wallbox-evcc.de.md)) | ✅ (noch nicht mit einer echten Wallbox getestet) |
| Auto an der Wallbox: Ladestand im Energiefluss, ohne angestecktes Auto keine Regelung und keine Planung, **Sofort laden** mit voller Leistung | ✅ (noch nicht mit einer echten Wallbox getestet) |

| Planung und Regelung | Status |
|---|---|
| Verteilung des Überschusses auf Batterien und Verbraucher (Priorität, Aufteilung, Mindestlaufzeit/-pause) | ✅ |
| Echtzeit-Regelung von Batterien und Verbrauchern (Betriebsmodus **Aktiv**) | ✅ |
| Gemittelter Netzüberschuss (0–300 s) | ✅ |
| Ziel-Netzüberschuss beim Laden/Entladen, maximale Einspeisung beim Entladen | ✅ |
| Netzdienliches Laden: PV-Einspeisespitzen abfangen | ✅ |
| Einspeisebegrenzung: PV-Energie über einer Einspeisegrenze speichern, rechtzeitig Platz schaffen | ✅ |
| Nachtentladung bis zu einer prognosebasierten Reserve | ✅ |
| Bezugsspitzen abfangen: eine Bezugsgrenze und eine Ladezustand-Schwelle, unter der die Batterien ihre Energie für die Spitzen zurückhalten (mit Leistungspreis das günstigste Paar, eine davon aus der Verbrauchsstatistik berechnet oder beide eingestellt; manuell aktivierbar); mit Leistungspreis im Tarif gilt die Grenze im Mittel über die Viertelstunde, erzwungene Verbraucher treten zurück, die Batterien laden vor den Spitzen unter der Grenze aus dem Netz nach, die Übersicht zeigt die Monatsspitze und ihre Mehrkosten, und der Tarifvergleich schätzt, was Spitzenabfang gespart hätte | ✅ |

| Dashboard | Status |
|---|---|
| Dashboard (Seitenleiste): Energiefluss, Kennzahlen, Tagesdiagramm mit Prognose und Plan, Batterien, Verbraucher, Einstellungen | ✅ |
| Simulation im Dashboard: Tagesplan mit anderen Einstellungen im Vergleich zum aktuellen (heute oder morgen), mit Preisdiagramm | ✅ |

| Tarife und Preise | Status |
|---|---|
| Tarife wie auf der Rechnung (Zeitfenster, Umsatzsteuer je Gruppe, prozentuale Abgaben) mit Prüfung gegen eine Rechnung | ✅ |
| Tarif-Vorlagen: Österreich (Netzentgelte aller Netzbereiche, Abgaben, Lieferanten inkl. Optionen wie Ökostrom-Upgrade), Deutschland (Abgaben, EEG-Einspeisevergütung, Netzentgelte von 19 Netzbetreibern inkl. § 14a-Module und Messstellenbetrieb), Schweiz (Abfrage der offenen Daten der ElCom); eine allgemeine Vorlage für dynamische Tarife (Börsenpreis plus Aufschlag aus dem Vertrag) | ✅ |
| Vorlagen-Updates: neue Preise, Nachfolgetarife und Korrekturen als Reparaturhinweis, Zurücksetzen auf die Vorlagenwerte | ✅ |
| Tarife als YAML (Import, Export, eigene Vorlagen) mit einem Prompt, um sie mit einer KI aus einer Rechnung zu erstellen | ✅ |
| Börsenpreise je Viertelstunde (APG, SMARD, Energy-Charts) und offizielle Monatsmarktwerte (E-Control) nur mit Zustimmung; keine Einspeisevergütung bei negativen Preisen, wo der Tarif es vorsieht (EEG) | ✅ |
| Monatlicher Tarifvergleich: Bezugskosten und Einspeisevergütung je Tarif, Tarife ein- und ausblendbar, geschätzte Ersparnis der preisbewussten Steuerung | ✅ |
| Preisbewusste Steuerung: gespeicherte Energie für die teuren Stunden halten, Netzanteil der Tagesziele im günstigsten Fenster, optional Laden aus dem Netz und Einspeisen aus den Batterien | ✅ |
| Geschätzte Börsenpreise, bis die Preise des Folgetags erscheinen (Profil der letzten Tage, Wochenende und Feiertage) | ✅ |

## Installation

### HACS (benutzerdefiniertes Repository)

[![Öffne deine Home-Assistant-Instanz und das Repository im Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=gojux&repository=SLEMS&category=integration)

Der Button öffnet SLEMS im HACS deiner Home-Assistant-Instanz und fügt das
benutzerdefinierte Repository hinzu; danach weiter mit Schritt 2. Oder von
Hand:

1. HACS → ⋮ → **Benutzerdefinierte Repositories** →
   `https://github.com/gojux/SLEMS` eintragen, Typ **Integration**.
2. **SLEMS** installieren und Home Assistant neu starten.

### Manuell

`custom_components/slems` in den Ordner `custom_components` deiner
Home-Assistant-Konfiguration kopieren und Home Assistant neu starten.

Voraussetzung: Home Assistant 2026.9 oder neuer.

## Einrichtung

1. **Einstellungen → Geräte & Dienste → Integration hinzufügen → SLEMS**.
2. Die Geräte deines Smart Meters und deiner PV-Anlagen auswählen (optional,
   vorausgewählt aus dem Energie-Dashboard von Home Assistant; die am besten
   passenden Geräte stehen oben, z. B. ein Shelly Pro 3EM oder der Zähler
   eines Wechselrichters). SLEMS schlägt ihre Sensoren vor und ergänzt, was
   das Energie-Dashboard schon kennt, auch das Vorzeichen seiner
   Netzleistung, die PV-Prognose und das Wetter. Die Netzleistungs-Entität
   prüfen (positiv = Bezug, negativ = Einspeisung; das Formular zeigt ihren
   aktuellen Wert; **Vorzeichen umkehren** aktivieren, wenn dein Zähler es
   umgekehrt meldet), optional PV-Leistung, PV-Prognose und Wetter. Mehrere
   PV-Anlagen (z. B. Dach und Balkon) werden addiert.
3. Auf der Seite der SLEMS-Integration für jede Batterie **Batterie
   hinzufügen** und für jeden Verbraucher, der gemessen oder gesteuert werden
   soll, **Verbraucher hinzufügen** wählen.

**Phasen** (optional): Misst dein Smart Meter jede Phase, kannst du in den
SLEMS-Einstellungen seine **Netzleistung L1**, **L2** und **L3** auswählen.
SLEMS lernt dann, an welcher Phase jedes Gerät hängt, und zeigt sie an; auf
die Regelung hat das noch keinen Einfluss (siehe
[Phase von Batterien und Verbrauchern](#phase-von-batterien-und-verbrauchern)).

### Smart Meter per Modbus (optional)

Die Entität des Smart Meters wird einmal pro Abfragedurchlauf ihrer
Integration aktualisiert, oft nur etwa einmal pro Sekunde, und zeigt dann nur
einen Teil der Zählerwerte. Mit **Smart Meter zusätzlich per Modbus lesen**
liest SLEMS die Netzleistung direkt von einem SunSpec-Zähler alle
0,5 Sekunden (0,2 bis 5 s). Die Regelung
sieht jeden Wert früher; am meisten bringt das bei schnellen Batterien.

- SLEMS nutzt die geteilte Modbus-Verbindung von Home Assistant. Solange die
  Integration des Wechselrichters dessen einzige Modbus-TCP-Verbindung selbst
  belegt, muss ein Modbus-Proxy eingerichtet werden, z. B. die App [Modbus
  Proxy](https://github.com/TCzerny/ha-modbusproxy).
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

### Tarife (optional)

Mit einem Tarif rechnet SLEMS in Geld: Es prüft deine Stromrechnung nach,
zeigt jeden Monat, was ein anderer Tarif gekostet hätte, und kann die
Batterien nach den Preisen steuern.

- **Tarif eintragen** wie auf der Rechnung, Posten für Posten, aus
  **Vorlagen** mit öffentlichen Listenpreisen (Österreich, Deutschland, die
  Schweiz über die ElCom) oder als YAML, auch von einer KI aus einer Rechnung
  erstellt.
- **Dynamische Tarife** mit Börsenpreisen je Viertelstunde (abgerufen nur,
  wenn du es einschaltest) und **Leistungspreise** für die Monatsspitze.
- **Preisbewusste Steuerung** (optional): hebt die Energie der Batterien für
  teure Stunden auf, lädt auf Wunsch günstig aus dem Netz oder speist teuer
  ein und misst, was das gespart hat.

Alle Einstellungen und Details: [Tarife und Preise](docs/guide/tariffs.de.md).

### PV-Prognose

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

### Wetter (optional)

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

### Verbrauchsprognose

SLEMS lernt den Verbrauch deines Hauses aus den Langzeitstatistiken von Home
Assistant und prognostiziert heute und morgen stundenweise: Wärmepumpen über
die Außentemperatur, Feiertage wie Wochenenden. Steigt der Verbrauch plötzlich
(z. B. zu Beginn der Heizsaison), hebt SLEMS die Prognose sofort an. Die Karte
**Prognosegüte** zeigt, wie gut Verbrauchs- und PV-Prognose treffen. Beim
Umstieg von einer anderen Batterie-Integration deren Hausverbrauchssensor als
**Historie Hausverbrauch** wählen, dann lernt SLEMS sofort aus der ganzen
Historie.

Details: [Verbrauchsprognose](docs/guide/consumption-forecast.de.md).

## Ohne Batterien

SLEMS funktioniert auch ohne Batterie: einfach nur die Verbraucher anlegen.

- **Funktioniert**: den PV-Überschuss in gesteuerte Verbraucher lenken
  (Heizstab, Wallbox auch mit evcc, Luftentfeuchter, Wärmepumpe über einen
  Schalteingang) mit Prioritäten, Mindestlaufzeit und -pause, **Takten
  vermeiden** und externer Sperre; Tagesziele mit den Quellen **Nur Überschuss**
  und **+ Netz** (mit preisbewusster Steuerung auch im günstigsten Fenster);
  Tarife, Rechnungsprüfung, Börsenpreise, Tarifvergleich und Preisdiagramm;
  Energiefluss, Verbrauchs- und PV-Prognose mit ihrer Güte.
- **Nicht ohne Batterien**: alles, was eine Batterie lädt, entlädt oder plant
  – netzdienliches Laden, Nachtentladung, Bezugsspitzen abfangen, Halten für
  teure Stunden, Laden aus dem Netz, Einspeisen aus den Batterien – und
  derzeit die Einspeisebegrenzung (sie braucht eine Batterie, auch mit
  unterstützenden Verbrauchern).

## Batterien

SLEMS steuert eine oder mehrere Heimbatterien: Es verteilt die Leistung auf
sie, plant ihren Einsatz über den Tag (netzdienliches Laden, Nachtentladung,
Preise, Bezugsspitzen) und schützt sie dabei. Eine Batterie lässt sich auf drei
Wegen einbinden:

- **Marstek Venus E 3.0** direkt per Modbus TCP: am schnellsten und mit den
  meisten Daten (Zellspannungen, Zähler, Firmware). SLEMS sucht sie im Netz.
  Die Venus erlaubt nur eine Modbus-Verbindung.
- **Vorhandene Home-Assistant-Entitäten** (Steuerung experimentell): jede
  Batterie, die eine andere Integration einbindet, z. B. über Omnibattery,
  auch ein Balkonspeicher oder Hybrid-Wechselrichter mit eigenen PV-Modulen.
- **Batterie aus einem Modbus-Profil** (experimentell): eine Batterie mit
  Modbus TCP, beschrieben durch ein Profil, das eine KI aus ihrem Handbuch
  erstellt.

Was SLEMS für jede Batterie mitbringt:

- Einen **Selbsttest** beim Hinzufügen, der Vorzeichen, Werte und Reaktion
  prüft.
- **Gelernte Werte**: Reaktionszeit, Phase und Wirkungsgrad. Bei mehreren
  Batterien laufen nur so viele, wie effizient ist, mit sanftem Wechsel.
- **Grenzen und Schutz**: Ladezustand, Lade- und Entladeleistung,
  Mindestleistung; sanftes Laden nahe voll, Zell-Delta und Zellausgleich, eine
  Pause für Firmware-Updates.
- Optional **Verschleißkosten**, damit sich preisbewusstes Laden aus dem Netz
  nur lohnt, wenn es mehr einbringt.

Alle Einstellungen und Details: [Batterien](docs/guide/batteries.de.md) und
[Batterie aus einem Modbus-Profil](docs/guide/battery-modbus-profile.de.md).

## Verbraucher

Jeder Verbraucher braucht einen eigenen Leistungs- **und** Energiesensor.

Welche Verbraucher anlegen: die, die SLEMS steuert (Heizstab, Wallbox,
Luftentfeuchter, …), und Wärmepumpen (ihr Verbrauch wird wetterabhängig
prognostiziert). Andere Geräte nur zum Messen anzulegen, z. B. Waschmaschine
oder Geschirrspüler, verbessert die Planung derzeit nicht: Ihr Verbrauch ist
bereits im Hausverbrauch enthalten, und dessen Prognose (ein Profil je
Wochentag und Stunde) enthält ihren durchschnittlichen Verbrauch. Sie
bekommen nur eine eigene Karte und ein Feld im Energiefluss.

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
- **Freigabe** (Steuerung): Für Geräte, die eine eigene Automation startet,
  weil sie mehr wissen muss als SLEMS (z. B. eine Waschmaschine mit
  Startbefehl, oder die Warmwasserladung einer Wärmepumpe über SG Ready nur,
  wenn ihr Kompressor gerade stabil läuft). SLEMS schaltet selbst nichts,
  plant den Verbraucher aber wie einen Ein/Aus-Verbraucher mit der
  eingestellten Leistung und stellt bereit:
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
    Karte (Zeile **Zeitfenster**) und Tagesdiagramm zeigen es, und es wird
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

  **Wärmepumpe mit Freigabe** (Warmwasser): Mit dem Typ **Wärmepumpe** gilt
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
  Die [Anleitung zum Warmwasser einer Wärmepumpe](docs/heat-pump-hot-water.de.md)
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

### Stromvorgabe

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
  [Wallbox mit evcc anbinden](docs/wallbox-evcc.de.md). Ohne
  Start/Stopp-Entität entscheidet evcc selbst über Start und Stopp (z. B. im
  PV-Modus), und SLEMS begrenzt nur den Strom; mit dem Lademodus als
  Start/Stopp-Entität steuert SLEMS das Laden vollständig, dann entscheiden
  seine Batterie-Unterstützung und Prognosen. Die Karte zeigt die geplante
  Leistung mit Ampere und Phasen, z. B. **4.140 W (6 A, 3 Phasen)**.

### Auto an der Wallbox

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

### Temperaturfühler des Speichers

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

### Batterie-Unterstützung

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

### Einsatz in der Einspeisebegrenzung

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

### Tagesziel

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
  (blass), die Freigabe vor dem erzwungenen Lauf, den erzwungenen Lauf
  und die Frist (Strich). Ein Verbraucher mit dem Einsatz
  **Unterstützend** bleibt beim Überschuss-Teil außen vor, solange die
  Einspeisebegrenzung an ist: Sie plant ihn mit dem Überschuss über der
  Grenze ein.

## Dashboard

SLEMS fügt der Seitenleiste von Home Assistant den Eintrag **SLEMS** hinzu.

### Übersicht

- **Energiefluss** zwischen Netz, PV, Haus, jeder Batterie (mit ihrem
  Ladezustand) und den Verbrauchern; die animierten Punkte laufen in
  Flussrichtung, je höher die Leistung, desto schneller und auf einer dickeren
  Linie. Das SLEMS-Logo in der Mitte öffnet ein Menü: den
  [Schlechtwetter-Modus](#schlechtwetter-modus) und die **Details der
  Regelung** (Status, Regelverstärkung, Regelintervall und Mittelungsfenster,
  Quelle und Aktualisierungsintervall des Smart Meters, bei Modbus mit
  Antwortzeit, Fehlern und der Zeit über die Entität heute, und die
  Reaktionszeiten der Batterien).
  Mit einer Batterie mit eigener PV (Hybrid-Wechselrichter, Balkonkraftwerk
  mit Batterie) kannst du oben rechts zwischen **Summiert** und **Nach
  Geräten** wählen; die Wahl merkt sich der Browser. **Summiert** zeigt alle
  PV in einer Box, die Batterie mit der Leistung, die ihr Wechselrichter
  einschließlich ihrer PV aufnimmt. **Nach Geräten** zeigt jede PV-Anlage
  einzeln und eine solche Batterie so, wie die Energie fließt: ihren
  Wechselrichter mit der Leistung an seinem Anschluss (**Abgabe** ins Haus
  oder **Aufnahme** aus dem Haus), darunter ihre PV und die Batterie mit
  ihrer Leistung auf der DC-Seite. Was dabei fehlt, sind die Verluste des
  Wechselrichters.
- **Kennzahlen**: Status (zuerst Probleme – Smart Meter ohne Werte, Batterie
  nicht lesbar oder reagiert nicht –, sonst der Betriebsmodus), Strategie
  (ein Wechsel erscheint erst, wenn er 30 Sekunden besteht; die Regelung
  wechselt sofort), Ladezustand, gespeicherte Energie und Kapazität, Einspeisegrenze, Prognosen
  und die **erwartete Einspeisung heute**: die seit Mitternacht gemessene
  Einspeisung plus die, die der Plan noch erwartet (die blaue Linie des
  Diagramms).
- **Prognose-Diagramm** (mittlere Leistung je halbe Stunde in kW):
  - **Heute** zeigt PV- und Verbrauchsprognose (gestrichelt), die bisher
    gemessenen Werte (durchgezogen), die erwartete und die gemessene
    Einspeisung ins Netz (blau; die erwartete aus dem Plan, einschließlich
    Nachtentladung, höchstens bis zur Einspeisebegrenzung), den erwarteten und
    den gemessenen Netzbezug (blau, unter der Nulllinie; nur wenn es welchen
    gibt), das geplante Laden
    der Batterien (helle Balken) und das gemessene Laden (kräftige Balken), die
    geplante Last der Tagesziele der Verbraucher (rosa, gestrichelt; Teil der
    Verbrauchsprognose), dazu den prognostizierten Gesamt-Ladezustand
    (gestrichelt) und den gemessenen (durchgezogen) mit ihrer Skala in %
    rechts.
  - **Grenzlinien**, je in der Farbe der Kurve, die sie begrenzen, gedämpft
    und mit eigener Strichart: die **PV-Grenze der Einspeisebegrenzung**
    (orange-grau), die **Grenze netzdienliches Laden** (grün-grau, heute ab
    jetzt, solange PV erwartet wird: Darüber laden die Batterien) und die
    **Grenze Bezugsspitzen** (blau-grau, unter der Nulllinie). Sind mehrere
    davon aktiv, sind alle ausgeblendet, bis man sie in der Legende einschaltet;
    eine einzelne ist sichtbar. Die Auswahl in der Legende merkt sich der
    Browser. Die Simulation zeichnet die Grenzen mit ihren eigenen
    Einstellungen.
  - Laden aus dem Netz ist blau: beim geplanten Laden der Teil aus dem Netz
    oben auf dem Teil aus PV. Das gemessene Laden ist grün, wenn es fast nur
    aus PV kam, blau, wenn es fast nur aus dem Netz kam (geladen bei
    gleichzeitigem Netzbezug), und grün-blau gestreift, wenn beides zugleich
    lud. Der Netzanteil wird aus 5-Minuten-Mittelwerten geschätzt und bei
    schwankender Leistung überschätzt; unter 20 % oder unter 50 Wh je halbe
    Stunde gilt das Laden daher als PV.
  - **Morgen** zeigt Prognosen, geplantes Laden und Ladezustand des nächsten
    Tages, fortgeführt aus der Prognose von heute.
  - Die Prognose folgt der Planung: Laden nur mit dem geplanten Überschuss,
    Defizite aus den Batterien, Bezugsspitzen abfangen und Nachtentladung,
    falls aktiviert.
  - Beim Überfahren erscheinen die Werte einer halben Stunde (am Handy durch
    Antippen, Tippen daneben schließt sie); das Auswählen eines Eintrags der
    Legende blendet diese Kurve aus oder ein (die Skala folgt den angezeigten
    Kurven; der Browser merkt sich die Auswahl; **PV über der Grenze** der
    Einspeisebegrenzung ist ausgeblendet, bis man es einschaltet); **Tabelle
    anzeigen** schaltet auf eine Tabelle um.
- **Preisdiagramm** (nur mit einem [Tarif](#tarife-optional), dessen Preise
  sich über den Tag ändern, mit Börsenpreisen oder mit preisbewusster
  Steuerung), unter dem
  Prognose-Diagramm auf derselben Zeitachse: je Viertelstunde der Preis einer
  bezogenen kWh mit dem aktuellen Tarif und die Einspeisevergütung (ct/kWh
  inkl. USt., mit Zeitfenstern und dynamischen Preisen, ohne Grundgebühren)
  und der Börsenpreis, wenn Börsenpreise abgerufen werden, mit seiner Quelle.

### Reiter Batterien

![Reiter Batterien: eine Karte je Batterie mit Ladezustand, Leistung, Vorgabe und Zell-Delta](docs/images/dashboard_batteries_de.png)

Ladezustand, gespeicherte Energie und Kapazität (kWh), netzseitige Leistung
(AC) mit Richtung (eine stehende Batterie ist **Voll** oder **Leer**, wenn SLEMS
sie wegen ihres Ladezustands oder ihres BMS nicht weiter lädt oder entlädt,
sonst **Standby**), die Vorgabe von SLEMS, Wirkungsgrad, Status und der
Schalter **Aktiviert** jeder Batterie (das Deaktivieren muss bestätigt werden),
das Zell-Delta mit seinem Status, eine Empfehlung für den aktiven
Zellausgleich und die Phase eines laufenden Ausgleichs.

### Reiter Verbraucher

![Reiter Verbraucher: Wärmepumpe nur gemessen, Heizstab mit Einstellungen und Tagesziel](docs/images/dashboard_consumers_de.png)

Eine Karte je Verbraucher mit den wichtigen Werten: aktuelle Leistung,
Temperatur, **Zeitfenster** (das geplante Fenster, sonst die eingestellte Zeit),
**Energiebedarf** (der noch nötige, bei erreichtem Ziel der der nächsten
Periode), Stand des Tagesziels und die Freigabe; bei einer Wallbox das Auto.
Eine Wärmepumpe zeigt oben ihren **Status** und die Prognose des Tages (mit
gesteuertem Warmwasser **Heizen heute**: ohne die Warmwasser-Energie, nur bei
einer einzigen Wärmepumpe) und die Werte des Warmwassers im Abschnitt
**Warmwasser-Steuerung**. Über **Einstellungen anzeigen** klappt die Karte auf
(der Browser merkt sich die Auswahl).

Das Menü (⋮) neben dem Namen, das auch eine Verbraucher-Box im Energiefluss
öffnet, schaltet die Steuerung, startet oder beendet **Sofort laden**, lernt die
Phase neu und öffnet den **Verlauf** und die Geräteseite. Seine **Details**
zeigen die geplante Leistung, die Phase, die gelernten Reaktionszeiten beim Ein-
und Ausschalten (eigener Sensor und am Zähler; das Einschalten enthält eine
Anlaufverzögerung des Geräts, z. B. eines Kompressors), den ganzen Stand und
Plan des Tagesziels und die Batterie-Unterstützung.

### Simulation

![Simulation: Tagesplan ohne Einspeisebegrenzung im Vergleich zu den aktuellen Einstellungen](docs/images/dashboard_simulation_de.png)

Andere Einstellungen ausprobieren, ohne etwas zu ändern.

- **Was sich ändern lässt**: Neben dem Tagesdiagramm (heute ab jetzt oder
  morgen) lassen sich netzdienliches Laden, Nachtentladung, Bezugsspitzen
  abfangen und Einspeisebegrenzung ein- und ausschalten; ihre Werte erscheinen
  wie in den Einstellungen unter dem jeweiligen Schalter. Auch die Batterien
  (Kapazität, minimaler/maximaler Ladezustand, Lade- und Entladeleistung;
  vorgegeben ist die Leistung der Batterien ohne die momentanen Grenzen durch
  den Ladezustand), der
  Sicherheitspuffer der Nachtentladung, die maximale Einspeisung beim Entladen
  und die PV- und Verbrauchsprognose (±%) lassen sich ändern. Mit
  Vergleichstarifen ([Tarife](#tarife-optional)) wählt die Gruppe **Tarif**
  ganz oben einen anderen **Tarif für Bezug** und **Tarif für Einspeisung**
  (z. B. einen dynamischen Tarif, einen mit günstigerem Netzpreis zu Mittag
  oder einen mit Leistungspreis). Alles, was Preise nutzt, plant damit: die
  preisbewusste Steuerung, die Läufe der Tagesziele im günstigsten Fenster
  und Bezugsspitzen abfangen mit Leistungspreis; das Preisdiagramm zeigt dann
  deren Preise.
- **Ergebnis**: SLEMS rechnet die Tagespläne wie die echten, ab dem aktuellen
  Ladezustand mit den aktuellen Prognosen; der Plan mit den aktuellen
  Einstellungen ist zum Vergleich grau gepunktet, darunter stehen Kennzahlen
  nebeneinander (Einspeisung, Netzbezug, höchster Bezug und höchste
  Einspeisung, abgeregelte Energie, Ladezustand um Mitternacht; mit einem
  Tarif auch Bezugskosten, Einspeisevergütung und beides zusammen, aus den
  Preisen je kWh inkl. USt. ohne Jahresgebühren; mit Leistungspreis die
  höchste geplante Stunde und eine Schätzung der verrechneten Leistung und
  des Leistungspreises des Monats, weil der Plan in Stunden rechnet).
- **Nichts davon wird gespeichert** oder für die Regelung verwendet; jeder
  Besuch beginnt mit den aktuellen Einstellungen.
- **Heute** wird ab jetzt simuliert (die Werte davor sind gemessen); am
  Nachmittag schlägt ein Hinweis **Morgen** für einen ganzen simulierten Tag
  vor.
- Die Tagesziele der Verbraucher werden mit den simulierten Einstellungen und
  dem simulierten Tarif neu geplant (z. B. ein Lauf im günstigeren Fenster
  eines anderen Tarifs); sonst
  werden von SLEMS gesteuerte Verbraucher und der Batterievorrang (er wirkt in
  der Echtzeit-Verteilung) nicht simuliert.

**Tarifvergleich**: Mit [Tarifen](#tarife-optional) zeigt der Reiter
Simulation für jeden Monat des letzten Jahres (und den laufenden Monat bis
heute) den aufgezeichneten Netzbezug und die Einspeisung und je Tarif
inklusive Umsatzsteuer die Kosten des Bezugs und die Vergütung der Einspeisung
(abzüglich ihrer Gebühren; ein Schalter zeigt beides summiert), unter jedem
Vergleichstarif die Differenz zum aktuellen (grün besser, rot schlechter). Die Vergleichstarife lassen sich durch Auswählen
ihres Namens ein- und ausblenden (im Browser gemerkt).
Es ist ein passiver Vergleich: die aufgezeichnete Energie anders bepreist,
nicht das, was SLEMS mit einem anderen Tarif anders gemacht hätte (z. B. die
Batterien in günstigen Stunden laden). Dynamische Tarife nutzen die
gespeicherten Börsenpreise; Energie ohne Börsenpreis wird markiert. Mit
Batterien steht unter den Kosten eines Tarifs eine Schätzung, was die
[preisbewusste Steuerung](#tarife-optional) gespart hätte: Aufgezeichneter
Hausverbrauch und PV laufen stündlich durch ein einfaches Batteriemodell,
einmal wie üblich und einmal geplant wie die preisbewusste Steuerung (mit
Laden aus dem Netz, wenn es eingeschaltet ist). Der Plan kennt Verbrauch und
PV genau, die Schätzung ist daher eine Obergrenze. Tarife, bei denen die
Steuerung nichts spart (Fixpreis), haben keine solche Zeile.

### Reiter Einstellungen

Alle Einstellwerte gruppiert und direkt änderbar; wird ein Wert gelernt
(Schalter **… automatisch**), steht dort der gelernte Wert. Die Karte
**Regelung** zeigt auch das Aktualisierungsintervall des Smart Meters und die
Reaktionszeit der Batterie. Einstellungen, die gerade nichts bewirken (kein
Tarif, keine steuerbare Batterie, für den Batteriewechsel weniger als zwei),
folgen am Ende unter **Aktuell nicht nutzbar**: ausgegraut, gesperrt und mit dem
Hinweis, was sie brauchen.

### Bedienung des Dashboards

Das Auswählen eines Werts (Kachel, Kasten im Energiefluss außer dem eines
Verbrauchers, der sein Menü öffnet, Zeile einer Karte) öffnet den Home-Assistant-Dialog der zugehörigen Entität mit Verlauf und
Einstellungen.

Das Dashboard folgt der Sprache und dem hellen/dunklen Design von Home
Assistant und funktioniert auch am Handy (bei wenig Platz stehen die Batterien
im Energiefluss untereinander).

## Betrieb und Regelung

### Betriebsmodus

Die Entität **SLEMS Betriebsmodus** schaltet zwischen:

- **Aus**: Es wird nichts geplant oder gesendet.
- **Nur beobachten**: Prognosen und Pläne werden berechnet und angezeigt, aber
  keine Befehle an Batterien oder Verbraucher gesendet. Das ist die
  Voreinstellung.
- **Aktiv**: Pläne werden ausgeführt: SLEMS sendet Vorgaben an die Batterien
  und schaltet bzw. stellt die Verbraucher. Es reagiert auf jede Änderung des
  Smart Meters. Die Entität **Regelstatus** zeigt, ob die Regelung aktiv ist oder
  pausiert, weil der Smart Meter eine Weile nichts gemeldet hat (60 s bzw.
  zehn Aktualisierungsintervalle; die Batterien folgen dann ihrer eigenen
  Logik, bis der Zähler wieder meldet).

### Schlechtwetter-Modus

Kommt schlechtes Wetter, speichert der Schalter **Schlechtwetter-Modus** (auch
im Menü des SLEMS-Logos im Energiefluss) möglichst viel des heutigen
Überschusses und behält ihn über die nächste Nacht:

- Die Batterien laden jeden Überschuss sofort, statt netzdienlich zu laden,
  und die Nachtentladung ist aus.
- Ziel-Netzüberschuss, Einspeisebegrenzung und die Grenzen der Batterien
  bleiben.
- Er endet von selbst am Morgen nach dem nächsten Abend: tagsüber
  eingeschaltet am nächsten Morgen, nachts eingeschaltet (nach dem Abend oder
  vor dem Morgen) am übernächsten Morgen.
- Der Abend ist das Ende der letzten Stunde, in der die PV-Prognose über der
  Verbrauchsprognose liegt (ohne eine solche Stunde der Sonnenuntergang). Der
  Morgen ist der Beginn der ersten Stunde danach, in der die PV-Prognose
  wieder über der Verbrauchsprognose liegt, denn bis dahin plant die
  Nachtentladung.
- Ohne eine solche Stunde in der Prognose (mehrere trübe Tage) läuft er
  weiter bis zum Sonnenaufgang nach dem Ende der Prognose. Das Ende folgt
  jeder neuen Prognose; das Attribut `until` zeigt es.
- Solange er an ist, trägt das Logo eine Regenwolke; Prognose-Diagramm und
  erwartete Einspeisung berücksichtigen ihn.

### So funktioniert die Regelung

Im Betriebsmodus **Aktiv** reagiert SLEMS auf jeden neuen Wert des Smart Meters:

1. Aus der Energiebilanz berechnet es, welche Batterieleistung die
   Netzleistung genau auf den Zielwert bringen würde (z. B. 100 W Einspeisung
   beim Laden).
2. Es berücksichtigt nur Batteriebefehle, die der Smart Meter bereits zeigen
   kann. Ein neuer Befehl braucht eine Weile, bis er im Zählerwert auftaucht;
   diese **Reaktionszeit** lernt SLEMS und zählt einen Befehl nicht doppelt.
   Dasselbe gilt für die gesteuerten Verbraucher: Bis ein Befehl den Zähler
   erreicht (**Reaktionszeit am Zähler**, je Verbraucher gelernt), zählt die
   Leistung davor, danach die gemessene, oder die befohlene, solange der
   eigene Sensor des Verbrauchers noch nicht nachgezogen hat. So beschreiben
   Zählerwert, Batterien und Verbraucher immer denselben Zeitpunkt.
3. Es springt nicht sofort auf den berechneten Wert, sondern geht pro Zyklus
   einen Teil des Weges, die **Regelverstärkung**. Bei 0,5 wird pro Zyklus die
   Hälfte der verbleibenden Abweichung korrigiert. Eine hohe Verstärkung
   reagiert schneller, eine zu hohe schießt über und lässt die Netzleistung
   hin- und herschwingen.

**Automatische Regelverstärkung** (Schalter, standardmäßig an): Die
eingestellte **Regelverstärkung** ist die Obergrenze; SLEMS senkt sie nur, wenn
die Regelung schwingt:

- SLEMS beobachtet den Smart Meter: Pendelt die Netzleistung dreimal
  hintereinander um das Ziel zwischen Bezug und Einspeisung, jeweils kurz
  nachdem die Regelung ihre Richtung gewechselt hat, und werden die
  Ausschläge nicht kleiner, schwingt die Regelung: Die Verstärkung wird
  sofort verringert (× 0,8, bis 0,2).
- Nach jeweils 5 Minuten ohne Schwingen steigt sie in kleinen Schritten (+
  0,05) wieder bis zur eingestellten Regelverstärkung.
- Nach einer Verringerung wartet SLEMS 30 Sekunden, um die Wirkung zu sehen.
  Normale Laständerungen (Wasserkocher, Wolke, ein taktender Herd, die kurzen
  Pulse einer Kaffeemaschine) werden nicht als Schwingen gewertet: Dort
  springt der Zähler stärker, als die Batterien ihn ändern können, oder der
  Zähler bewegt sich zuerst und die Regelung folgt. Der gelernte Wert bleibt
  über Neustarts erhalten.

Eine höhere Verstärkung macht die Batterien nicht schneller, denn ihre
Reaktionszeit ist schon eingerechnet. Sie lässt die Regelung nur stärker auf
Fehler reagieren, z. B. auf eine falsch gelernte Reaktionszeit. Meldet der
Smart Meter schnell (z. B. über Modbus alle 0,5 s), ist 0,5 ein guter Wert;
meldet er nur alle paar Sekunden, darf sie höher sein (z. B. 0,7).

Die gerade verwendete Verstärkung zeigt der Sensor **Aktuelle
Regelverstärkung**. Ist die Automatik ausgeschaltet, gilt die
**Regelverstärkung** als fester Wert; wird sie wieder eingeschaltet, macht die
Anpassung mit der zuletzt gelernten Verstärkung weiter, höchstens mit der
eingestellten. Das
**Regelintervall** (Standard 1 s) begrenzt, wie oft SLEMS Befehle sendet.

Diagnose-Sensoren zeigen, was SLEMS gelernt hat:

| Sensor | Bedeutung |
|---|---|
| **Aktuelle Regelverstärkung** | die gerade verwendete Verstärkung |
| **Aktualisierungsintervall Smart Meter** | wie oft der Smart Meter meldet; Attribut `source`: Modbus oder Entität |
| **Reaktionszeit Batterie** | Zeit von einem Batteriebefehl, bis der Smart Meter ihn zeigt, für alle Batterien gemeinsam; je Batterie (aus Schritten, die sie weitgehend allein macht) unter **Details** der Batterie, von der Regelung genutzt, sobald gelernt |
| **Geplante Leistung** eines Verbrauchers, Attribut `response_time_s` | Zeit von einem Befehl, bis der eigene Leistungssensor des Verbrauchers reagiert |

Die Automatik nur ausschalten, wenn sich die Verstärkung ständig deutlich
ändert, z. B. weil der Smart Meter sehr unregelmäßig meldet; dann einen festen
Wert setzen (0,3–0,5 ist ein guter Start).

### Welche Option wann?

Alle Optionen sind optional und lassen sich kombinieren. Die Diagramme zeigen
dasselbe Beispiel ohne und mit der jeweiligen Option: 8 kWp PV an einem
sonnigen Tag, ein Haushalt mit Morgen- und Abendspitze, eine 10-kWh-Batterie,
von 18:00 bis Mitternacht des nächsten Tages. Berechnet sind sie mit der
SoC-Projektion von SLEMS (Stundenmittel); sie zeigen also, wie SLEMS plant,
die echten Kurven hängen von deinen Prognosen ab. Oben: PV-Erzeugung und
Verbrauch (ohne und mit der Option gleich); Mitte: Gesamt-Ladezustand; unten:
Netzleistung (+ Bezug / − Einspeisung); grau gestrichelt ohne, farbig mit der
Option.

**Netzdienliches Laden** (standardmäßig an)

![Netzdienliches Laden](docs/images/grid_friendly_de.svg)

Ohne sind die Batterien schon vor Mittag voll, und die ganze Mittagsspitze
geht ins Netz (hier 5,7 kW). Mit laden sie mit dem Überschuss über einer
Einspeisegrenze und sind am Nachmittag trotzdem voll; die Spitze sinkt auf
etwa 4,4 kW. Sinnvoll, wann immer viele PV-Anlagen gleichzeitig einspeisen
(Netz, Energiegemeinschaft). Ausschalten, wenn die Batterien möglichst früh
voll sein sollen, z. B. für die Notstromversorgung.

**Nachtentladung**

![Nachtentladung](docs/images/night_discharge_de.svg)

Energie, die morgens noch in den Batterien ist, wird über Nacht eingespeist,
bis auf eine Reserve, die die PV-Prognose am nächsten Tag wieder auffüllen
kann (hier **Morgenreserve** 10 %: etwa 2,9 kWh werden über Nacht
eingespeist und mittags 3,2 kWh weniger, weil die Batterien mehr Platz für die
PV haben). Sinnvoll, wenn die Batterien morgens noch gut geladen sind (große
Batterie, wenig Verbrauch in der Nacht, Sommer):

- In einer **Energiegemeinschaft** findet deine Energie nachts eher einen
  Abnehmer: tagsüber erzeugen die meisten Mitglieder selbst, nachts
  verbrauchen sie. Die Nachtentladung verschiebt einen Teil deines
  Überschusses von Mittag in die Nacht.
- Sie wirkt gut mit der **Einspeisebegrenzung** zusammen: Die Batterien
  starten mit Platz für die Energie über der Grenze in den Tag, SLEMS muss
  dann selten kurz vor der Spitze einspeisen.

Die nachts eingespeiste Energie geht zweimal durch die Batterie (Lade- und
Entladeverluste, etwa 10 %), und bis zur nächsten Ladung bleibt weniger
Energie für einen Stromausfall. Ausschalten, wenn die Batterien als
Notstromreserve dienen sollen.

**Bezugsspitzen abfangen** (sinnvoll an Tagen mit wenig PV, z. B. im Winter)

![Bezugsspitzen abfangen](docs/images/peak_shaving_de.svg)

Ohne deckt die Batterie alles, bis sie am Abend leer ist; die Morgenspitze
kommt dann voll aus dem Netz (hier 2,1 kW). Mit deckt sie unter der
Ladezustand-Schwelle nur die Leistung über der Bezugsgrenze: Die Grundlast
kommt aus dem Netz, die Batterie hält ihre Energie für die Spitzen (hier
höchstens 1,5 kW). Manche Spitzen werden höher als ohne (hier um 19:00 1,5
statt 0,4 kW, weil die Batterie nicht mehr alles deckt), aber keine geht über
die Bezugsgrenze, die höchste Spitze sinkt also. Sinnvoll bei einem
leistungsabhängigen Tarif oder Netzentgelt oder bei einem schwachen
Netzanschluss. Etwas mehr Energie kommt aus dem Netz (hier 1 kWh), sie ist am
Ende aber noch in der Batterie: Die Spitzen sinken, die Energiebilanz bleibt
etwa gleich.

**Einspeisebegrenzung** (wenn die Einspeisung begrenzt ist, z. B. auf 60 % der
Spitzenleistung)

![Einspeisebegrenzung](docs/images/feed_in_cap_de.svg)

Ohne sind die Batterien mittags voll, und der Wechselrichter regelt alles über
der Grenze ab (rot, hier 7,2 kWh bei einer Grenze von 45 %). Mit hält SLEMS
genug Platz frei; hier schafft die Nachtentladung den Platz, und die Batterien
nehmen die Energie über der Grenze auf. Nötig, sobald dein Netzbetreiber die
Einspeisung begrenzt; sie hat Vorrang vor den anderen Optionen.

### Netzdienliches Laden

Wird geladen, sobald Überschuss da ist, sind die Batterien schon am Vormittag
voll, und die PV-Einspeisespitze zu Mittag geht vollständig ins Netz. Mit
**Netzdienliches Laden** (Schalter, standardmäßig an) verschiebt SLEMS das Laden
in die Spitze:

- Aus PV- und Verbrauchsprognose berechnet es eine **Einspeisegrenze**: die
  höchste Einspeisung, bei der der Überschuss darüber die Batterien bis
  Tagesende trotzdem füllt (Ladeverluste, maximale Ladeleistung und **Puffer
  netzdienliches Laden** eingerechnet; Standard 1 kWh, ein größerer Puffer
  senkt die Grenze und macht die Batterien früher und zuverlässiger voll, wenn
  die Prognose zu optimistisch ist). Ohne Grenze zeigt die Kachel in der
  Übersicht den Grund: **keine – sofort laden** (der Überschuss reicht nicht),
  **aus (Schlechtwetter-Modus)** oder **keine Prognose**; bei ausgeschaltetem
  netzdienlichem Laden ist die Kachel ausgeblendet. Der Sensor hat dann keinen
  Wert und den Grund im Attribut `reason`.
- Die Batterien laden nur mit dem Überschuss oberhalb dieser Grenze; darunter
  geht die Leistung an die Verbraucher oder ins Netz. Gekappt werden die
  höchsten Stunden des Tages, egal wohin die Wolken sie schieben.
- Die Grenze wird laufend aus dem aktuellen Ladezustand und der restlichen
  Prognose neu berechnet. Hinkt das Laden hinterher (z. B. mehr Wolken als
  vorhergesagt), sinkt sie von selbst.
- Die PV-Prognose wird mit der tatsächlichen Erzeugung des Tages korrigiert
  (Diagnose-Sensor **Korrektur PV-Prognose**: Verhältnis der Erzeugung zur
  Prognose bis jetzt, 50–120 %). Ein Morgen sagt wenig über den ganzen Tag
  (Nebel, ein Hügel, der die ersten Stunden abschattet), deshalb wirkt das
  Verhältnis gewichtet: auf die laufende Stunde zu 80 %, über zwei Stunden
  auslaufend, und auf den Rest des Tages erst, wenn 15 % der prognostizierten
  Tagesenergie vorbei sind, voll ab 50 % (Attribute des Sensors). Nach einem
  Neustart übernimmt SLEMS die bisherige Erzeugung aus den Statistiken des
  PV-Sensors, es geht also nichts verloren.
- Solange die Ladung nicht gesichert ist (Ladezustand unter **Batterievorrang
  unter Ladezustand** oder die Prognose reicht nicht), lädt SLEMS wie bisher
  sofort.

Wie viel der Spitze abgefangen werden kann, hängt von der Batteriegröße im
Verhältnis zum Tagesüberschuss ab: An einem klaren Sommertag mit großem
Überschuss nimmt eine 10-kWh-Batterie etwa das oberste Kilowatt der Spitze ab,
an Tagen mit weniger Überschuss einen deutlich größeren Anteil.

### Einspeisebegrenzung

Manche Netzbetreiber oder Vorschriften erlauben einer PV-Anlage nur, einen
Teil ihrer Spitzenleistung einzuspeisen (z. B. 60 %); der Wechselrichter
regelt alles darüber ab. Mit **Einspeisebegrenzung** (Schalter, standardmäßig
aus) speichert SLEMS diese Energie stattdessen in den Batterien. Die Grenze
ist **PV-Spitzenleistung** (kWp) × **Grenze der Einspeisebegrenzung** (%),
gemessen am Netzanschlusspunkt (Einspeisung, nach dem Hausverbrauch).

- **Planung**: Aus der PV-Prognose in ihrer feinsten Auflösung (15/30/60 min,
  damit kurze Spitzen nicht weggemittelt werden) und der Verbrauchsprognose
  berechnet SLEMS bis Ende morgen, wie viel Energie über der Grenze liegt und
  wie viel freien Platz die Batterien dafür brauchen. Mehrere Spitzen am Tag
  (Wolken dazwischen) und Spitzen heute und morgen werden berücksichtigt: Eine
  Wolkenlücke oder die Nacht dazwischen, in der die Batterien das Haus
  versorgen, schafft wieder Platz; ein Überschuss unter der Grenze nicht.
- **Puffer**: **Puffer der Einspeisebegrenzung** (Standard +20 %, negative Werte
  planen mit weniger) kommt auf die aufzunehmende Energie. Mit **Puffer der
  Einspeisebegrenzung automatisch** verwendet SLEMS stattdessen die
  aufgezeichneten Abweichungen der PV-Prognose (siehe **Prognosegüte**): Von den
  Tagen mit mehr PV als prognostiziert hebt die Unterschätzung, die an 80 %
  davon nicht überschritten wurde, die PV-Prognose an. Das braucht 14
  aufgezeichnete Tage; bis dahin gilt der feste Puffer. Zusätzlich bleibt je
  Spitze auf jeden Fall ein fester Mindestpuffer von 5 % der
  PV-Spitzenleistung als Energie einer Stunde frei (0,5 kWh bei 10 kWp): Er
  deckt Fehler bei Zeitpunkt und Höhe einer Spitze ab, die in den
  Tagesabweichungen nicht sichtbar sind, und kleine Spitzen, bei denen der
  prozentuale Puffer winzig ist. Die Einstellungen zeigen die Grenze, den
  verwendeten Puffer und den Mindestpuffer.
- **Platz schaffen**: Mit Überschuss unter der Grenze wird nur geladen,
  solange der später nötige Platz frei bleibt; über der Grenze laden die
  Batterien immer. Die Nachtentladung (falls eingeschaltet) entlädt so weit,
  dass der Platz frei ist. Reichen Hausverbrauch und Nachtentladung nicht,
  speist SLEMS vor der Spitze Batterieenergie ein, möglichst spät: geplant so,
  dass es eine Stunde vor der Spitze mit 70 % der möglichen Leistung fertig
  ist, nie über der Grenze. Dafür darf es **Maximale Einspeisung beim Entladen**
  überschreiten.
- **Reihenfolge in der Spitze**: Der Überschuss über der Grenze geht an die
  Batterien (unabhängig von Batterievorrang, Batterieanteil und netzdienlichem
  Laden), dann an die Verbraucher mit dem Einsatz **Unterstützend**, dann an die
  mit **Normal**; erst der Rest wird abgeregelt. Zeigt die Prognose, dass eine
  Spitze nicht in die Batterien passt (zu klein oder zu spät für Platz, mit
  den Puffern), plant SLEMS die unterstützenden Verbraucher für den Teil, der
  nicht passt, ab Beginn der Spitze ein: Ein Verbraucher mit wenig Leistung
  würde sonst erst anfangen, wenn die Batterien voll sind, und könnte dann
  viel weniger als den Überschuss aufnehmen. Mit Temperaturfühlern plant SLEMS
  ihn nur so weit ein, wie sein Speicher noch Energie aufnehmen kann. Die
  Einspeisebegrenzung hat Vorrang vor netzdienlichem Laden (dessen
  Einspeisegrenze liegt nie über der Begrenzung), Nachtentladung und
  Batterievorrang; die Schwelle des Spitzenabfangs bleibt beim Einspeisen eine
  Untergrenze.
- **Übersicht**: Die Kachel **Einspeisebegrenzung** zeigt die nächste Spitze,
  die Energie, die die Batterien aufnehmen müssen, und falls nötig die
  Energie, die davor einzuspeisen ist, und bis wann. Das Tagesdiagramm zeigt
  die **PV-Grenze der Einspeisebegrenzung** als orange-grau gestrichelte Linie:
  erwarteter Verbrauch + Grenze, also die PV-Leistung, ab der die Einspeisung
  über der Grenze läge (die Grenze gilt nach dem Hausverbrauch, deshalb folgt
  die Linie der Verbrauchsprognose). Die Energie darüber steht als Balken auf
  dieser Linie; rot der Teil, der laut Plan trotzdem verloren geht, weil die
  Batterien voll sind oder zu langsam laden (den Grund zeigt der Tooltip;
  Verbraucher, die Überschuss über der Grenze aufnehmen, sind berücksichtigt).
  Die Kennzahl **Abgeregelt** in der Simulation ist dieselbe Energie.
- **Warnungen** (Übersicht und Benachrichtigungen): Batterien zu klein für den
  nötigen Platz, zu wenig Zeit oder Leistung, um vor der Spitze einzuspeisen,
  Ladeleistung zu gering für den Überschuss über der Grenze, und Einspeisung
  seit mehr als 5 Minuten über der Grenze. **Batterien zu klein** heißt, dass
  schon die Prognose nicht hineinpasst; passt nur der Sicherheitspuffer nicht
  vollständig, zeigt die Übersicht stattdessen den Hinweis **Prognose im
  Pufferbereich** (ohne Benachrichtigung). Energie, die nicht in die Batterien
  passt (zu klein oder zu spät für Platz), gilt nicht als Problem, soweit die
  unterstützenden und normalen Verbraucher sie in der Spitze aufnehmen können;
  die Übersicht zeigt dann den Hinweis **Verbraucher übernehmen den Rest** mit
  der Energie und den Verbrauchern. Batterien, die gerade nicht in der Planung
  sind (Zellausgleich, Kommunikation pausiert, deaktiviert, reagiert nicht),
  nennen die Texte.

Sensoren: **Einspeisebegrenzung: aufzunehmende Energie** (am Tag der nächsten
Spitze, mit den Spitzen, dem nötigen Platz, dem Einspeiseplan und dem Puffer
als Attribute) und **Einspeisebegrenzung: vor der Spitze einzuspeisen**.

### Bezugsspitzen abfangen und Leistungspreis

**Bezugsspitzen abfangen** (Schalter, standardmäßig aus) hält Energie in den
Batterien für Verbrauchsspitzen zurück: bei einem Tarif mit Leistungspreis
(z. B. die österreichischen Netztarife ab 2027, die die höchste Viertelstunde
des Monats verrechnen) oder bei einem schwachen Netzanschluss.

**Grundprinzip.** Zwei Werte bestimmen es:

- **Bezugsgrenze für Spitzenabfang**: Bezug bis zu dieser Leistung kommt aus
  dem Netz, darüber decken die Batterien.
- **Ladezustand-Schwelle für Spitzenabfang**: Ab diesem Ladezustand (aller
  Batterien zusammen) abwärts decken die Batterien *nur noch* den Bezug über
  der Grenze. Die Energie zwischen dem minimalen Ladezustand (z. B. 12 % bei
  der Venus) und der Schwelle ist damit für Spitzen reserviert. Darüber
  arbeiten die Batterien wie sonst und decken den Bezug über der Grenze auch,
  wenn sie sonst nicht entladen würden (Preisplan, Netzladen, Verbraucher
  ohne Batterie-Unterstützung). Netzladen bleibt unter der Grenze.

**Modus Spitzenabfang** wählt, was SLEMS davon berechnet:

| Modus | Du stellst ein | SLEMS berechnet |
|---|---|---|
| **Wirtschaftlich** (Standard, nur mit Leistungspreis) | – | Grenze und Schwelle mit den niedrigsten erwarteten Kosten |
| **Automatische Grenze** | Schwelle | die niedrigste Grenze, für die die Energie unter der Schwelle reicht |
| **Automatische Schwelle** | Grenze | die Schwelle, deren Energie die Spitzen über der Grenze deckt |
| **Manuell** | Grenze und Schwelle | – |

Ohne Leistungspreis im aktuellen Tarif ist **Wirtschaftlich** in den
Einstellungen ausgegraut und arbeitet wie **Automatische Grenze**. Berechnete
Werte zeigen die Einstellungen als Anzeige mit „(automatisch)“, der Sensor
**Wirksame Bezugsgrenze Spitzenabfang** die verwendete Grenze.

**Erwartete Spitzen.** Die Stundenprognose mittelt kurze Spitzen (Backofen,
Wasserkocher) weg. SLEMS nimmt deshalb die 5-Minuten-Statistik des
Hausverbrauchs der letzten etwa 10 Tage: je Stunde des Tages, wie viel Energie
über einer Leistung lag. Ohne Leistungspreis zählt der Mittelwert der Tage,
mit Leistungspreis der zweitschlechteste Tag (eine einzige verfehlte
Viertelstunde kostet den ganzen Monat). Die Energie muss bis zum Nachladen
reichen: bis der prognostizierte PV-Überschuss die Batterien vom minimalen
Ladezustand wieder bis zur Schwelle füllt (etwas Überschuss an einem
Regentag zählt nicht), höchstens 36 Stunden, gezählt nur die Stunden ohne
Überschuss. **Sicherheitsreserve Spitzenabfang** (Standard 20 %) hält
zusätzlich einen Teil zurück, gegen mehr Verbrauch als in den letzten Tagen.
Grenze und Schwelle werden laufend neu berechnet; über der Schwelle so, als
wäre sie erreicht, tagsüber für den kommenden Abend und die Nacht.

**Mit Leistungspreis** (Tarifposten mit „pro kW und Monat“, siehe
[Tarife](#tarife-optional)) arbeitet SLEMS mit dem, was die Rechnung zählt:

- **Viertelstunden-Budget**: Die Grenze gilt im Mittel über die Viertelstunde
  (oder Stunde), die der Tarif misst, nach der Uhr wie beim Smart Meter. Eine
  kurze Spitze, z. B. während die Batterien an ihrer Leistungsgrenze sind
  (eine Balkonbatterie bei 800 W), gleicht SLEMS mit weniger Bezug im Rest der
  Viertelstunde aus; solange Budget übrig ist, darf eine kurze Spitze über die
  Grenze gehen. Einspeisung zählt nicht gegen den Bezug, SLEMS speist also nie
  zum Ausgleich ein.
- **Was schon bezahlt ist**: Die Grenze liegt nie unter dem, was die
  Monatsspitze schon kostet (die bisher verrechnete Leistung, mindestens die
  Mindestleistung, aufgerundet wie im Tarif, minus 200 W Puffer gegen die
  Abweichung zwischen deinem Zähler und dem Smart Meter des Netzbetreibers).
  Weniger Bezug spart bis Monatsende nichts.
- **Zeitfenster**: Hat der Leistungspreis eines, zählen Viertelstunden
  außerhalb nicht; die Batterien heben ihre Energie für das Fenster auf.
- **Verbraucher treten zurück**: Können die Batterien den Bezug über der
  Grenze nicht decken, werden Verbraucher gedrosselt, die für ein Tagesziel
  laufen, die niedrigste Priorität zuerst: leistungsgeregelte bis auf ihre
  Mindestleistung, danach schalten die außerhalb ihrer Mindestlaufzeit ab.
  Das Tagesziel holt das später nach.

**Für Spitzenabfang aus dem Netz laden** (Schalter, standardmäßig an, nur mit
Leistungspreis sichtbar): Bezug unter der Grenze kostet keinen
Leistungspreis. Liegt der Ladezustand unter der Schwelle, laden die Batterien
deshalb vor der nächsten erwarteten Spitze aus dem Netz nach, bis zur
Schwelle: in den günstigsten Viertelstunden (mit dynamischem Tarif), mit
festem Preis möglichst spät (Heimspeicher altern vor allem mit der Zeit bei
hohem Ladezustand), und nur so weit, dass der prognostizierte Bezug unter der
Grenze bleibt; ein PV-Überschuss davor zählt mit. Die Energie für Spitzen
muss dann nur bis zum Ende des nächsten Spitzenblocks reichen (zwei Stunden
ohne erwartete Spitze beenden ihn) statt bis zum Nachladen durch PV. Die
Strategie zeigt dann **Spitzenabfang: Netzladen**.

**Wirtschaftlichkeitsrechnung** (Modus **Wirtschaftlich**): SLEMS probiert
Grenzen in 100-W-Schritten, ab dem, was der Monat schon kostet, bis zur
höchsten erwarteten Spitze, jede mit ihrer Schwelle, und rechnet die
erwarteten Kosten bis Monatsende:

- **Leistungspreis** des Monats für die Grenze (mit Abgaben und USt.),
- **zurückgehaltene Energie**, die beim Nachladen noch übrig ist (Reserve,
  Verluste): Sie nimmt dem PV-Überschuss Platz, der dann eingespeist wird,
  statt später Bezug zu ersetzen, je verbleibendem Tag mit Bezugspreis minus
  Einspeisevergütung,
- mit Nachladen aus dem Netz die **Lade- und Entladeverluste** und der
  **Verschleiß** der täglich nachgeladenen Spitzenenergie.

Die Grenze mit den niedrigsten Kosten gewinnt. Grenzen, deren höchste
erwartete Spitze über der Entladeleistung der Batterien liegt oder deren
Schwelle den maximalen Ladezustand erreichen würde, scheiden aus. Beispiel:
Ein kW weniger Spitze spart mit 2,46 € pro kW und Monat netto (mit 20 % USt.
2,95 €) knapp 3 € im Monat; 2 kWh täglich für die Abendspitzen nachzuladen
kostet bei 25 ct/kWh und 90 % Wirkungsgrad je Richtung etwa 12 ct am Tag,
gut 3,50 € im Monat (ohne Verschleiß). Es lohnt sich also, wenn die Spitze
dadurch um mehr als gut 1 kW sinkt.

**Anzeige**:

- Die Kachel **Leistungsspitze diesen Monat** in der Übersicht (nur mit
  Leistungspreis) zeigt die bisher verrechnete Leistung, wann die höchste
  Viertelstunde war, bis zu welcher Leistung eine Viertelstunde nichts mehr
  kostet, die Mehrkosten des Monats über der Mindestleistung und die laufende
  Viertelstunde mit dem Bezug, der für ihren Rest noch erlaubt ist.
- Der **Tarifvergleich** schätzt für Tarife mit Leistungspreis je Monat, was
  Spitzenabfang gespart hätte und bei welcher Grenze, aus den aufgezeichneten
  Viertelstunden (die niedrigste Grenze, die die Batterien mit ihrer
  Entladeleistung und ihrer nutzbaren Energie je Tag hätten halten können,
  abzüglich Verlusten und Verschleiß). So lässt sich mit einem
  Vergleichstarif schon vor einem Leistungspreis sehen, was er kosten würde
  und was Spitzenabfang bringt.
- Die Einstellungen weisen darauf hin, wenn die Schwelle den maximalen
  Ladezustand erreicht (die Grenze ist voraussichtlich nicht zu halten) oder
  die Monatsspitze die eingestellte Grenze anhebt.
- Die Diagnose enthält die Monatsspitze, das Budget der laufenden
  Viertelstunde, die geprüften Grenzen der Wirtschaftlichkeitsrechnung und
  den Nachladeplan; das Debug-Log nennt jede neue wirtschaftliche Grenze und
  jedes geplante Nachladen.

### Gelernte Werte

Jeder dieser Werte hat einen Schalter **… automatisch** (bzw. **Gelernte …
verwenden**). SLEMS lernt immer, unabhängig vom Schalter; er entscheidet nur,
was verwendet wird. Aus gilt dein eingestellter Wert; an verwendet SLEMS den
gelernten, sobald genug Daten vorliegen, bis dahin weiter deinen. Die Schalter
sind standardmäßig an (eine bestehende Installation behält ihre Schalter, wie
sie sind). Die Einstellungen zeigen den gelernten Wert (**gelernt**) oder den
Hinweis, dass noch Daten fehlen; der Diagnose-Sensor **Gelernte Werte in
Verwendung** enthält alle gelernten Werte als Attribute.

| Wert | Gelernt aus | Braucht |
|---|---|---|
| Puffer netzdienliches Laden | aufgezeichneten PV-Prognosen: der Anteil, um den die PV-Prognose zu hoch war, nicht überschritten für 85 % der prognostizierten Energie aller Tage (ein Tag mit mindestens der prognostizierten PV zählt mit 0, jeder Tag mit seiner Prognose gewichtet: trübe Tage mit kleiner Prognose bestimmen so nicht den Puffer eines klaren Tages), angewendet auf die heute noch erwartete PV; dazu, sobald gelernt, der Anteil zu niedriger Verbrauchsprognose wie beim Sicherheitspuffer, angewendet auf den Verbrauch, solange heute noch PV erzeugt wird | 14 aufgezeichnete Tage |
| Sicherheitspuffer gesicherte Ladung (auch Ziel der Nachtentladung) | dasselbe für die PV plus zu niedrige Verbrauchsprognose (Rückrechnung: von den Tagen mit zu niedriger Prognose die Abweichung, die an 85 % davon nicht überschritten wurde), angewendet auf den restlichen Tag bzw. die nächsten 24 Stunden; solange nur ein Teil gelernt ist, der größere Wert aus diesem und dem eingestellten Puffer | 14 PV-Tage, 7 Verbrauchstage |
| Ziel-Netzüberschuss beim Laden und Entladen | wie weit die Netzleistung Richtung Bezug schwankt, während die Batterien regeln; Laden: das Ziel hält das Netz 90 % der Zeit auf der Einspeiseseite (20–1000 W); Entladen: das Netz schwankt um das Ziel, je die Hälfte der Zeit auf jeder Seite (−100 bis 300 W, mit **Kein negativer Ziel-Netzüberschuss** 0 bis 300 W), weil ein kurzer Bezug dort wenig kostet, dauerhaftes Einspeisen aber Batterieenergie verschenkt | etwas Regelbetrieb im Modus **Aktiv** |
| Regelintervall und Mittelungsfenster | dem gelernten Meldeintervall des Smart Meters (0,8 × und 3 ×) | einige Meldungen des Zählers |
| Nutzbare Kapazität (je Batterie, **Gelernte Kapazität verwenden**) | Lade- und Entladevorgängen über mindestens 20 % Ladezustand: DC-Energie / Änderung des Ladezustands (Vorgänge mit einem Sprung des Ladezustands werden verworfen), Median der letzten zehn | drei Vorgänge |
| Leistung eines Ein/Aus-Verbrauchers nach der Außentemperatur | mittlerer Leistung und Außentemperatur (eigener Fühler, sonst Wetter-Entität) jedes Laufs ab 10 Minuten, die letzten 60 Läufe; eine Gerade, nur genutzt bei mindestens 8 K Spanne und mindestens 10 % Änderung der Leistung darüber (eine Sole-Wärmepumpe oder ein Heizstab zeigt keine, dann wirkt es nicht); Attribute des Sensors **Gelernte Leistung** | 6 Läufe |
| Morgenreserve (**Deckung der Morgenreserve** bestimmt, wie vorsichtig; nur sichtbar, solange die Reserve automatisch ist) | der Morgenlücke: Energie, die das Haus zwischen der geplanten Übernahme durch die PV (Ende der Nachtentladung) und der tatsächlichen (PV deckt den Verbrauch 15 Minuten lang) aus Batterie oder Netz brauchte, in % des prognostizierten Tagesverbrauchs; die Reserve deckt den gewählten Anteil der Morgen (90 % = 9 von 10), über 100 % die größte Lücke mal dem Wert (110 % = 10 % mehr als der schlechteste Morgen) | 14 gemessene Morgen (gemessen wird auch bei ausgeschalteter Nachtentladung) |
| Leistung und Thermostat eines Verbrauchers (je Verbraucher, **Gelernte Werte verwenden**) | der Leistung im eingeschalteten Zustand (Ein/Aus-Verbraucher: ersetzt die Nennleistung; leistungsgeregelte Verbraucher: die typische Leistung bei Volllast, gemessen bei einer Vorgabe ab 90 % der maximalen Leistung seit mindestens 15 s bzw. der doppelten Reaktionszeit, für die Planung: Prognose, Tagesziel, Einspeisebegrenzung; angesteuert wird weiter bis zur maximalen Leistung) und Pausen des eigenen Thermostats trotz Vorgabe von 30 s bis 10 min (zwei innerhalb von 30 Tagen: der eigene Thermostat taktet, siehe **Eigener Thermostat**; gilt auch ohne **Gelernte Werte verwenden**; längere Pausen, z. B. ein Luftentfeuchter bei erreichter Zielfeuchte, zählen nicht) | 30 Messwerte, zwei Pausen |

#### Phase von Batterien und Verbrauchern

Misst dein Smart Meter jede Phase, wähle in den SLEMS-Einstellungen seine
**Netzleistung L1**, **L2** und **L3** aus (vorgeschlagen aus dem Gerät des
Smart Meters, mit demselben Vorzeichen wie die Netzleistung). SLEMS lernt dann
die Phase jedes Verbrauchers hinter dem Smart Meter, und die jeder Batterie in
ihrem Selbsttest und ihrem Probeschritt:

- **Verbraucher**: SLEMS vergleicht die Leistung aller Batterien, Verbraucher
  und der PV-Anlage mit den Phasen des Smart Meters, jeweils über zwei Minuten
  gemittelt. Eine Änderung der Leistung eines Verbrauchers zeigt sich an einer
  Phase in etwa gleicher Größe, bei einem dreiphasigen Gerät zu einem Drittel
  an jeder Phase, auch wenn die Batterien sie gleichzeitig ausgleichen. SLEMS
  beginnt mit den letzten zwei Tagen aus dem Verlauf von Home Assistant und
  rechnet dann mit den aktuellen Werten weiter; ein Verbraucher, der in dieser
  Zeit ein paarmal geschaltet hat, bekommt seine Phase also sofort. Sensoren,
  die bis zu einer Minute verzögert melden, sind kein Problem.
- **Batterien**: Die Regelung bewegt die Batterien gemeinsam und gegen jede
  andere Last, daher lässt sich ihre Phase im normalen Betrieb nicht erkennen.
  Der [Selbsttest](docs/guide/batteries.de.md#selbsttest-einer-batterie) zeigt sie: Die anderen
  Batterien und die Verbraucher werden dabei festgehalten. Ebenso der
  [Probeschritt](docs/guide/batteries.de.md#reaktionszeit-und-phase-messen), wenn der Smart Meter seine
  Phasen so schnell meldet wie die Gesamtleistung.

Der Sensor **Phase** des Geräts zeigt das Ergebnis (**L1**, **L2**, **L3** oder
**Dreiphasig**), sein Attribut *shares* den gemessenen Anteil je Phase. Bei
einer Wallbox, deren aktive Phasen eine Entität nennt, lernt SLEMS nur die eine
Phase, mit der sie einphasig lädt. Die Phase steht in den **Details** der
Batterie und des Verbrauchers. Sie wird nur angezeigt und geprüft; die
Regelung verwendet sie noch nicht.

- **Wechsel**: Zeigt sich ein Verbraucher deutlich an einer anderen Phase
  (z. B. ein Heizlüfter in einer anderen Steckdose; ältere Werte verblassen
  über einige Tage), oder misst ein Selbsttest eine andere Phase, fragt
  **Einstellungen → Reparaturen**, an welcher Phase das Gerät hängt; die
  gemessene ist vorausgewählt.
- **Phase neu lernen** (Button des Geräts, **Neu lernen** neben der Phase in
  den **Details** oder im Menü (⋮) eines Verbrauchers, nach einer Rückfrage): verwirft die Phase und lernt sie neu aus dem Verlauf; bei einer
  Batterie danach einen Selbsttest machen. Kommt wieder dieselbe heraus, fragt
  Reparaturen, ob sie stimmt oder welche Phase stattdessen gelten soll. Eine
  andere als die gemessene Phase, die du gewählt hast, bleibt erhalten; ein
  Wechsel wird weiter gemeldet.

### Weitere Einstellungen (Entitäten)

- **Urlaub** (Schalter): Der Haushalt ist abwesend; manuell oder per Automation
  schalten. Tage, an denen der Schalter irgendwann an war, lässt SLEMS ganz aus
  dem gelernten Verbrauch heraus (Verbrauchsprognose samt Wärmepumpe,
  Sicherheitspuffer, Bezugsspitzen, Reserve der Nachtentladung, übliche
  Tagesenergie der Verbraucher), damit die Prognose nach der Rückkehr nicht
  wochenlang zu tief liegt. Blieben in den letzten 56 Tagen weniger als 7 Tage
  übrig, zählen alle Tage.
- **Batterievorrang unter Ladezustand** (Standard 30 %), **Sicherheitspuffer
  gesicherte Ladung** (Standard 1 kWh) und **Batterieanteil bei gesicherter
  Ladung** (Standard 75 %): Die Batterien bekommen den gesamten Überschuss, bis
  ihre Ladung gesichert ist, d. h. der Ladezustand über der Schwelle liegt und
  der erwartete PV-Überschuss des Tages die Energie bis zur Vollladung plus
  Sicherheitspuffer deckt. Danach wird aufgeteilt, der Anteil der Verbraucher
  geht nach Priorität; ein laufender Ein/Aus-Verbraucher behält seine Leistung
  vor leistungsgeregelten Verbrauchern höherer Priorität (die nehmen dafür
  weniger, damit er nicht aus- und wieder eingeschaltet wird). Was eine Seite
  nicht nutzen kann, bekommt die andere. Der Anteil gilt nur ohne
  **Netzdienliches Laden** (und wird nur dann angezeigt): Mit ihm bekommen die
  Batterien den Überschuss über seiner Einspeisegrenze, die Verbraucher den
  darunter.
- **Ziel-Netzüberschuss beim Laden** (0–5000 W, Standard 100 W): Die Batterien
  laden nur aus dem Überschuss oberhalb dieses Werts.
- **Ziel-Netzüberschuss beim Entladen** (−1000…+1000 W, Standard 50 W; positiv =
  Einspeisung, negativ = Bezug): der Netzwert, auf den die entladenden
  Batterien regeln. Zwischen den beiden Zielwerten sind die Batterien im
  Standby.
- **Maximale Einspeisung beim Entladen** (0 bis Summe der maximalen
  Entladeleistung aller Batterien, Standard 5000 W): Das Entladen verursacht
  nie mehr Einspeisung als diesen Wert. 0 W heißt, nie Batterieenergie
  einspeisen; das Maximum schaltet die Grenze ab. Liegt sie unter dem
  **Ziel-Netzüberschuss beim Entladen**, gilt sie (das Dashboard zeigt einen
  Hinweis).
- **Morgenreserve** (Standard 25 % des prognostizierten Verbrauchs von morgen):
  Energie, die morgens über dem minimalen Ladezustand bleiben soll, für
  Morgen, an denen die PV später übernimmt als prognostiziert; genutzt von der
  Nachtentladung und der [Batterie-Unterstützung](#batterie-unterstützung).
- **Nachtentladung** (Schalter, standardmäßig aus): Über Nacht
  entladen die Batterien gleichmäßig bis zur Reserve (nutzbare Energie über
  dem minimalen Ladezustand der Batterien), bis die PV-Erzeugung den Verbrauch
  wieder übersteigt; der Ziel-Netzüberschuss beim Entladen wird dabei
  ignoriert (die maximale Einspeisung gilt weiter). Reicht die PV-Prognose für
  morgen nicht, um die Batterien von der Reserve aus wieder zu füllen, bleibt
  eine höhere Reserve; beim Nachladen zählt je Stunde höchstens die
  Ladeleistung der Batterien, und der Überschuss, den Tagesziele von
  Verbrauchern an dem Tag voraussichtlich nehmen, muss daneben Platz haben.
  Mit preisbewusster Steuerung lässt sie die Energie in den Batterien, die
  der Preisplan noch für teure Stunden braucht (z. B. den nächsten Abend
  nach einem trüben Tag). Benötigt die Verbrauchsprognose.
- **Mittelungsfenster Überschuss** (0–300 s, Standard 5 s, 0 = aus): Gemittelt
  wird, was Haus und PV ohne die Batterien tun; es gilt der ungünstigere Wert
  aus Mittelwert und aktuellem Wert, damit die Regelung bei schwankender PV
  nicht überschießt. Ein Anstieg des Verbrauchs (oder ein Einbruch der PV)
  zählt erst, wenn er doppelt so lange anhält wie die Reaktionszeit der
  Batterien (höchstens 2 s): Kurze Lastspitzen, z. B. einer Kaffeemaschine,
  die ihre Heizung warm hält, sind vorbei, bevor eine Batterie ihnen folgen
  könnte.
- **Bezugsspitzen abfangen** (Schalter, standardmäßig aus) mit **Modus
  Spitzenabfang**, **Bezugsgrenze für Spitzenabfang**, **Ladezustand-Schwelle
  für Spitzenabfang**, **Sicherheitsreserve Spitzenabfang** und **Für
  Spitzenabfang aus dem Netz laden**: siehe
  [Bezugsspitzen abfangen und Leistungspreis](#bezugsspitzen-abfangen-und-leistungspreis).

### Probleme und Benachrichtigungen

Laufende Probleme erscheinen unter **Einstellungen → Reparaturen** und
verschwinden von selbst, sobald sie behoben sind:

- eine Batterie reagiert nicht (ausgeschlossen, siehe oben),
- eine Batterie kann seit mehr als 5 Minuten nicht gelesen werden,
- eine deaktivierte Batterie mit dem Zustand **Automatik** bei der Freigabe
  kann seit mehr als 5 Minuten nicht gelesen werden (sie zählt mit 0 W, der
  Hausverbrauch ist ungenau),
- eine Batterie konnte seit mehr als 5 Minuten nicht an ihre eigene Logik
  zurückgegeben werden (sie läuft womöglich noch mit der letzten Vorgabe;
  SLEMS versucht es laufend erneut, jede Freigabe wird durch Zurücklesen
  bestätigt),
- der Smart Meter meldet nicht, während SLEMS im Betriebsmodus **Aktiv** ist,
- der Smart Meter kann seit mehr als 5 Minuten nicht per Modbus gelesen werden
  (SLEMS nutzt bis dahin die Entität),
- die Verbraucher hinter dem Smart Meter melden seit mehr als 15 Minuten
  zusammen deutlich mehr Leistung als das ganze Haus (ein Verbraucher misst
  falsch oder hängt nicht hinter dem Zähler; Grundverbrauch und
  Verbrauchsprognose stimmen so lange nicht),
- der aktuelle Tarif hängt vom Börsenpreis ab, Home Assistant ist aber auf eine
  andere Währung als Euro eingestellt (siehe Börsenpreise),
- eine Batterie oder ein Verbraucher scheint an einer anderen Phase zu hängen,
  oder das Neu-Lernen der Phase hat wieder dieselbe ergeben (mit einem Dialog
  zum Beantworten, siehe **Phasen**),
- die Energiezähler des Smart Meters saldieren nicht über die Phasen: In
  mehreren Viertelstunden sind Bezugs- und Einspeisezähler beide um etwa
  denselben Betrag mehr gelaufen, als die Netzleistung zeigt (z. B. ein Shelly
  Pro 3EM, der je Phase zählt, während Zähler der Netzbetreiber in Deutschland
  und Österreich saldieren). Tarife und Rechnungen würden Energie zählen, die
  nie über den Zähler ging. Für einen Shelly Pro 3EM liefert
  [Shelly Phase Netting](https://github.com/gojux/shelly_phase_netting)
  saldierte Zähler; sonst die Netzleistung mit Template-Sensoren und Helfern
  **Integral** aufteilen oder die Zähler entfernen (SLEMS verwendet dann seine
  eigenen saldierten Werte). Ignoriere den Hinweis, wenn auch dein Zähler des
  Netzbetreibers je Phase zählt.

Das Dashboard zeigt sie ebenfalls: ein roter Hinweis auf der Batteriekarte
(**nicht lesbar**, **reagiert nicht**), im Energiefluss und für den Smart Meter
oben in der Übersicht. Das Ende eines aktiven Zellausgleichs (im normalen
Bereich, ohne weiteren Fortschritt, nach 24 Stunden oder mit einem Fehler)
erzeugt eine
Benachrichtigung mit dem Zell-Delta vorher und nachher und der Dauer; ein
selbst abgebrochener Ausgleich nicht. Für eine Fehlermeldung speichert
**Einstellungen → Geräte & Dienste → SLEMS → ⋮ → Diagnosedaten herunterladen**
(oder dasselbe bei einem Batterie-Gerät) die Konfiguration und den internen
Zustand als Datei: Messwerte, Pläne, Grenzen der Batterien, die letzten
Registerwerte, den Stand der Zell-Delta-Messung und eines Zellausgleichs. IP-
und MAC-Adressen werden entfernt. Die Warnungen der Einspeisebegrenzung
(Batterien zu klein, zu wenig Zeit für Platz, Ladeleistung zu gering,
Einspeisung über der Grenze, siehe **Einspeisebegrenzung**) sind ebenfalls
Benachrichtigungen; sie verschwinden von selbst, sobald das Problem behoben
ist.

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

## Roadmap

Mögliche Erweiterungen, nach Themen geordnet. Gesucht: Rückmeldungen von
Nutzern mit einer echten Wallbox zur [evcc-Anleitung](docs/wallbox-evcc.de.md).

**Elektroauto**

- Ziele, die ab dem Anstecken des Autos gelten („in 3 Stunden 80 %“).
- Batterien mit Verfügbarkeit (z. B. „Auto angesteckt“) und einer Reserve bis
  zu einer Uhrzeit: Grundlage für ein Auto, das das Haus versorgt (V2H).
- Gelernte Anwesenheit des Autos für die Planung.

**Planung und Preise**

- Eine gemeinsame Planung für alle Batterieziele: Laden, Entladen und
  Einspeisen je Viertelstunde nach Kosten über die nächsten 36–48 Stunden
  (Preise, Nachtentladung, Einspeisebegrenzung, netzdienliches Laden) statt
  getrennter Regeln mit jeweils eigenem Horizont. Dazu gehören:
  - die Tagesziele der Verbraucher gemeinsam mit den Batterien geplant,
  - ein stabilerer Plan der preisbewussten Steuerung (nur bei deutlicher
    Verbesserung geändert).
- Verbraucher bei negativen Preisen bevorzugen (z. B. den Heizstab).
- Geschätzte Preise nach dem Wetter: die Prognose von Wind, Sonne und Last des
  Marktgebiets (Energy-Charts) verschiebt die Schätzung; bei genau bekannten
  Werten wäre die Abweichung im letzten Jahr um etwa ein Drittel gesunken.

**Tarife**

- Preise aus einer Home-Assistant-Entität des Lieferanten (z. B. die
  Tibber-Integration, Octopus Deutschland, Ostrom): eine Posten-Art mit
  Adaptern für die verbreiteten Vorschau-Formate, mit der Angabe, ob der Preis
  Netzentgelte und Abgaben schon enthält.
- Index-Tarife (z. B. nach dem Österreichischen Strompreisindex ÖSPI): eine
  Posten-Art mit der Formel des Lieferanten, die Monatswerte des Index mit
  Zustimmung von der Energieagentur abgerufen; damit auch Vorlagen für
  Index-Tarife.
- Einspeisen aus den Batterien auch in der Schätzung des Tarifvergleichs.
- Tarifvergleich mit anderer Hardware: was eine größere Batterie, mehr
  Ladeleistung, ein anderer höchster Ladezustand oder mehr PV im
  aufgezeichneten Jahr gebracht hätten (das Batteriemodell der Schätzung mit
  den Einstellungen der Simulation).

**Prognose**

- Eigene Prognose für den Urlaub: ein Grundlastprofil, gelernt aus ganzen
  Urlaubstagen (alle Urlaube zusammen), und ein Faktor für die Wärmepumpe aus
  denselben Tagen; bis etwa drei Urlaubstage gelernt sind, die heutige
  Schätzung.
- Programmgeräte (Waschmaschine, Geschirrspüler, Trockner): den Verlauf eines
  Programms lernen und den Rest eines laufenden Programms in die Prognose der
  nächsten Stunden aufnehmen (hilft der Abend- und Nachtplanung); später
  eventuell bei Überschuss starten, wenn das Gerät einen Fernstart erlaubt.

**Verbraucher**

- SG Ready / Smart Grid der Wärmepumpe: bei PV-Überschuss oder günstigen
  Preisen die Solltemperatur anheben (Gebäude, Pufferspeicher, Warmwasser als
  Wärmespeicher), über die SG-Ready-Zustände oder eine Offset-Entität;
  Vorheizen und Vorkühlen als Freigabe mit Grenzen (höchste Temperatur,
  Offset, Dauer), nicht als Tagesziel. Die Prognose weiß, dass die vorgezogene
  Wärme später fehlt.
- Temperatur-Tagesziele mit Sicherheitsnetz: fällt die Temperatur außerhalb
  des Zeitfensters unter das Minimum (z. B. Warmwasser nach dem Duschen am
  Abend), wird das Minimum innerhalb einer eingestellten Zeit wieder erreicht
  statt erst zur Frist am nächsten Tag.

**Dashboard**

- Ein übersichtlicherer Reiter **Einstellungen**: nach ihrer Rolle in der
  Regelung gruppiert (Netzanschluss, Laden aus PV, Reserven, Entladen, Preise,
  Batterien), die Experten-Einstellungen eingeklappt, Unterpunkte
  ausgeblendet, solange ihre Funktion aus ist, und nur die Einstellungen, die
  in der eigenen Anlage etwas bewirken (z. B. keine Batterie-Einstellungen
  ohne Batterien). Dazu weniger Einstellungen, wo SLEMS den Wert selbst
  lernen kann.

**Batterien und Anlagen**

- Batteriegruppen mit gemeinsamer Absicherung: mehrere Batterien an einem
  Stromkreis (z. B. zwei Venus hinter einem Leitungsschutzschalter) bekommen
  eine gemeinsame Höchstleistung, die sich die aktiven Batterien der Gruppe
  teilen, statt fest der Hälfte für jede.
- Batterien mit eigener PV: die ersten echten Geräte (Marstek B2500, Zendure
  SolarFlow) und Hybrid-Wechselrichter über ihre Integrationen oder über
  Modbus-Profile (Kandidaten zum Testen vorhanden), ein
  gemeinsamer höchster Ausgang für mehrere Einheiten an einem Wechselrichter
  und die Prüfung auf doppelt gezählte PV auch in den Systemeinstellungen.
- Weitere Anlagenarten: Batterien mit eigenem Zähler oder einem
  Notstromausgang mit Lasten dahinter, und Batterien an mehreren
  Wechselrichtern.
- Laufende Prüfung der Batterien im Betrieb: saubere Änderungen der Vorgabe
  (eine Batterie, kein schaltender Verbraucher, ruhiger Smart Meter) mit dem
  Smart Meter und der gemeldeten Leistung verglichen; nach vielen
  übereinstimmenden Abweichungen (z. B. ein durch ein Update der Integration
  gedrehtes Vorzeichen) rät eine Reparaturmeldung zum Selbsttest. Zuerst nur in
  den Diagnosedaten gezählt, um die Grenzen mit echten Daten festzulegen.
- Weitere Batteriemodelle: mitgelieferte Modbus-Profile aus eigenen Profilen
  und [Kandidaten](docs/battery-profiles/README.de.md), die bei Nutzern dieser
  Batterien ihren Probebetrieb bestanden haben; ein eigener Treiber nur für
  Modelle, die nicht in ein Profil passen.

## Entwicklung

Siehe [developers.md](developers.md).

## Lizenz

GPL-3.0, siehe [LICENSE](LICENSE). Register-Map und Ansteuerung der Marstek
Venus basieren auf [Omnibattery](https://github.com/ffunes/Omnibattery)
(GPL-3.0).
