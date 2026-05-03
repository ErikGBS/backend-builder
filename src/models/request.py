from pydantic import BaseModel


class BuildRequest(BaseModel):
    user_story: str
    project_name: str | None = None   # si no lo da, el agente lo sugiere
    branch_name: str | None = None    # para el push a git


class BuildResponse(BaseModel):
    project_path: str
    files_generated: list[str]
    framework: str
    database: str
    branch_name: str | None = None
    message: str
