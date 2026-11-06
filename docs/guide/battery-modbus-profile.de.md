# Batterie aus einem Modbus-Profil (experimentell)

[Zurück zur README](../../README.de.md#batterien) · [Batterien](batteries.de.md) · [English](battery-modbus-profile.md)

Für eine Batterie, die SLEMS nicht direkt unterstützt, die sich aber über
Modbus steuern lässt: Ein **Profil** beschreibt ihre Register (was SLEMS
liest) und die Schritte zum Laden, Entladen und Freigeben. SLEMS schreibt nur
die Register, die das Profil dafür angibt; das Format kennt keine freien
Schreibbefehle. Batterien aus einem Profil sind experimentell: Bitte berichte
deine Erfahrungen in einem [Issue](https://github.com/gojux/SLEMS/issues).

**Voraussetzungen**: Modbus TCP (eine Batterie nur mit RS485 / Modbus RTU
braucht einen Modbus-TCP-Adapter); Register für Ladezustand, Leistung (mit
Vorzeichen oder je Richtung ein Register) sowie Lade- und Entladeleistung (oder eine Leistung mit Vorzeichen, oder eine
Leistung ohne Vorzeichen, deren Richtung ein Befehl setzt, wie bei Sungrow),
aus der
Registerliste des Herstellers oder, da viele Hersteller keine
veröffentlichen, aus mehreren Quellen im Netz; ein Smart Meter in SLEMS für
den Probebetrieb.

**Eine Batterie, ein Steuerpunkt**: Eine Batterie in SLEMS ist ein
Steuerpunkt mit einem Ladezustand, egal wie viele Wechselrichter oder Module
dahinter stecken. Gestapelte Module oder ein Wechselrichter mit mehreren
Batterieeingängen, von dem sich nur die Summe steuern lässt, sind eine
Batterie mit der Gesamtkapazität. Trag dieselbe Batteriebank nie zweimal ein
(z. B. je Wechselrichter oder je Stapel): Ihre Kapazität zählte doppelt, und
zwei Regelkreise arbeiteten gegeneinander.

1. **Profil erstellen** aus der Registerliste mit dem
   [KI-Prompt](../battery-profile-prompt.de.md), in einer KI mit
   Websuche; ChatGPT, Claude und Mistral haben sich in Probeläufen bewährt.
   Ohne Registerliste sucht die KI online mehrere Quellen, vergleicht sie
   und nennt sie oben im Profil. Ein Profil von einer KI ist ein Entwurf:
   Prüfe die Zeilen mit `# unsicher` und `# Frage`.
2. **Batterie hinzufügen** → Modell **Batterie aus einem Modbus-Profil
   (experimentell)** und das Profil einfügen (die ganze Antwort der KI geht
   auch, wenn sie nur einen Codeblock enthält). SLEMS prüft es und zeigt
   einen Fehler mit Zeile und Schlüssel, z. B. einen unbekannten Schlüssel
   oder einen Wert außerhalb des Ruhebereichs: Gib die Fehlermeldung im
   selben Chat an die KI zurück und füge ihr korrigiertes Profil ein.
   Liefert SLEMS ein Profil für dein Modell mit oder hat eine andere
   Batterie ein eigenes Profil, wähl es stattdessen aus.
3. **Verbindung**: Host, Port und Unit-ID (aus dem Profil vorbelegt) und die
   Verbindung, **Eigener Modbus-Client** oder **Home Assistant Modbus**
   (geteilt mit anderen Integrationen am selben Gerät). Viele Batterien nehmen
   nur eine Modbus-Verbindung an: Beende zuerst andere Integrationen an der
   Batterie.
4. **Gelesene Werte**: SLEMS liest jedes Register einmal und schreibt nichts.
   Vergleiche die Werte mit der App oder dem Display der Batterie: Ein
   falscher Wert bedeutet meist eine falsche Adresse oder einen falschen
   Faktor. ⚠ markiert unwahrscheinliche Werte (Temperatur, Zellspannungen,
   Leistungen über dem Doppelten der Grenze, negative Zähler oder
   Leistungen, unbekannte Zustände, eine Leistung gegen den gelesenen
   Zustand, nicht lesbare Werte). In Ruhe ist die Leistung 0 W: Lass die Batterie laden oder
   entladen (eigene Logik oder ihre App) und wähl **Erneut lesen**, dann
   zeigt sich auch ein falsches Vorzeichen der Leistung. Gib falsche Werte
   mit den gelesenen Werten an die KI zurück. **Weiter** gibt es nur, wenn
   der Ladezustand lesbar ist und zwischen 0 und 100 % liegt und jedes
   Schreibregister lesbar ist und in seinem Ruhebereich (`at_rest`) liegt;
   ein Register außerhalb enthält meist etwas anderes (eine verschobene
   Adresse). Sonst **Profil bearbeiten** oder **Verbindung ändern**.
5. **Grenzen** wie bei den anderen Batterien; **Zustand, wenn SLEMS die
   Batterie freigibt** bietet nur die Zustände des Profils an.
6. **Schreibvorschau**: was SLEMS im Probebetrieb schreibt, Register für
   Register mit dem Wert jetzt und dann: Laden, Entladen und die Freigabe.
   Vergleiche jedes Schreibregister mit dem Handbuch oder den Quellen im
   Profil (Adresse, Einheit, Bereich) und bestätige **Ich habe die Schreibregister mit dem Handbuch
   verglichen**. Die Prüfungen von SLEMS fangen die meisten Fehler ab, aber
   nicht alle: Ein Wert im falschen Register kann eine Einstellung der
   Batterie ändern. Ein mitgeliefertes Profil braucht keine Bestätigung,
   außer einem ungeprüften (siehe unten).
7. **Probebetrieb**: SLEMS speichert die Batterie, regelt sie aber noch
   nicht; sie läuft mit ihrer eigenen Logik. Im Betriebsmodus **Aktiv**
   startet ihr Selbsttest mit den Prüfungen des Profils von selbst und dauert
   etwa 8–9 Minuten (länger, wenn er zuerst Platz im Ladezustand schafft,
   siehe [Selbsttest](batteries.de.md#selbsttest-einer-batterie)), in denen die anderen
   Batterien und die Verbraucher gehalten werden: Die Schreibregister werden eine Minute in Ruhe gelesen,
   bevor etwas geschrieben wird (springen Ladezustand oder Probe-Register
   dabei, markiert SLEMS eine vermutlich verschobene Adresse), jede Richtung beginnt mit 100 W, danach
   folgt die Leistung des Selbsttests, wobei die Register bis zu 90 s ohne
   Schreiben gelesen werden, und nach der Freigabe werden sie noch einmal
   gelesen. **Bestanden**: SLEMS regelt die Batterie. **Teilweise unklar**:
   nach 10 Minuten erneut versucht; du kannst es auch in der Reparatur
   übernehmen. **Nicht bestanden**: Die Batterie bleibt im Probebetrieb, bis
   das Profil korrigiert ist oder ein von dir gestarteter Selbsttest besteht.
   Der Sensor **Probebetrieb**, eine Reparatur und die Batteriekarte zeigen
   den Stand.

**Firmware-Updates**: SLEMS vergleicht die Geräteinformationen des Profils
(z. B. die Firmware-Versionen) nach einem Start und nach einer Pause. Nach
einer Änderung wird eine bestandene Batterie einen Tag im normalen Betrieb
beobachtet (erste Vorgaben auf 100 W begrenzt, die Register mit dem
Geschriebenen verglichen); erst ein Befund beginnt den vollen Probebetrieb
erneut.

**Ändern**: **Batterie bearbeiten** → **Profil oder Verbindung ändern**
(erneut lesen, Vorschau und Probebetrieb), **Name und Grenzen** (nichts wird
gelesen, der Probebetrieb bleibt) oder **Profil als YAML anzeigen** (zum
Aufbewahren oder für eine andere Batterie). Ein geändertes eigenes Profil
wird den anderen Batterien mit einem Profil derselben id angeboten. Eine
andere Adresse beginnt den Probebetrieb erneut.

**Teilen**: Ein Profil, das seinen Probebetrieb bestanden hat, kann ein
mitgeliefertes Profil für alle mit derselben Batterie werden:
[so schlägst du es vor](../battery-profile-prompt.de.md#profil-als-mitgeliefertes-profil-vorschlagen).

**Kandidaten**: Für einige Batterien gibt es
[Kandidaten für Profile](../battery-profiles/README.de.md), die der Dialog
nicht anbietet, weil sie noch niemand getestet hat; füge einen als eigenes
Profil ein und prüfe die Punkte, die oben darin stehen.

**Ungeprüfte mitgelieferte Profile**: Ein mitgeliefertes Profil mit dem
Zusatz **(ungeprüft)** ist aus der Registerliste des Herstellers und anderen
Quellen erstellt, hat seinen Probebetrieb aber noch an keiner echten Batterie
bestanden (bisher die **Marstek Venus E Gen 2**, über RS485 mit einem
Modbus-TCP-Gateway, z. B. einem Elfin EW11). Es durchläuft dieselben
Prüfungen und denselben Probebetrieb wie ein eigenes Profil, und in seiner Schreibvorschau bestätigst du **Mir ist
bewusst, dass dieses Profil noch an keiner echten Batterie geprüft wurde; ich
habe die Schreibregister mit dem Handbuch verglichen, soweit es eines gibt**.
Besteht deine Batterie den Probebetrieb (der Sensor **Probebetrieb** zeigt
**Bestanden**; **Als bestanden übernommen** aus der Reparatur zählt nicht),
melde es bitte in einem [Issue](https://github.com/gojux/SLEMS/issues) mit
dem Modell und dem Abschnitt `trial` deiner Batterie aus den Diagnosedaten
(**Einstellungen → Geräte & Dienste → SLEMS → ⋮ → Diagnosedaten
herunterladen**). Dann wird das Profil als geprüft geführt, mit der Firmware
deiner Batterie. Stell nur diesen Abschnitt ein: Der Download enthält auch
deine ganze Konfiguration.
