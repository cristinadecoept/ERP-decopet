#!/bin/bash
# Doble clic para abrir la plataforma Decopet (prototipo con datos de prueba).
cd "$(dirname "$0")"
open "http://127.0.0.1:8765" 2>/dev/null &
sleep 1
./.venv/bin/uvicorn plataforma.app:app --host 127.0.0.1 --port 8765
