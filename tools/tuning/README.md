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

---

## End-to-end Pipeline (upload -> analyze -> telegram)

### 1) Start server

`carrot_pipeline_server.py` exposes:

- `POST /upload?route=...&segment=...&filename=...` (raw file body)
- `POST /complete` (json: `{"route":"..."}`) -> enqueue analysis
- `GET /result?route=...`
- `GET /health`

```bash
PYTHONPATH=/workspace python3 tools/tuning/carrot_pipeline_server.py \
  --bind 0.0.0.0 \
  --port 9090 \
  --token "YOUR_UPLOAD_TOKEN" \
  --storage-root "/tmp/carrot_pipeline" \
  --telegram-bot-token "123456:ABCDEF..." \
  --telegram-chat-id "123456789"
```

### 2) Start in-car uploader daemon

`carrot_upload_client.py` watches `IsOnroad/IsOffroad`, then uploads the latest closed route automatically.

```bash
PYTHONPATH=/workspace python3 tools/tuning/carrot_upload_client.py \
  --server-url "https://your-server:9090" \
  --token "YOUR_UPLOAD_TOKEN" \
  --include-rlog
```

Use `--once` for one-shot upload:

```bash
PYTHONPATH=/workspace python3 tools/tuning/carrot_upload_client.py \
  --server-url "https://your-server:9090" \
  --token "YOUR_UPLOAD_TOKEN" \
  --once
```

### 3) Flow

1. drive ends (`onroad -> offroad`)
2. uploader sends qlog (and optionally rlog) to server
3. uploader calls `/complete`
4. server runs `analyze_carrot_log.py`
5. server sends suggested parameters to Telegram
