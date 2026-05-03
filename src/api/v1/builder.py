import uuid

from fastapi import APIRouter, Depends, HTTPException, Security
from fastapi.security import APIKeyHeader
from pydantic import BaseModel, Field

from src.agent.graph import resume_builder, run_builder
from src.core.config import settings
from src.models.request import BuildRequest, BuildResponse

router = APIRouter(prefix="/builder", tags=["builder"])

_api_key_header = APIKeyHeader(name="X-API-Key")


def _verify_api_key(key: str = Security(_api_key_header)) -> str:
    if key != settings.api_key:
        raise HTTPException(status_code=403, detail="API key inválida")
    return key


class BuildV2Request(BuildRequest):
    thread_id: str = Field(default_factory=lambda: str(uuid.uuid4()))


class BuildV2Response(BaseModel):
    thread_id: str
    interrupted: bool = False
    blueprint_request: dict | None = None   # payload when waiting for blueprint approval
    result: BuildResponse | None = None


class ResumeRequest(BaseModel):
    thread_id: str
    decision: str  # "approve" | "aprobado" | feedback text


@router.post("/build")
async def build_project(
    body: BuildV2Request,
    _: str = Depends(_verify_api_key),
) -> BuildV2Response:
    """
    Inicia la construcción de un proyecto backend desde una historia de usuario.

    El agente realiza el discovery conversacional y propone un blueprint.
    Cuando el blueprint está listo, devuelve interrupted=true con el blueprint_request.
    Usa POST /build/resume para aprobar o dar feedback.
    """
    import anthropic
    import os

    if settings.langsmith_tracing:
        os.environ["LANGSMITH_TRACING"] = "true"
        os.environ["LANGSMITH_PROJECT"] = settings.langsmith_project
        if settings.langsmith_api_key:
            os.environ["LANGSMITH_API_KEY"] = settings.langsmith_api_key

    client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
    result = await run_builder(body, client, thread_id=body.thread_id)

    if result.interrupted:
        return BuildV2Response(
            thread_id=result.thread_id,
            interrupted=True,
            blueprint_request=result.interrupt_payload,
        )

    return BuildV2Response(
        thread_id=result.thread_id,
        interrupted=False,
        result=result.response,
    )


@router.post("/build/resume")
async def resume_build(
    body: ResumeRequest,
    _: str = Depends(_verify_api_key),
) -> BuildV2Response:
    """
    Retoma la construcción después de la aprobación del blueprint.

    Decisiones válidas:
      - "approve" / "aprobado" → genera el código
      - Cualquier texto          → feedback que el agente incorpora y re-propone
    """
    import anthropic

    client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
    result = await resume_builder(body.thread_id, body.decision, client)

    if result.interrupted:
        return BuildV2Response(
            thread_id=result.thread_id,
            interrupted=True,
            blueprint_request=result.interrupt_payload,
        )

    return BuildV2Response(
        thread_id=result.thread_id,
        interrupted=False,
        result=result.response,
    )
