# ERP Decopet — imagen de la aplicación.
# Los datos NO van en la imagen: viven en el volumen /data (DECOPET_DATOS). El programa
# es desechable y se reconstruye solo; los datos se respaldan aparte.
FROM python:3.11-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DECOPET_DATOS=/data

# requirements.txt incluye uvloop, que solo compila en Unix: por eso la imagen va en Linux.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY plataforma plataforma
COPY scripts scripts
COPY alembic alembic
COPY alembic.ini .
COPY pyproject.toml .

RUN mkdir -p /data

EXPOSE 8765

# forwarded-allow-ips=* porque el proxy de Railway no tiene IP fija. Es seguro mientras el
# puerto no esté expuesto más que por el proxy (que es el caso: Cloudflare Access delante).
CMD ["sh", "-c", "python -m alembic upgrade head && exec uvicorn plataforma.app:app --host 0.0.0.0 --port ${PORT:-8765} --proxy-headers --forwarded-allow-ips=*"]
