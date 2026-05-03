from __future__ import annotations

from typing import List, Optional
from pydantic import BaseModel

from src.models.refinement import RefinementContext


class RepoToModify(BaseModel):
    """Un repositorio que debe ser modificado para esta historia de usuario."""
    name: str          # nombre del repo (ej: maestro-bff-api)
    project_path: str  # ruta absoluta en la Mac (ej: ~/cantera/maestro-bff-api)
    branch_name: str   # rama a crear (ej: feature/HU-142-cotizaciones)
    repo_type: str = "python"  # python | dotnet | functions-python | functions-dotnet


class BuildRequest(BaseModel):
    user_story: str
    project_name: Optional[str] = None
    branch_name: Optional[str] = None
    refinement_context: Optional[RefinementContext] = None


class MultiRepoModifyRequest(BaseModel):
    """Modifica múltiples repositorios para una misma historia de usuario."""
    user_story: str
    repos: List[RepoToModify]           # lista de repos a modificar (del output del refinamiento)
    refinement_context: Optional[RefinementContext] = None


class BuildResponse(BaseModel):
    project_path: str
    files_generated: List[str]
    framework: str
    database: str
    branch_name: Optional[str] = None
    message: str


class MultiRepoResponse(BaseModel):
    repos_modified: List[str]
    files_per_repo: dict
    branches_created: List[str]
    message: str
