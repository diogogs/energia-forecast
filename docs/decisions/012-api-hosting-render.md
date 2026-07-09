# ADR-012: Host the API on Render, not Hugging Face Spaces (zero-cost constraint)

**Date:** 2026-07-09 · **Status:** accepted · **Supersedes** the serving-host part of the charter's HF-Spaces plan.

## Context

The charter placed the read-only API on **Hugging Face Spaces (Docker)**, with Render as a
fallback. On deploy we hit a hard gate: HF now returns

> "Static Spaces are free for everyone, but hosting Gradio and Docker Spaces on free cpu-basic
> requires a PRO subscription."

So a Docker Space costs $9/mo. That breaks principle #5 — **custo zero** (zero-cost, all free
tiers). Static Spaces are free but cannot run a FastAPI backend.

## Decision

Host the API on **Render** (a free Docker web service), the charter's own fallback:

- `render.yaml` blueprint → `runtime: docker`, `dockerfilePath: ./Dockerfile`, `plan: free`,
  `healthCheckPath: /health`, `autoDeploy: true`, and `DATABASE_URL_RO` as the one manual env var.
- The existing `Dockerfile` already honours `$PORT`, which Render injects — no image change.
- Render watches the GitHub repo and redeploys on push, so no deploy workflow is needed (the
  HF `deploy-hf.yml` + `deploy_hf.py` automation is removed).
- The dashboard stays on **Streamlit Community Cloud** (free, unaffected).

## Consequences

- **Zero cost preserved.** Render's free web service spins down after ~15 min idle (a cold
  start on the next request) — acceptable for a portfolio dashboard; the daily crons and Neon
  are unaffected (serving is stateless, all state in Neon).
- One-time setup is simpler than HF (connect repo → blueprint → paste `DATABASE_URL_RO`); no
  HF token or Space secret to manage.
- If HF PRO is ever desired for the portfolio showcase, the same `Dockerfile` deploys there
  unchanged — this ADR only moves the *default* free host.
