### Problem: Frontend fetch fails with 500 Internal Server Error when hitting `/automation/run-once` in Docker setup

## 1. High-level overview

The frontend “fetch” is failing because the backend API call is returning **500 Internal Server Error**, not because Docker networking is broken.

Your logs show:

`POST /automation/run-once HTTP/1.1" 500 Internal Server Error`

Then the real backend crash is:

`TypeError: _select_best_resume_match() got an unexpected keyword argument 'resumes'`

## 2. Repository structure checked

I checked the Docker files on the `semantic-embeddings` branch:

* `docker-compose.yml`
* `backend/Dockerfile`
* `dashboard/Dockerfile`
* backend scoring/resume-selection flow around `run_orchestrator.py`
* backend wrapper in `main.py`

## 3. Main technologies used

The Docker setup runs:

* FastAPI backend on port `8000`
* Vite React dashboard on port `5173`
* Redis on port `6379`
* SQLite DB stored in Docker volume `backend_data`

The compose file maps backend `8000:8000`, dashboard `5173:5173`, and sets `VITE_API_BASE_URL: http://localhost:8000`, so the frontend is pointed at the correct backend URL.

## 4. Core features involved

The failing feature is **Sync + Queue / fetch automation**, triggered by:

```txt
POST /automation/run-once
```

This enters:

```txt
main.py
→ OrchestrationService
→ RunOrchestrator.execute()
→ select_best_resume_match()
```

## 5. Architecture and code flow

The bug is here:

`run_orchestrator.py` calls:

```python
request.deps.select_best_resume_match(
    subject=subject,
    body=body,
    parsed=parsed_for_selection,
    user_settings=request.user_settings,
    email_row=existing,
    resumes=request.enabled_resumes,
    fallback_resume=request.active_resume,
    db=request.db,
    owner_id=request.owner_id,
    external_thread_id=...
)
```

So it sends two keywords:

```txt
resumes
fallback_resume
```

But in `main.py`, `_select_best_resume_match()` is defined without those parameters:

```python
def _select_best_resume_match(
    *,
    subject,
    body,
    parsed,
    user_settings,
    email_row,
    db,
    owner_id,
    external_thread_id,
)
```

Inside that wrapper, it separately calls `_enabled_resumes(db)` and `_active_resume(db)` itself. So when the orchestrator passes `resumes=...`, Python throws:

```txt
TypeError: _select_best_resume_match() got an unexpected keyword argument 'resumes'
```

That exactly matches your Docker log crash.  

## 6. Important files and folders

### `docker-compose.yml`

The backend is built from `./backend`, loads `./backend/.env`, uses SQLite at `./data/codejob.db`, and persists `/app/data` into `backend_data`.

Important point: there is **no backend source-code bind mount**. The backend code is copied into the Docker image during build.

### `backend/Dockerfile`

The backend Dockerfile installs dependencies from `pyproject.toml`, then copies the backend code into `/app`.

That means after changing Python code, the container must be rebuilt. But in this case, even the branch code itself has the signature mismatch, so rebuild alone would not fix the underlying bug.

### `dashboard/Dockerfile`

The dashboard runs Vite dev server on `0.0.0.0:5173`, which is correct for Docker browser access.

## 7. How the application likely runs

The app starts correctly:

* Alembic runs.
* Uvicorn starts.
* `/gmail/status`, `/ai/status`, `/settings`, `/settings/resumes`, `/candidates`, etc. return `200 OK`.

So Docker networking is working.

The failure happens only when the dashboard triggers `/automation/run-once`. The backend crashes during resume selection before the automation run can complete.

## 8. Key observations for you

This is **not mainly a Docker-file issue**.

The real issue is a **code contract mismatch** between:

```txt
backend/app/automation/run_orchestrator.py
```

and

```txt
backend/app/main.py
```

`run_orchestrator.py` was updated to pass `resumes` and `fallback_resume`, but `main.py` wrapper was not updated to accept those keyword arguments.

Also, the Docker setup explains why this can be confusing: since backend code is copied into the image and not mounted live, local code changes will not appear unless the backend image is rebuilt. But the current branch still has the mismatch, so the fetch fails because `/automation/run-once` crashes with 500.

Solution:

### Solution

The backend did not fully crash. The `Resume Database` routes are up right now, and `GET /settings/resumes` is returning `200`. The failure is in the queue run path, not the settings/resume UI path.

What the logs show:

* `POST /automation/run-once` is failing with `500 Internal Server Error`
* the exception is:

```text
TypeError: _select_best_resume_match() got an unexpected keyword argument 'resumes'
```

Where it breaks:

* [run_orchestrator.py](/D:/My%20Websites/CodeJob/backend/app/automation/run_orchestrator.py:119) calls `request.deps.select_best_resume_match(...)` with `resumes=` and `fallback_resume=`
* that dependency points to the lambda in [main.py](/D:/My%20Websites/CodeJob/backend/app/main.py:690)
* the wrapper function in [main.py](/D:/My%20Websites/CodeJob/backend/app/main.py:859) still has the older signature and does not accept `resumes` or `fallback_resume`

So this is a signature mismatch introduced by the multi-resume resume-selection work:

* the orchestrator now expects the newer contract
* the `main.py` wrapper still exposes the old contract

Why the UI says `Failed to fetch`:

* the frontend `runAutomation()` in [App.tsx](/D:/My%20Websites/CodeJob/dashboard/src/App.tsx:1388) is calling `/automation/run-once`
* that backend request blows up before a normal success response
* so the dashboard only surfaces a generic network/request failure instead of the real traceback

Important clarification:

* this is not the `Resume Database` card crashing the backend
* it is the `Sync Now` / `Batch Queue` backend path using the broken resume-match dependency wiring

Best solution:

* update `_select_best_resume_match(...)` in [main.py](/D:/My%20Websites/CodeJob/backend/app/main.py:859) to accept the newer optional kwargs:
  * `resumes`
  * `fallback_resume`
* then forward those directly to `scoring_runtime_service.select_best_resume_match(...)`
* keep backward compatibility by defaulting to `_enabled_resumes(db)` and `_active_resume(db)` if those args are not supplied

That is the safest fix because:

* it matches the orchestrator’s current call shape
* it preserves existing callers
* it keeps the multi-resume design intact instead of reverting newer code to the old single-resume contract
