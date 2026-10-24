# Turn a battery manual into a SLEMS profile with an AI

[Deutsch](battery-profile-prompt.de.md)

An AI (e.g. Claude or ChatGPT) can read the Modbus register list of a battery
or hybrid inverter and turn it into a battery profile for SLEMS. Paste the
result in SLEMS under **Settings → Devices & services → SLEMS → Add battery**
with the model **Battery from a Modbus profile (experimental)**.
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
4. Paste the YAML in SLEMS. It reads every register once and shows the
   values, then what it would write, before anything is written.

An AI can misread a table or count addresses wrongly. A value written to a
wrong register can change a setting of the battery; the checks of SLEMS catch
most of these errors, but not all. Compare the write registers with the
manual before you save the battery. A profile from sources found online is
less certain than one from the manual: open the sources named at the top and
check the write registers there.

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
comments in the language of this message; the keys stay as given.

If there is no register for the charge and discharge power (or one signed
power), or if the register of the power set point itself is stored
permanently (flash, EEPROM), output no profile: say in one sentence why the
battery cannot be controlled this way. Settings stored permanently (e.g. the
highest power) and uncertain sources are no reason for that: output the
profile with "# uncertain".

Format (every key that exists; no other keys are allowed):

slems_battery_profile: 1
id: <manufacturer and model in lower case letters, digits and _, e.g. acme_x5>
name: <manufacturer and model>
manufacturer: <manufacturer>                  # optional
revision: 1
notes: <firmware, interface>                  # optional
connection:                                   # optional, defaults as shown
  port: 502
  unit_id: 1
  message_spacing_s: 0                        # least time between two requests in s
  timeout_s: 3
limits:
  max_charge_w: <highest charge power in W, integer>
  max_discharge_w: <highest discharge power in W, integer>
  min_power_w: <smallest power the battery accepts in W>   # optional
timing:                                       # optional, see the rules
  start_delay_s: <s>                          # from a set point until the power shows
  start_extra_s: <s>                          # extra time out of standby
  reversal_extra_s: <s>                       # extra time for a change of direction
  min_command_interval_s: <s>                 # least time between two set points
own_pv:                                       # only for a battery with own PV modules
  max_output_w: <highest output into the house in W>
  ac_charge: true                             # can charge from the grid
  passes_pv_through: false                    # feeds its PV into the house when full
read:                                         # values SLEMS reads; soc_pct is required
  soc_pct:        {address: ..., type: ..., scale: ...}   # state of charge in %
  power_dc_w:     {..., positive: charge|discharge}       # battery power in W
  power_ac_w:     {..., positive: charge|discharge}       # AC power in W
  pv_w:           {...}                       # own PV only: PV power in W
  output_w:       {...}                       # own PV only: output into the house in W
  input_w:        {...}                       # own PV only: input from the grid in W
  connection_w:   {..., positive: input|output}  # own PV only: signed power at the AC connection
  charged_kwh:    {...}                       # lifetime charge counter in kWh
  discharged_kwh: {...}                       # lifetime discharge counter in kWh
  voltage_v:      {...}                       # battery voltage in V
  temperature_c:  {...}                       # temperature in °C
  max_cell_v:     {...}                       # highest cell voltage in V
  min_cell_v:     {...}                       # lowest cell voltage in V
  cycles:         {...}                       # full cycles
  state:                                      # battery state
    address: ...
    type: uint16
    values: {<raw value>: <sleep|standby|charge|discharge|backup|firmware_update|bypass>}
device_info:                                  # read only, free lower case names
  <name>: {address: ..., type: ...}
control:
  type: split                                 # split: charge and discharge registers; signed: one register
  positive: charge|discharge                  # signed only: direction of a positive value
  setpoint: battery                           # own PV only: battery or connection
  keepalive_s: <s>                            # write everything again after this time
  confirm: {delay_s: 0.2, tolerance_w: 100, tolerance_pct: 0}   # optional
  apply:                                      # written to charge and discharge, in this order
    - {address: ..., value: ..., at_rest: [min, max], confirm: true}
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
A power may be the sum of several registers: a list of registers (e.g. the
PV inputs of several MPPTs).

Keys of a step under control.apply:
  address, type (no char), word_order as above
  value                a fixed raw value, or a placeholder:
                         "{charge_w}" and "{discharge_w}" (split), "{power_w}" (signed): power in W
                         "{mode}" with modes: {idle: ..., charge: ..., discharge: ...}
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
# question: the manual does not say whether 40001-40004 are stored in flash.
slems_battery_profile: 1
id: example_battery
name: Example battery 5 kWh
manufacturer: Example
revision: 1
notes: Firmware 1.2, Modbus TCP over the LAN port.

connection:
  port: 502
  unit_id: 1
  message_spacing_s: 0.1    # page 4: at least 100 ms between two requests
  timeout_s: 3

limits:
  max_charge_w: 2500
  max_discharge_w: 2500

