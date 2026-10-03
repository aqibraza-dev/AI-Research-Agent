from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict

MODELS = ("openrouter/free", "qwen/qwen3.8-27b:free", "nvidia/nemotron-3.5-lightning:free")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    database_url: str = ""
    database_ssl: bool = True
    supabase_url: str = ""
    supabase_publishable_key: str = ""
    open_router_free_api_key: str = ""
    tavily_api_key: str = ""
    redis_url: str = ""
    credential_encryption_key: str = ""
    scheduler_secret: str = ""
    allowed_origins: str = "http://localhost:5173"
    worker_enabled: bool = True


@lru_cache
def settings():
    return Settings()
