# ADR-003: Neon Postgres (not Supabase)

**Date:** 2026-07-07 · **Status:** accepted

## Context

The system needs a free hosted Postgres that receives one small batch write per day from GitHub Actions and serves reads to a public dashboard. The charter allowed Neon or Supabase.

## Decision

**Neon** free tier, EU region. Pooled connection string for app/ingestion; direct string for alembic migrations; a dedicated read-only role for API + dashboard.

## Why

- Neon's scale-to-zero is **self-healing**: compute suspends after idle and wakes automatically in ~300 ms on the next connection. There is no manual-resume failure mode.
- Supabase (2026) still **pauses projects after 7 days without activity and requires a manual resume**. Combined with GitHub Actions' known failure mode (scheduled workflows disabled after 60 days of repo inactivity), that is a silent-death cascade: stalled cron → 7-day pause → dashboard dead until a human clicks.
- Free-tier limits (0.5 GB storage, 100 CU-h/month) comfortably fit this workload with backfills bounded to 2024-01→.

## Consequences

- Ephemeral CI runners hit a cold endpoint daily → all DB access uses connect-retry with backoff.
- 0.5 GB ceiling is a watched metric; `features.model_input` is regenerable and prunable by design.
- Weekly `pg_dump` of the `pred` + `ops` schemas to a GitHub Release — the prediction history is the one irreplaceable asset.
