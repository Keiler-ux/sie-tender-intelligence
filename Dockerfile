# Image de l'outil : interface web Streamlit, données dans /data (volume persistant).
ARG BASE=python:3.12-slim
FROM ${BASE}

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    SIE_ESPACE=/data/affaires SIE_INDEX=/data/archives_index.json PORT=8501

# Tesseract pour l'OCR des PDF scannés
RUN apt-get update && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-fra \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY sie_tender ./sie_tender
COPY scripts ./scripts
COPY app.py docker-entrypoint.sh ./
RUN chmod +x docker-entrypoint.sh && useradd -m sie && mkdir -p /data && chown sie /data
USER sie
VOLUME /data
EXPOSE 8501
HEALTHCHECK CMD python -c "import urllib.request,os; urllib.request.urlopen(f'http://localhost:{os.environ[\"PORT\"]}/_stcore/health')"
ENTRYPOINT ["./docker-entrypoint.sh"]
