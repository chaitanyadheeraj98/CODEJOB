from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "CodeJob Email Automation API"
    app_env: str = "dev"
    debug: bool = True

    database_url: str = "sqlite:///./data/codejob.db"
    redis_url: str = "redis://localhost:6379/0"
    openai_api_key: str = ""
    deepseek_api_key: str = Field(default="", validation_alias="Deepseek_API_KEY")
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model_fast: str = "deepseek-chat"
    deepseek_timeout_seconds: float = 20.0
    feature_deepseek_enabled: bool = False
    google_client_id: str = ""
    google_client_secret: str = ""
    google_redirect_uri: str = "http://localhost:8080/"
    google_token_path: str = "./data/google_token.json"
    gmail_label_filter: str = ""
    google_login_hint: str = ""
    google_sheets_tracking_enabled: bool = False
    google_sheets_tracking_spreadsheet_id: str = "1F73Iax75j2rGGb53GGqTmAkEK0o19nmg"
    google_sheets_tracking_tab_name: str = "Sheet1"
    owner_id: str = "default-owner"
    resume_storage_dir: str = "./data/resumes"
    attachment_storage_dir: str = "./data/attachments"
    qualification_threshold: float = 0.6
    feature_auto_polling: bool = False
    feature_auto_poll_interval_minutes: int = 10
    feature_auto_send: bool = False
    feature_retry_queue: bool = False
    semantic_embedding_provider: str = "sbert"
    semantic_embedding_model: str = "text-embedding-3-small"
    semantic_embedding_dimension: int = 256
    google_embedding_provider: str = Field(
        default="",
        validation_alias=AliasChoices("GOOGLE_EMBEDDING_PROVIDER", "GoogleEmbedding_PROVIDER"),
    )
    google_embedding_api_key: str = Field(
        default="",
        validation_alias=AliasChoices("GOOGLE_EMBEDDING_API_KEY", "GoogleEmbedding_API_KEY"),
    )
    google_embedding_model: str = Field(
        default="gemini-embedding-2",
        validation_alias=AliasChoices("GOOGLE_EMBEDDING_MODEL", "GoogleEmbedding_MODEL"),
    )
    google_embedding_base_url: str = Field(
        default="https://generativelanguage.googleapis.com/v1beta",
        validation_alias=AliasChoices("GOOGLE_EMBEDDING_BASE_URL", "GoogleEmbedding_BASE_URL"),
    )
    semantic_embedding_fallback_provider: str = "openrouter"
    semantic_embedding_fallback_model: str = "openai/text-embedding-3-small"
    semantic_embedding_sbert_model: str = "sentence-transformers/all-MiniLM-L6-v2"
    semantic_embedding_sbert_device: str = "cpu"
    hf_token: str = Field(
        default="",
        validation_alias=AliasChoices("HF_TOKEN", "HUGGINGFACE_HUB_TOKEN"),
    )
    openrouter_api_key: str = ""
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    semantic_embedding_timeout_seconds: float = 20.0
    semantic_embedding_latency_log_enabled: bool = True
    semantic_keyword_weight: float = 0.6
    semantic_similarity_weight: float = 0.4
    telegram_bot_token: str = ""
    telegram_allowed_chat_ids: str = ""
    telegram_action_pin: str = ""
    telegram_alerts_enabled: bool = True
    telegram_auth_ttl_minutes: int = 30
    allow_runtime_schema_patch: bool = False

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        populate_by_name=True,
    )

    def _normalize_runtime_embedding_provider(self, provider: str | None) -> str | None:
        normalized = (provider or "").strip().lower()
        if normalized in {"sbert", "hash"}:
            return normalized
        if normalized in {"gemini", "openrouter", "openai"}:
            return "sbert"
        return None

    @property
    def effective_semantic_embedding_provider(self) -> str:
        primary = self._normalize_runtime_embedding_provider(self.semantic_embedding_provider)
        if primary:
            return primary
        legacy = self._normalize_runtime_embedding_provider(self.google_embedding_provider)
        if legacy:
            return legacy
        return "sbert"

    @property
    def effective_semantic_embedding_model(self) -> str:
        if self.effective_semantic_embedding_provider == "hash":
            dims = max(32, int(self.semantic_embedding_dimension or 256))
            return f"hash:{dims}"
        return (self.semantic_embedding_sbert_model or "sentence-transformers/all-MiniLM-L6-v2").strip() or "sentence-transformers/all-MiniLM-L6-v2"


settings = Settings()
