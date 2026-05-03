from __future__ import annotations

from typing import List, Optional
from pydantic import BaseModel

from src.models.refinement import RefinementContext


class BuildRequest(BaseModel):
    user_story: str
    project_name: Optional[str] = None
    branch_name: Optional[str] = None
    # Contexto del agente de refinamiento (opcional)
    # Si se provee, el builder genera código que sigue los patrones del proyecto existente
    refinement_context: Optional[RefinementContext] = None


class BuildResponse(BaseModel):
    project_path: str
    files_generated: List[str]
    framework: str
    database: str
    branch_name: Optional[str] = None
    message: str
