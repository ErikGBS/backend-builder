from pydantic import BaseModel


class EndpointSpec(BaseModel):
    method: str          # GET | POST | PUT | DELETE | PATCH
    path: str            # /api/v1/users/{id}
    description: str
    request_schema: str | None = None
    response_schema: str | None = None


class EntitySpec(BaseModel):
    name: str
    fields: list[str]    # ["id: int", "name: str", "created_at: datetime"]
    relations: list[str] = []


class Blueprint(BaseModel):
    project_name: str
    framework: str
    database: str
    auth: str
    entities: list[EntitySpec]
    endpoints: list[EndpointSpec]
    folder_structure: list[str]   # ["src/models/", "src/routes/", ...]
    tradeoffs: str                # qué decisiones se tomaron y por qué
    open_questions: list[str] = []
