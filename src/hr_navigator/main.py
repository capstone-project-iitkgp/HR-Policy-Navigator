"""FastAPI entrypoint for the HR Policy Navigator."""

from fastapi import FastAPI

from hr_navigator import __version__

app = FastAPI(title="HR Policy Navigator", version=__version__)


@app.get("/health")
def health() -> dict[str, str]:
    """Liveness probe for local setup and deployment checks."""
    return {"status": "ok"}
