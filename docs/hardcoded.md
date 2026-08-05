<!-- markdownlint-configure-file {"MD013": false} -->

# Hardcoded Values and Constants Review

## Deployment-Sensitive Defaults

| Location | Value | Impact |
| --- | --- | --- |
| `backend/app/main.py` | `allow_origins=["*"]` | permissive CORS default |
| `backend/app/config.py` | localhost-style OAuth redirect fallback | non-prod default can leak into deployment if env is incomplete |
| `backend/app/config.py` | owner/project fallback values | single-owner assumptions unless overridden |

## Workflow Constants With Behavioral Impact

| Location | Constant class | Runtime effect |
| --- | --- | --- |
| `backend/app/premium_numbers/extraction.py` | SBERT prototypes and `SBERT_MARGIN_THRESHOLD=0.12` | determines keep/drop threshold for fallback extraction candidates |
| `backend/app/premium_numbers/extraction.py` | noise/context token lists and regex guards | blocks unsubscribe/footer numeric noise from becoming leads |
| `backend/app/main.py` | analytics weight map and range options | influences trend scoring and dashboard KPI buckets |
| `backend/app/query_bucket/service.py` | saved-query dedupe and limit constraints | caps and normalizes query bucket persistence |

## Current Drift Risks

- Policy defaults split across backend and frontend can drift.
- Hardcoded permissive defaults can be accidentally promoted to higher environments.
- Extraction heuristics and threshold constants can change behavior without API-shape changes.

Mermaid not needed: this update is a constants inventory and risk note only.

- Audit date: 2026-08-05
- Branch: semantic-embeddings
- Commit: cb68a92737671f5db2546c380205327b4f082b68
- Evidence basis: code inspection
- Verification limits: no constant-specific regression suite was run; the focused premium-number batch has one unrelated re-extraction failure.
