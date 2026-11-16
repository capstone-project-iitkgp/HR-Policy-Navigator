"""FastAPI entrypoint for the HR Policy Navigator."""

from fastapi import FastAPI

from hr_navigator import __version__
from hrms.api import router as hrms_router

app = FastAPI(title="HR Policy Navigator", version=__version__)
app.include_router(hrms_router)  # dummy HRMS endpoints under /hrms (see hrms/README.md)


@app.get("/health")
def health() -> dict[str, str]:
    """Liveness probe for local setup and deployment checks."""
    return {"status": "ok"}
