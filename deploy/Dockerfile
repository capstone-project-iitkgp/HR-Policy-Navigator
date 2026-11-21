# HR Policy Navigator: one image for the API (FastAPI) and the chat page (Streamlit).
# Built by docker-compose.yml at the repo root; see deploy/README.md.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src:/app \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

# Code and the data the app reads at run time. The corpus text is small and already in git, so the
# index is rebuilt at start-up (well under a second); no index files are baked in.
COPY src ./src
COPY hrms ./hrms
COPY app ./app
COPY data/manifests ./data/manifests
COPY data/eval/source_text ./data/eval/source_text

# Run as a normal user. .cache holds LLM/embedding caches, sessions and traces (a volume in compose);
# hrms/ must be writable because the dummy HR database is built there on first use.
RUN useradd --create-home app && mkdir -p /app/.cache && chown -R app /app/.cache /app/hrms
USER app

EXPOSE 8000 8501
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health')" || exit 1

CMD ["python", "-m", "uvicorn", "hr_navigator.main:app", "--host", "0.0.0.0", "--port", "8000"]
