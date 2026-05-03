from __future__ import annotations
from typing import TypedDict, Optional

from src.models.request import BuildRequest
from src.models.blueprint import Blueprint


class BuilderState(TypedDict):
    # Conversation history — serializable plain dicts
    messages: list[dict]

    # Input
    request: BuildRequest

    # Mode — determina el flujo del grafo
    # "new"      → crea proyecto desde cero en projects_workspace
    # "existing" → modifica proyecto existente en project_path
    mode: str

    # Existing project mode
    project_path: str          # ruta absoluta al repo existente
    branch_name: str           # rama a crear (feature/HU-XXX)
    existing_structure: str    # snapshot de la estructura del proyecto leida

    # New project mode (discovery)
    project_name: str
    framework: str
    database: str
    auth: str
    extra_context: str

    # Blueprint phase
    blueprint: Blueprint | None
    blueprint_approved: bool

    # Generation tracking
    files_generated: list[str]
    files_modified: list[str]   # archivos modificados en proyecto existente
    generation_round: int
    generation_complete: bool

    # Flow control
    _stop_reason: str
    human_decision: str | None
