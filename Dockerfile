# CARLA 0.9.16 publishes a CPython 3.12 Linux client wheel.
FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    API_HOST=0.0.0.0 \
    API_PORT=8000 \
    STORAGE_DIR=/data \
    DATABASE_URL=sqlite:////data/database/poltergeist.db

WORKDIR /app
COPY requirements-core.lock ./
RUN pip install --no-cache-dir -r requirements-core.lock

# Only application sources belong in the runtime image. In particular, neither
# developer environment files nor a host database are copied into it.
COPY pyproject.toml README.md ./
COPY backend/ backend/
COPY shared/ shared/
COPY simulator/ simulator/
COPY scenarios/ scenarios/
COPY evaluation/ evaluation/
COPY optimization/ optimization/
COPY scripts/ scripts/
RUN pip install --no-cache-dir --no-deps .

# The default image supports the API, synthetic runs, and random search. Opt in
# when connecting to CARLA; its client and numeric extras are a separate install.
ARG INSTALL_SIMULATION=false
RUN case "$INSTALL_SIMULATION" in \
      true) apt-get update \
        && apt-get install -y --no-install-recommends libstdc++6 libgomp1 libgl1 libglib2.0-0 \
        && rm -rf /var/lib/apt/lists/* \
        && pip install --no-cache-dir -c requirements-core.lock ".[simulation,optimization]" ;; \
      false) ;; \
      *) echo "INSTALL_SIMULATION must be true or false" >&2; exit 2 ;; \
    esac

RUN groupadd --gid 10001 poltergeist \
    && useradd --uid 10001 --gid poltergeist --create-home poltergeist \
    && mkdir -p /data/database \
    && chown -R poltergeist:poltergeist /data
USER poltergeist

EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:'+os.getenv('API_PORT','8000')+'/health',timeout=3).close()"

# One process owns the SQLite worker lease. Do not add uvicorn --workers here.
CMD ["python", "-m", "backend"]
