#!/bin/bash
# Copia la base de datos a la nube, fuera del disco de la Mac, y deja los últimos 30 días.
# Guarda en TODAS las nubes que encuentre (Google Drive, iCloud): dos copias en sitios
# distintos es mejor que una. En cuanto se instale Google Drive, empieza a usarlo solo.
set -e
RAIZ="$(cd "$(dirname "$0")/.." && pwd)"
SELLO="$(date +%Y-%m-%d-%H%M)"
TMP="$(mktemp -d)/decopet-$SELLO.db"

# el modo backup de sqlite copia sin corromper aunque el ERP esté escribiendo
sqlite3 "$RAIZ/plataforma/data/plataforma.db" ".backup '$TMP'"

# la base importada de Airtable (5.285 clientes, 7.794 pedidos) también hay que guardarla
TMP2=""
if [ -f "$RAIZ/data/decopet.db" ]; then
  TMP2="$(dirname "$TMP")/decopet-airtable-$SELLO.db"
  sqlite3 "$RAIZ/data/decopet.db" ".backup '$TMP2'"
fi

# y el programa: sin él, los datos solos no se pueden abrir
TMPC="$(dirname "$TMP")/decopet-programa-$SELLO.tgz"
tar -czf "$TMPC" -C "$RAIZ" \
  --exclude=".venv" --exclude="__pycache__" --exclude="*.pyc" \
  --exclude="plataforma/data" --exclude="data" --exclude="Branding y Fotos" \
  plataforma scripts app docs .git .gitignore LEEME.md 2>/dev/null || true

DESTINOS=(); VISTOS=()
for base in "$HOME/Library/CloudStorage"/GoogleDrive-*/"Mi unidad" \
            "$HOME/Library/CloudStorage"/GoogleDrive-*/"My Drive" \
            "$HOME/Library/CloudStorage"/iCloudDrive*; do
  # macOS deja contenedores de nube viejos que existen pero no dejan escribir: se descartan
  [ -d "$base" ] && [ -w "$base" ] || continue
  id="$(stat -f "%d:%i" "$base" 2>/dev/null)" || continue
  repetida=0
  for y in "${VISTOS[@]:-}"; do [ "$y" = "$id" ] && repetida=1; done
  [ $repetida -eq 1 ] && continue
  VISTOS+=("$id")
  DESTINOS+=("$base/Decopet respaldos")
done

if [ ${#DESTINOS[@]} -eq 0 ]; then
  echo "$(date '+%Y-%m-%d %H:%M') ERROR: no encontré ninguna nube (ni Google Drive ni iCloud)" >&2
  rm -f "$TMP"; exit 1
fi

for D in "${DESTINOS[@]}"; do
  [ -d "$D" ] || mkdir -p "$D"
  cp "$TMP" "$D/decopet-$SELLO.db"
  cp "$TMP" "$D/decopet-ultimo.db"
  [ -n "$TMP2" ] && cp "$TMP2" "$D/decopet-airtable-ultimo.db"
  [ -f "$TMPC" ] && cp "$TMPC" "$D/decopet-programa-ultimo.tgz"
  ls -1t "$D"/decopet-2*.db 2>/dev/null | tail -n +31 | while read -r v; do rm -f "$v"; done
  echo "$(date '+%Y-%m-%d %H:%M') respaldo ok → $D/decopet-$SELLO.db"
done
rm -f "$TMP" "$TMP2" "$TMPC"
