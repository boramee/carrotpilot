# Tuning Tools

## `analyze_carrot_log.py`

Offline analyzer that reads route logs (`rlog/qlog`) and produces tuning suggestions for:

- `SteerActuatorDelay`
- `LongActuatorDelay`
- `LateralTorqueAccelFactor`
- `LateralTorqueFriction`

It is intended for iterative, data-driven tuning with real driving logs.

### Usage

```bash
python3 tools/tuning/analyze_carrot_log.py "<log-or-route-identifier>"
```

Examples:

```bash
python3 tools/tuning/analyze_carrot_log.py "/path/to/rlog.bz2"
python3 tools/tuning/analyze_carrot_log.py "a2a0ccea32023010|2023-07-27--13-01-19/4"
python3 tools/tuning/analyze_carrot_log.py "a2a0ccea32023010|2023-07-27--13-01-19/4" --json-out /tmp/gv70_tuning.json
```

### Notes

- Recommendations are conservative and should be validated with additional logs.
- For `LateralTorqueAccelFactor` and `LateralTorqueFriction`, apply with `LateralTorqueCustom > 0`.
- Best results come from logs that include:
  - highway straight + gentle curves
  - lane keeping active periods
  - speed range with meaningful lateral and longitudinal control activity
