"""Measure what changes if the intent gate moves providers. Read-only.

Three questions this answers, and nothing else:

1. **Does the candidate provider agree with the incumbent?** A gate verdict decides
   what enters the queue at all, so a migration that quietly shifts verdicts changes
   the product. Measure it against stored history; do not infer it.
2. **Where is the rules taxonomy already right?** `intent_gate_min_taxonomy_confidence`
   should come from the highest confidence band where taxonomy-vs-model agreement is
   near-total, read off a table - not from intuition.
3. **Does thinking earn its cost?** Same emails, thinking off and on. If the answers
   are identical, the effort ladder stays one rung, and that is a result rather than
   a failure.

Writes nothing: no `--apply`, no session commits, no runtime_state mutation. It
prints classifications and token counts only - never body text, never a key.

Usage:
    python -m scripts.measure_intent_gate_agreement --limit 200
    python -m scripts.measure_intent_gate_agreement --limit 200 --thinking --csv out.csv
"""

from __future__ import annotations

import argparse
import csv
import sys
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.ai.deepseek_client import (
    DeepSeekJSONError,
    DeepSeekJSONResult,
    deepseek_json_completion_with_diagnostics,
)
from app.ai.json_object import validate_schema_subset
from app.config import settings
from app.db import SessionLocal
from app.gates.job_description_gate import GATE_SYSTEM_PROMPT, GROQ_SCHEMA
from app.job_intent_learning import prepare_job_intent_model_text
from app.models import RecruiterEmail
from app.taxonomy.job_description_taxonomy import classify_job_description_taxonomy


@dataclass
class Row:
    email_id: int
    stored_intent: str | None
    stored_provider: str | None
    taxonomy_intent: str
    taxonomy_action: str
    taxonomy_confidence: float
    fast_intent: str = ""
    fast_action: str = ""
    fast_error: str = ""
    fast_prompt_tokens: int | None = None
    fast_completion_tokens: int | None = None
    fast_cache_hit_tokens: int | None = None
    fast_cache_miss_tokens: int | None = None
    thinking_intent: str = ""
    thinking_action: str = ""
    thinking_error: str = ""
    thinking_completion_tokens: int | None = None


def _user_prompt(email: RecruiterEmail, taxonomy) -> str:
    """Deliberately narrower than the gate's own prompt.

    The gate mixes in per-inbox learning signals and trusted-group context that are
    not reconstructible from a stored row. Including a *guessed* version of them
    would make the measurement measure the guess. What stays is the part that is
    recoverable verbatim, so a disagreement here is a real disagreement.
    """
    return (
        f"Sender: {email.sender}\n"
        f"Subject: {email.subject}\n"
        f"Snippet: \n"
        f"Recruiter-like signal: False\n"
        f"Trusted group matched: False\n"
        f"Fallback taxonomy intent: {taxonomy.intent_type}\n"
        f"Fallback taxonomy action: {taxonomy.action}\n"
        f"Fallback taxonomy confidence: {taxonomy.confidence:.2f}\n"
        f"Body:\n{prepare_job_intent_model_text(email.body or '')}\n"
    )


def _ask(
    system_prompt: str, user_prompt: str, thinking: str
) -> tuple[dict[str, object] | None, str, DeepSeekJSONResult | None]:
    try:
        result = deepseek_json_completion_with_diagnostics(
            system_prompt,
            user_prompt,
            model_name=settings.intent_gate_model or settings.deepseek_model_fast,
            timeout_seconds=settings.intent_gate_timeout_seconds,
            max_tokens=500,
            thinking=thinking,  # type: ignore[arg-type]
        )
    except DeepSeekJSONError as exc:
        return None, "empty_content" if not exc.raw_content.strip() else "invalid_json", None
    except Exception as exc:  # noqa: BLE001 - a harness reports failures, it does not raise them
        return None, exc.__class__.__name__, None
    if not validate_schema_subset(result.payload, GROQ_SCHEMA):
        return None, "invalid_shape", result
    return result.payload, "", result


