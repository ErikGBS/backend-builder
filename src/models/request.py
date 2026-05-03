from __future__ import annotations

from typing import List, Optional
from pydantic import BaseModel


class BuildRequest(BaseModel):
    user_story: str
    project_name: Optional[str] = None
    branch_name: Optional[str] = None


class BuildResponse(BaseModel):
    project_path: str
    files_generated: List[str]
    framework: str
    database: str
    branch_name: Optional[str] = None
    message: str
