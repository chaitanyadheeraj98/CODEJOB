from __future__ import annotations

from app.ai.deepseek_client import deepseek_chat_completion
from app.gmail_labeling.rules import ALLOWED_LABELS


def classify_label_with_ai(*, sender: str, subject: str, body: str) -> str:
    system_prompt = (
        "You are a strict email label classifier. "
        "Return exactly one label name from the allowed set and nothing else."
    )
    user_prompt = f"""
Allowed labels:
{", ".join(ALLOWED_LABELS)}

Sender: {sender}
Subject: {subject}
Body:
{body}

Return exactly one allowed label.
""".strip()
    response = deepseek_chat_completion(system_prompt, user_prompt).strip()
    normalized = response.lower()
    for label in ALLOWED_LABELS:
        if normalized == label.lower():
            return label
    for label in ALLOWED_LABELS:
        if label.lower() in normalized:
            return label
    return "screening"

