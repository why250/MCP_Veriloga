#!/bin/bash
# Deploy VerilogA MCP Server to remote Linux server at 172.16.4.25
# Run this script from the MCP_Veriloga project root on a Linux/Mac machine.
# On Windows, execute the equivalent commands manually via WinSCP or other tools.
#
# Usage:
#   bash deploy/deploy_remote.sh

set -euo pipefail

REMOTE_HOST="172.16.4.25"
REMOTE_USER="mcp"          # adjust to actual username
REMOTE_PORT=22
REMOTE_DIR="/opt/mcp/veriloga-help"
SERVICE_PORT=8096

echo "=== Deploying VerilogA MCP Server to ${REMOTE_HOST} ==="

echo "[1/5] Creating remote directory..."
ssh -p "${REMOTE_PORT}" "${REMOTE_USER}@${REMOTE_HOST}" "mkdir -p ${REMOTE_DIR}/server ${REMOTE_DIR}/reference"

echo "[2/5] Copying server code..."
scp -P "${REMOTE_PORT}" -r server/ "${REMOTE_USER}@${REMOTE_HOST}:${REMOTE_DIR}/"

echo "[3/5] Copying reference documents..."
scp -P "${REMOTE_PORT}" -r reference/ "${REMOTE_USER}@${REMOTE_HOST}:${REMOTE_DIR}/"

echo "[4/5] Installing Python dependencies..."
ssh -p "${REMOTE_PORT}" "${REMOTE_USER}@${REMOTE_HOST}" "
    cd ${REMOTE_DIR}
    python3 -m venv .venv
    .venv/bin/pip install --upgrade pip
    .venv/bin/pip install -r server/requirements.txt
"

echo "[5/5] Building document index..."
ssh -p "${REMOTE_PORT}" "${REMOTE_USER}@${REMOTE_HOST}" "
    cd ${REMOTE_DIR}
    .venv/bin/python server/main.py --build-index
"

echo ""
echo "=== Installing systemd service ==="
scp -P "${REMOTE_PORT}" deploy/veriloga-mcp.service "${REMOTE_USER}@${REMOTE_HOST}:/tmp/"
ssh -p "${REMOTE_PORT}" "${REMOTE_USER}@${REMOTE_HOST}" "
    sudo mv /tmp/veriloga-mcp.service /etc/systemd/system/
    sudo systemctl daemon-reload
    sudo systemctl enable veriloga-mcp
    sudo systemctl restart veriloga-mcp
    sleep 2
    sudo systemctl status veriloga-mcp --no-pager
"

echo ""
echo "=== Deployment complete ==="
echo "Service endpoint: http://${REMOTE_HOST}:${SERVICE_PORT}/mcp/sse"
echo ""
echo "Update your mcp.json to use the remote URL:"
echo '  "veriloga-help": { "url": "http://'"${REMOTE_HOST}:${SERVICE_PORT}"'/mcp/sse" }'
