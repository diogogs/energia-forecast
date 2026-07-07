# ADR-001: Open-Meteo (not IPMA) as the weather-forecast source

**Date:** 2026-07-07 · **Status:** accepted

## Context

Wind, solar radiation, and temperature *forecasts* are critical features for both targets (load and MIBEL price). The original charter named IPMA (the Portuguese met office) as the source.

## Decision

Use **Open-Meteo** (`api.open-meteo.com`) for all weather-forecast features. Drop IPMA from the feature pipeline entirely.

## Why

- IPMA's free open-data API only provides **daily aggregates per município**: min/max temperature, a categorical wind class (1–4, not m/s), precipitation probability. It has **no hourly wind speed and no solar radiation at all** — unusable for renewables/price features.
- Open-Meteo provides hourly `wind_speed_100m`, `shortwave_radiation`, `direct_normal_irradiance`, `temperature_2m` by lat/lon, up to 16 days ahead; free for non-commercial use (this is a non-commercial portfolio project), no API key, CC-BY 4.0 attribution (in README).
- Crucially for temporal rigor, Open-Meteo also archives **past forecast runs** (Previous Runs API) — the only way to train on forecasts-as-they-were instead of leaking observed weather. Empirically verified (2026-07-07): for `ecmwf_ifs025` over Iberia, `previous_day1` coverage starts ~2024-03 for temperature and **~2024-04 for wind and radiation** — this pins the modelling matrix start (see ADR on training window, forthcoming).

## Consequences

- IPMA remains available as an optional secondary source (warnings, sanity checks) but is not part of any feature.
- Attribution line required in README (CC-BY 4.0).
