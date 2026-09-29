#!/usr/bin/env bash
# Install harbor-view.service on sc-metro-05 to match the Brev CPU coordinator
# (vss-skill-validator-v3) as of 2026-09-21:
#   uv 0.12.5, harbor 0.22.0, bind 0.0.0.0:8080, --jobs
# Differences from Brev (intentional):
#   - viewer root is /opt/skill-eval/results/_viewer (20T), not /tmp
#   - harbor is pinned to 0.22.0 (Brev unit is unpinned uvx; live process is 0.22.0)
set -euo pipefail

SERVICE_USER="${SERVICE_USER:-}"
VIEWER_ROOT="${VIEWER_ROOT:-/opt/skill-eval/results/_viewer}"
HARBOR_VERSION="${HARBOR_VERSION:-0.22.0}"
UV_VERSION="${UV_VERSION:-0.12.5}"
PORT="${PORT:-8080}"

if [[ -z "$SERVICE_USER" ]]; then
  if id ubuntu >/dev/null 2>&1; then
    SERVICE_USER=ubuntu
  else
    SERVICE_USER="$(id -un)"
  fi
fi

SERVICE_HOME="$(getent passwd "$SERVICE_USER" | cut -d: -f6)"
if [[ -z "$SERVICE_HOME" ]]; then
  echo "user $SERVICE_USER has no home" >&2
  exit 1
fi

echo "Installing harbor-view as ${SERVICE_USER} (${SERVICE_HOME})"
echo "Viewer root: ${VIEWER_ROOT}"

sudo mkdir -p "$VIEWER_ROOT"
sudo chown -R "${SERVICE_USER}:${SERVICE_USER}" /opt/skill-eval
sudo chmod 775 /opt/skill-eval /opt/skill-eval/results "$VIEWER_ROOT"

if [[ ! -x "${SERVICE_HOME}/.local/bin/uv" ]]; then
  echo "Installing uv ${UV_VERSION} for ${SERVICE_USER}"
  sudo -u "$SERVICE_USER" -H bash -lc \
    "curl -LsSf https://astral.sh/uv/${UV_VERSION}/install.sh | sh"
fi

sudo -u "$SERVICE_USER" -H bash -lc "
  export PATH=\"${SERVICE_HOME}/.local/bin:\$PATH\"
  uv --version
  uvx --python 3.12 --from 'harbor==${HARBOR_VERSION}' harbor --version
"

sudo tee /etc/systemd/system/harbor-view.service >/dev/null <<EOF
[Unit]
Description=Harbor viewer for skill-eval trials
Documentation=https://github.com/NVIDIA-AI-Blueprints/video-search-and-summarization
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${SERVICE_USER}
Group=${SERVICE_USER}
WorkingDirectory=${SERVICE_HOME}
Environment=HOME=${SERVICE_HOME}
Environment=PATH=${SERVICE_HOME}/.local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
ExecStart=${SERVICE_HOME}/.local/bin/uvx --python 3.12 --from 'harbor==${HARBOR_VERSION}' harbor view ${VIEWER_ROOT} --jobs --host 0.0.0.0 --port ${PORT}
Restart=on-failure
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now harbor-view.service
sleep 2
systemctl status harbor-view.service --no-pager
echo "--- HTTP ---"
curl -sS -o /dev/null -w "GET / -> %{http_code}\n" "http://127.0.0.1:${PORT}/" || true
curl -sS "http://127.0.0.1:${PORT}/api/jobs" | head -c 300 || true
echo
echo "harbor-view.service is enabled. Traces go in ${VIEWER_ROOT}/<job>/"
