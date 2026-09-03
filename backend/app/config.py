from typing import Literal

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
    deepseek_model_fast: str = "deepseek-v4-flash"
    deepseek_model_pro: str = Field(default="deepseek-v4-pro", validation_alias="DEEPSEEK_MODEL_PRO")
    deepseek_timeout_seconds: float = 20.0
    feature_deepseek_enabled: bool = False
    role_manifest_child_creation_enabled: bool = Field(
        default=False,
        validation_alias="ROLE_MANIFEST_CHILD_CREATION_ENABLED",
    )
    groq_api_key: str = Field(
        default="",
        validation_alias=AliasChoices("CodeJobGroq", "GROQ_API_KEY", "Groq_API_KEY"),
    )
    groq_base_url: str = "https://api.groq.com/openai/v1"
    groq_gate_model: str = Field(
        default="llama-3.1-8b-instant",
        validation_alias=AliasChoices("CodeJobGroq_Model", "GROQ_GATE_MODEL"),
    )
    groq_gate_timeout_seconds: float = 20.0
    groq_gate_strict_json: bool = True
    groq_gate_body_char_limit: int = 6000
    groq_gate_max_retries: int = 2
    groq_gate_redact_contact_info: bool = True
    # Which backend answers the job-intent gate. "taxonomy" disables the LLM entirely.
    # This is the rollback lever: flipping to "groq" restores the pre-migration
    # behaviour without a deploy, which matters because this gate decides what
    # enters the queue at all.
    intent_gate_provider: Literal["deepseek", "groq", "taxonomy"] = "deepseek"
    intent_gate_model: str = ""            # "" -> deepseek_model_fast
    # Reasoning-effort ladder, attempted in order; first success wins. Comma-separated
    # so it is env-overridable, and its length is the hard attempt cap.
    #
    # Ships as a single no-thinking rung: escalation is INERT until the agreement
    # harness shows it earns its cost. Candidate once measured: "disabled,high".
    #
    # This is the escalation axis that actually works. The role-manifest ladder escalates
    # on `temperature`, which thinking mode silently ignores - reasoning effort is
    # honoured, so it replaces a dead dimension rather than adding a new one.
    intent_gate_effort_ladder: str = "disabled"
    # Escalate when the model returns valid JSON but disagrees with the rules taxonomy.
    # Hard failures always escalate; this covers the confident-but-ambiguous case, which
    # is where the real misclassifications live. Off until measured.
    intent_gate_escalate_on_disagreement: bool = False
    # Skip the LLM when the rules taxonomy is at least this confident.
    # 0.0 preserves today's always-call behaviour; raise only on measured agreement.
    intent_gate_min_taxonomy_confidence: float = 0.0
    intent_gate_timeout_seconds: float = 12.0   # total budget per email, not per attempt
    role_manifest_final_rung: Literal["deepseek_pro", "groq", "off"] = "deepseek_pro"
    role_manifest_groq_model: str = "llama-3.1-8b-instant"
    role_manifest_extraction_passes_deterministic: int = 1
    role_manifest_extraction_passes_variance: int = 2
    role_manifest_retry_temperature: float = 0.4
    role_manifest_max_tokens_groq: int = 1600
    role_manifest_max_calls_per_email: int = 12
    role_manifest_max_source_chars: int = 12000
    ollama_base_url: str = "http://localhost:11434"
    ollama_chat_model: str = "gemma4:31b-cloud"
    ollama_chat_model_fallback: str = "minimax-m3:cloud"
    ollama_chat_model_fallback2: str = "nemotron-3-nano:30b-cloud"
    ollama_timeout_seconds: float = 60.0
    ollama_max_tool_iterations: int = 6
    feature_chat_enabled: bool = False
    feature_chat_actions_enabled: bool = False
    chat_history_max_messages: int = 20
    chat_message_char_limit: int = 4000
    searxng_url: str = Field(default="", validation_alias=AliasChoices("SEARXNG_URL"))
    chat_web_search_max_results: int = 5
    google_client_id: str = ""
    google_client_secret: str = ""
    google_redirect_uri: str = "http://localhost:8080/"
    google_token_path: str = "./data/google_token.json"
    gmail_label_filter: str = ""
    google_login_hint: str = ""
    public_base_url: str = ""
    tracking_secret_key: str = ""
    google_sheets_tracking_enabled: bool = False
    google_sheets_tracking_spreadsheet_id: str = "1F73Iax75j2rGGb53GGqTmAkEK0o19nmg"
    google_sheets_tracking_tab_name: str = "Sheet1"
    owner_id: str = "default-owner"
    resume_storage_dir: str = "./data/resumes"
    attachment_storage_dir: str = "./data/attachments"
    chat_attachment_storage_dir: str = "./data/chat-attachments"
    chat_attachment_max_bytes: int = 10 * 1024 * 1024
    chat_attachment_max_extract_chars: int = 20000
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
    nvoids_detail_connect_timeout_seconds: float = 10.0
    nvoids_detail_read_timeout_seconds: float = 45.0
    nvoids_detail_write_timeout_seconds: float = 10.0
    nvoids_detail_pool_timeout_seconds: float = 10.0
    nvoids_detail_retry_attempts: int = 3
    nvoids_detail_retry_backoff_seconds: str = "2,5,10"
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
    github_token: str = Field(default="", validation_alias=AliasChoices("GITHUB_TOKEN"))
    github_repo: str = Field(default="", validation_alias=AliasChoices("GITHUB_REPO"))
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
