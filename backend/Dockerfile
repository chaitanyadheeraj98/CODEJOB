# syntax=docker/dockerfile:1
FROM python:3.12.11-slim@sha256:47ae396f09c1303b8653019811a8498470603d7ffefc29cb07c88f1f8cb3d19f

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 \
    libmagic1 \
    libreoffice \
    && rm -rf /var/lib/apt/lists/*

# Dependency manifests only, and deliberately before `COPY . .` below: this
# layer is the expensive one (212 packages, including torch and the CUDA stack),
# so it must not be invalidated by an ordinary source edit. Touching
# pyproject.toml or uv.lock rebuilds it; nothing else does.
COPY pyproject.toml uv.lock ./
# `uv pip install .` ignores uv.lock - it is the pip-compatible interface and
# re-resolves all 212 packages from pyproject.toml on every rebuild. The lock
# was being copied into the image and never read. Exporting it first pins every
# version to the one already resolved on the host, so a rebuild installs a known
# answer instead of solving for it again.
#
# --no-emit-project: the app runs from /app via WORKDIR, not as an installed
# package (nothing imports `backend`), so only its dependencies are needed here.
# uv itself is pinned because an unpinned build tool is an unpinned dependency.
RUN --mount=type=cache,target=/root/.cache/uv \
    pip install --no-cache-dir uv==0.11.8 \
    && uv export --frozen --no-dev --no-emit-project --no-hashes \
        --format requirements-txt -o /tmp/requirements.txt \
    && uv pip install --system -r /tmp/requirements.txt
RUN python -c "from unstructured.nlp.tokenize import _load_spacy_model; _load_spacy_model()"

COPY . .
RUN mkdir -p /app/data

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
