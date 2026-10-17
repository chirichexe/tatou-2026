# Server image: minimal Flask + Gunicorn
# RMAP v1.0.2 uses PGPy, which supports Python through 3.12 (Python 3.13+
# removed stdlib imghdr, which PGPy imports).
FROM python:3.12-slim@sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update \
 && apt-get install -y --no-install-recommends git ca-certificates openssh-client tesseract-ocr \
    fonts-dejavu-core \
 && rm -rf /var/lib/apt/lists/*

# Copy dependency definitions before the source code
COPY pyproject.toml requirements.txt /app/

# Install pinned runtime dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy sources after dependencies installation
COPY src /app/src
COPY flag /app/flag
COPY entrypoint.sh /app/entrypoint.sh
RUN pip install --no-cache-dir --no-deps -e .

EXPOSE 5000

# Default command runs the web server
CMD ["./entrypoint.sh"]
