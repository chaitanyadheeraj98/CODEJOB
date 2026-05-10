# Hardcoded Values Audit

This audit focuses on runtime source files in `backend/app`, `dashboard/src`, container config, and key project config files.

## Legend
- **Keep hardcoded:** acceptable static value or UX label.
- **Move to config/constants/env:** should be externally configurable or centralized.
- **Sensitive:** should not live in source defaults.

## 1. Backend hardcoded values

| File | Line(s) | Hardcoded value | Purpose / Usage | Recommendation |
|---|---:|---|---|---|
| `backend/app/config.py` | 6 | `CodeJob Email Automation API` | FastAPI app title default | Move to env/config |
| `backend/app/config.py` | 7 | `dev` | default environment mode | Move to env/config |
| `backend/app/config.py` | 10 | `sqlite:///./data/codejob.db` | default DB connection | Keep for local dev; override in env for prod |
| `backend/app/config.py` | 11 | `redis://localhost:6379/0` | default Redis URL | Move to env for deployment |
| `backend/app/config.py` | 14 | `https://api.deepseek.com` | DeepSeek base URL | Keep as default constant |
| `backend/app/config.py` | 15 | `deepseek-chat` | default AI model | Move to env/model config |
| `backend/app/config.py` | 20 | `http://localhost:8080/` | Google OAuth redirect URI | Move to environment |
| `backend/app/config.py` | 21 | `./data/google_token.json` | token file path | Move to env/config |
| `backend/app/config.py` | 25 | `1F73Iax75j2rGGb53GGqTmAkEK0o19nmg` | Google Sheet ID default | Sensitive-ish; move to env/secret store |
| `backend/app/config.py` | 26 | `Sheet1` | Sheets tab name default | Keep as configurable default |
| `backend/app/config.py` | 27 | `default-owner` | owner partition key default | Move to env/tenant config |
| `backend/app/config.py` | 29 | `0.6` | qualification threshold default | Keep in config/constants |
| `backend/app/phase0.py` | 6-21 | skill keyword list | rule-based skill extraction/scoring | Keep in central constants module |
| `backend/app/phase0.py` | 23-30 | recruiter hint list | recruiter-like mail detection | Keep in central constants module |
| `backend/app/phase0.py` | 35 | employer domains set | routing logic for CC classification | Move to configurable allowlist |
| `backend/app/phase0.py` | 382-384 | `Chaithanya Dheeraj N`, `+1 940-629-6920`, `chaithanyadheeraj1026@gmail.com` | default signature identity | **Sensitive**; move to user settings/env only |
| `backend/app/phase0.py` | 386-401 | multi-line fallback template content | default outbound email template | Keep template externalized in config/template file |
| `backend/app/phase0.py` | 417 | `Visa: H1B`, `Dallas, TX` | injected requested details block | Move to per-user profile/config |
| `backend/app/phase0.py` | 520-522 | fixed sign-off identity lines | fallback draft body | **Sensitive**; remove from code defaults |
| `backend/app/main.py` | 137-140 | CORS `*` for origins/methods/headers | permissive cross-origin policy | Tighten via environment-based allowlist |
| `backend/app/main.py` | 275 | `is:unread in:inbox recruiter` | default Gmail query fallback | Keep in constants/config |
| `backend/app/main.py` | 671-672 | `is:unread in:inbox recruiter` | seed default settings | Keep in constants/config |
| `backend/app/main.py` | 704-723 | default dynamic policy object | base policy defaults | Keep in policy constants file |
| `backend/app/main.py` | 726-771 | Aggressive/Balanced/Strict profile literals | profile presets | Keep centralized constants; maybe DB-driven |
| `backend/app/main.py` | 1492-1531 | OAuth status/detail text strings | user-facing status messages | Keep as constants for i18n readiness |
| `backend/app/main.py` | 1669 | `Could not resolve recruiter To and employer CC` | failed routing error reason | Keep constant/shared enum |
| `backend/app/main.py` | 2088-2104 | manual routing evidence JSON literals | audited correction evidence | Keep as constant helpers |
| `backend/app/gmail_client.py` | 21 | Gmail scopes list | API permissions | Keep hardcoded (expected for OAuth scopes) |
| `backend/app/gmail_client.py` | 64-65 | Google OAuth auth/token URIs | OAuth endpoints | Keep hardcoded (provider constants) |
| `backend/app/gmail_client.py` | 105-109 | localhost bind + port 8080 auth prompt | local OAuth callback server | Move host/port/message to config |
| `backend/app/gmail_client.py` | 332 | `Re: ` prefix | email subject normalization | Keep hardcoded |
| `backend/app/gmail_client.py` | 359 | `UNREAD` label removal | Gmail post-process behavior | Keep hardcoded constant |
| `backend/app/gmail_client.py` | 470 | `Sheet1` fallback tab | default sheets tab | Keep as configurable default |
| `backend/app/telegram_bot.py` | 91 | `https://api.telegram.org/bot...` | Telegram API base URL | Keep provider constant |
| `backend/app/telegram_bot.py` | 114 | text truncation `3900` | Telegram message size guard | Keep in named constant |
| `backend/app/telegram_bot.py` | 123 | polling timeout `30` | Telegram long polling | Move to config constant |

