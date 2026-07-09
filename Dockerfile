# Read-only API image (Hugging Face Spaces Docker; fallback Render). The dashboard is a
# separate Streamlit Community Cloud app that talks to this over HTTP.
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy

# Dependencies first (cached across code changes); no dev/dashboard groups.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY src ./src
RUN uv sync --frozen --no-dev

# HF Spaces routes to 7860 by default; DATABASE_URL_RO is injected as a Space secret.
ENV PORT=7860
EXPOSE 7860
CMD uv run uvicorn src.api.main:app --host 0.0.0.0 --port ${PORT}
