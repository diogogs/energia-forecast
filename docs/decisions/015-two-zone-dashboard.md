# ADR-015: Two-zone dashboard — information product first, engineering under "About"

**Date:** 2026-07-18 · **Status:** accepted

## Context

A user-task walkthrough (post-ADR-014) showed the dashboard failed the reader who comes to
EXTRACT information rather than to evaluate the project. "When is power cheap tomorrow?"
required opening a tab, hovering a line chart and finding the minimum by eye; "is tomorrow
dearer than usual?" had no answer anywhere; uncertainty and accuracy spoke evaluation
language (P10-P90/CQR, MAE) on the landing page. Root cause: the dashboard grew as the
system's showcase, so every element answered "how was this built / can we trust it" and
none answered "what do I need to know". The audiences are real and distinct: portfolio
evaluators, and Portuguese readers on OMIE-indexed tariffs for whom tomorrow's hourly
price is money.

## Decision

1. **Two zones in one app.** The landing ("Tomorrow") is an information product: KPI row
   (average price with a vs-last-7-days delta, cheapest and dearest 3-hour windows, demand
   peak), price first (that is where actionability lives), tercile background tint and
   plain words ("likely range", "typically within ~X"), a compact today-so-far, and one
   link to the record. All methodology vocabulary moves to the grouped "About the system"
   section (Track record, Performance, Methodology, Status), which stays intact — the
   separation lets each zone do one job well.
2. **Cleared prices replace the forecast the same afternoon.** The auction clears ~12:45
   CET and OMIE publishes D+1 prices ~13h. The daily ingest window for OMIE now extends to
   D+1 (same pattern and rationale as Open-Meteo's, ADR history 2026-07-11) and an
   afternoon ingest run (cron-job.org 13:15 UTC primary, GH schedule 13:20 fallback)
   lands them; the landing detects tomorrow's realised prices via /history and switches —
   official prices solid, the morning forecast kept dashed with its miss stated. Once the
   truth exists, showing the forecast as the headline would be theatre; showing both is
   the honest product.
3. Not-yet-published D+1 OMIE files in the morning run count as `days_missing`
   (severity info), not failures — the runner's severity contract already distinguished
   them.

## Consequences

- English stays (public-surface rule) although the indexed-tariff audience is Portuguese;
  a PT toggle is the natural next step if the info product is ever promoted as such.
- The landing recomputes windows from cleared prices after the switch, so the "cheapest
  hours" advice is exact in the afternoon, forecast-based in the morning.
- The old Forecasts page is removed; its baselines explainer moved to Performance.
- The morning dq_log gains one benign "no file published" info line for OMIE's D+1 probe.
