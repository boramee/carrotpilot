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
