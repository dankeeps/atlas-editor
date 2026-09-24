#!/usr/bin/env bash
# Atlas Editor — instalação local (Mac ou Linux).
#
#   curl -fsSL https://raw.githubusercontent.com/dankeeps/atlas-editor/main/install.sh | bash
#
# Baixa o projeto, monta o ambiente Python, baixa os modelos de IA, cria um ícone
# na área de trabalho (iniciar.sh) e abre o editor no navegador. Pode rodar de novo
# a qualquer momento para atualizar; nada se perde (projetos/chaves ficam fora do
# repositório). Depois da primeira vez, o ícone já basta — sem precisar do terminal.
set -euo pipefail

REPO_URL="https://github.com/dankeeps/atlas-editor.git"
DEST="${ATLAS_DEST:-$HOME/Atlas Editor}"
export PORTA="${PORTA:-4123}"

echo "== Atlas Editor — instalação local =="
echo

falta=()
command -v git >/dev/null 2>&1 || falta+=(git)
command -v ffmpeg >/dev/null 2>&1 || falta+=(ffmpeg)
PYTHON=""
for cand in python3.12 python3; do
  if command -v "$cand" >/dev/null 2>&1 && "$cand" -c 'import sys; sys.exit(0 if sys.version_info[:2]==(3,12) else 1)' 2>/dev/null; then
    PYTHON="$cand"; break
  fi
done
[ -n "$PYTHON" ] || falta+=(python3.12)

if [ ${#falta[@]} -gt 0 ]; then
  echo "Faltam estas dependências: ${falta[*]}"
  if [[ "$OSTYPE" == darwin* ]] && command -v brew >/dev/null 2>&1; then
    echo "Instalando com Homebrew (pode pedir sua senha)..."
    brew install "${falta[@]}"
    command -v python3.12 >/dev/null 2>&1 && PYTHON=python3.12
  else
    echo
    echo "Instale manualmente e rode este comando de novo:"
    [[ "$OSTYPE" == darwin* ]] && echo "  brew install ${falta[*]}"
    [[ "$OSTYPE" != darwin* ]] && echo "  sudo apt-get update && sudo apt-get install -y ${falta[*]/python3.12/python3.12 python3.12-venv}"
    exit 1
  fi
fi
[ -n "$PYTHON" ] || { echo "Não encontrei um Python 3.12 utilizável mesmo após a instalação. Pare e verifique manualmente."; exit 1; }

echo "Usando $($PYTHON --version)"
echo

if [ -d "$DEST/.git" ]; then
  echo "Já existe uma instalação em \"$DEST\" — atualizando..."
  git -C "$DEST" pull --ff-only
else
  echo "Baixando o Atlas Editor em \"$DEST\"..."
  git clone "$REPO_URL" "$DEST"
fi
cd "$DEST"

echo
echo "Preparando o ambiente Python (só na primeira vez, demora alguns minutos)..."
if [ ! -d .venv ]; then
  "$PYTHON" -m venv .venv
fi
.venv/bin/python -m pip install --upgrade pip -q
.venv/bin/python -m pip install -r requirements.txt -q

echo "Baixando os modelos de IA (remoção de fundo e pose, ~120 MB, só na primeira vez)..."
.venv/bin/python lib/matte.py --instalar mobilenetv3
.venv/bin/python lib/pose.py --instalar

chmod +x iniciar.sh parar.sh

echo
echo "Criando um ícone na área de trabalho para abrir o Atlas Editor depois, sem"
echo "precisar do terminal..."
if [[ "$OSTYPE" == darwin* ]] && command -v osacompile >/dev/null 2>&1 && [ -d "$HOME/Desktop" ]; then
  APP="$HOME/Desktop/Atlas Editor.app"
  LAUNCH_CMD="'$DEST/iniciar.sh' >>'$HOME/.atlas-editor.log' 2>&1"
  AS_ESCAPED=$(printf '%s' "$LAUNCH_CMD" | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g')
  SCPT_SRC=$(mktemp -t atlas-launcher).applescript
  printf 'do shell script "%s"\n' "$AS_ESCAPED" > "$SCPT_SRC"
  if osacompile -o "$APP" "$SCPT_SRC" 2>/dev/null; then
    echo "Pronto: \"Atlas Editor\" na sua área de trabalho. Dê dois cliques nele da próxima vez."
  else
    echo "Não consegui criar o ícone automaticamente — sem problema, é só rodar este mesmo comando de novo."
  fi
  rm -f "$SCPT_SRC"
elif command -v xdg-open >/dev/null 2>&1 && [ -d "$HOME/Desktop" ]; then
  cat > "$HOME/Desktop/Atlas Editor.desktop" <<DESKTOP
[Desktop Entry]
Type=Application
Name=Atlas Editor
Exec=$DEST/iniciar.sh
Terminal=false
DESKTOP
  chmod +x "$HOME/Desktop/Atlas Editor.desktop"
  echo "Pronto: \"Atlas Editor\" na sua área de trabalho (pode pedir para confirmar \"permitir execução\" no primeiro clique)."
fi

echo
echo "Tudo pronto. Abrindo o Atlas Editor em http://localhost:$PORTA ..."
echo "(Configure sua própria chave da Claude API em Configurações assim que abrir.)"
echo "Da próxima vez, é só clicar no ícone — não precisa rodar este comando de novo."
echo

./iniciar.sh
