# CLAUDE.md — Observatório e Previsão de Energia (MIBEL / Portugal)

## Contexto do projeto

Projeto pessoal de portfólio para demonstrar **ML engineering end-to-end**: sistema em produção contínua que recolhe dados reais do sistema elétrico português, prevê o consumo nacional e o preço day-ahead MIBEL, e expõe os resultados num dashboard público com monitorização. Prioridade: sistema completo e robusto > modelo state-of-the-art. Autor: developer full stack (Python/Data Science/ML), part-time, ~8 semanas (Jul–Ago 2026).

> **Nota de revisão (2026-07-07):** este charter foi emendado após verificação das APIs e free tiers reais de 2026 e uma revisão adversarial de correção temporal. As emendas estão registadas em `docs/decisions/` (ADR-001…006). O plano detalhado das 8 semanas vive fora do repo; as decisões relevantes estão nos ADRs.

## Princípios orientadores

1. **Simplicidade primeiro.** Baseline ingénuo antes de modelo sofisticado. LightGBM antes de deep learning. Cron antes de orquestrador.
2. **Tudo em produção.** Notebooks só para exploração; todo o código de valor migra para módulos testáveis.
3. **Rigor temporal absoluto.** Ver secção "Modelo temporal" — é a identidade técnica do projeto.
4. **Registar tudo.** Todas as previsões persistidas com timestamp de emissão (insert-only). Experiências em MLflow (DagsHub).
5. **Custo zero.** Stack inteiro em free tiers (validados em 2026 — ver ADRs).
6. **Documentar decisões.** ADRs curtos em `docs/decisions/` (contexto, opções, decisão, porquê).

## Modelo temporal (regras não negociáveis)

- **Convenção:** D = dia de emissão; D+1 = dia de entrega. **Dia de entrega = dia de mercado CET/CEST** para ambos os targets (dias DST têm 23/25 horas; 92/100 quartos). Conversão para hora local só na apresentação.
- **Cutoff nominal `t_issue` = 07:00 UTC do dia D** — constante fixa, nunca `now()`. Features construídas "as-of t_issue" independentemente da hora real de execução.
- **Legalidade por tempo de publicação**, não por valid time: uma feature só pode usar dados com `published_at ≤ t_issue` (`first_seen_at` para dados ingeridos ao vivo; regras por série para histórico backfilled). Todo o acesso a dados para features passa pelo `AsOfRepo` — nunca queries diretas.
- **Lags legais (relativos ao target):** consumo **≥ 48h** ({48, 72, 168, 336h}) — o lag-24h de consumo é LEAKAGE (o dia D está incompleto à hora de emissão); preço **≥ 24h** ({24, 48, 168h}) — legal porque os preços do dia D foram publicados em D−1.
- **Meteo de treino = previsões arquivadas** (Open-Meteo Previous Runs, `previous_day1/day2`, modelo pinado `ecmwf_ifs025`), nunca reanálise/observado. ERA5 só para monitorização. Produção usa o mesmo modelo pinado.
- **Baselines target-relative:** persistência consumo = `y(target−48h)`; persistência preço = `p(target−24h)`; sazonal semanal = `y(target−168h)`. Correm pelo mesmo `build_features` e escrevem em `pred.predictions` como modelos de primeira classe.
- **Backtesting rolling-origin:** fold = dia de emissão; treinar com publicação ≤ t_issue de T, prever T+1, avançar; refresh semanal do modelo; ≥8–12 semanas out-of-sample; nenhum modelo é aceite sem bater ambas as baselines nos mesmos folds. Splits aleatórios são proibidos.
- **`pred.predictions` é insert-only.** Nunca mutar `issued_at`. Previsões tardias levam flag `late_issue` e ficam fora do scoring headline.
- Qualquer gráfico "histórico simulado" lê exclusivamente `pred.backtest_predictions` (previsões fold-wise); nenhum código de charts chama `model.predict` sobre datas passadas.

## Fontes de dados

