# Turn a battery manual into a SLEMS profile with an AI

[Deutsch](battery-profile-prompt.de.md)

An AI (ChatGPT, Claude and Mistral proved themselves in trials) can read
the Modbus register list of a battery or hybrid inverter and turn it into a
battery profile for SLEMS. Paste the result in SLEMS under
**Settings → Devices & services → SLEMS → Add battery** with the model
**Battery from a Modbus profile (experimental)**.
The whole way from the manual to a controlled battery is described in the
README under
[Battery from a Modbus profile](../README.md#battery-from-a-modbus-profile-experimental).

How to do it:

1. Get the Modbus register list of your battery (manual, "Modbus protocol" or
   "register map" of the manufacturer, PDF or text) and note the model and
   its firmware version. Many manufacturers publish none: then attach
   nothing, and the AI searches online for several sources (integrations
   for Home Assistant or ioBroker, evcc, other open source projects) and
   compares them. For that the AI needs web search.
2. Copy the prompt below into a new chat, fill in the first lines and attach
   the register list, if you have one.
3. Check the answer: lines marked `# uncertain`, the `# question` lines at the
   top and above all every register under `control` against the manual or
   the sources at the top (address, unit, range).
4. Paste the YAML in SLEMS (the whole answer works too if it holds only one
   code block). SLEMS reads every register once and shows the values, then
   what it would write, before anything is written.
5. If SLEMS shows an error or a value read looks wrong (marked, or different
   from the battery's app), give the error message or the values read back
   to the AI in the same chat and paste its corrected profile.

A profile from an AI is a draft. An AI can misread a table or count
addresses wrongly. A value written to a wrong register can change a setting
of the battery; the checks of SLEMS catch most of these errors, but not all.
Compare the write registers with the manual before you save the battery. A
profile from sources found online is less certain than one from the manual:
open the sources named at the top and check the write registers there.

## Prompt

````text
Battery: <manufacturer and model>
Firmware: <firmware version, if known>

Read the attached Modbus register list and output a battery profile for
SLEMS in the format "slems_battery_profile", version 1. If no register list
is attached, search online for the registers of this model: the
manufacturer, integrations for Home Assistant or ioBroker, evcc, other open
source projects; use several independent sources and compare them. Output
only the YAML in a code block, without further explanations. Write the
comments in the language of this message; the keys stay as given. Put texts
(name, manufacturer, notes) in double quotes.

If there is no register for the charge and discharge power (or one signed
power, or one power without a sign whose direction a command sets), or if the register of the power set point itself is stored
permanently (flash, EEPROM), output no profile: say in one sentence why the
battery cannot be controlled this way. That the battery stores other
registers permanently (e.g. its highest power) is no reason for that; the
profile does not write such settings (see the rules). Uncertain sources are
no reason either: output the profile and mark every uncertain value with
"# uncertain: <reason>". If you output no profile, name the sources you
found.

Format (every key that exists; no other keys are allowed):

slems_battery_profile: 1
id: <manufacturer and model in lower case letters, digits and _, e.g. acme_x5>
name: "<manufacturer and model>"
manufacturer: "<manufacturer>"                # optional
revision: 1
notes: "<firmware, interface>"                # optional
connection:                                   # optional, defaults as shown
  port: 502
  unit_id: 1
  message_spacing_s: 0.0                      # least time between two requests in s
  timeout_s: 3.0
limits:
  max_charge_w: <highest charge power in W, integer>
  max_discharge_w: <highest discharge power in W, integer>
  min_power_w: <smallest power the battery accepts in W>   # optional
timing:                                       # optional, see the rules
  start_delay_s: <s, e.g. 2.5>                # from a set point until the power shows
  start_extra_s: <s, e.g. 2.5>                # extra time out of standby
  reversal_extra_s: <s, e.g. 2.5>             # extra time for a change of direction
  min_command_interval_s: <s, e.g. 2.5>       # least time between two set points
own_pv:                                       # only for a battery with own PV modules
  max_output_w: <highest output into the house in W>
  ac_charge: true                             # can charge from the grid
  passes_pv_through: false                    # feeds its PV into the house when full
read:                                         # values SLEMS reads, see the rules
  soc_pct:         {address: ..., type: ..., scale: ...}  # state of charge in %, required
  battery_power_w: {..., positive: charge|discharge}      # power at the battery (DC) in W
  ac_power_w:      {..., positive: input|output}          # power at the AC connection to the house and the grid in W (without a backup output)
  pv_power_w:      {...}                      # own PV only: power of the own PV modules in W
  charged_kwh:     {...}                      # lifetime charge counter in kWh
  discharged_kwh:  {...}                      # lifetime discharge counter in kWh
  voltage_v:       {...}                      # battery voltage in V
  temperature_c:   {...}                      # battery temperature in °C (cells or BMS; if there is none, of the device)
  max_cell_v:      {...}                      # highest cell voltage in V
  min_cell_v:      {...}                      # lowest cell voltage in V
  cycles:          {...}                      # full cycles
  state:                                      # battery state
    address: ...
    type: uint16                              # or int16, uint32, int32
    values: {<raw value>: <sleep|standby|charge|discharge|backup|firmware_update|bypass>}
device_info:                                  # read only, free lower case names
  <name>: {address: ..., type: ...}
control:
  type: split                                 # split, signed or magnitude, see the rules
  positive: charge|discharge                  # signed only: direction of a positive value
  setpoint: battery                           # own PV only: battery or connection
  keepalive_s: <s, e.g. 30.0>                 # write all steps again at the latest after this time, also without a change
  confirm: {delay_s: 0.2, tolerance_w: 100, tolerance_pct: 0.0}   # optional
  apply:                                      # written to charge and discharge, in this order
    - {address: ..., type: ..., value: ..., at_rest: [min, max], confirm: true}
  release:                                    # written when SLEMS hands the battery back
    auto:                                     # its own logic takes over again
      - {address: ..., value: ...}
    standby:                                  # it stays idle at 0 W
      - {address: ..., value: ...}
probe: {address: ..., type: ..., range: [min, max]}   # read to test the connection

Keys of a register (read, device_info, probe):
  address     protocol address 0-65535 (see the rules)
  type        uint16, int16, uint32, int32, float32 or char (text, with count)
  count       char only: number of registers
  scale       value = raw value × scale (e.g. 0.1 for 0.1 %); default 1; not for probe (its range is raw)
  word_order  big (default) or little: order of the two registers of 32 bit values
  input       true for input registers (function code 04); default holding (03)
  positive    powers with a sign only, see above
  fallback    soc_pct only: a second register if the first cannot be read
Instead of one register with positive, battery_power_w and ac_power_w may be
a pair of two registers without a sign, one per direction:
  battery_power_w: {charge: {address: ..., type: ...}, discharge: {address: ..., type: ...}}
  ac_power_w:      {input: {address: ..., type: ...}, output: {address: ..., type: ...}}
A power may be the sum of several registers: a list of registers (e.g. the
PV inputs of several MPPTs), also each side of a pair.

Keys of a step under control.apply:
  address, word_order as above
  type                 required: uint16 for one register, uint32, int32 or float32 for two (no char)
  value                a fixed raw value, or a placeholder:
                         "{charge_w}" and "{discharge_w}" (split), "{power_w}" (signed, magnitude): power in W
                         "{mode}" with modes: {idle: ..., charge: ..., discharge: ...} (required with magnitude)
  scale                power placeholders only: one raw unit is scale W (e.g. 10 for 10 W units, 1000 for kW)
  at_rest: [min, max]  raw range of the register while the battery rests
  at_rest_values: [..] instead of at_rest: the only raw values it holds at rest (e.g. key values)
  confirm: true        SLEMS reads the register back after writing
  persistent: true     the battery stores the register permanently (flash, EEPROM)
  max_changes_per_day  persistent only: changes allowed in 24 h (default 100)
Steps under release have only address, value (a fixed number) and confirm.

Example of a fictional battery (do not copy any address or value from it):

```yaml
# Source: "Example battery Modbus protocol V1.2", table 5, pages 8-11.
# Addresses: holding registers numbered from 40001 (address = number - 40001),
# input registers numbered from 30001 (address = number - 30001).
# question: page 5: "Modbus control" must be enabled once in the app; is it?
# question: the manual does not say whether 40001-40007 are stored in flash.
slems_battery_profile: 1
id: example_battery
name: "Example battery 5 kWh"
manufacturer: "Example"
revision: 1
notes: "Firmware 1.2, Modbus TCP over the LAN port."

connection:
  port: 502
  unit_id: 1
  message_spacing_s: 0.1    # page 4: at least 100 ms between two requests
  timeout_s: 3.0

limits:
  max_charge_w: 2500
  max_discharge_w: 2500

read:
  soc_pct:        {address: 100, type: uint16, input: true, scale: 0.1}          # 30101, 0.1 %
  ac_power_w:     {address: 110, type: int32, input: true, positive: output}     # 30111-30112, W, "positive: discharging" (page 9)
  voltage_v:      {address: 120, type: uint16, input: true, scale: 0.01}         # 30121, 0.01 V
  charged_kwh:    {address: 130, type: uint32, input: true, scale: 0.1}          # 30131-30132, 0.1 kWh
  discharged_kwh: {address: 132, type: uint32, input: true, scale: 0.1}          # 30133-30134, 0.1 kWh
  temperature_c:  {address: 140, type: int16, input: true, scale: 0.1}           # 30141, 0.1 °C, battery
  state:                                                                         # 30151 (page 9)
    address: 150
    type: uint16
    input: true
    values: {0: standby, 1: charge, 2: discharge, 3: firmware_update}

device_info:
  serial_number:    {address: 200, type: char, count: 8, input: true}            # 30201-30208
  firmware_version: {address: 210, type: uint16, input: true}                    # 30211

control:
  type: split
  keepalive_s: 60.0         # half of the time-out written to 40007
  apply:
    # 40001 "Remote control" (page 10): 0xA5A5 = on, 0x5A5A = off
    - {address: 0, type: uint16, value: 0xA5A5, at_rest_values: [0xA5A5, 0x5A5A], confirm: true}
    # 40007 "Remote time-out" (page 10): s, 0-3600; back to automatic after it without a write
    - {address: 6, type: uint16, value: 120, at_rest: [0, 3600], confirm: true}
    # 40005-40006 "Discharge power" (page 10): U32, high word first, 10 W units, 0-250
    - {address: 4, type: uint32, value: "{discharge_w}", scale: 10, at_rest: [0, 250], confirm: true}
    # 40003-40004 "Charge power" (page 10): U32, high word first, 10 W units, 0-250
    - {address: 2, type: uint32, value: "{charge_w}", scale: 10, at_rest: [0, 250], confirm: true}
    # 40002 "Force mode" (page 10): 0 = none, 1 = charge, 2 = discharge
    - {address: 1, type: uint16, value: "{mode}", modes: {idle: 0, charge: 1, discharge: 2}, at_rest: [0, 2], confirm: true}
  release:
    auto:
      - {address: 4, value: 0}
      - {address: 2, value: 0}
      - {address: 1, value: 0, confirm: true}
      - {address: 0, value: 0x5A5A, confirm: true}
    standby:
      - {address: 4, value: 0}
      - {address: 2, value: 0}
      - {address: 1, value: 0, confirm: true}

probe: {address: 100, type: uint16, input: true, range: [0, 1000]}
```

Rules:
- Take every register from the attached list or, without one, from the
  sources found; "the manual" in these rules means them. Do not invent
  registers, addresses, scales or ranges. If a value is uncertain, add the
  comment "# uncertain: <reason>" at the end of the line.
- Sources found online: name each at the top of the profile with its link
  (and file). Say in the comment of every register which sources name it.
  Take a write register only if at least two independent sources agree on
  its address, values and range (a copy or fork of another source is not
  independent); otherwise leave it out. If the sources differ on a read
  register, add "# uncertain: sources differ". A document of the
  manufacturer found online (e.g. a Modbus protocol or a technical note)
  counts as the manual.
- Addresses are the protocol address sent in the Modbus frame (0-65535;
  the first register has the address 0). Manuals often give a register
  number counted from 1 instead (Modicon convention: the first register is
  1, 40001 or 30001). Check which case applies:
  - Protocol address, subtract nothing: the manual says "protocol
    address", "PDU address", "offset", "0-based" or "zero-based", the
    table starts at 0 (or 40000, 30000), or the addresses are hexadecimal
    (e.g. 0xE004).
  - Register number from 1: the manual says "register number", "PLC
    address", "Modicon", "1-based" or "address = register - 1", or the
    table starts at 1. Subtract 1.
  - Register number with a prefix (40001 or 400001 for holding, 30001 or
    300001 for input registers, "4x"/"3x reference"): subtract the prefix
    (40001 becomes 0, 40101 becomes 100).
  "address" or "register address" alone proves neither: manuals use them
  for both. If the table has a number and an address column, take the
  address. Write addresses as the source does: a hexadecimal address stays
  hexadecimal. Say at the top of the profile how the manual counts, which
  words or table showed it and what you subtracted. If the signs are
  missing or contradict each other, add "# question: ..." at the top.
  Sources found online write either the
  number of the manual or the protocol address (e.g. address 13049 in an
  integration for register 13050 of the manual): say for each source at the
  top which one it writes, and subtract only from numbers of the manual.
- Registers of function code 04 (input registers, often 3xxxx) get
  input: true. Registers SLEMS writes are always holding registers. If the
  manual controls the battery through coils (function code 05 or 15), add
  "# question: control through coils - the format cannot write coils" at
  the top.
- Every step under control.apply needs type, also uint16: a value over two
  registers (32 bit) is uint32, int32 or float32, with word_order if the
  low word comes first. The format has no 64 bit types: leave such
  registers out (e.g. 64 bit counters).
- For every power register state its unit and sign from the manual in a
  comment (e.g. "positive: discharging"). battery_power_w needs positive:
  charge or discharge, ac_power_w positive: input (from the grid into the
  battery) or output (into the house); if the sign is not given, write
  "# uncertain: sign". A manual can give a power in three ways:
  - one register with a sign (e.g. int16, -1500 = charging, +1500 =
    discharging): give it as one register; positive names the direction in
    which the value is positive (here positive: discharge);
  - two registers without a sign, one per direction (e.g. "Charge power"
    and "Discharge power", both 0 or more): give both as a pair (see
    above);
  - one register without a sign whose direction only another register
    shows (e.g. a status bit "charging"): SLEMS cannot read this. Take
    another register of the same power with a sign or leave the key out.
- read needs battery_power_w or ac_power_w, or both. ac_power_w is the
  power at the AC connection to the house and the grid, never a backup or
  EPS output.
- control.type: split for one register for charging and one for
  discharging, signed for one register with a sign (with positive),
  magnitude for one register without a sign whose direction a command or
  mode register sets (then "{power_w}" and "{mode}"). Never give one
  register two placeholders.
- scale is the factor from the raw value to the value in %, W, kWh, V or
  °C: value = raw value × scale (0.1 for 0.1 %, 10 for 10 W units, 1000
  for kW). Under control the factor is the same: SLEMS writes raw value =
  power in W / scale (with scale 10, 2500 W become the raw value 250).
- Above every step under control.apply give in a comment: the register
  number and name in the manual, the page or table, and its meaning.
- Every step under control.apply needs at_rest (or at_rest_values) from the
  manual: the raw range the register holds while the battery rests. 0, the
  highest powers as raw values (max_charge_w / scale), fixed values, all
  values of modes and every value written by release must lie inside it.
  A register with key values (e.g. 0xA5A5 = on, 0x5A5A = off) gets
  at_rest_values with exactly these values, never a range; a time-out or
  a duration gets its range from the manual.
- Leave out a write register without a clear entry in the manual; never
  guess one. Write only registers SLEMS needs to charge, discharge and
  release; no settings, limits, protection or reset registers.
- Settings the manual requires once before remote control (e.g. enabling
  remote control, allowing charging from the grid, a default mode) are not
  written: name each at the top as "# question: ...". If release auto needs
  such a setting (e.g. a storage control mode switched to remote and back),
  write it in apply and release with persistent: true.
- Registers the manual describes as a setting, parameter, EEPROM or flash
  get persistent: true. Never a register with a power placeholder. If the
  manual says nothing about it for a write register, add
  "# question: is <register> stored permanently?" at the top.
- Order of apply: first enable remote control and set the time-out
  register (watchdog, heartbeat, remote control time-out; see
  keepalive_s), then the powers, then the mode. So a new direction starts
  with valid values, and the battery already falls back if SLEMS fails.
- release: give auto and/or standby, whichever the manual supports and
  holds without SLEMS writing again; at least one of them. If the battery
  falls back to a set mode after a time-out, leave that state out or add
  "# question: ...". A release writes only registers that are also in
  apply, and only fixed numbers:
  - power registers to 0. If the battery's own logic uses these registers
    as an upper limit, write the highest power instead, else it would
    stay limited to 0 W.
  - the mode register to the value for idle.
  - for auto, remote control off.
- confirm: true on every write register the manual says can be read back.
- min_power_w only if the manual names a least power; never estimate it.
- keepalive_s: if the manual names a time after which the battery falls
  back to its own logic without a new set point (time-out, watchdog,
  heartbeat), set keepalive_s clearly below it (e.g. half): SLEMS then
  writes all steps again at the latest after this time, even if nothing
  changes, and the battery does not fall back. If the manual names no such
  time, leave keepalive_s out; SLEMS then writes all steps again every
  60 s. If that time-out (or the duration of a forced charge) is a register,
  write it as a fixed value under apply, as short as possible: about two to
  four times keepalive_s. If only one mode of the battery has such a
  time-out (e.g. a heartbeat), use that mode if the sources show that the
  control registers work in it. If there is none, add at the top
  "# question: No time-out – if SLEMS fails, the last set point stays in
  force".
- message_spacing_s and timeout_s only if the manual names a least time
  between requests or a response time; if it says nothing, add
  "# question: least time between requests?" at the top.
- timing: leave it out (SLEMS learns the response times) unless the manual
  names a delay of the set points or a least time between them.
- device_info: the firmware versions and the serial number, if given:
  SLEMS notices a firmware update by them. For a hybrid inverter the
  firmware of both the inverter and the battery.
- probe: the register of the state of charge with its raw range (e.g.
  [0, 1000] for 0.1 %).
- state: only the states listed above; leave out raw values that fit none.
  A state over two registers is uint32 or int32.
- own_pv only for a battery with own PV modules: then pv_power_w and
  ac_power_w (the battery and the PV behind one inverter); a pair without
  input only with ac_charge: false.
- SLEMS reaches the battery only over Modbus TCP. If the manual describes
  only RS485 (Modbus RTU), add "# question: Modbus TCP adapter?" at the top.
````

## Suggest a profile as a shipped profile

A profile that works well can become a shipped profile for everybody with the
same battery. Open an [issue](https://github.com/gojux/SLEMS/issues) with:

- the profile (in SLEMS: **Edit battery** → **Show the profile as YAML**),
- manufacturer, model and firmware versions (in the diagnostics of SLEMS),
- the result of its trial (the self-test notification or the diagnostics),
- where the register list comes from (a link or the name and version of the
  manual, or the sources found online).

A profile becomes a shipped one once it passed its trial with at least one,
better two batteries, its write registers are checked against the manual or
several sources and it has a test in the repository.
