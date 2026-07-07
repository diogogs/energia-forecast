# ADR-005: uv-managed Python 3.12 alongside system Python 3.10

**Date:** 2026-07-07 · **Status:** accepted

## Context

The charter requires Python 3.12+. The development machine (Windows 11) has system Python 3.10.11, used by other things.

## Decision

Use **uv** as the single tool for interpreter + dependency management: `uv python install 3.12` provisions an isolated CPython 3.12 without touching the system installation; `uv sync` + committed `uv.lock` give reproducible environments locally and in CI (`astral-sh/setup-uv`).

Additionally, the repository lives at `C:\dev\energia-forecast` — **outside OneDrive** — because OneDrive sync fights `.venv` (thousands of files), git lock files, and long paths.

## Consequences

- CI uses `uv sync --frozen` so the lockfile is the single source of truth.
- Contributors never need a specific system Python; `.python-version` pins 3.12.
