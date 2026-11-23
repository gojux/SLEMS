# Alle Funktionen

[Zurück zur README](../../README.de.md#funktionen-im-überblick) · [English](features.md)

Die vollständige Liste dessen, was SLEMS kann. Ohne Zusatz ist eine Funktion
im Produktivbetrieb erprobt; alles andere ist als *experimentell* oder
*ungetestet* gekennzeichnet.

<!-- toc -->

- [Einrichtung](#einrichtung)
- [Prognosen](#prognosen)
- [Batterien](#batterien)
- [Verbraucher](#verbraucher)
- [Planung und Regelung](#planung-und-regelung)
- [Was SLEMS selbst lernt](#was-slems-selbst-lernt)
- [Dashboard](#dashboard)
- [Tarife und Preise](#tarife-und-preise)
- [Ohne Batterien](#ohne-batterien)
- [Lokal und transparent](#lokal-und-transparent)

<!-- /toc -->

## Einrichtung

- Beliebig viele Batterien, jederzeit hinzufügen, bearbeiten, entfernen.
- Smart Meter, PV-Leistung und Wetter frei wählbar.
- Einrichtung aus den Geräten von Smart Meter und PV-Anlagen und aus dem
  Energie-Dashboard; mehrere PV-Anlagen addiert.
- Smart Meter zusätzlich direkt per Modbus lesen (SunSpec), mit dem Sensor als
  Rückfallebene.
- Betriebsmodus **Aus / Nur beobachten / Aktiv**.
- Urlaubsschalter und Schlechtwetter-Modus.
- Reparaturhinweise, Benachrichtigungen und Diagnosedaten.

Anleitung: [Installation und Einrichtung](setup.de.md).

## Prognosen

- PV-Prognose aus beliebiger Solarprognose-Integration (Forecast.Solar,
  Solcast, …).
- Verbrauchsprognose heute/morgen (Historie + Wetter, Wärmepumpe
  temperaturabhängig, Feiertage des eigenen Bundeslands oder Kantons), nach
  einem plötzlichen Anstieg sofort angehoben.
- Korrektur der PV-Prognose im Tagesverlauf und Prognosegüte von PV und
  Verbrauch.
- Prognose des Ladezustands für heute und morgen.

Anleitung: [Verbrauchsprognose](consumption-forecast.de.md).

## Batterien

- Marstek Venus E 3.0 über Modbus TCP.
- Batterie aus vorhandenen Entitäten: nur lesend oder gesteuert über Sollwert,
  Lade-/Entladeleistung oder Skript, Entitäten aus dem Gerät vorgeschlagen
  (*Steuerung experimentell*).
- Batterie aus einem Modbus-Profil: ein eigenes Profil mit einem KI-Prompt aus
  dem Handbuch erstellt, gelesene Werte und Schreibvorschau, bevor etwas
  geschrieben wird, Probebetrieb mit dem Selbsttest, bevor sie geregelt wird,
  beobachtet nach einem Firmware-Update; mitgeliefert (ungeprüft): Marstek
  Venus E Gen 2 (*experimentell*).
- Wirkungsgrad der Batterie (Batteriezähler, gelernt oder manuell), gelernte
  nutzbare Kapazität.
- Verschleißkosten je Batterie (aus Kaufpreis und Zyklen) in der Planung.
- Batteriemenü: Kommunikation für Firmware-Updates pausieren, Batterie
  deaktivieren, Gerät öffnen.
- Aufteilung auf Batterien nach Wirkungsgrad: Es laufen nur so viele, wie für
  die aktuelle Leistung am effizientesten ist, die übrigen bleiben im Standby
  in Reserve; die schnellste Batterie übernimmt rasche Änderungen zuerst,
  Wechsel mit sanftem Übergang.
- Grenzen je Batterie: minimaler/maximaler Ladezustand, Grenze
  Lade-/Entladeleistung (z. B. 800 W), Ladebegrenzung nach Temperatur,
  vorzeitiger Entladestopp des BMS nahe dem Minimum erkannt.
- Erkennung von Batterien, die die vorgegebene Leistung nicht liefern,
  Bestätigung der Vorgaben.
- Zell-Delta und aktiver Zellausgleich (Marstek Venus E 3.0).
- Selbsttest einer Batterie: Wirkung am Smart Meter, Vorzeichen und Einheit
  der Leistung, Energiezähler, Ladezustand, Reaktionszeit, Freigabe am Smart
  Meter geprüft, sofortiger Abbruch bei falscher Richtung.
- Totzeit und Phase je Batterie mit einem kurzen Probeschritt gemessen (bei
  einer neuen Batterie, nach Änderungen an Batterie, Firmware, Smart Meter
  oder Integration, alle 90 Tage, von Hand).
- Regelmäßige Vollladung zur SoC-Kalibrierung der LFP-Zellen: SLEMS lädt die
  Batterie mit der ältesten letzten Vollladung zuerst, immer nur eine und nur
  mit dem Überschuss, der ohnehin gespeichert würde, und schont sie beim
  Entladen, damit sie am nächsten Tag höher startet. Es erkennt auch eine
  Vollladung, die das BMS knapp unter 100 % beendet, und misst dabei das
  Zell-Delta am oberen Ladeende.

Anleitungen: [Batterien](batteries.de.md),
[Batterie aus einem Modbus-Profil](battery-modbus-profile.de.md).

## Verbraucher

- Verbraucher mit eigenen Leistungs-/Energiesensoren, im oder außerhalb des
  Smart Meters.
- Verbraucher aus seinem Gerät einrichten: Geräte nach Eignung sortiert (z. B.
  Wärmepumpe, Heizstab, Wallbox), Entitäten, Steuerung und ihr Bereich
  vorgeschlagen.
- Phase von Verbrauchern aus den Phasen des Smart Meters gelernt (beginnend
  mit dem Verlauf), von Batterien im Selbsttest, Wechsel gemeldet, neu lernen;
  Energiezähler, die nicht über die Phasen saldieren, erkannt (*Regelung je
  Phase noch nicht*).
- Wärmepumpe als Verbrauchertyp (wetterabhängige Prognose), optional Freigabe
  ihrer Warmwasserladung (Betriebsmodus, gelernte Warmwasserleistung,
  [Anleitung](../heat-pump-hot-water.de.md)).
- Steuerung von Verbrauchern: Ein/Aus, Leistungsvorgabe oder Freigabe für eine
  eigene Automation (mit spätestem Start), Priorität, Mindestlaufzeit/-pause,
  Takten vermeiden, externe Sperre, Steuerung je Verbraucher abschaltbar.
- Verbraucher mit eigenem Thermostat: Pausen erkannt, ihre Leistung geht
  solange an die Batterien.
- Temperaturfühler eines Speichers: gelernte Energie pro Grad, noch
  aufnehmbare Energie, gelernte Abkühlung je Stunde.
- Einsatz eines Verbrauchers in der Einspeisebegrenzung (unterstützend,
  normal, nie).
- Tagesziele: Laufzeit, Freigabezeit, Energie, Temperatur oder Ladestand des
  Autos, mit frühestem Start, Frist und Quelle (Überschuss, Batterie, Netz);
  Temperaturziele mit Sicherheitsnetz (Minimum spätestens wieder nach einer
  eingestellten Zeit).
- Batterie-Unterstützung je Verbraucher (immer, automatisch mit der Energie,
  die die Batterien übrig haben, nie).
- Wallbox / evcc-Ladepunkt: Stromvorgabe in A mit Phasen, Start/Stopp
  ([Anleitung](../wallbox-evcc.de.md)) (*noch nicht mit einer echten Wallbox
  getestet*).
- Auto an der Wallbox: Ladestand im Energiefluss, ohne angestecktes Auto keine
  Regelung und keine Planung, **Sofort laden** mit voller Leistung (*noch
  nicht mit einer echten Wallbox getestet*).

Anleitung: [Verbraucher](consumers.de.md).

## Planung und Regelung

- Verteilung des Überschusses auf Batterien und Verbraucher (Priorität,
  Aufteilung, Mindestlaufzeit/-pause); Batterievorrang bis zur gesicherten
  Ladung, automatisch aus dem Verbrauch der kommenden Nacht.
- Echtzeit-Regelung von Batterien und Verbrauchern (Betriebsmodus **Aktiv**):
  ereignisgesteuert bei jeder Meldung des Smart Meters; die gelernte
  Reaktionszeit der Batterien (auch eines Starts aus dem Standby und eines
  Richtungswechsels) wird ausgeglichen, die Regelverstärkung passt sich selbst
  an.
- Gemittelter Netzüberschuss (0–300 s).
- Ziel-Netzüberschuss beim Laden/Entladen, maximale Einspeisung beim Entladen.
- Netzdienliches Laden: PV-Einspeisespitzen abfangen. Die Einspeisegrenze wird
  laufend neu berechnet und sinkt von selbst, wenn der Tag schlechter wird als
  vorhergesagt.
- Einspeisebegrenzung: PV-Energie über einer Einspeisegrenze speichern,
  rechtzeitig Platz schaffen.
- Nachtentladung bis zu einer prognosebasierten Reserve.
- Bezugsspitzen abfangen: eine Bezugsgrenze und eine Ladezustand-Schwelle,
  unter der die Batterien ihre Energie für die Spitzen zurückhalten (mit
  Leistungspreis das günstigste Paar, eine davon aus der Verbrauchsstatistik
  berechnet oder beide eingestellt; manuell aktivierbar). Mit Leistungspreis
  im Tarif gilt die Grenze im Mittel über die Viertelstunde, erzwungene
  Verbraucher treten zurück, die Batterien laden vor den Spitzen unter der
  Grenze aus dem Netz nach, die Übersicht zeigt die Monatsspitze und ihre
  Mehrkosten, und der Tarifvergleich schätzt, was Spitzenabfang gespart hätte.

Anleitung: [Betrieb und Regelung](control.de.md).

## Was SLEMS selbst lernt

Neben der Verbrauchsprognose lernt SLEMS die Umwandlungsverluste und die
Reaktionszeit der Batterien, das Meldeintervall des Smart Meters, die
Regelverstärkung und die Verbrauchsspitzen; auf Wunsch auch die
Prognose-Puffer, die Ziel-Netzüberschüsse, die Regelzeiten, die nutzbare
Kapazität jeder Batterie sowie Leistung und Thermostatverhalten von
Verbrauchern. Jeder gelernte Wert lässt sich wieder auf einen festen
umstellen (siehe [Gelernte Werte](control.de.md#gelernte-werte)).

## Dashboard

- Dashboard (Seitenleiste): Energiefluss, Kennzahlen, Tagesdiagramm mit
  Prognose und Plan, Batterien, Verbraucher, Einstellungen.
- Simulation im Dashboard: Tagesplan mit anderen Einstellungen (netzdienliches
  Laden, Nachtentladung, Einspeisebegrenzung, Grenzen der Batterien, Prognosen
  ±%) im Vergleich zum aktuellen (heute oder morgen), mit Preisdiagramm; was
  andere Batterien, mehr PV oder ein anderer Verbrauch im aufgezeichneten Jahr
  geändert hätten.

Anleitung: [Dashboard](dashboard.de.md).

## Tarife und Preise

- Tarife wie auf der Rechnung (Zeitfenster, Umsatzsteuer je Gruppe,
  prozentuale Abgaben) mit Prüfung gegen eine Rechnung.
- Tarif-Vorlagen: Österreich (Netzentgelte aller Netzbereiche, Abgaben,
  Lieferanten inkl. Optionen wie Ökostrom-Upgrade), Deutschland (Abgaben,
  EEG-Einspeisevergütung, Netzentgelte von 19 Netzbetreibern inkl.
  § 14a-Module und Messstellenbetrieb), Schweiz (Abfrage der offenen Daten der
  ElCom); eine allgemeine Vorlage für dynamische Tarife (Börsenpreis plus
  Aufschlag aus dem Vertrag).
- Vorlagen-Updates: neue Preise, Nachfolgetarife und Korrekturen als
  Reparaturhinweis, Zurücksetzen auf die Vorlagenwerte.
- Tarife als YAML (Import, Export, eigene Vorlagen) mit einem Prompt, um sie
  mit einer KI aus einer Rechnung zu erstellen.
- Börsenpreise je Viertelstunde (APG, SMARD, Energy-Charts) und offizielle
  Monatsmarktwerte (E-Control) nur mit Zustimmung; keine Einspeisevergütung
  bei negativen Preisen, wo der Tarif es vorsieht (EEG).
- Monatlicher Tarifvergleich: Bezugskosten und Einspeisevergütung je Tarif,
  Tarife ein- und ausblendbar, geschätzte Ersparnis der preisbewussten
  Steuerung.
- Preisbewusste Steuerung: gespeicherte Energie für die teuren Stunden halten,
  Netzanteil der Tagesziele im günstigsten Fenster, optional Laden aus dem
  Netz und Einspeisen aus den Batterien.
- Geschätzte Börsenpreise, bis die Preise des Folgetags erscheinen (Profil der
  letzten Tage, Wochenende und Feiertage).

Anleitung: [Tarife und Preise](tariffs.de.md).

## Ohne Batterien

SLEMS funktioniert auch ohne Batterie: einfach nur die Verbraucher anlegen.

- **Funktioniert**: den PV-Überschuss in gesteuerte Verbraucher lenken
  (Heizstab, Wallbox auch mit evcc, Luftentfeuchter, Wärmepumpe über einen
  Schalteingang) mit Prioritäten, Mindestlaufzeit und -pause, **Takten
  vermeiden** und externer Sperre; Tagesziele mit den Quellen **Nur Überschuss**
  und **Überschuss + Batterie + Netz** (mit preisbewusster Steuerung auch im günstigsten Fenster);
  Tarife, Rechnungsprüfung, Börsenpreise, Tarifvergleich und Preisdiagramm;
  Energiefluss, Verbrauchs- und PV-Prognose mit ihrer Güte.
- **Nicht ohne Batterien**: alles, was eine Batterie lädt, entlädt oder plant
  – netzdienliches Laden, Nachtentladung, Bezugsspitzen abfangen, Halten für
  teure Stunden, Laden aus dem Netz, Einspeisen aus den Batterien – und
  derzeit die Einspeisebegrenzung (sie braucht eine Batterie, auch mit
  unterstützenden Verbrauchern).

## Lokal und transparent

Regelung, Prognosen und Lernen laufen lokal in Home Assistant, ohne
Cloud-Konto. Ins Internet geht SLEMS nur für Börsenpreise und
Monatsmarktwerte (nur mit deiner Zustimmung), für Schweizer Tarife aus den
offenen Daten der ElCom und einmal für den Vorschlag der Feiertagsregion
(Nominatim, siehe [Feiertage](consumption-forecast.de.md#feiertage)). Im
Betriebsmodus **Nur beobachten** siehst du, was SLEMS tun würde, bevor es
etwas steuert, auch neben einer bestehenden Batterie-Integration.
