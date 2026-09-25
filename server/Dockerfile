# Server image: minimal Flask + Gunicorn
# RMAP v1.0.2 uses PGPy, which supports Python through 3.12 (Python 3.13+
# removed stdlib imghdr, which PGPy imports).
# Use a patched Python 3.12 base image to avoid known Debian/OpenSSL CVEs.
FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_ROOT_USER_ACTION=ignore

WORKDIR /app

RUN apt-get update \
 && apt-get install -y --no-install-recommends git ca-certificates openssh-client fonts-dejavu-core tesseract-ocr \
 && rm -rf /var/lib/apt/lists/*

# Copy the package metadata and sources before the single editable install.
COPY pyproject.toml /app/pyproject.toml
COPY src /app/src

RUN pip install --no-cache-dir -e . \
 && pip check

COPY flag /app/flag
COPY entrypoint.sh /app/entrypoint.sh

EXPOSE 5000

# Default command runs the web server
CMD ["./entrypoint.sh"]
