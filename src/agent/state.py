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
    # "existing" → modifica un solo proyecto existente
    # "multi"    → modifica múltiples repos (el más complejo)
    mode: str

    # Single existing project mode
    project_path: str
    branch_name: str
    existing_structure: str

    # Multi-repo mode
    # repos_registry: {name → {path, branch_name, repo_type}}
    # Serializable como dict[str, dict] para MemorySaver
    repos_registry: dict
    active_repo: str            # nombre del repo activo para las tools

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
    files_modified: list[str]
    generation_round: int
    generation_complete: bool

    # Flow control
    _stop_reason: str
    human_decision: str | None
    nudge_count: int            # cuántas veces empujamos al modelo a usar tools
