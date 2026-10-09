"""FastAPI entry point. Run with: uvicorn app.main:app --reload"""

from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api import incidents, pages, transactions
from app.db import init_db


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Runs once at startup: make sure the tables exist.
    init_db()
    yield


app = FastAPI(
    title="Aegis",
    version="0.1.0",
    summary="Fintech risk & operations API (Loop 1: rule-based skeleton)",
    lifespan=lifespan,
)

app.include_router(transactions.router)
app.include_router(incidents.router)
app.include_router(pages.router)


@app.get("/health", tags=["meta"])
def health() -> dict:
    return {"status": "ok"}
