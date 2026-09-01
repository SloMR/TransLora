# syntax=docker/dockerfile:1.7

# Pinned by digest so a moved tag cannot change what ships.
ARG PYTHON_IMAGE=python:3.12-alpine@sha256:d09d15e60962ca365d1cd544a48773bac9d33f2fb1b00f2aa0deec78ade7dc31

# requirements.txt carries ranges; exporting uv.lock here installs the hashed pins
# without checking a second pin list into the repo.
FROM ${PYTHON_IMAGE} AS deps
COPY --from=ghcr.io/astral-sh/uv:0.11.28-alpine@sha256:f77e7329f8eefce966133052b64ae62a483908dc9784194c3ce75c2b6321e0f0 \
     /usr/local/bin/uv /usr/local/bin/uv
WORKDIR /src
COPY pyproject.toml uv.lock ./
RUN uv export --frozen --no-dev --no-emit-project -o /requirements.lock

FROM ${PYTHON_IMAGE} AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUTF8=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Install deps first so source-only edits don't bust the pip cache layer.
# All deps are pure-Python wheels — alpine works without compilers.
COPY --from=deps /requirements.lock ./requirements.lock
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --no-compile --no-deps --require-hashes -r requirements.lock \
 && rm requirements.lock \
 && find /usr/local/lib/python3.12 -name '__pycache__' -type d -exec rm -rf {} + \
 && find /usr/local/lib/python3.12 -name 'tests' -type d -exec rm -rf {} +

COPY core ./core
COPY translora.py ./translora.py

# Runs as uid 1000 so bind-mount output is not root-owned; pass --user to override.
RUN addgroup -g 1000 -S app && adduser -u 1000 -S app -G app

# Subtitle files live in the user's mount, not in the image.
WORKDIR /work
USER app
ENTRYPOINT ["python", "/app/translora.py"]
