#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

SERVICE_SRC="${SCRIPT_DIR}/carrot-pipeline-server.service"
ENV_EXAMPLE_SRC="${SCRIPT_DIR}/carrot-pipeline-server.env.example"

SERVICE_DST="/etc/systemd/system/carrot-pipeline-server.service"
ENV_DST="/etc/default/carrot-pipeline-server"

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run as root: sudo bash ${0}"
  exit 1
fi

if [[ ! -f "${SERVICE_SRC}" || ! -f "${ENV_EXAMPLE_SRC}" ]]; then
  echo "Template files not found in ${SCRIPT_DIR}"
  exit 1
fi

install -m 0644 "${SERVICE_SRC}" "${SERVICE_DST}"
if [[ ! -f "${ENV_DST}" ]]; then
  install -m 0644 "${ENV_EXAMPLE_SRC}" "${ENV_DST}"
  echo "Created ${ENV_DST} from example."
else
  echo "${ENV_DST} already exists. Keeping existing file."
fi

systemctl daemon-reload
systemctl enable carrot-pipeline-server.service

echo ""
echo "Installed service: ${SERVICE_DST}"
echo "Edit config file:   ${ENV_DST}"
echo "Then start:"
echo "  sudo systemctl restart carrot-pipeline-server.service"
echo "Check status:"
echo "  sudo systemctl status carrot-pipeline-server.service --no-pager"
