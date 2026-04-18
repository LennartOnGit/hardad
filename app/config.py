from pydantic import Field
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings, read from environment variables or .env file."""

    ANTHROPIC_API_KEY: str = Field(...)
    # Model used for the tutor reply — quality matters, pay for it.
    ANTHROPIC_MODEL: str = "claude-sonnet-4-5"
    # Cheaper/faster model used for translations, dictionary fallbacks, and
    # news topic generation — volume is higher and output is short.
    ANTHROPIC_FAST_MODEL: str = "claude-haiku-4-5"
    DATABASE_URL: str = "postgresql+psycopg://tutor:dev@localhost:5432/tutor"
    ADMIN_TOKEN: str = Field(...)
    HISTORY_RETENTION_TURNS: int = 50
    ROOT_PATH: str = "/docker_demo"
    LOG_LEVEL: str = "INFO"

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        # Tolerate stale keys in .env (e.g. DEMO_ACCESS_TOKEN left over from
        # before the multi-user auth rewrite) so rollouts don't break on a
        # still-to-be-updated local file.
        "extra": "ignore",
    }


settings = Settings()
