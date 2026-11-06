# Kandidaten für Batterieprofile

[English](README.md)

Profile für Batterien, die noch niemand mit SLEMS getestet hat. Sie sind aus
der Registerliste des Herstellers und anderen Quellen erstellt, aber einige
Punkte sind offen; deshalb bietet SLEMS sie im Dialog nicht an. Hast du eine
dieser Batterien, ist ein Kandidat ein leichterer Einstieg, als ein Profil mit
dem KI-Prompt zu erstellen.

| Datei | Batterie | Offene Punkte |
|---|---|---|
| [foxess_h3_smart.yaml](foxess_h3_smart.yaml) | Fox ESS H3 Smart / H3 Pro (Register ab 46001) | Ziel Batterie an einer H3 nicht bestätigt, Rückfall nach dem Timeout der Fernsteuerung, Rücklesen bis 5 s verzögert, wenige Modbus-TCP-Verbindungen |
| [goodwe_et.yaml](goodwe_et.yaml) | GoodWe ET (GW10K-ET) und Serien mit dem EMS-Modus 47511 | Charge-BAT/Discharge-BAT mit wechselnder Leistung, PV bei 0 W, kein Rückfall mit dem Standard von 47505, Vorzeichen |
| [kostal_plenticore.yaml](kostal_plenticore.yaml) | KOSTAL PLENTICORE plus G2/G3 mit Batterie | Rückgabe erst nach dem Timeout des Wechselrichters, Vorzeichen der Batterie- und AC-Leistung, Vorgabe bei manchen G2 ohne Wirkung |
| [marstek_venus_a.yaml](marstek_venus_a.yaml) | Marstek Venus A | Vorgabe bei eigener PV, 32 Register je Lesen, RS485-Steuerung endet von selbst |
| [marstek_venus_d.yaml](marstek_venus_d.yaml) | Marstek Venus D | Vorgabe bei eigener PV, Höchstleistungen lesen nach einem Neustart 0 |
| [sigenergy_sigenstor.yaml](sigenergy_sigenstor.yaml) | Sigenergy-Anlage mit Batterie (SigenStor, Sigen Hybrid) | kein Rückfall beschrieben, falls SLEMS ausfällt, Grenzen evtl. im Flash gespeichert, Grenze beim Entladen, Vorzeichen der AC-Leistung, 1 s zwischen Anfragen |
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
[Issue](https://github.com/gojux/SLEMS/issues/new?template=battery_profile.yml) mit dem Modell, der Firmware,
deinen Befunden zu den Punkten oben und, nach einem bestandenen Probebetrieb,
dem Abschnitt `trial` deiner Batterie aus den Diagnosedaten. Ein Kandidat,
der seinen Probebetrieb bestanden hat, wird ein mit SLEMS geliefertes Profil.