def _confidence_band(confidence: float) -> str:
    return f"{round(confidence, 1):.1f}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--limit", type=int, default=100, help="how many stored emails to replay")
    parser.add_argument("--thinking", action="store_true", help="also run each email with thinking enabled")
    parser.add_argument("--offline", action="store_true", help="taxonomy-vs-stored only; makes no API calls")
    parser.add_argument("--csv", type=Path, default=None, help="write the per-email table here")
    args = parser.parse_args()

    if not args.offline and not settings.deepseek_api_key:
        print("Deepseek_API_KEY is not set. Re-run with --offline for the taxonomy-vs-stored half.")
        return 2

    rows: list[Row] = []
    with SessionLocal() as db:
        emails = (
            db.query(RecruiterEmail)
            .filter(RecruiterEmail.gate_provider.isnot(None))
            .order_by(RecruiterEmail.id.desc())
            .limit(max(1, args.limit))
            .all()
        )
        for email in emails:
            taxonomy = classify_job_description_taxonomy(
                sender=email.sender or "",
                subject=email.subject or "",
                body=email.body or "",
            )
            row = Row(
                email_id=email.id,
                stored_intent=email.intent_type,
                stored_provider=email.gate_provider,
                taxonomy_intent=taxonomy.intent_type,
                taxonomy_action=taxonomy.action,
                taxonomy_confidence=taxonomy.confidence,
            )
            if not args.offline:
                user_prompt = _user_prompt(email, taxonomy)
                payload, error, result = _ask(GATE_SYSTEM_PROMPT, user_prompt, "disabled")
                row.fast_error = error
                if result is not None:
                    row.fast_prompt_tokens = result.prompt_tokens
                    row.fast_completion_tokens = result.completion_tokens
                    # The cache-prefix claim lives or dies on these two numbers: a
                    # hit count that stays at zero across the run means the system
                    # prompt is not actually a stable prefix.
                    row.fast_cache_hit_tokens = result.prompt_cache_hit_tokens
                    row.fast_cache_miss_tokens = result.prompt_cache_miss_tokens
                if payload is not None:
                    row.fast_intent = str(payload.get("intent_type") or "")
                    row.fast_action = str(payload.get("action") or "")
                if args.thinking:
                    payload, error, result = _ask(GATE_SYSTEM_PROMPT, user_prompt, "enabled")
                    row.thinking_error = error
                    if result is not None:
                        row.thinking_completion_tokens = result.completion_tokens
                    if payload is not None:
                        row.thinking_intent = str(payload.get("intent_type") or "")
                        row.thinking_action = str(payload.get("action") or "")
            rows.append(row)

    _report(rows, offline=args.offline, thinking=args.thinking)
    if args.csv:
        with args.csv.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(asdict(rows[0])) if rows else [])
            writer.writeheader()
            writer.writerows(asdict(row) for row in rows)
        print(f"\nPer-email table written to {args.csv}")
    return 0


def _report(rows: list[Row], *, offline: bool, thinking: bool) -> None:
    if not rows:
        print("No rows with a stored gate_provider.")
        return
    print(f"Sampled {len(rows)} emails.\n")

    print("gate_provider distribution (the historical record):")
    for provider, count in Counter(row.stored_provider for row in rows).most_common():
        print(f"  {provider or '(null)':<30} {count}")

    llm_rows = [row for row in rows if (row.stored_provider or "") in {"groq", "deepseek"}]
    if llm_rows:
        bands: dict[str, list[bool]] = defaultdict(list)
        for row in llm_rows:
            bands[_confidence_band(row.taxonomy_confidence)].append(row.taxonomy_intent == row.stored_intent)
        agree = sum(1 for row in llm_rows if row.taxonomy_intent == row.stored_intent)
        print(
            f"\nTaxonomy vs the stored LLM verdict: {agree}/{len(llm_rows)} "
            f"({agree / len(llm_rows):.1%}) agree."
        )
        print("  confidence  agree  disagree  disagree%")
        for band in sorted(bands):
            results = bands[band]
            disagree = results.count(False)
            print(
                f"  {band:<11} {results.count(True):<6} {disagree:<9} "
                f"{disagree / len(results):.1%}"
            )
        print(
            "  -> intent_gate_min_taxonomy_confidence belongs at the highest band whose\n"
            "     disagree% you are willing to accept, weighted by how many rows it covers."
        )

    if offline:
        print("\n--offline: no provider was called, so agreement and thinking are unmeasured.")
        return

    answered = [row for row in rows if row.fast_intent]
    failures = Counter(row.fast_error for row in rows if row.fast_error)
    print(f"\nCandidate provider answered {len(answered)}/{len(rows)}.")
    for error, count in failures.most_common():
        print(f"  failure {error:<24} {count}")
    cache_hits = sum(row.fast_cache_hit_tokens or 0 for row in rows)
    cache_misses = sum(row.fast_cache_miss_tokens or 0 for row in rows)
    if cache_hits or cache_misses:
        total = cache_hits + cache_misses
        print(
            f"Prompt cache: {cache_hits:,} hit / {cache_misses:,} miss tokens "
            f"({cache_hits / total:.1%} hit).\n"
            "  -> a hit rate near zero means the system prompt stopped being a stable prefix."
        )

    comparable = [row for row in answered if row.stored_intent]
    if comparable:
        match = sum(1 for row in comparable if row.fast_intent == row.stored_intent)
        print(
            f"Candidate vs stored verdict: {match}/{len(comparable)} ({match / len(comparable):.1%}) agree.\n"
            "  -> this is the number that says whether the queue's contents change."
        )

    if thinking:
        both = [row for row in rows if row.fast_intent and row.thinking_intent]
        if both:
            same = sum(1 for row in both if row.fast_intent == row.thinking_intent)
            print(
                f"\nThinking on vs off, same emails: {same}/{len(both)} ({same / len(both):.1%}) identical."
            )
            print(
                "  -> near-100% identical means the effort ladder stays one rung.\n"
                "     Only the disagreements can justify paying for a second."
            )
            for row in both:
                if row.fast_intent != row.thinking_intent:
                    print(
                        f"     email {row.email_id}: off={row.fast_intent} on={row.thinking_intent} "
                        f"stored={row.stored_intent} taxonomy={row.taxonomy_intent}"
                    )


if __name__ == "__main__":
    raise SystemExit(main())