| Fonte | Dados | Acesso | Notas |
|---|---|---|---|
| REN Data Hub | Consumo PT + geração por tecnologia, 15-min, desde ~2019 | API JSON pública, sem chave | **Principal (PT):** target de consumo + geração. `POST datahub.ren.pt/service/Electricity/ProductionBreakdown/{id}?dayToSearchString={.NET ticks}` — um call dá `Consumption` (target) + todas as tecnologias. Hora **Lisboa** (WET/WEST), não CET. ADR-007, ADR-008. |
| OMIE | Preços day-ahead oficiais PT e ES | Ficheiros públicos, sem chave | **Principal (preço):** parser próprio (ADR-006 — a lib OMIEData corrompe ficheiros 15-min). ADR-007. |
| Open-Meteo | Previsões meteo horárias (vento 100m, radiação, temperatura) + arquivo de previsões passadas | Sem chave; CC-BY | Substitui o IPMA (ADR-001). **Previous Runs** p/ treino (`{var}_previous_dayN`, ligado ao valid-time — anti-leakage verificado), Forecast API p/ produção; modelo pinado `ecmwf_ifs025`. Guarda só lead 1/2, nunca o run corrente (leaky). ADR-010. |
| Energy-Charts (Fraunhofer ISE) | Load + geração de ES (features) | API pública, sem chave; CC-BY 4.0 | ES como feature (a REN é só PT). `GET api.energy-charts.info/public_power?country=es` — `unix_seconds` (UTC nativo), 15-min. **Conjunto curado** (Load, Solar, Wind onshore, Cross border) por legalidade temporal + budget Neon. ADR-007, ADR-009. |
| ENTSO-E Transparency | Fonte canónica pan-europeia | Token via email (≤3 dias) | **Adiada / opcional:** validação cruzada futura, fora do caminho crítico (ADR-007). |

**Regras de ingestão:** idempotente (upsert por chave natural; `first_seen_at` escrito só no INSERT e nunca tocado — é o proxy de publicação); validação à entrada → `ops.dq_log`, nunca descartes silenciosos; camadas `raw` (resolução nativa, nunca mutada) → `clean` (grelha horária) → `features`; janela deslizante [now−3d, now] re-ingerida todas as manhãs (self-healing), incluindo preços.

## Problemas de ML

### Fase 1 — Consumo nacional PT, D+1
24 valores horários (dia de mercado CET), emitidos às 07:00 UTC de D. Features: calendário (`holidays` PT), lags {48,72,168,336h}, rolling ≤ fim de D−1, meteo prevista (temp/HDD/CDD, radiação). LightGBM `regression_l1`, um modelo com hora como feature. Métricas: MAE/MAPE por hora, dia útil/fim de semana, pico/vazio.

### Fase 2 — Preço day-ahead MIBEL PT, D+1
Média horária dos preços 15-min (ADR-002), prevista ANTES do fecho SDAC (~12:00 CET). Features extra: previsão de consumo da Fase 1 **as-issued**, lags de preço {24,48,168h} + agregados do dia D, preço ES + spread lag-24, proxy de renováveis (vento³ capped, radiação). **Tripleto quantílico P10/P50/P90 — o P50 é a previsão pontual.** Avaliar com MAE (nunca MAPE) + pinball loss + cobertura empírica do intervalo. Se o modelo não bater as baselines, publica-se a baseline (rotulada) — nunca um modelo perdedor no dashboard.

## Arquitetura

```
ENTSO-E / Open-Meteo / OMIE / REN
        │
        ▼
GitHub Actions (crons com offset, sweeper no job de deadline, keepalive=hero-chart)
        │
        ▼
Neon Postgres — schemas: raw / clean / features / pred / ops / meta  (tudo UTC, timestamptz)
        │
        ├── Treino/backtest (retreino semanal + gate like-for-like; tracking DagsHub MLflow;
        │    artefactos servidos de GitHub Releases — MLflow NUNCA no caminho de serving)
        ▼
FastAPI (HF Spaces Docker; role read-only) ──► Streamlit Community Cloud (dashboard)
        │
        ▼
Monitorização: erro realizado, drift, dq_log, watchdog de frescura, backup semanal pg_dump
```

## Stack técnico

- **Python 3.12 via uv** (ADR-005); `ruff` (lint+format), `mypy` gradual (strict em `src/features` e `src/db`), `pytest`.
- **Dados:** `entsoe-py`, `httpx`, `pandas`; BD `SQLAlchemy` + `alembic`; `tenacity` para retries.
- **ML:** `lightgbm`, `scikit-learn`, `holidays`; MLflow no DagsHub.
- **Serving:** FastAPI + pydantic v2; dashboard Streamlit Community Cloud; API em HF Spaces Docker (fallback Render). Discos efémeros — estado só no Neon.
- **CI:** GitHub Actions — ruff, mypy, pytest em cada push/PR; testes anti-leakage são gate de merge.

## Estrutura do repositório

```
energia-forecast/
├── CLAUDE.md / README.md (produto) / pyproject.toml / uv.lock
├── .github/workflows/       # ci, ingest-morning, ingest-prices, predict, retrain, hero-chart, backup
├── src/
│   ├── ingestion/sources/   # entsoe.py, openmeteo.py, omie.py, ren.py
│   ├── ingestion/           # validation.py, runner.py
│   ├── db/                  # modelos SQLAlchemy, migrações alembic, repositórios
│   ├── features/            # AsOfRepo, build_features — TODA a feature recebe t_issue explícito
│   ├── models/              # baselines, treino, backtesting, retreino, gate
│   ├── api/                 # FastAPI
│   └── monitoring/          # erro realizado, drift, watchdog
├── dashboard/               # Streamlit
├── notebooks/               # exploração; nunca importados por src/
├── tests/                   # anti-leakage (marker `leakage`), fixtures DST/decoupling
└── docs/decisions/ + docs/posts/
```

