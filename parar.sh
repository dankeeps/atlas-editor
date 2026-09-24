#!/usr/bin/env bash
# Encerra o Atlas Editor que o iniciar.sh deixou rodando em segundo plano.
set -euo pipefail
PORTA="${PORTA:-4123}"
if command -v lsof >/dev/null 2>&1; then
  PID=$(lsof -tiTCP:"$PORTA" -sTCP:LISTEN 2>/dev/null || true)
else
  PID=$(pgrep -f "app/servidor.py" || true)
fi
if [ -z "$PID" ]; then
  echo "Atlas Editor não está rodando."
else
  kill $PID
  echo "Atlas Editor encerrado."
fi
