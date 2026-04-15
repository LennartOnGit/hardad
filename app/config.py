from pydantic import Field
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings, read from environment variables or .env file."""

    ANTHROPIC_API_KEY: str = Field(...)
    ANTHROPIC_MODEL: str = "claude-sonnet-4-5"
    DATABASE_URL: str = "postgresql+psycopg://tutor:dev@localhost:5432/tutor"
    DEMO_ACCESS_TOKEN: str = Field(...)
    HISTORY_RETENTION_TURNS: int = 50
    ROOT_PATH: str = "/docker_demo"
    LOG_LEVEL: str = "INFO"

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
