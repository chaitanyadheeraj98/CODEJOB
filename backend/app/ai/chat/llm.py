from langchain_ollama import ChatOllama

from app.config import settings


def build_chat_llm(model: str | None = None) -> ChatOllama:
    return ChatOllama(
        base_url=settings.ollama_base_url.rstrip("/"),
        model=model or settings.ollama_chat_model,
        client_kwargs={"timeout": settings.ollama_timeout_seconds},
    )
