from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    anthropic_api_key: str
    claude_model: str = "claude-sonnet-4-6"
    api_key: str

    # Workspace where generated projects are created
    projects_workspace: str = "~/projects"

    # LangSmith observability (optional)
    langsmith_tracing: bool = False
    langsmith_api_key: str | None = None
    langsmith_project: str = "backend-builder"


settings = Settings()
