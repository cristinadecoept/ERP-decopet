# ERP Decopet — imagen para Railway.
# Los datos NO van en la imagen: viven en el volumen /data (DECOPET_DATOS). El programa
# es desechable y se reconstruye solo; los datos se respaldan aparte.
FROM public.ecr.aws/docker/library/python:3.12-slim

# tzdata: sin ella el servidor vive en UTC y desde las 8 pm de Caracas el ERP ya cree que es mañana.
# sqlite3: la usa scripts/respaldo.sh para copiar la base sin corromperla mientras se usa.
RUN apt-get update && apt-get install -y --no-install-recommends tzdata sqlite3 \
    && rm -rf /var/lib/apt/lists/*

ENV TZ=America/Caracas \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DECOPET_DATOS=/data \
    DECOPET_RESPALDOS=/data/respaldos

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY plataforma plataforma
COPY scripts scripts
# Si se publica desde Windows los .sh llegan con CRLF y bash no los ejecuta: se normalizan aquí, pase lo que pase.
RUN for f in scripts/*.sh; do tr -d '\015' < "$f" > "$f.tmp" && mv "$f.tmp" "$f"; done && chmod +x scripts/*.sh

# Un solo proceso a propósito: la tasa BCV y el respaldo corren en hilos dentro del ERP,
# con dos procesos se harían dos veces. Para ~8 personas sobra.
# forwarded-allow-ips=*: el proxy de Railway no tiene IP fija; sin esto no se sabe la IP real
# de quien entra y el freno de intentos de clave no sirve.
CMD ["sh", "-c", "exec uvicorn plataforma.app:app --host 0.0.0.0 --port ${PORT:-8765} --workers 1 --proxy-headers --forwarded-allow-ips='*'"]
