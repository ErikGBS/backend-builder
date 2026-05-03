from __future__ import annotations

from typing import List, Optional
from pydantic import BaseModel


class RepoImpact(BaseModel):
    repo: str
    project: str
    reason: str
    touch_type: str   # MODIFICAR | NUEVO | INVESTIGAR


class FlowNode(BaseModel):
    label: str
    detail: Optional[str] = None
    kind: Optional[str] = None   # endpoint | schema | service | repo | orm | mapper


class RefinementContext(BaseModel):
    """Output del agente de refinamiento — usado como contexto para el constructor.

    Permite al builder generar código que sigue los patrones reales del proyecto
    en vez de generar código genérico desde cero.
    """
    title: str
    summary: str
    repos_impacted: List[RepoImpact]
    endpoints_affected: List[str]
    schemas_affected: List[str]
    db_changes: List[str]
    external_integrations: List[str] = []
    complexity_signals: List[str] = []
    open_questions: List[str] = []
    flow: List[FlowNode] = []
    markdown: str = ""
