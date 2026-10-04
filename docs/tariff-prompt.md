# Turn an electricity bill into a SLEMS tariff with an AI

[Deutsch](tariff-prompt.de.md)

An AI (e.g. Claude or ChatGPT) can read an electricity bill or a price sheet
and turn it into a tariff in the SLEMS format. Paste the result in SLEMS under
**Settings → Devices & services → SLEMS → Add tariff → Paste YAML**.

How to do it:

1. First black out what the AI does not need: name, address, customer number,
   metering point number, bank details. The prices and items are enough.
2. Copy the prompt below into a new chat and attach the bill (PDF or photo).
3. Check the answer, especially lines marked `# uncertain`.
4. Paste the YAML in SLEMS and then compare the tariff with the same bill
   using **Check against a bill**: the difference per side should be a few
   percent at most.

An AI can misread or miscalculate. The comparison with the bill shows you
whether the tariff is right.

## Prompt

````text
Read the attached electricity bill (or price sheet) and output the tariff as
YAML in the format "slems-tariff", version 1. Output only the YAML in a code
block, without further explanations.

Format:

format: slems-tariff
version: 1
name: <product name of the tariff, e.g. "Power Fix 2026">
supplier: <energy supplier>              # optional
grid_operator: <grid operator>           # optional, if grid fees are included
grid_area: <grid area, network level>    # optional, e.g. "grid area X, level 7"
country: <country code, e.g. AT or DE>   # optional
currency: <currency, e.g. EUR or CHF>    # currency of the prices
year: <year of the prices>               # optional
parts: [energy, grid, levies]            # which parts of the bill are included
valid_from: <YYYY-MM-DD>                 # optional: from when the prices apply
valid_to: <YYYY-MM-DD>                   # optional: until when they apply
source: <e.g. "bill 03/2026" or "price sheet 01/2026">
vat_pct:                                 # VAT in % per side and group
  import: {energy: 20, grid: 20, levies: 20}
  export: {energy: 0, grid: 20, levies: 20}
items:
  - name: <label as on the bill>
    side: import            # import = consumption, export = feed-in
    group: energy           # energy = supplier, grid = grid operator, levies = taxes and levies
    unit: kwh               # kwh = ct/kWh, year = €/year, spot = market price (hourly), market_month = monthly market price, percent = % of the group
    price: 12.34

Optional fields per item:
  factor_pct: 5             # spot/market_month only: market price × (1 + 5 %)
  valid_from: 2026-04-01    # price applies from this day (one item per price level)
  months: [4, 5, 6, 7, 8, 9]          # only in these months
  weekdays: [mon, tue, wed, thu, fri] # only on these weekdays
  time_from: "10:00"        # time window, give both times
  time_to: "16:00"
  month_prices: {"2026-01": 8.5}      # published monthly market prices in ct/kWh
  zero_when_negative: true  # 0 while the day-ahead price is negative (e.g. EEG feed-in since 25 Feb 2025)

Rules:
- All prices without VAT (net). If the bill only shows gross prices, convert
  them to net with the VAT rate given.
- Energy prices in ct/kWh (unit kwh), standing charges and flat fees in
  €/year (unit year). Monthly amounts × 12, daily prices × 365. For other
  currencies the same in hundredths/kWh and whole units/year (e.g. Rappen
  and CHF).
- Feed-in credits are positive prices on the export side; deductions from
  them (e.g. a metering fee for the feed-in) are items of their own.
- Dynamic tariffs: unit spot or market_month, price is the markup in ct/kWh
  (negative for a discount), factor_pct a markup in % on the market price.
- The bill of a dynamic tariff (price per hour or quarter hour following the
  exchange) shows only a mean price of the period. Never take it as a fixed
  price: the energy price is unit spot with the markup of the contract
  (often in the contract summary or the price sheet, may be 0), the monthly
  fee an item with unit year (× 12). If the markup is nowhere given, set
  price 0 with "# uncertain: markup not on the bill". Example:
    - {name: Energy, side: import, group: energy, unit: spot, price: 0}
    - {name: Monthly fee, side: import, group: energy, unit: year, price: 60}
- Surcharges in percent (e.g. a municipal levy of 7 % on energy and grid):
  one item per group with unit percent and the rate as price; it applies to
  all other items of the same side and group.
- If prices changed within the billing period, give one item per price level
  with valid_from.
- Put each item in a group: what the supplier charges is energy; grid use,
  grid losses and metering fees of the grid operator are grid; taxes,
  renewable surcharges and levies are levies.
- Leave out quantities, partial amounts, totals, credits and instalments;
  prices only.
- Personal discounts and bonuses (e.g. a new customer bonus) as an item of
  their own with the comment "# personal" at the end of the line.
- Do not copy personal data (name, address, customer number, metering point
  number, bank details).
- Do not invent values. If a value is uncertain, add the comment
  "# uncertain: <reason>" at the end of the line.
````

## Suggest a tariff as a template

A tariff with publicly available list prices (e.g. from a supplier's price
sheet) can help others as a template. Export it in SLEMS with **Export as
YAML**, remove personal discounts and report it as an
[issue](https://github.com/gojux/SLEMS/issues) with the source of the prices.
