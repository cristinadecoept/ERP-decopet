#!/bin/bash
# Sube los cambios al servidor. Se corre desde la Mac, en la carpeta del ERP.
#   bash servidor/publicar.sh
set -e
SERVIDOR="${DECOPET_SERVIDOR:-decopet@erp.shopdecopet.com}"

echo "==> Comprobando que no haya nada sin guardar"
if [ -n "$(git status --porcelain)" ]; then
  echo "Hay cambios sin guardar en el historial. Guárdalos antes de publicar."
  git status --short
  exit 1
fi

echo "==> Pasando las pruebas antes de publicar"
./.venv/bin/python pruebas.py

echo "==> Enviando al servidor"
git push "$SERVIDOR:erp.git" main

echo "==> Actualizando allá"
ssh "$SERVIDOR" 'cd ~/erp && git reset --hard origin/main 2>/dev/null || git pull; \
  ./.venv/bin/pip install -q -r requirements.txt; \
  sudo systemctl restart decopet && sleep 2 && systemctl is-active decopet'

echo "Publicado."