read:
  soc_pct:        {address: 100, type: uint16, input: true, scale: 0.1}          # 30101, 0.1 %
  power_ac_w:     {address: 110, type: int32, input: true, positive: discharge}  # 30111-30112, W, "positive: discharging" (page 9)
  voltage_v:      {address: 120, type: uint16, input: true, scale: 0.01}         # 30121, 0.01 V
  charged_kwh:    {address: 130, type: uint32, input: true, scale: 0.1}          # 30131-30132, 0.1 kWh
  discharged_kwh: {address: 132, type: uint32, input: true, scale: 0.1}          # 30133-30134, 0.1 kWh
  temperature_c:  {address: 140, type: int16, input: true, scale: 0.1}           # 30141, 0.1 °C
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
  keepalive_s: 30           # page 10: back to automatic after 60 s without a write
  apply:
    # 40001 "Remote control" (page 10): 0xA5A5 = on, 0x5A5A = off
    - {address: 0, value: 0xA5A5, at_rest_values: [0xA5A5, 0x5A5A], confirm: true}
    # 40004 "Discharge power" (page 10): 10 W units, 0-250
    - {address: 3, value: "{discharge_w}", scale: 10, at_rest: [0, 250], confirm: true}
    # 40003 "Charge power" (page 10): 10 W units, 0-250
    - {address: 2, value: "{charge_w}", scale: 10, at_rest: [0, 250], confirm: true}
    # 40002 "Force mode" (page 10): 0 = none, 1 = charge, 2 = discharge
    - {address: 1, value: "{mode}", modes: {idle: 0, charge: 1, discharge: 2}, at_rest: [0, 2], confirm: true}
  release:
    auto:
      - {address: 3, value: 0}
      - {address: 2, value: 0}
      - {address: 1, value: 0, confirm: true}
      - {address: 0, value: 0x5A5A, confirm: true}
    standby:
      - {address: 3, value: 0}
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
  register, add "# uncertain: sources differ".
- Addresses are the protocol address sent in the Modbus frame (0-65535).
  Manuals often number registers instead: with a prefix (40001, 400001 for
  holding, 30001 for input registers) subtract it; numbered from 1, subtract
  1; hexadecimal addresses may be written as 0x1A2B. Say at the top of the
  profile how the manual counts and what you subtracted. If the manual does
  not make this clear, add "# question: ..." at the top.
- Registers of function code 04 (input registers, often 3xxxx) get
  input: true. Registers SLEMS writes are always holding registers.
- For every power register state its unit and sign from the manual in a
  comment (e.g. "positive: discharging"). positive is required for
  power_dc_w, power_ac_w and connection_w; if the sign is not given, write
  "# uncertain: sign".
- scale is the factor from the raw value to %, W, kWh, V or °C (0.1 for
  0.1 %, 1000 for kW to W). In control it is the other way round: one raw
  unit is scale W (10 for 10 W units).
- Above every step under control.apply give in a comment: the register
  number and name in the manual, the page or table, and its meaning.
- Every step under control.apply needs at_rest (or at_rest_values) from the
  manual: the raw range the register holds while the battery rests. 0, the
  highest powers as raw values (max_charge_w / scale), fixed values, all
  values of modes and every value written by release must lie inside it.
  A register with key values (e.g. 0xA5A5 = on, 0x5A5A = off) gets
  at_rest_values with exactly these values, never a range.
- Leave out a write register without a clear entry in the manual; never
  guess one. Write only registers SLEMS needs to charge, discharge and
  release; no settings, limits, protection or reset registers.
- Registers the manual describes as a setting, parameter, EEPROM or flash
  get persistent: true. Never a register with a power placeholder. If the
  manual says nothing about it for a write register, add
  "# question: is <register> stored permanently?" at the top.
- Order of apply: first enable remote control, then the powers, then the
  mode, so a new direction starts with valid values.
- release: give auto and/or standby, whichever the manual supports. They
  write only registers from apply, with fixed numbers: powers 0, mode
  idle, remote control off for auto. Never leave out the release.
- confirm: true on every write register the manual says can be read back.
- min_power_w only if the manual names a least power; never estimate it.
- keepalive_s: if the manual names a time-out after which the battery falls
  back to its own logic, a value clearly below it (e.g. half); else leave it
  out.
- message_spacing_s and timeout_s only if the manual names a least time
  between requests or a response time; if it says nothing, add
  "# question: least time between requests?" at the top.
- timing: leave it out (SLEMS learns the response times) unless the manual
  names a delay of the set points or a least time between them.
- device_info: the firmware versions and the serial number, if given:
  SLEMS notices a firmware update by them.
- probe: the register of the state of charge with its raw range (e.g.
  [0, 1000] for 0.1 %).
- state: only the states listed above; leave out raw values that fit none.
- own_pv only for a battery with own PV modules: then pv_w, and output_w
  (with input_w if it charges from the grid) or connection_w.
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
