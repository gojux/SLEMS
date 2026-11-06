# Candidate battery profiles

[Deutsch](README.de.md)

Profiles for batteries that nobody has tested with SLEMS yet. They are made
from the manufacturer's register list and other sources, but some points are
open, so SLEMS does not offer them in its dialog. If you have one of these
batteries, a candidate is an easier start than making a profile with the AI
prompt.

| File | Battery | Open points |
|---|---|---|
| [foxess_h3_smart.yaml](foxess_h3_smart.yaml) | Fox ESS H3 Smart / H3 Pro (registers from 46001) | target battery not confirmed on an H3, fall-back after the remote time-out, read back up to 5 s late, few Modbus TCP connections |
| [goodwe_et.yaml](goodwe_et.yaml) | GoodWe ET (GW10K-ET) and series with the EMS mode 47511 | Charge-BAT/Discharge-BAT with a changing power, PV at 0 W, no fall-back with the default 47505, signs |
| [kostal_plenticore.yaml](kostal_plenticore.yaml) | KOSTAL PLENTICORE plus G2/G3 with a battery | hand-back only after the inverter's time-out, signs of the battery and AC power, set point without effect on some G2 |
| [marstek_venus_a.yaml](marstek_venus_a.yaml) | Marstek Venus A | set point with own PV, 32 registers per read, RS485 control ending by itself |
| [marstek_venus_d.yaml](marstek_venus_d.yaml) | Marstek Venus D | set point with own PV, highest powers reading 0 after a restart |
| [sigenergy_sigenstor.yaml](sigenergy_sigenstor.yaml) | Sigenergy plant with a battery (SigenStor, Sigen Hybrid) | no fall-back documented if SLEMS fails, limits possibly stored in flash, limit on discharging, sign of the AC power, 1 s between requests |
| [sungrow_sh10rt.yaml](sungrow_sh10rt.yaml) | Sungrow SH10RT with SBR128 | set point in the External EMS mode, heartbeat, sign of the battery power, AC power |
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
[issue](https://github.com/gojux/SLEMS/issues/new?template=battery_profile.yml) with the model, the firmware,
your findings on the points at the top and, after a passed trial, the
section `trial` of your battery from the diagnostics. A candidate that passed
its trial becomes a profile shipped with SLEMS.
