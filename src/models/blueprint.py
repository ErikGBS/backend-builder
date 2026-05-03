from __future__ import annotations

from typing import List, Optional
from pydantic import BaseModel


class EndpointSpec(BaseModel):
    method: str
    path: str
    description: str
    request_schema: Optional[str] = None
    response_schema: Optional[str] = None


class EntitySpec(BaseModel):
    name: str
    fields: List[str]
    relations: List[str] = []


class Blueprint(BaseModel):
    project_name: str
    framework: str
    database: str
    auth: str
    entities: List[EntitySpec]
    endpoints: List[EndpointSpec]
    folder_structure: List[str]
    tradeoffs: str
    open_questions: List[str] = []
