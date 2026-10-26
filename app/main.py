"""FastAPI entry point. Run with: uvicorn app.main:app --reload"""

from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api import incidents, transactions
from app.web import routes as web
from app.db import init_db


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Runs once at startup: apply any pending database migrations.
    init_db()
    yield


app = FastAPI(
    title="Aegis",
    version="0.3.0",
    summary="Fintech risk & operations API (Loop 3: web app and analyst workflow)",
    lifespan=lifespan,
)

app.include_router(transactions.router)
app.include_router(incidents.router)
app.include_router(web.router)


@app.get("/health", tags=["meta"])
def health() -> dict:
    return {"status": "ok"}
