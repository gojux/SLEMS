# Stromrechnung mit einer KI in einen SLEMS-Tarif umwandeln

[English](tariff-prompt.md)

Eine KI (z. B. Claude oder ChatGPT) kann eine Stromrechnung oder ein Preisblatt
lesen und daraus einen Tarif im Format von SLEMS machen. Das Ergebnis fügst du
in SLEMS unter **Einstellungen → Geräte & Dienste → SLEMS → Tarif hinzufügen →
YAML einfügen** ein.

So gehst du vor:

1. Schwärze vorher, was die KI nicht braucht: Name, Adresse, Kundennummer,
   Zählpunktnummer, Bankverbindung. Die Preise und Posten reichen.
2. Kopiere den Prompt unten in einen neuen Chat und hänge die Rechnung (PDF
   oder Foto) an.
3. Prüfe die Antwort, vor allem Zeilen mit `# unsicher`.
4. Füge das YAML in SLEMS ein und vergleiche den Tarif danach mit *Mit einer
   Rechnung vergleichen* mit derselben Rechnung: Die Abweichung je Seite
   sollte nur wenige Prozent betragen.

Eine KI kann sich verlesen oder verrechnen. Der Vergleich mit der Rechnung
zeigt dir, ob der Tarif stimmt.

## Prompt

````text
Lies die angehängte Stromrechnung (oder das Preisblatt) und gib den Tarif als
YAML im Format "slems-tariff", Version 1, aus. Gib nur das YAML in einem
Codeblock aus, ohne weitere Erklärungen.

Format:

format: slems-tariff
version: 1
name: <Produktname des Tarifs, z. B. "Strom Fix 2026">
supplier: <Energielieferant>            # optional
grid_operator: <Netzbetreiber>          # optional, wenn Netzentgelte enthalten sind
grid_area: <Netzgebiet, Netzebene>      # optional, z. B. "Netzgebiet X, Netzebene 7"
country: <Ländercode, z. B. AT oder DE> # optional
currency: <Währung, z. B. EUR oder CHF> # Währung der Preise
year: <Jahr der Preise>                 # optional
parts: [energy, grid, levies]           # welche Teile der Rechnung enthalten sind
valid_from: <JJJJ-MM-TT>                # optional: ab wann die Preise gelten
valid_to: <JJJJ-MM-TT>                  # optional: bis wann sie gelten
source: <z. B. "Rechnung 03/2026" oder "Preisblatt 01/2026">
vat_pct:                                # Umsatzsteuer in % je Seite und Gruppe
  import: {energy: 20, grid: 20, levies: 20}
  export: {energy: 0, grid: 20, levies: 20}
items:
  - name: <Bezeichnung wie auf der Rechnung>
    side: import            # import = Bezug, export = Einspeisung
    group: energy           # energy = Energie (Lieferant), grid = Netz (Netzbetreiber), levies = Steuern und Abgaben
    unit: kwh               # kwh = ct/kWh, year = €/Jahr, spot = Börsenpreis (stündlich), market_month = Monatsmarktpreis, percent = % der Gruppe
    price: 12.34

Optionale Felder je Posten:
  factor_pct: 5             # nur spot/market_month: Börsenpreis × (1 + 5 %)
  valid_from: 2026-04-01    # Preis gilt ab diesem Tag (ein Posten je Preisstand)
  months: [4, 5, 6, 7, 8, 9]          # nur in diesen Monaten
  weekdays: [mon, tue, wed, thu, fri] # nur an diesen Wochentagen
  time_from: "10:00"        # Zeitfenster, beide Zeiten angeben
  time_to: "16:00"
  month_prices: {"2026-01": 8.5}      # veröffentlichte Monatsmarktpreise in ct/kWh
  zero_when_negative: true  # 0, solange der Day-Ahead-Preis negativ ist (z. B. EEG-Einspeisung seit 25.02.2025)

Regeln:
- Alle Preise ohne Umsatzsteuer (netto). Stehen auf der Rechnung nur
  Bruttopreise, rechne sie mit dem angegebenen Steuersatz auf netto um.
- Arbeitspreise in ct/kWh (unit kwh), Grund- und Pauschalpreise in €/Jahr
  (unit year). Monatliche Beträge × 12, Tagespreise × 365. Bei Franken:
  Rappen/kWh und CHF/Jahr (dieselben Felder).
- Einspeisevergütungen sind positive Preise auf der Seite export; Abzüge davon
  (z. B. ein Messentgelt für die Einspeisung) sind eigene Posten.
- Bei dynamischen Tarifen: unit spot oder market_month, price ist der
  Aufschlag in ct/kWh (negativ bei einem Abschlag), factor_pct ein
  prozentualer Aufschlag auf den Marktpreis.
- Prozentuale Aufschläge (z. B. eine Gebrauchsabgabe von 7 % auf Energie und
  Netz): je Gruppe ein Posten mit unit percent und dem Prozentsatz als price;
  er gilt für alle anderen Posten derselben Seite und Gruppe.
- Haben sich Preise im Abrechnungszeitraum geändert, gib für jeden Preisstand
  einen eigenen Posten mit valid_from an.
- Ordne jeden Posten einer Gruppe zu: Was der Lieferant verrechnet, ist
  energy; Netznutzung, Netzverlust und Messentgelte des Netzbetreibers sind
  grid; Steuern, Förderbeiträge und Abgaben sind levies.
- Lass Verbrauchsmengen, Teilbeträge, Summen, Guthaben und Teilzahlungen weg;
  nur Preise.
- Persönliche Rabatte und Boni (z. B. Neukundenbonus) als eigenen Posten mit
  dem Kommentar "# persönlich" am Zeilenende.
- Übernimm keine persönlichen Daten (Name, Adresse, Kundennummer,
  Zählpunktnummer, Bankverbindung).
- Erfinde keine Werte. Ist ein Wert unsicher, schreib ans Zeilenende einen
  Kommentar "# unsicher: <Grund>".
````

## Einen Tarif als Vorlage vorschlagen

Ein Tarif mit öffentlich verfügbaren Listenpreisen (z. B. aus einem
Preisblatt des Lieferanten) kann anderen als Vorlage helfen. Exportiere ihn in
SLEMS mit *Als YAML exportieren*, entferne persönliche Rabatte und melde ihn
als [Issue](https://github.com/gojux/SLEMS/issues) mit der Quelle der Preise.
