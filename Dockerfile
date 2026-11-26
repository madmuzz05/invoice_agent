# Image produksi (target: Google Cloud Run). Build manual:
#   docker build --platform linux/amd64 -t invoice-verifier .
# Lihat README bagian "Menjalankan via Docker" untuk push & deploy.

# ── Stage 1: dependency Python di virtualenv terpisah ────────────────────────
FROM python:3.12-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN python -m venv /venv
COPY requirements.txt .
RUN /venv/bin/pip install --upgrade pip \
    && /venv/bin/pip install -r requirements.txt

# ── Stage 2: runtime ─────────────────────────────────────────────────────────
FROM python:3.12-slim

# Tesseract dipakai PyMuPDF untuk OCR halaman scan/foto (PDF & gambar).
# Lokasi tessdata berbeda antar versi Debian → buat path tetap /usr/share/tessdata.
RUN apt-get update \
    && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-eng \
    && rm -rf /var/lib/apt/lists/* \
    && ln -s "$(dirname "$(find /usr/share/tesseract-ocr -name eng.traineddata | head -n 1)")" \
        /usr/share/tessdata

# Data runtime (SQLite & file upload sementara) di /app/runtime. Di Cloud Run
# disk ini sementara (hilang saat deploy/restart) — lihat README.
ENV PATH="/venv/bin:$PATH" \
    PYTHONPATH="/app/invoice_verifier" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    APP_ENV=production \
    PORT=8080 \
    TESSDATA_PREFIX=/usr/share/tessdata \
    UPLOAD_DIR=/app/runtime/uploads \
    SQLITE_DB_PATH=/app/runtime/invoice_verifier.db

RUN useradd --create-home --system --uid 10001 --shell /usr/sbin/nologin appuser

COPY --from=builder /venv /venv

WORKDIR /app
# Hanya kode aplikasi; data lokal, .env, dan tes dikecualikan lewat .dockerignore.
COPY invoice_verifier ./invoice_verifier
RUN mkdir -p /app/runtime/uploads \
    && chown -R appuser:appuser /app/runtime

USER appuser
EXPOSE 8080

# Cloud Run tidak memakai HEALTHCHECK Docker (pakai startup probe sendiri),
# tapi berguna untuk docker run / compose lokal.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/health' % os.environ.get('PORT', '8080'), timeout=4)"

# - Port dari env PORT (Cloud Run mengisinya; default 8080).
# - --proxy-headers: IP klien asli dari X-Forwarded-For (rate limit per klien,
#   bukan per IP proxy Cloud Run).
# - 1 proses: job ekstraksi berjalan sebagai background task in-process dan
#   memakai SQLite lokal, jadi jangan pakai --workers > 1.
CMD ["sh", "-c", "exec uvicorn web.main:app --host 0.0.0.0 --port \"${PORT}\" --proxy-headers --forwarded-allow-ips='*' --timeout-graceful-shutdown 20"]