## Convenções para o Claude Code

- Código e comentários em inglês; comunicação com o autor em português.
- Commits pequenos, mensagens convencionais em inglês (`feat:`, `fix:`, `chore:`…).
- Antes de tocar em `src/features/` ou `src/models/`: reler o "Modelo temporal" acima. Toda a função de features recebe `issue_ts` explícito e lê dados só via `AsOfRepo`.
- Segredos SEMPRE via env vars / GitHub Secrets; `.env.example` atualizado; repo é público — logs são públicos.
- Decisão de arquitetura relevante ⇒ ADR novo em `docs/decisions/`.
- Em dúvida entre "mais completo" e "mais simples mas em produção": a segunda.
- Datas/horas: armazenar UTC (`timestamptz`); `DTZ` do ruff ativo — datetimes naïve são bugs.

## Variáveis de ambiente

Ver `.env.example` — ENTSOE_API_TOKEN, DATABASE_URL (pooled), DATABASE_URL_DIRECT (alembic), DATABASE_URL_RO (serving), MLFLOW_TRACKING_URI/USERNAME/PASSWORD, API_BASE_URL.

## Estado atual

**Última atualização:** 2026-07-13. **Contexto multi-projeto:** ver `C:\dev\CLAUDE.md` (mapa-mestre do portfólio: este projeto + dr-watch + site pessoal + plano de carreira).

