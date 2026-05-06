from __future__ import annotations

from openai import OpenAI

from app.config import settings


def deepseek_chat_completion(system_prompt: str, user_prompt: str) -> str:
    if not settings.deepseek_api_key:
        raise RuntimeError("DeepSeek API key is missing")

    client = OpenAI(
        api_key=settings.deepseek_api_key,
        base_url=settings.deepseek_base_url,
        timeout=settings.deepseek_timeout_seconds,
    )
    response = client.chat.completions.create(
        model=settings.deepseek_model_fast or "deepseek-chat",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.35,
        max_tokens=550,
    )
    content = response.choices[0].message.content if response.choices else ""
    if not content:
        raise RuntimeError("DeepSeek returned empty content")
    return content.strip()
