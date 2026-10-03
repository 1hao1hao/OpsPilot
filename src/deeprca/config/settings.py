"""Settings used only by the independent mock environment."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    agent_url: str = "http://localhost:8000"
    analysis_timeout: int = 60


@lru_cache
def get_settings() -> Settings:
    return Settings()
