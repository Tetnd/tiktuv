# Optional. Docker is NOT required to run Tiktuv as a desktop application.
# See docker-compose.yml for when this is useful.

FROM python:3.12-slim

# FFmpeg is a hard requirement; everything else is build tooling.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ffmpeg \
        fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

# CPU wheels by default; docker-compose overrides this for the GPU profile.
ARG TORCH_INDEX=https://download.pytorch.org/whl/cpu

WORKDIR /app

COPY apps/backend/pyproject.toml apps/backend/pyproject.toml
COPY apps/backend/tiktuv apps/backend/tiktuv

RUN python -m pip install --no-cache-dir --upgrade pip \
    && python -m pip install --no-cache-dir "torch==2.5.1" --index-url "${TORCH_INDEX}" \
    && python -m pip install --no-cache-dir -e "apps/backend[asr]"

# Do not run as root: this process reads and writes user media.
RUN useradd --create-home --uid 1000 tiktuv \
    && mkdir -p /data \
    && chown -R tiktuv:tiktuv /data /app
USER tiktuv

ENV TIKTUV_DATA_DIR=/data \
    TIKTUV_HOST=127.0.0.1 \
    TIKTUV_PORT=8756 \
    PYTHONUNBUFFERED=1

EXPOSE 8756

CMD ["python", "-m", "tiktuv"]
