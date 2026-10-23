# The llama-swap capture harvester (spec 0008): this repository's package on a
# digest-pinned Python base, run as `agentic-telemetry captures-pull` by the
# `captures-pull` service in collector/compose.yaml. Build context: the
# repository root, narrowed by the root .dockerignore to pyproject.toml, uv.lock
# and src/. Index digests read 2026-10-22 with `docker buildx imagetools inspect`;
# see collector/README.md -> Images.
FROM python:3.12-slim-bookworm@sha256:6a0e06f5ba0b373d051aabd3fffb9b873f27f9a2a3a14bcbe37dee6f0e3bbc70

# uv 0.13.1 (released 2026-10-14; digest read 2026-10-22): inside pyproject.toml's
# uv_build>=0.13.1,<0.14, so uv builds the package with its own backend and downloads
# none. Only its binary is copied.
COPY --from=ghcr.io/astral-sh/uv:0.13.1@sha256:4a1f3666816dafadd20ceb71eac9a7d56d7ef30279f7d64048519ab901ada336 /uv /usr/local/bin/uv

ENV PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app
# Dependencies first, so a source-only change reuses this layer.
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-editable --no-install-project
COPY src ./src
RUN uv sync --locked --no-dev --no-editable

USER 10001
ENTRYPOINT ["/opt/venv/bin/agentic-telemetry"]
