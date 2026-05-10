# Hardcoded Values Inventory

This document lists hardcoded values identified across backend, frontend, infra, and scripts.

## Recommendation legend
- **Keep**: Reasonable to remain hardcoded in code/tests.
- **Move to config/constants**: Better centralized in env/config/constants.
- **Sensitive — move immediately**: Personal/credential-like values should never be committed.

## 1) Backend hardcoded values

| File | Line | Hardcoded value | Purpose/usage | Recommendation |
|---|---:|---|---|---|
| `backend/app/config.py` | 6 | `"CodeJob Email Automation API"` | API title | Move to env/config |
| `backend/app/config.py` | 7 | `"dev"` | Default runtime env | Move to env |
| `backend/app/config.py` | 10 | `sqlite:///./data/codejob.db` | Default DB URL | Keep for local default; override in env for non-local |
| `backend/app/config.py` | 11 | `redis://localhost:6379/0` | Default Redis URL | Move to env for deployment flexibility |
| `backend/app/config.py` | 14 | `https://api.deepseek.com` | AI provider base URL | Move to env |
| `backend/app/config.py` | 15 | `deepseek-chat` | AI model default | Move to constants/env |
| `backend/app/config.py` | 20 | `http://localhost:8080/` | OAuth redirect URI default | Move to env |
| `backend/app/config.py` | 25 | `1F73Iax75j2rGGb53GGqTmAkEK0o19nmg` | Default Google Sheets ID | Move to env (project-specific) |
| `backend/app/config.py` | 27 | `default-owner` | Default owner_id | Move to env or per-user identity source |
| `backend/app/models.py` | 70,75 | Gmail URL templates | Direct deep-link generation | Keep as provider URL constant |
| `backend/app/models.py` | 84-85 | `is:unread in:inbox recruiter` | Default Gmail query | Move to constants/settings seed |
| `backend/app/phase0.py` | 6-21 | `SKILL_KEYWORDS` list | Rule-based keyword scoring | Keep in dedicated constants module |
| `backend/app/phase0.py` | 23-30 | `RECRUITER_HINTS` list | Recruiter detection heuristics | Keep in dedicated constants module |
| `backend/app/phase0.py` | 35 | `EMPLOYER_DOMAINS = {...}` | Domain routing rule | Move to configurable list |
| `backend/app/phase0.py` | 382-384 | Name/phone/email signature defaults | Fallback signature | Sensitive — move immediately to env/user settings |
| `backend/app/phase0.py` | 386+ | `DEFAULT_FALLBACK_DRAFT_TEMPLATE` | Default fallback email body | Move to template file/config seed |
| `backend/app/gmail_client.py` | 21-22 | Gmail/Sheets scopes | OAuth permissions | Keep as integration constants |
| `backend/app/gmail_client.py` | 64-65 | Google auth/token URIs | OAuth endpoint constants | Keep as integration constants |
| `backend/app/gmail_client.py` | 470 | `Sheet1` | Default tab name | Move to env/settings |
| `backend/app/main.py` | 141-144 | CORS `allow_origins=["*"]` | CORS policy | Move to env and tighten in production |
| `backend/app/main.py` | 167-168 | range/bucket option sets | Analytics query constraints | Keep as domain constants |
| `backend/app/main.py` | 812-813 | `is:unread in:inbox recruiter` | Seed default query | Move to shared constant |
| `backend/app/main.py` | 846+ | default policy object values | Baseline run/query/qualification behavior | Keep in policy constants module |
| `backend/app/main.py` | 867+ | profile values Aggressive/Balanced/Strict | Preset policy behavior | Keep, but centralize to policy config module |
| `backend/app/main.py` | 742 | `"Rejected from Telegram"` | Default reject reason | Keep as UX default constant |

## 2) AI prompt and generation hardcoded values

| File | Line | Hardcoded value | Purpose/usage | Recommendation |
|---|---:|---|---|---|
| `backend/app/ai/prompting.py` | 18-76 | Full system + user prompt template | AI drafting instruction contract | Keep template, but externalize to versioned prompt files |
| `backend/app/ai/prompting.py` | 39-45 | `Visa: H1B`, `Dallas, TX`, personal signature | Injected candidate identity details | Sensitive — move to profile settings/env |
| `backend/app/ai/deepseek_client.py` | 23-24 | `temperature=0.35`, `max_tokens=550` | AI generation parameters | Move to model config settings |

## 3) Frontend hardcoded values

| File | Line | Hardcoded value | Purpose/usage | Recommendation |
|---|---:|---|---|---|
| `dashboard/src/App.tsx` | 231 | `http://localhost:8000` | API base URL fallback | Keep for local default; override by env |
| `dashboard/src/App.tsx` | 229-230 | `QUEUE_LIMIT=100`, `RECENT_RUNS_LIMIT=100` | UI fetch/log limits | Move to constants module |
| `dashboard/src/App.tsx` | 232+ | `defaultPolicy` and profile literals | Frontend policy defaults | Keep, but centralize in policy constants |
| `dashboard/src/App.tsx` | 551 | `90000`/`45000` timeoutMs | Run timeout limits | Move to constants/env |
| `dashboard/src/App.tsx` | 650 | `Rejected by user before send` | Reject reason text | Keep as UX text constant |
| `dashboard/src/components/Sidebar.tsx` | 44 | `New Campaign` button label | Static UI label | Keep |
| `dashboard/src/index.css` | 1 | Google Fonts URL | External font source | Keep (or self-host for controlled builds) |
| `dashboard/src/App.css` | many | Hex colors and sizing values | Design tokens/layout constants | Keep; already tokenized mostly |

## 4) Infrastructure hardcoded values

| File | Line | Hardcoded value | Purpose/usage | Recommendation |
|---|---:|---|---|---|
| `docker-compose.yml` | 9 | `sqlite:///./data/codejob.db` | Backend DB env for container | Keep local default; override per env |
| `docker-compose.yml` | 10 | `redis://redis:6379/0` | Backend Redis env | Keep for compose-local |
| `docker-compose.yml` | 12-13, 27, 35 | Port mappings (`8000`, `8080`, `5173`, `6379`) | Local service exposure | Keep for local dev |
| `docker-compose.yml` | 25 | `http://localhost:8000` | Frontend API env | Keep local default |
| `backend/Dockerfile` | 16 | Uvicorn host/port command | Runtime launch | Keep |
| `dashboard/Dockerfile` | 12 | Vite host/port command | Dev server launch | Keep |

## 5) Test hardcoded values

| File | Line | Hardcoded value | Purpose/usage | Recommendation |
|---|---:|---|---|---|
| `backend/tests/test_phase0_routing.py` | 14+ | fixed email fixture text and addresses | deterministic routing behavior tests | Keep |
| `backend/tests/test_prompting.py` | 9-19 | static prompt inputs | contract assertions | Keep |
| `backend/tests/test_schemas.py` | 15+ | static sample payload fields | schema parsing assertions | Keep |
| `backend/tests/test_productivity_trend.py` | 15 | `trend-test-<uuid>` owner prefix | isolated test data | Keep |

## 6) Potentially sensitive values found

The following placeholders represent literal personal/profile values currently hardcoded in source and prompt text (intentionally redacted here):

- `[USER_NAME]`
- `[USER_PHONE]`
- `[USER_EMAIL]`
- `[USER_VISA_STATUS]`
- `[USER_LOCATION]`

**Recommendation:** Move to user/profile configuration persisted in DB or injected via environment and never hardcode personal identity details in source.
