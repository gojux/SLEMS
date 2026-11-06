# Verbrauchsprognose

[Zurück zur README](../../README.de.md#verbrauchsprognose) · [English](consumption-forecast.md)

## So entsteht die Prognose

SLEMS lernt den Verbrauch aus den Langzeitstatistiken von Home Assistant und
prognostiziert heute und morgen stundenweise. Jüngere Tage zählen mehr als
ältere, Änderungen werden so innerhalb weniger Tage übernommen. Wärmepumpen
werden über die Außentemperatur des Tages prognostiziert, ein warmer Tag in
der Heizsaison ergibt also sofort weniger Heizenergie. Der Anteil ohne
Temperaturabhängigkeit (vor allem Warmwasser) kommt nur aus den letzten zwei
Wochen, die Abhängigkeit von der Temperatur aus dem ganzen Jahr: Heizt im
Sommer eine Heizpatrone das Warmwasser und im Winter die Wärmepumpe, folgt die
Prognose dem Wechsel in wenigen Tagen. Nach einem plötzlichen
Anstieg (z. B. wenn die Wärmepumpe die Heizsaison beginnt) hebt SLEMS die
Prognose sofort an: Lagen die letzten drei Stunden im Mittel mindestens 200 W
und 30 % über der Prognose, kommt dieser Mehrverbrauch auf die nächsten 24
Stunden, damit Nachtentladung und netzdienliches Laden damit planen, bis die
gelernte Prognose aufgeholt hat (Karte **Prognosegüte**: **Angehoben**). Abgesenkt
wird so nie. Zwei optionale Einstellungen helfen beim Start:

- **Außentemperatur**: ein Temperatursensor mit Historie. Ohne ihn zeichnet
  SLEMS die Temperatur der Wetter-Entität selbst auf; das Wärmepumpenmodell
  nutzt die Temperatur dann nach etwa einer Woche.
- **Historie Hausverbrauch**: ein Leistungssensor des Hausverbrauchs mit
  vorhandener Historie (z. B. aus einer anderen Batterie-Integration), für die
  Zeit, bevor SLEMS eigene Werte aufgezeichnet hat.

## Feiertage

Feiertage zählen wie Wochenendtage, im gelernten Profil und in der Prognose:
die des in Home Assistant eingestellten Landes und des Bundeslands oder
Kantons, das in den Systemeinstellungen bei **Feiertage** ausgewählt ist.
Werden die Systemeinstellungen geöffnet und ist noch keine Region gespeichert,
schlägt SLEMS eine aus dem Standort von Home Assistant vor: eine einzige
Anfrage an Nominatim (OpenStreetMap) mit auf etwa 100 m gerundeten
Koordinaten, den Dienst, den auch das Onboarding von Home Assistant für das
Land verwendet. Mit **Landesweite Feiertage**, oder wenn keine Region gefunden
wird, zählen nur die Feiertage des ganzen Landes (in der Schweiz die, die in
mindestens der Hälfte der Kantone gelten).

## Umstieg von einer anderen Batterie-Integration

Empfohlene Einrichtung:

1. Den Hausverbrauchssensor dieser Integration als **Historie Hausverbrauch**
   wählen. SLEMS lernt dann sofort aus der gesamten Historie. Ohne ihn leitet
   SLEMS die Historie nur aus Netz und PV ab; die Batterieleistung vor SLEMS
   ist unbekannt, Laden und Entladen würden den gelernten Verbrauch
   verfälschen.
2. Falls vorhanden, einen Außentemperatursensor mit Historie wählen. Sonst
   nutzt die Wärmepumpenprognose den Durchschnitt der letzten Tage, bis SLEMS
   etwa eine Woche Temperaturen aufgezeichnet hat.

## Prognosegüte

**Prognosegüte** (Karte in der Übersicht, Sensoren **Treffsicherheit
Verbrauchsprognose** und **Treffsicherheit PV-Prognose**):

- Verbrauch: SLEMS rechnet die Prognose jedes der letzten 14 Tage so nach, wie
  sie um Mitternacht mit der Historie bis dahin entstanden wäre, und
  vergleicht sie mit dem gemessenen Verbrauch. Angezeigt werden die
  Treffsicherheit der Tagesenergie (100 % minus mittlere Abweichung), die
  Tendenz (zu hoch oder zu niedrig), die Abweichung pro Stunde (wie gut der
  Tagesverlauf getroffen wird), die Datenbasis (Tage mit Verbrauch, Tage mit
  Wärmepumpe und Temperatur) und die Prognose für morgen mit ihrer erwarteten
  Abweichung. Die Wärmepumpe wird mit der gemessenen Temperatur des Tages
  nachgerechnet; ihr Anteil wirkt dadurch etwas besser, als er war.
- PV: Vergangene Prognosen liefert die Solarprognose-Integration nicht mehr,
  daher speichert SLEMS die Prognose jedes Tages zu Tagesbeginn und vergleicht
  sie abends mit der Erzeugung. Die Karte zeigt Treffsicherheit und erwartete
  Abweichung ab 7 verglichenen Tagen (vorher: **sammelt noch Daten**), weil
  wenige Tage wenig aussagen.

## Hausverbrauch

Der Sensor **Hausverbrauch** (Grundlage der Prognose und Anzeige im
Energiefluss) wird als Netz + PV − Batterien berechnet. Der Smart Meter meldet
eine Änderung oft später als PV und Batterien; ein kurzzeitig negatives
Ergebnis wird daher durch den letzten gültigen Wert ersetzt (höchstens 30
Sekunden, danach unbekannt).
Kann eine aktivierte, steuerbare Batterie nicht gelesen werden, zählt sie bis
zu 5 Minuten mit ihrer zuletzt gemessenen Leistung, weil sie ihre letzte
Vorgabe ohne neue Befehle weiterführt. Danach ist der Hausverbrauch unbekannt,
bis die Batterie wieder lesbar ist, statt ihre Leistung dem Haus zuzurechnen.
Eine deaktivierte Batterie macht den Hausverbrauch nie unbekannt: Solange sie
noch nicht an ihre eigene Logik zurückgegeben ist, zählt sie weiter mit ihrer
zuletzt gemessenen Leistung, nach der Freigabe mit 0 W. Mit dem Zustand
**Automatik** bei der Freigabe lädt oder entlädt sie womöglich selbst: Sie
zählt bis zu 5 Minuten mit ihrer zuletzt gemessenen Leistung, danach mit 0 W,
und eine Reparatur weist darauf hin, dass der Hausverbrauch ungenau ist.
Stunden, in denen der Statistik einer Batterie ein Wert fehlt, lässt die
Verbrauchsprognose aus.
