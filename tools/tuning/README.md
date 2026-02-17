# Tuning Tools

## `analyze_carrot_log.py`

Offline analyzer that reads local route logs (`rlog/qlog`) and produces tuning suggestions for:

- `SteerActuatorDelay`
- `LongActuatorDelay`
- `LateralTorqueAccelFactor`
- `LateralTorqueFriction`

It is intended for iterative, data-driven tuning with real driving logs.

### Usage

```bash
PYTHONPATH=/workspace python3 tools/tuning/analyze_carrot_log.py "<log-file> [<log-file> ...]"
```

Examples:

```bash
PYTHONPATH=/workspace python3 tools/tuning/analyze_carrot_log.py "/path/to/rlog.bz2"
PYTHONPATH=/workspace python3 tools/tuning/analyze_carrot_log.py "/path/to/seg0.rlog.bz2" "/path/to/seg1.rlog.bz2"
PYTHONPATH=/workspace python3 tools/tuning/analyze_carrot_log.py "/path/to/rlog.bz2" --json-out /tmp/gv70_tuning.json
```

### Notes

- Recommendations are conservative and should be validated with additional logs.
- For `LateralTorqueAccelFactor` and `LateralTorqueFriction`, apply with `LateralTorqueCustom > 0`.
- Best results come from logs that include:
  - highway straight + gentle curves
  - lane keeping active periods
  - speed range with meaningful lateral and longitudinal control activity
