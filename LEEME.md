# Plataforma Decopet — prototipo navegable

## Cómo abrirla
Doble clic en **`iniciar.command`** (si macOS lo bloquea: clic derecho → Abrir). Se abre en el navegador en http://127.0.0.1:8765.
Solo funciona en esta Mac; no es pública.

## Qué hay
- `plataforma/` — la plataforma nueva (la que se está construyendo por partes según `docs/`).
  - Parte 1 lista: **Órdenes** (lista, panel lateral, acciones, permisos Cristina/Logística, alertas, resumen para despachador).
  - Datos de prueba (5 clientes ficticios): `./.venv/bin/python plataforma/semilla.py`
  - Base limpia (sin clientes ni órdenes, conserva catálogo/usuarios/tasa): `./.venv/bin/python plataforma/semilla.py --vacia`
  - Ambos comandos BORRAN lo que haya; no usarlos cuando ya haya datos reales.
- `docs/` — análisis y mapa funcional por partes.
- `app/` + `scripts/` — prototipo inicial del 13-sep con la importación real del Excel de ventas (se reutilizará en la migración final).

## Historial de ventas
El Excel `~/Downloads/DECOPET - Historial de Ventas.xlsx` se importa con `./.venv/bin/python plataforma/importar_excel.py` (se puede repetir; reemplaza lo importado antes, no toca las órdenes creadas en la plataforma).
