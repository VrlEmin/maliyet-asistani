FROM python:3.12-slim

# Root kullanıcısı ile çalış (izin sorunlarını önlemek için)
USER root

WORKDIR /app

# main modülünün bulunması için (ModuleNotFoundError önleme)
ENV PYTHONPATH=/app

# Sistem bağımlılıkları: gcc, libpq (postgres) + Playwright/Chromium için gerekli kütüphaneler
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        gcc libpq-dev \
        # Playwright Chromium bağımlılıkları
        libnss3 libatk-bridge2.0-0 libdrm2 libxcomposite1 \
        libxdamage1 libxrandr2 libgbm1 libasound2 \
        libpango-1.0-0 libcairo2 libatspi2.0-0 libcups2 \
        libxkbcommon0 libgtk-3-0 libx11-xcb1 \
    && rm -rf /var/lib/apt/lists/*

# Önce requirements.txt'yi kopyala ve bağımlılıkları yükle
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# Playwright Chromium browser'ını yükle
RUN playwright install chromium

# Tüm proje dosyalarını kopyala
COPY . /app/

# İzinleri düzelt
RUN chmod -R 755 /app && \
    chown -R root:root /app

EXPOSE 8000

# Reload kapalı (Fedora/watchfiles izin hatası önleme)
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
