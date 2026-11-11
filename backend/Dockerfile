# Relay backend: FastAPI + scheduler + TfL poller in one process (ARCHITECTURE §1).
FROM python:3.12-slim

# ffmpeg: pydub needs it to stitch MP3 clips (live TTS). Mock mode uses WAV and does not.
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg \
 && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir uv==0.11.32
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PROJECT_ENVIRONMENT=/opt/venv PATH=/opt/venv/bin:$PATH

WORKDIR /app/backend
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --frozen --no-dev --no-install-project
COPY . .
RUN uv sync --frozen --no-dev

# data/ (SQLite and the audio cache) is a volume in docker-compose.yml.
EXPOSE 8000
HEALTHCHECK --interval=5s --timeout=3s --retries=20 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')"
CMD ["uvicorn", "relay.main:app", "--host", "0.0.0.0", "--port", "8000"]
