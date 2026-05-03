from __future__ import annotations
from typing import TypedDict

from src.models.request import BuildRequest
from src.models.blueprint import Blueprint


class BuilderState(TypedDict):
    # Conversation history — serializable plain dicts
    messages: list[dict]

    # Input
    request: BuildRequest

    # Discovery phase
    project_name: str
    framework: str       # fastapi | express | nestjs | django | spring
    database: str        # postgresql | mysql | mongodb | sqlite
    auth: str            # jwt | api_key | none
    extra_context: str   # anything the dev added during discovery

    # Blueprint phase
    blueprint: Blueprint | None
    blueprint_approved: bool

    # Generation tracking
    files_generated: list[str]
    generation_round: int
    generation_complete: bool   # True only after Claude calls generation_complete tool

    # Flow control
    _stop_reason: str
    human_decision: str | None
