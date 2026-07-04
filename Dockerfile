FROM python:3.12-slim

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=5555 \
    HOST=0.0.0.0

RUN apt-get update && apt-get install -y --no-install-recommends \
      ocrmypdf \
      tesseract-ocr \
      tesseract-ocr-por \
      tesseract-ocr-eng \
      tesseract-ocr-spa \
      tesseract-ocr-fra \
      tesseract-ocr-ita \
      tesseract-ocr-deu \
      ffmpeg \
      poppler-utils \
      ghostscript \
      unpaper \
      pngquant \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py ./
COPY templates ./templates
COPY static ./static

EXPOSE 5555

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:5555/health').read()" || exit 1

CMD ["python", "app.py"]