**Estado global — sistema completo e autónomo para AMBOS os targets, em produção.** As 4 fontes na camada `raw` (~1.85M linhas: OMIE 84k, REN 1.06M, Energy-Charts 353k, Open-Meteo 358k) → fundação temporal anti-leakage (`AsOfRepo`, publicação modelada, ADR-011) → `build_features` (calendário Lisboa, lags legais, meteo). **Fase 1 (consumo):** LightGBM `regression_l1` MAE 166 MW / **MAPE 2.77%** vs sazonal 5.95% → **ACEITE**. **Fase 2 (preço):** tripleto quantílico P10/P50/P90 (intervalo calibrado por CQR conformal), **P50 MAE 13.24 €/MWh** vs persistência 15.98 → **ACEITE**, cobertura P10-P90 76.1% (alvo 80%; resíduo = não-estacionariedade de regime). **Ambos live** via os mesmos 3 crons GitHub Actions (`ingest` 06:30, `predict` 07:05 emite os dois targets, `backtest` semanal); previsões em `pred.predictions` (insert-only, `target_name` na PK) + `pred.backtest_predictions` (história simulada, ambos). Tracking MLflow (DagsHub-ready, fallback local). **Serving LIVE** (ADR-012, custo zero): API FastAPI read-only no **Render** (`src/api`, endpoints forecast/backtest/performance/monitoring/dq) → [energia-forecast-api.onrender.com](https://energia-forecast-api.onrender.com) + dashboard Streamlit Cloud (`dashboard/`, Altair, palette validada) → [app](https://energia-forecast-bwwhirmyetaphmsk84dkqg.streamlit.app/) + monitorização (freshness watchdog + erro realizado live + `ops.dq_log`). **Hardening:** `ops.dq_log` durável (saúde da ingestão) + backup semanal `pg_dump` (artifact GitHub, verificado ao vivo). Migrações **0001-0008**, ADRs **001-013**, **127 testes verdes** (marker `leakage` = gate), CI verde, custo zero (Neon 440/512 MB — vigiar). Triggers via **cron-job.org** (ADR-013; GH schedule = fallback), auto-deploy da API por deploy hook, keepalive em `/ping` (sem BD). Dashboard **v3** (identidade navy + análise), write-up **publicado**, cross-check ENTSO-E diário (MAE 1.3 MW). **Autonomia pontual:** 07-11 expôs 2 bugs reais (meteo do dia de entrega nunca era ingerida → janela +1; emissão early por fuso cron-job → guard anti-t_issue) — corrigidos; **07-12 e 07-13 perfeitas às 07:06 UTC** (a 3ª, em 07-14, fecha o critério W2). **A seguir:** drift monitoring a solo pelo autor.

**Repositório:** código em `C:\dev\energia-forecast` (fora do OneDrive, ADR-005). GitHub: https://github.com/diogogs/energia-forecast (público, CI verde).

**Como correr (Windows):** `uv` foi instalado via winget mas pode não estar no PATH numa shell nova — prefixar com:
`$env:Path = "C:\Users\dgsil\AppData\Local\Microsoft\WinGet\Packages\astral-sh.uv_Microsoft.Winget.Source_8wekyb3d8bbwe;$env:Path"` (ou correr `uv python update-shell` uma vez). O Python de sistema é 3.10; usar sempre `uv run` (Python 3.12 gerido pelo uv). `python` não está no PATH — usar o launcher `py` ou `uv run python`.

### Histórico (condensado — detalhe no `git log` e nos ADRs 001-013)

- Spikes dia-1: OMIEData corrompe ficheiros 15-min → parser próprio (ADR-006); fontes token-free REN/OMIE/EC/Open-Meteo (ADR-007/8/9/10); matriz de modelação desde 2024-04.
- Ingestão idempotente das 4 fontes + backfills (~1.9M linhas raw; `first_seen_at` = proxy de publicação, nunca mutado); janela self-healing diária; REN aceita dia parcial em curso.
- Fundação temporal (ADR-011): `t_issue` 07:00 UTC fixo, publicação modelada por fonte, `AsOfRepo` único caminho de leitura; testes `leakage` = gate de CI.
- Features + baselines target-relative pelo MESMO pipeline; backtest rolling-origin 71 folds OOS, refresh semanal, `PreloadedRepo` provado equivalente ao AsOfRepo.
- **Fase 1 aceite:** LightGBM MAE 166 MW / MAPE 2.77% (sazonal 354/5.95, persistência 664/11.5). **Fase 2 aceite:** tripleto quantílico P50 MAE 13.24 vs persistência 15.98; CQR → cobertura 76.1% (alvo 80, resíduo = regime shift, reportado).
- `pred.*` insert-only com `target_name` na PK (bug real de colisão cross-target, mig 0007); predict diário retrain-on-emit; `late_issue` > t_issue+2h fora do headline; MLflow DagsHub-ready.
- Serving custo-zero (ADR-012): API read-only no Render (+`/history`, `/monitoring/dq`, `/ping` sem BD) + dashboard Streamlit; auto-deploy por deploy hook (repo público → sem GitHub App).
- Hardening: `ops.dq_log` durável; backup semanal `pg_dump` (artifact, restaurável); testes de `run_daily` com seam de BD stubado (episódio de poluição do dq_log de produção, limpo).
- Dashboard v2 multipage sóbrio (EN, sem tells de LLM — preferência forte do autor) → v3: tema navy, hero card, KPIs-cartão, banda P10-P90 na história, cobertura rolling 7d, MAE por hora.
- Ops: GH scheduler inviável no caminho crítico (atraso uniforme ~3h20, ~90% dos eventos de alta frequência descartados) → triggers via cron-job.org com PAT (ADR-013), fusos dos jobs = UTC; keepalive aponta a `/ping` (o `/health` tocava a BD e esgotava as 100 CU-h/mês do Neon).
- 2026-07-11 (1ª manhã autónoma): emissão early (fuso Lisboa no cron-job) expôs que a **meteo do dia de entrega nunca era ingerida** (janela acabava em hoje; emissões live com meteo NaN — skew invisível ao backtest) → janela openmeteo +1 dia + guard `too_early` no predict.
- 2026-07-12/13: emissões perfeitas (pontuais + meteo fresca) às 07:06 UTC.
- Write-up "The model was the easy part" publicado (site + `docs/posts/`); cross-validation ENTSO-E diária no dq_log.

**Dashboard v4 (2026-07-18, ADR-014):** capítulo "Track record" 100% produção — MAE por
dia de mercado vs benchmark do backtest (manhãs pré-fix dimmed, nunca cortadas), replay de
qualquer dia de entrega, log de emissões + streak de pontualidade (endpoint novo
`/emissions`); headline live do Status passou a janela 7 dias (o agregado since-launch
lia-se como "2× pior que o backtest" quando a última semana está MELHOR que ele — 74-98 MW
vs 166); scorecard de ontem na landing (prova antes da promessa); agregação por dia de
mercado CET (`market_day()`) em todas as vistas diárias.

### A seguir (retomar aqui)
- [x] Critério W2 fechado a 07-14; streak de manhãs pontuais em curso (7 a 07-18).
- [ ] **Drift monitoring a solo pelo autor** — spec em `C:\dev\spec-drift-monitoring.md`; Claude só review no fim.
- [ ] Vigiar cross-check ENTSO-E×REN (07-18: MAE 50-85 MW vs ~1.3 habitual, corr 0.99 — se persistir, investigar que série foi revista).
- [ ] Backlog dashboard: pior-dia com contexto, weekday/weekend no MAE por hora, passagem mobile, retry no cold start, agregação semanal do Track record quando o registo crescer (ADR-014); feature importance; DagsHub MLflow remoto (opcional); storage Neon 86% (alavanca ADR-009).
- **Contexto paralelo:** dr-watch LIVE (`C:\dev\dr-watch`), site pessoal LIVE (`C:\dev\diogogs.github.io`). Mapa: `C:\dev\CLAUDE.md`.
