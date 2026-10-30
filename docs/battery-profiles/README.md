# Candidate battery profiles

[Deutsch](README.de.md)

Profiles for batteries that nobody has tested with SLEMS yet. They are made
from the manufacturer's register list and other sources, but some points are
open, so SLEMS does not offer them in its dialog. If you have one of these
batteries, a candidate is an easier start than making a profile with the AI
prompt.

| File | Battery | Open points |
|---|---|---|
| [sungrow_sh10rt.yaml](sungrow_sh10rt.yaml) | Sungrow SH10RT with SBR128 | set point in the External EMS mode, heartbeat, sign of the battery power |
| [solaredge_storedge.yaml](solaredge_storedge.yaml) | SolarEdge StorEdge with a 48 V battery (SE10K-RWB48) | remote control falling back, sign of the battery power |

**Using a candidate**

1. Read the comments at the top: the settings to make before, the sources,
   and the lines marked `# uncertain` and `# question`. For another model of
   the same series, adjust the limits.
2. **Add battery** → model **Battery from a Modbus profile (experimental)** →
   **Paste an own profile (YAML)** and paste the whole file. SLEMS treats it
   like an own profile: values read, write preview and a trial before it
   controls the battery.
3. Check the points listed at the top while the battery runs.

**Reporting**: whether it worked or not, please open an
[issue](https://github.com/gojux/SLEMS/issues) with the model, the firmware,
your findings on the points at the top and, after a passed trial, the
section `trial` of your battery from the diagnostics. A candidate that passed
its trial becomes a profile shipped with SLEMS.
