# docloom studio: single-image deployment.
#
# Stage 1 builds the SPA; stage 2 is a slim Python runtime that serves the API
# and the built SPA from one process on one port. Data (SQLite DB, uploaded
# assets, exports) lives under /data. Mount a volume there to persist it.
#
# docloom-studio depends on the docloom engine, which lives in a sibling
# directory and is not published to PyPI, so the image needs both directories
# in its build context. Run docker build from the repository root (the parent
# of docloom/ and docloom-studio/), not from inside docloom-studio/:
#
#   docker build -t docloom-studio -f docloom-studio/Dockerfile .
#   docker run -p 8899:8899 -v docloom-data:/data docloom-studio

# ---- stage 1: build the web SPA -------------------------------------------
FROM node:22-slim AS web
WORKDIR /web
COPY docloom-studio/web/package.json docloom-studio/web/package-lock.json* ./
RUN npm ci
COPY docloom-studio/web/ ./
RUN npm run build

# ---- stage 2: python runtime ----------------------------------------------
FROM python:3.12-slim AS runtime
WORKDIR /app

# System libs some renderers/parsers need at runtime (PDF, images).
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# The engine isn't on PyPI: install it from the sibling directory first so
# docloom-studio's "docloom[pdf]>=0.2" dependency resolves locally instead of
# reaching out to the index. Dropped afterward; only the installed package
# is needed in the final image.
COPY docloom/ ./docloom-src/
RUN pip install --no-cache-dir "./docloom-src[pdf]" && rm -rf ./docloom-src

COPY docloom-studio/pyproject.toml docloom-studio/README.md ./
COPY docloom-studio/server/ ./server/
# Ship the built SPA where main._webdist() looks first (installed-wheel path).
COPY --from=web /web/dist ./server/docloom_studio/webdist

RUN pip install --no-cache-dir .

# Bind to all interfaces, don't try to open a browser, keep app data on /data.
# For multi-node deploys, add psycopg (pip install .[postgres]) and set
# DOCLOOM_DB_URL=postgresql://user:pass@host/db instead of the on-disk SQLite.
ENV DOCLOOM_STUDIO_HOST=0.0.0.0 \
    DOCLOOM_STUDIO_PORT=8899 \
    DOCLOOM_STUDIO_NO_BROWSER=1 \
    DOCLOOM_STUDIO_HOME=/data \
    HOME=/data
RUN mkdir -p /data
VOLUME ["/data"]
EXPOSE 8899

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8899/api/health').status==200 else 1)"

CMD ["docloom-studio"]
