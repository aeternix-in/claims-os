from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="NHCX_MOCK_", extra="ignore")

    database_url: str = "sqlite:///./nhcx_mock.db"
    callback_base_url: str = "http://127.0.0.1:8000/receiver"
    callback_delay_seconds: float = 2.0
    callback_max_attempts: int = 3
    default_scenario: str = "approve"
    log_level: str = "INFO"
    # Require NHCX meta.profile URLs on the request Bundle and Claim (mock rule).
    strict_profiles: bool = True
    # "plain": payload is a JSON object {headers, fhir}. "jwe": payload is a compact JWE string
    # (RSA-OAEP-256 / A256GCM) with x-hcx-* headers in the protected header, like the sandbox.
    envelope_mode: str = "plain"
    # Folder with <participant_code>.private.pem / .public.pem (generate with scripts/gen_dev_keys.py)
    keys_dir: str = "../dev-keys"


@lru_cache
def get_settings() -> Settings:
    return Settings()
