from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="AETERNIX_", extra="ignore")

    database_url: str = "sqlite:///./aeternix.db"
    # NHCX gateway (the local mock for now; the sandbox later: https://apisbx.abdm.gov.in)
    nhcx_base_url: str = "http://127.0.0.1:8000"
    nhcx_token: str = "aeternix-dev"          # used only when auth_mode = "static" (the mock)
    # "static": send nhcx_token as-is (local mock). "session": get a token from the ABDM gateway
    # with the sandbox client credentials, cache it, refresh before expiry (NHA guide checkpoint 06).
    auth_mode: str = "static"
    sandbox_client_id: str | None = None
    sandbox_client_secret: str | None = None
    # ABDM gateway v3 sessions API (confirmed working with sandbox credentials, 2026-10-13)
    session_url: str = "https://dev.abdm.gov.in/api/hiecm/gateway/v3/sessions"
    session_refresh_margin_seconds: int = 120
    # NHCX registry (participant list, fetch certs, policies)
    registry_base_url: str = "https://apisbx.abdm.gov.in/pmjay/sbxhcx/participanthcxservice"
    nhcx_timeout_seconds: float = 15.0
    log_level: str = "INFO"
    # "plain" (JSON payload) or "jwe" (encrypted, sandbox format). Must match the gateway.
    envelope_mode: str = "plain"
    # <participant_code>.private.pem (ours) and .public.pem (insurers'). Sandbox: insurer
    # certificates come from the NHCX Fetch Certs API instead of files.
    keys_dir: str = "../dev-keys"
    # Where insurers' PUBLIC keys come from: "files" (<keys_dir>/<code>.public.pem, local mock) or
    # "nhcx" (Fetch Certs API, cached). Our own PRIVATE key always comes from keys_dir.
    key_source: str = "files"
    cert_cache_hours: float = 24.0
    # Demo mode: one-click demo cases, choosing the mock insurer's decision, simulated payment.
    # Only for showing the system against nhcx-mock. Never enable against a real gateway.
    demo_mode: bool = False

    # ---- ABDM Milestone 1: ABHA creation / verification / Scan & Share ----
    # ABHA v3 APIs (sandbox). Prod: https://abha.abdm.gov.in/api/abha
    abha_base_url: str = "https://abhasbx.abdm.gov.in/abha/api"
    abha_timeout_seconds: float = 20.0
    # ABDM's encryption public key rotates (~3 months): fetched from the API, never hard-coded.
    abha_key_cache_hours: float = 12.0
    # ABDM HIE-CM gateway (Scan & Share callback: patient-share on-share). Prod: https://apis.abdm.gov.in
    abdm_gateway_url: str = "https://dev.abdm.gov.in"
    abdm_cm_id: str = "sbx"
    patient_share_on_share_path: str = "/api/hiecm/patient-share/v3/on-share"
    # Our facility on ABDM (HFR / bridge service). Needed for the Scan & Share counter QR.
    hip_id: str | None = None
    counter_id: str = "REG-DESK-1"
    phr_host: str = "phrsbx.abdm.gov.in"
    # OPD token validity told to the patient's app after Scan & Share (minutes)
    share_token_minutes: int = 30


@lru_cache
def get_settings() -> Settings:
    return Settings()
