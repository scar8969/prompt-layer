# Studio Platform - single container.
#
# Base image is python:3.14-slim because pyproject.toml declares
# requires-python = "~=3.14.0", and the checkout used to write this was
# Python 3.14.3.
#
# No secrets are baked in. STUDIO_ENCRYPTION_KEY and STUDIO_AUTH_SECRET are
# supplied at run time (see docker-compose.yml) and never appear below.
#
# On dependencies: requirements.txt is empty on purpose - Prompt Layer and its
# Vercel functions are standard library only. The one non-stdlib package the
# studio/ package needs is cryptography, for AES-256-GCM and scrypt in
# studio/crypto.py. Without it the platform refuses to store provider
# credentials rather than degrading to something weaker.

FROM python:3.14-slim

# Unbuffered stdout so container logs appear as they happen, and no .pyc files
# in the image.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    STUDIO_DATA_DIR=/data \
    STUDIO_ENV=production \
    STUDIO_STORAGE_BACKEND=local \
    STUDIO_STORAGE_ROOT=/data/objects \
    STUDIO_ALLOW_INSECURE_ENDPOINTS=false \
    STUDIO_ENABLE_MOCK_PROVIDER=false

WORKDIR /app

# Dependencies first, so a source edit does not re-run pip.
# requirements.txt stays empty on purpose: it is what tells Vercel's Python
# builder this repo is a functions project, and the Prompt Layer functions are
# stdlib-only by design. Studio's own dependencies are installed explicitly, the
# same way cryptography already was. psycopg is only needed when
# STUDIO_DATABASE_URL points at Postgres; the SQLite default still runs without
# it, which is why the import in studio/db.py is optional.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt \
    && pip install --no-cache-dir "cryptography>=42" \
    && pip install --no-cache-dir "psycopg[binary]>=3.2,<4" "psycopg-pool>=3.2"

# The application. tests/, scripts/ and docs/ are excluded by .dockerignore -
# they are development input, not runtime input. web/dist is shipped, so the
# container serves the built client as well as the API.
COPY . .

# Non-root. The uid is fixed so a mounted volume can be chowned to it once.
RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin studio \
    && mkdir -p /data \
    && chown -R studio:studio /data /app
USER studio

# SQLite (studio.db plus its -wal and -shm), the local object store, and - if
# the two secrets are left unset - the generated key files. All three must
# survive the container, so /data is a volume and not part of the image.
VOLUME ["/data"]

EXPOSE 8090

# /api/system/status is Studio's own route: it answers only after the database
# has been migrated and the schema is readable, which is a stronger check than
# "the process accepted a socket". urllib keeps a slim image dependency-free.
HEALTHCHECK --interval=30s --timeout=5s --start-period=25s --retries=3 \
    CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8090/api/system/status', timeout=3).status == 200 else 1)"]

# studio/app.py is the composition root: it migrates the database, builds the
# object store and the services, starts the worker threads, and serves the API
# plus the built client from web/dist on one origin.
CMD ["python", "-m", "studio.app", "--host", "0.0.0.0", "--port", "8090"]
