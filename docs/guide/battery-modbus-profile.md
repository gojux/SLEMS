# Battery from a Modbus profile (experimental)

[Back to the README](../../README.md#batteries) · [Batteries](batteries.md) · [Deutsch](battery-modbus-profile.de.md)

For a battery that SLEMS does not support directly but that can be controlled
over Modbus: a **profile** describes its registers (what SLEMS reads) and the
steps to charge, discharge and release it. SLEMS writes only the registers the
profile declares for that; the format has no free write commands. Batteries
from a profile are experimental: please report your experience in an
[issue](https://github.com/gojux/SLEMS/issues).

**Requirements**: Modbus TCP (a battery with only RS485 / Modbus RTU needs a
Modbus TCP adapter); registers for the state of charge, the power (with a
sign, or one register per direction) and the charge and discharge power (or
one signed power, or one power without a sign whose direction a command
sets, as at Sungrow), from the manufacturer's register list or, as many manufacturers publish none, from several sources
found online; a smart meter in SLEMS for the trial.

**One battery, one control point**: a battery in SLEMS is one control point
with one state of charge, however many inverters or modules are behind it.
Stacked modules, or an inverter with several battery inputs of which only the
total can be controlled, are one battery with the total capacity. Never add
the same battery bank twice (e.g. once per inverter or per stack): its
capacity would count twice and two control loops would work against each
other.

1. **Make the profile** from the register list with the
   [AI prompt](../battery-profile-prompt.md), in an AI with web search;
   ChatGPT, Claude and Mistral proved themselves in trials. Without a
   register list the AI searches online for several sources, compares them
   and names them at the top of the profile. A profile from an AI is a
   draft: check the lines marked `# uncertain` and `# question`.
2. **Add battery** → model **Battery from a Modbus profile (experimental)**
   and paste the profile (the whole answer of the AI works too if it holds
   only one code block). SLEMS checks it and shows an error with the line and
   the key, e.g. an unknown key or a value outside the range at rest: give
   the error message back to the AI in the same chat and paste its corrected
   profile. If SLEMS ships a profile for your model or another battery has
   an own profile, select it instead.
3. **Connection**: host, port and unit ID (preset from the profile) and the
   connection, **Own Modbus client** or **Home Assistant Modbus** (shared with
   other integrations on the same device). Many batteries accept only one
   Modbus connection: stop other integrations on the battery first.
4. **Values read**: SLEMS reads every register once and writes nothing.
   Compare the values with the battery's app or display: a wrong value
   usually means a wrong address or scale. ⚠ marks unlikely values
   (temperature, cell voltages, powers beyond twice the limit, negative
   counters or powers, unknown states, a power against the state read,
   unreadable values). At rest the power is 0 W: let the battery charge or discharge
   (its own logic or its app) and **Read again**, so a wrong sign of the
   power shows too. Give wrong values back to the AI with the values read.
   **Continue** is offered only if the state of charge is readable and
   0–100 % and every write register is readable and inside its range at rest
   (`at_rest`); a register outside usually holds something else (a shifted
   address). Otherwise **Edit the profile** or **Change the connection**.
5. **Limits** as for the other batteries; **State when SLEMS releases the
   battery** offers only the states of the profile.
6. **Write preview**: what SLEMS writes in the trial, register by register
   with the value now and then: charging, discharging and the release.
   Compare every write register with the manual or the sources named in the
   profile (address, unit, range) and
   confirm **I compared the write registers with the manual**. The checks of
   SLEMS catch most errors, but not all: a value in a wrong register can
   change a setting of the battery. A shipped profile needs no confirmation,
   except an unverified one (see below).
7. **Trial**: SLEMS saves the battery but does not control it yet; it runs on
   its own logic. In the operating mode **Active** its self-test with the
   checks of the profile starts by itself and takes about 8–9 minutes (longer
   when it first makes room in the state of charge, see
   [Self-test](batteries.md#self-test-of-a-battery)), while
   the other batteries and the consumers are held: the write registers are
   read at rest for a minute before anything is written (a state of charge
   or probe register jumping meanwhile is marked as a likely shifted
   address), each direction
   begins with 100 W, then the self-test power follows with the registers
   read for up to 90 s without writing, and the registers are read again
   after the release. **Passed**: SLEMS controls the battery. **Partly
   unclear**: tried again after 10 minutes; you can also accept it in its
   repair issue. **Failed**: the battery stays in its trial until the profile
   is corrected or a self-test you start passes. The sensor **Trial**, a
   repair issue and the battery card show where it stands.

**Firmware updates**: SLEMS compares the device information of the profile
(e.g. the firmware versions) after a start and after a pause. After a change
a battery that had passed is observed in normal operation for a day (first
commands capped at 100 W, the registers compared with what was written);
only a finding begins the full trial again.

**Changing it**: **Edit battery** → **Change the profile or the connection**
(read, preview and trial again), **Name and limits** (nothing is read, the
trial stays) or **Show the profile as YAML** (to keep it or to use it for
another battery). A changed own profile is offered to the other batteries
with a profile of the same id. Another address begins the trial again.

**Sharing it**: a profile that passed its trial can become a shipped profile
for everybody with the same battery:
[how to suggest it](../battery-profile-prompt.md#suggest-a-profile-as-a-shipped-profile).

**Candidates**: for some batteries there are
[candidate profiles](../battery-profiles/README.md), not offered in the
dialog because nobody has tested them yet; paste one as an own profile and
check the points listed at its top.

**Unverified shipped profiles**: a shipped profile marked **(unverified)**
was made from the manufacturer's register list and other sources but has not
passed its trial on a real battery yet (so far the **Marstek Venus E Gen 2**,
over RS485 through a Modbus TCP gateway, e.g. an Elfin EW11). It goes through
the same checks and
the same trial as an own profile, and its write preview asks you to confirm
**I know that this profile has not been verified on a real battery yet; I
compared the write registers with the manual, if there is one**. If your
battery passes its trial (the sensor **Trial** shows **Passed**; **Taken as
passed** from the repair issue does not count), please report it in an
[issue](https://github.com/gojux/SLEMS/issues/new?template=battery_profile.yml) with the model and the section
`trial` of your battery from the diagnostics (**Settings → Devices & services
→ SLEMS → ⋮ → Download diagnostics**). The profile is then listed as
verified, with the firmware of your battery. Post only that section: the
download also holds your whole configuration.
