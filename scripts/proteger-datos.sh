#!/bin/bash
# Impide que un dato del negocio o un secreto entre al historial de versiones.
# .gitignore evita el accidente; esto evita también el "git add -f" hecho sin pensar.
# Se instala como gancho:  ln -sf ../../scripts/proteger-datos.sh .git/hooks/pre-commit

malo=0
archivos=$(git diff --cached --name-only --diff-filter=ACM)

for f in $archivos; do
  case "$f" in
    *.db|*.db-shm|*.db-wal|*.sqlite|*.sqlite3)
      echo "  ✗ $f  — es una base de datos"; malo=1 ;;
    plataforma/data/*|data/*)
      echo "  ✗ $f  — está en la carpeta de datos"; malo=1 ;;
    *.jpg|*.jpeg|*.png|*.heic|*.HEIC|*.pdf)
      echo "  ✗ $f  — es una foto o un documento"; malo=1 ;;
    servidor/destino|.env|.env.*|*.pem|*.key|*id_ed25519*|*id_rsa*)
      echo "  ✗ $f  — es un secreto o una llave"; malo=1 ;;
  esac
done

# Un token de Airtable o una llave pegada dentro de un archivo de texto
if git diff --cached -U0 | grep -qE '^\+.*(\bpat[A-Za-z0-9]{13,}\.[A-Za-z0-9]{40,}|-----BEGIN [A-Z ]*PRIVATE KEY-----)'; then
  echo "  ✗ hay algo que parece un token o una llave privada dentro del texto"; malo=1
fi

if [ $malo -eq 1 ]; then
  echo
  echo "No se guardó nada. Esos archivos no pueden entrar al historial:"
  echo "una vez dentro, quedan para siempre aunque después se borren."
  echo
  echo "Si de verdad hace falta (y casi nunca hace falta):  git commit --no-verify"
  exit 1
fi
