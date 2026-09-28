#!/bin/bash
# Sube los cambios al servidor. Se corre desde la Mac, en la carpeta del ERP:
#   bash servidor/publicar.sh
set -e
cd "$(dirname "$0")/.."

# A dónde se publica. El nombre erp.shopdecopet.com lleva a Cloudflare, no al servidor,
# así que para entrar por la puerta de servicio se usa la IP. Se guarda en servidor/destino.
SERVIDOR="${DECOPET_SERVIDOR:-$(cat servidor/destino 2>/dev/null)}"
[ -n "$SERVIDOR" ] || { echo "No sé a dónde publicar. Escribe la IP en servidor/destino (ej: decopet@5.161.1.2)"; exit 1; }

echo "==> Comprobando que no haya nada sin guardar"
if [ -n "$(git status --porcelain)" ]; then
  echo "Hay cambios sin guardar en el historial. Guárdalos antes de publicar."
  git status --short
  exit 1
fi

echo "==> Pasando las pruebas antes de publicar"
./.venv/bin/python pruebas.py

echo "==> Enviando al servidor"
git push -q "$SERVIDOR:erp.git" main

echo "==> Actualizando allá"
ssh "$SERVIDOR" 'cd ~/erp && git fetch -q origin && git reset -q --hard origin/main && \
  ./.venv/bin/pip install -q -r requirements.txt && \
  sudo systemctl restart decopet && sleep 3 && systemctl is-active decopet'

echo "Publicado."
