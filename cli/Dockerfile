# syntax=docker/dockerfile:1.7

FROM python:3.12-alpine AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUTF8=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Install deps first so source-only edits don't bust the pip cache layer.
# All deps are pure-Python wheels — alpine works without compilers.
COPY requirements.txt ./
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --no-compile -r requirements.txt \
 && find /usr/local/lib/python3.12 -name '__pycache__' -type d -exec rm -rf {} + \
 && find /usr/local/lib/python3.12 -name 'tests' -type d -exec rm -rf {} +

COPY core ./core
COPY translora.py ./translora.py

# Subtitle files live in the user's mount, not in the image.
WORKDIR /work
ENTRYPOINT ["python", "/app/translora.py"]
