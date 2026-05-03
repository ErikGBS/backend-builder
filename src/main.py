import logging
import sys

from fastapi import FastAPI
from fastapi.responses import RedirectResponse

from src.api.v1.builder import router as builder_router

logging.basicConfig(
    stream=sys.stdout,
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

app = FastAPI(
    title="Backend Builder",
    description="Agente IA que genera proyectos backend completos desde historias de usuario",
    version="0.1.0",
)

app.include_router(builder_router, prefix="/api/v1")


@app.get("/", include_in_schema=False)
async def root() -> RedirectResponse:
    return RedirectResponse(url="/docs")


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}
