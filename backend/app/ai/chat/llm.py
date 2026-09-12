from langchain_ollama import ChatOllama

from app import tenancy
from app.config import settings
from app.db import session_scope
from app.services.provider_credential_service import require_credentials


def build_chat_llm(model: str | None = None, timeout: float | None = None) -> ChatOllama:
    with session_scope() as db:
        credentials = require_credentials(db, tenancy.owner_id(), "ollama")
    return ChatOllama(
        base_url=(credentials.base_url or settings.ollama_base_url).rstrip("/"),
        model=model or settings.ollama_chat_model,
        client_kwargs={
            "timeout": min(settings.ollama_timeout_seconds, timeout)
            if timeout is not None
            else settings.ollama_timeout_seconds,
            "headers": {"Authorization": f"Bearer {credentials.api_key}"},
        },
    )
