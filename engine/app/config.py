from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    supabase_url: str                       # https://<ref>.supabase.co
    database_url: str                       # Supabase "Session pooler" connection string
    supabase_jwt_secret: str | None = None  # only for legacy HS256 projects; else JWKS is used
    cors_origins: str = "http://localhost:5173"
    supabase_service_role_key: str | None = None   # server only; enables Supabase Storage
    local_storage_dir: str = "./.storage"           # dev fallback when no service key
    nhcx_enabled: bool = False                      # NHCX channel (settings for it: AETERNIX_* env, see nhcx/aeternix-core)


@lru_cache
def get_settings() -> Settings:
    return Settings()