## 2. Frontend hardcoded values

| File | Line(s) | Hardcoded value | Purpose / Usage | Recommendation |
|---|---:|---|---|---|
| `dashboard/src/App.tsx` | 199 | `http://localhost:8000` | fallback API base URL | Keep as local default; prefer env always |
| `dashboard/src/App.tsx` | 197-198 | `QUEUE_LIMIT=100`, `RECENT_RUNS_LIMIT=100` | pagination/history bounds | Move to constants file |
| `dashboard/src/App.tsx` | 248-249 | `is:unread` defaults | initial query defaults | Keep in shared constants with backend parity |
| `dashboard/src/App.tsx` | 340 | `is:unread` fallback normalization | settings fallback | Keep constant/shared config |
| `dashboard/src/App.tsx` | 469 | `90000` / `45000` timeout values | run request timeout behavior | Move to constants/env |
| `dashboard/src/App.tsx` | 566 | `Rejected by user before send` | reject reason text | Keep as shared constant |
| `dashboard/src/App.tsx` | 717-726 | `Batch Queue`, `Sync Now`, `Connect Gmail` labels | CTA labels | Keep in UI constants (optional i18n prep) |
| `dashboard/src/App.tsx` | 757 | `Run Queue Dashboard` | page heading | Keep as static UI label |
| `dashboard/src/App.tsx` | 985 | `is:unread in:inbox recruiter` placeholder | user hint for query | Keep as static helper text |
| `dashboard/src/App.tsx` | 1117-1122 | fallback template token strings | helper text for token usage | Keep hardcoded docs text |
| `dashboard/src/features/ai/ui.ts` | 5-7 | `DeepSeek`, `Rules fallback`, `Unknown` | draft source display mapping | Keep in UI constants |
| `dashboard/src/components/Sidebar.tsx` | 39-45 | `CodeJob MailOps`, `Recruitment Ops`, `New Campaign` | branding and CTA copy | Keep hardcoded unless white-labeling needed |

## 3. Styling/UI token hardcodes

| File | Line(s) | Hardcoded value | Purpose / Usage | Recommendation |
|---|---:|---|---|---|
| `dashboard/src/App.css` | 2-16 | color hex variables | app-wide design tokens | Keep (expected in CSS variables) |
| `dashboard/src/App.css` | 20 | sidebar width `326px` | layout sizing | Keep; move to design token if theme system grows |
| `dashboard/src/App.css` | multiple | fixed font sizes (e.g., `58px`, `44px`) | typography scale | Keep or centralize if introducing design system |
| `dashboard/src/index.css` | 1 | Google Fonts URL for Geist | typography import | Keep if CDN policy allows; otherwise self-host |

## 4. Infrastructure/config hardcodes

| File | Line(s) | Hardcoded value | Purpose / Usage | Recommendation |
|---|---:|---|---|---|
| `docker-compose.yml` | 9-10 | DB/Redis URLs | container env defaults | Keep for local dev; override per environment |
| `docker-compose.yml` | 12-13, 27, 35 | exposed ports 8000/8080/5173/6379 | local networking | Keep as compose defaults |
| `docker-compose.yml` | 25 | `VITE_API_BASE_URL=http://localhost:8000` | frontend-backend local link | Keep local dev default |
| `backend/main.py` | 5 | host `0.0.0.0`, port `8000`, `reload=True` | local run command | Keep for dev entrypoint |
| `dashboard/.env.example` | 1 | `VITE_API_BASE_URL=http://localhost:8000` | documented env default | Keep |

## 5. Sensitive and high-priority cleanup items

1. **Personal identity defaults in `phase0.py` (name/phone/email/visa/location).**
2. **Default Google Sheet ID in `config.py`.**
3. **Wildcard CORS policy in `main.py`.**

These should be moved to environment/configuration and tightened for production usage.
