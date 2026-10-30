# Kandidaten für Batterieprofile

[English](README.md)

Profile für Batterien, die noch niemand mit SLEMS getestet hat. Sie sind aus
der Registerliste des Herstellers und anderen Quellen erstellt, aber einige
Punkte sind offen; deshalb bietet SLEMS sie im Dialog nicht an. Hast du eine
dieser Batterien, ist ein Kandidat ein leichterer Einstieg, als ein Profil mit
dem KI-Prompt zu erstellen.

| Datei | Batterie | Offene Punkte |
|---|---|---|
| [marstek_venus_a.yaml](marstek_venus_a.yaml) | Marstek Venus A | Vorgabe bei eigener PV, 32 Register je Lesen, RS485-Steuerung endet von selbst |
| [marstek_venus_d.yaml](marstek_venus_d.yaml) | Marstek Venus D | Vorgabe bei eigener PV, Höchstleistungen lesen nach einem Neustart 0 |
| [sungrow_sh10rt.yaml](sungrow_sh10rt.yaml) | Sungrow SH10RT mit SBR128 | Vorgabe im External-EMS-Modus, Heartbeat, Vorzeichen der Batterieleistung, AC-Leistung |
| [solaredge_storedge.yaml](solaredge_storedge.yaml) | SolarEdge StorEdge mit 48-V-Batterie (SE10K-RWB48) | Rückfall der Fernsteuerung, Vorzeichen der Batterieleistung |

**Einen Kandidaten nutzen**

1. Lies die Kommentare oben: die vorher nötigen Einstellungen, die Quellen
   und die Zeilen mit `# uncertain` und `# question`. Für ein anderes Modell
   derselben Reihe passe die Grenzen an.
2. **Batterie hinzufügen** → Modell **Batterie aus einem Modbus-Profil
   (experimentell)** → **Eigenes Profil (YAML) einfügen** und die ganze Datei
   einfügen. SLEMS behandelt sie wie ein eigenes Profil: gelesene Werte,
   Schreibvorschau und Probebetrieb, bevor es die Batterie regelt.
3. Prüfe die oben aufgeführten Punkte, während die Batterie läuft.

**Melden**: Ob es geklappt hat oder nicht, eröffne bitte ein
[Issue](https://github.com/gojux/SLEMS/issues) mit dem Modell, der Firmware,
deinen Befunden zu den Punkten oben und, nach einem bestandenen Probebetrieb,
dem Abschnitt `trial` deiner Batterie aus den Diagnosedaten. Ein Kandidat,
der seinen Probebetrieb bestanden hat, wird ein mit SLEMS geliefertes Profil.
