from langchain_ollama import ChatOllama

from app.config import settings


def build_chat_llm(model: str | None = None, timeout: float | None = None) -> ChatOllama:
    return ChatOllama(
        base_url=settings.ollama_base_url.rstrip("/"),
        model=model or settings.ollama_chat_model,
        client_kwargs={"timeout": min(settings.ollama_timeout_seconds, timeout) if timeout is not None else settings.ollama_timeout_seconds},
    )
