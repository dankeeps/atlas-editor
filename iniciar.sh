#!/usr/bin/env bash
# Abre o Atlas Editor: sobe o servidor se ainda não estiver rodando, e abre o
# navegador. É isto que o ícone da área de trabalho chama — pode rodar direto
# também, sempre que quiser (idempotente: clicar de novo com o servidor já
# aberto só reabre o navegador).
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
export PORTA="${PORTA:-4123}"
LOG="$HOME/.atlas-editor.log"

if curl -fsS "http://127.0.0.1:$PORTA/api/health" >/dev/null 2>&1; then
  echo "Já estava rodando, só reabrindo o navegador." >> "$LOG"
else
  echo "Iniciando o Atlas Editor..." >> "$LOG"
  nohup .venv/bin/python app/servidor.py >> "$LOG" 2>&1 &
  disown
  for _ in $(seq 1 30); do
    curl -fsS "http://127.0.0.1:$PORTA/api/health" >/dev/null 2>&1 && break
    sleep 1
  done
fi

if command -v open >/dev/null 2>&1; then
  open "http://localhost:$PORTA"
elif command -v xdg-open >/dev/null 2>&1; then
  xdg-open "http://localhost:$PORTA"
fi
