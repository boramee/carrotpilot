# Carrot Standalone Pipeline (new files only)

This folder provides a standalone upload -> analyze -> telegram pipeline without modifying existing project files.

## Files

- `analyze_carrot_log_standalone.py`  
  Offline analyzer for local qlog/rlog files.
- `pipeline_server.py`  
  Upload API + analysis worker + telegram sender.
- `upload_client.py`  
  In-car uploader (daemon or one-shot).

## 1) Start server

```bash
PYTHONPATH=/workspace python3 tools/carrot_pipeline/pipeline_server.py \
  --bind 0.0.0.0 \
  --port 9090 \
  --token "YOUR_UPLOAD_TOKEN" \
  --storage-root "/tmp/carrot_pipeline" \
  --telegram-bot-token "123456:ABCDEF..." \
  --telegram-chat-id "123456789"
```

### Recommended (auto-start with systemd)

Install service template once:

```bash
sudo bash /workspace/tools/carrot_pipeline/deploy/install_server_service.sh
```

Then put values here:

- `/etc/default/carrot-pipeline-server`
  - `UPLOAD_TOKEN=...`
  - `TELEGRAM_BOT_TOKEN=...`
  - `TELEGRAM_CHAT_ID=...`
  - `OPENPILOT_DIR=...`

Start service:

```bash
sudo systemctl restart carrot-pipeline-server.service
```

Endpoints:

- `POST /upload?route=...&segment=...&filename=...`
- `POST /complete` with `{"route":"..."}`
- `GET /result?route=...`
- `GET /health`

## 2) Start uploader on device

```bash
PYTHONPATH=/workspace python3 tools/carrot_pipeline/upload_client.py \
  --server-url "https://your-server:9090" \
  --token "YOUR_UPLOAD_TOKEN" \
  --include-rlog
```

### Recommended (auto-start with systemd)

Install service template once:

```bash
sudo bash /workspace/tools/carrot_pipeline/deploy/install_upload_client_service.sh
```

Then put values here:

- `/etc/default/carrot-upload-client`
  - `SERVER_URL=https://...:9090`
  - `UPLOAD_TOKEN=...`
  - `INCLUDE_RLOG_FLAG=--include-rlog` (or empty)
  - `OPENPILOT_DIR=...`

Start service:

```bash
sudo systemctl restart carrot-upload-client.service
```

One-shot mode:

```bash
PYTHONPATH=/workspace python3 tools/carrot_pipeline/upload_client.py \
  --server-url "https://your-server:9090" \
  --token "YOUR_UPLOAD_TOKEN" \
  --once
```

## 3) Manual analyzer use

```bash
PYTHONPATH=/workspace python3 tools/carrot_pipeline/analyze_carrot_log_standalone.py \
  "/path/to/rlog.zst" \
  --json-out /tmp/carrot_result.json
```

## One-line answer: where to put values

- Server + Telegram values: `/etc/default/carrot-pipeline-server`
- Device server URL/token values: `/etc/default/carrot-upload-client`
