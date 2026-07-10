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

**Última atualização:** 2026-07-09.

**Estado global — sistema completo e autónomo para AMBOS os targets, em produção.** As 4 fontes na camada `raw` (~1.85M linhas: OMIE 84k, REN 1.06M, Energy-Charts 353k, Open-Meteo 358k) → fundação temporal anti-leakage (`AsOfRepo`, publicação modelada, ADR-011) → `build_features` (calendário Lisboa, lags legais, meteo). **Fase 1 (consumo):** LightGBM `regression_l1` MAE 166 MW / **MAPE 2.77%** vs sazonal 5.95% → **ACEITE**. **Fase 2 (preço):** tripleto quantílico P10/P50/P90 (intervalo calibrado por CQR conformal), **P50 MAE 13.24 €/MWh** vs persistência 15.98 → **ACEITE**, cobertura P10-P90 76.1% (alvo 80%; resíduo = não-estacionariedade de regime). **Ambos live** via os mesmos 3 crons GitHub Actions (`ingest` 06:30, `predict` 07:05 emite os dois targets, `backtest` semanal); previsões em `pred.predictions` (insert-only, `target_name` na PK) + `pred.backtest_predictions` (história simulada, ambos). Tracking MLflow (DagsHub-ready, fallback local). **Serving LIVE** (ADR-012, custo zero): API FastAPI read-only no **Render** (`src/api`, endpoints forecast/backtest/performance/monitoring/dq) → [energia-forecast-api.onrender.com](https://energia-forecast-api.onrender.com) + dashboard Streamlit Cloud (`dashboard/`, Altair, palette validada) → [app](https://energia-forecast-bwwhirmyetaphmsk84dkqg.streamlit.app/) + monitorização (freshness watchdog + erro realizado live + `ops.dq_log`). **Hardening:** `ops.dq_log` durável (saúde da ingestão) + backup semanal `pg_dump` (artifact GitHub, verificado ao vivo). Migrações **0001-0008**, ADRs **001-012**, **118 testes verdes** (marker `leakage` = gate), CI verde, custo zero (Neon ~435/512 MB). **A seguir:** iterar o dashboard (contexto/storytelling) + hardening restante (drift, DagsHub).

**Repositório:** código em `C:\dev\energia-forecast` (fora do OneDrive, ADR-005). GitHub: https://github.com/diogogs/energia-forecast (público, CI verde).

**Como correr (Windows):** `uv` foi instalado via winget mas pode não estar no PATH numa shell nova — prefixar com:
`$env:Path = "C:\Users\dgsil\AppData\Local\Microsoft\WinGet\Packages\astral-sh.uv_Microsoft.Winget.Source_8wekyb3d8bbwe;$env:Path"` (ou correr `uv python update-shell` uma vez). O Python de sistema é 3.10; usar sempre `uv run` (Python 3.12 gerido pelo uv). `python` não está no PATH — usar o launcher `py` ou `uv run python`.

### Feito
- [x] **Spikes de verificação dia-1** — OMIEData **corrompe** ficheiros 15-min (lê 96 quartos como 25 "horas") → parser próprio (ADR-006); REN Data Hub dá **consumo + geração a 15-min desde ~2019** sem token (ADR-007); Open-Meteo Previous Runs (`ecmwf_ifs025`) cobre Iberia com vento/radiação **desde 2024-04** → matriz de modelação começa **2024-04-01**.
- [x] **Decisão de fontes (ADR-007):** REN (consumo/geração PT) + OMIE (preços PT/ES) + Energy-Charts (load/geração ES, features) — tudo **token-free**. ENTSO-E adiada (email enviado 2026-07-07, opcional, validação cruzada).
- [x] **Scaffold + tooling + CI** — uv/Python 3.12, ruff, mypy (strict em `src/features` e `src/db`), pytest, pre-commit; CI GitHub Actions verde.
- [x] **Charter + ADRs 001-007** escritos.
- [x] **Parser OMIE** (`src/ingestion/sources/omie.py`) — resolução-aware (horário/15-min) e DST-correto (mapeamento UTC por passos); coluna PT/ES verificada empiricamente vs Energy-Charts. **15 testes verdes** com fixtures reais de todos os dias-DST (23/25h, 92/96/100q).
- [x] **Neon criado** (projeto `energia-forecast`, AWS eu-central-1, PG18). Connection strings (pooled + direct) verificadas e guardadas em `.env` **local** (fora do repo).
- [x] **Camada de BD** — `src/config.py` (pydantic-settings), `src/db/base.py` (DeclarativeBase + naming conventions), `src/db/models.py` (`raw.omie_price`), `src/db/engine.py` (psycopg3, pool_pre_ping).
- [x] **Migração alembic 001 aplicada ao Neon** — 6 schemas (raw/clean/features/pred/ops/meta) + `raw.omie_price` (`first_seen_at` escrito só no INSERT). `alembic_version=0001`.
- [x] **Repositório de upsert OMIE + backfill completo** (branch `feat/omie-upsert-backfill`, merged? não — ver git). `src/db/repositories/omie.py` (`ON CONFLICT DO UPDATE`, `first_seen_at` nunca mutado, caller controla a transação); teste de integração (marker `integration`, skip sem BD) + serviço Postgres no CI (`alembic upgrade head`); `src/ingestion/omie_backfill.py` (idempotente, commit por dia). **Fetcher com fallback de versão** — o OMIE às vezes retira o `.1` e só publica `.2`/`.3` (casos reais 2025-11-27→.2, 2025-10-30→.3); tenta `.1..5`, guarda a versão real em `source_file`. **`raw.omie_price`: 84 238 linhas, 918 dias contíguos 2024-01-01→2026-07-06, PT==ES, todos os dias-DST corretos.**
- [x] **Módulo REN completo + backfill corrido** (mesma branch). Contrato da API descoberto e verificado ao vivo (ADR-008): um só endpoint `ProductionBreakdown` dá `Consumption` (target Fase 1) + geração por tecnologia, 15-min. **Timezone Lisboa confirmado decisivamente** vs Energy-Charts PT (lag 0h, corr 1.0000, MW iguais ao decimal). `src/ingestion/sources/ren.py` (fetch+parse, âncora Europe/Lisbon DST-correta, skip de nulls, ticks .NET), `raw.ren_realised` (tall) + `meta.ren_series` (dimensão, 12 séries seeded), migração 0002, repositório upsert, backfill runner. **35 testes verdes.** Schema decidido por painel de design (3 propostas + juiz). **`raw.ren_realised`: 1 058 640 linhas, 919 dias contíguos 2024-01-01→2026-07-07, 12 séries × 88 220 slots, zero gaps, dias-DST todos 92/100 corretos, zero séries por classificar.** Curiosidade validada: mínimo de consumo 87.6 MW = apagão ibérico 2025-04-28 (dado real; próximo dia mais baixo 3 643 MW).
- [x] **Token ENTSO-E recebido e validado** (2026-07-08) — guardado em `.env` local (`ENTSOE_API_TOKEN`); smoke test OK (PT load horário via `entsoe-py`). Continua **fora do caminho crítico** (ADR-007, validação cruzada). Quando for usado em CI: adicionar a GitHub Secrets. NUNCA em ficheiros versionados (repo público; `.gitignore` reforçado contra dumps de segredos).
- [x] **Review adversarial + hardening + merge para `main`** (2026-07-08). Review multi-agente (6 dimensões, verificação cética por finding) sobre o diff completo da branch. Corrigido: retry agora cobra HTTP 5xx/429 (o `raise_for_status` vivia fora da função retried — um 502 transitório abortava um backfill inteiro); isolamento por dia nos backfills (`days_failed`, continua); parser REN valida slots vs comprimento real do dia Lisboa + rejeita séries duplicadas; fetch REN tolera 200 não-JSON; asserção `last_seen_at` estrita + rollback no cleanup dos testes de integração; conftest **falha** (não salta) no CI sem `DATABASE_URL`. Fallback lowest-version do OMIE documentado como decisão (o `.1` é a publicação D-1; OMIE retira ficheiros supersedidos). **39 testes verdes.** Branch merged (`aa41eb4`).
- [x] **Módulo Energy-Charts (ES features) + backfill** (ADR-009). `GET public_power?country=es` — `unix_seconds` **UTC nativo** (DST trivial, dia spring = 92 slots automático), 15-min desde 2024. `src/ingestion/sources/energy_charts.py` (fetch+parse, resolução derivada do espaçamento, conjunto **curado**), `raw.energy_charts_power` (tall, `country` na chave), migração 0003, repositório (upsert **em batches** — um mês excede o limite de 65 535 parâmetros do Postgres), backfill por chunks mensais. **`raw.energy_charts_power`: 352 868 linhas, 4 séries × 920 dias, zero gaps.** 55 testes verdes.
- [x] **Constrangimento Neon 512 MB resolvido** (ADR-009). O 1º backfill EC bateu no limite do free tier. Diagnóstico: índices secundários pesavam quase tanto como os dados + design tall multiplica linhas. Ações: **curar EC** (4 de 17 séries — legalidade temporal: geração ES do dia-alvo não está publicada às 07:00, logo só entra como lag) + **largar índices secundários** não usados (REN via migração 0004, EC nunca criado) — a clean layer criará os que precisar. DB 491 → **343 MB**. Alavanca futura registada: normalizar strings→IDs surrogate se voltar a apertar.

- [x] **Módulo Open-Meteo Previous Runs (meteo de treino) + backfill** (ADR-010). O ingestor temporalmente mais subtil: **previsões arquivadas** anti-leakage. Verificado ao vivo que `{var}_previous_dayN` está ligado ao valid-time (idêntico independentemente de quando se consulta) — o run corrente sem sufixo é leaky e **não** é guardado. `src/ingestion/sources/openmeteo.py` (multi-localização, modelo pinado `ecmwf_ifs025`, lead 1/2), `raw.openmeteo_forecast` (tall: location/variable/lead_days/ts_utc), migração 0005, repositório batched, backfill mensal. Localizações: lisbon/porto/evora. Variáveis: temperature_2m, wind_speed_100m, shortwave_radiation. Semântica de legalidade (lead 1 = run de D; lead 2 = run de D−1) documentada — a escolha do lead as-of t_issue é da camada features. **70 testes verdes.**

- [x] **Fundação temporal da camada features** (ADR-011) — o coração anti-leakage. `src/features/temporal.py`: `t_issue` fixo 07:00 UTC, grelha de entrega CET DST-correta (23/24/25h), e **modelo de publicação conservador** por fonte (REN/EC: meia-noite seguinte; OMIE: 13:00 CET de D−1; Open-Meteo: run de `valid_date−lead` às 06:00 UTC). `src/features/asof_repo.py`: **AsOfRepo** — único caminho de leitura legal (só `published_at ≤ t_issue`), resample horário on-the-fly ("clean" não materializado, poupa Neon). **Validado ao vivo** no fold 2024-06-10: consumo termina no fim do dia-Lisboa D−1 (zero leakage); preço PT inclui horas do dia D pós-07:00 (day-ahead já publicado) mas nunca D+1. **85 testes verdes** (marker `leakage` = gate de merge).

- [x] **`build_features` (consumo Fase 1) + baselines + primeiros números** (via AsOfRepo, sem queries diretas). `src/features/build_features.py`: calendário em **hora de Lisboa** (`holidays` PT — o offset PT/CET faz a 1ª hora do Ano Novo CET cair ainda em 31-dez Lisboa, tratado corretamente), lags legais {48,72,168,336h} relativos ao target, rolling recente ≤ fim D−1. `src/features/target.py`: label = consumo realizado no dia CET. `src/models/baselines.py`: persistência −48h e sazonal −168h (colunas de lag, mesmo pipeline). **Avaliação em 31 folds (2024-05→2025-03): persistência MAE 522 MW / MAPE 9.16%; sazonal-semanal MAE 297 / MAPE 4.83%.** A sazonal é a baseline a bater. 91 testes verdes.

- [x] **Meteo como feature** (`AsOfRepo.weather_forecast`) — seleção do lead legal mais fresco (lead 1 = run de D, legal às 07:00) + média sobre localizações; transforms HDD/CDD (bases 18/21°C), vento³ capped (~12 m/s), radiação. Meteo em falta → NaN (LightGBM tolera). 95 testes.
- [x] **1º modelo LightGBM + backtesting rolling-origin + gate PASSA** 🎯 (`src/models/backtest.py`). `PreloadedRepo` (as-of in-memory, legalmente idêntico ao AsOfRepo — provado em teste), `build_matrix` (812 folds), `rolling_origin_backtest` (refresh semanal, janela expansiva, 10 semanas OOS). LightGBM `regression_l1`. **Resultado (71 folds OOS, D+1 consumo): LightGBM MAE 166 MW / MAPE 2.77% vs sazonal 354/5.95% vs persistência 664/11.46% → ACEITE** (bate ambas, ~metade do erro da sazonal). Determinístico (`random_state=42`). 98 testes verdes.

- [x] **Ingestão diária automática — o sistema auto-alimenta-se** ✅ (critério de saída W2). `src/ingestion/daily.py`: re-ingere a janela deslizante `[hoje−3d, hoje]` nas 4 fontes (idempotente → cura gaps + revisões tardias, sem duplicar), isolamento por fonte, `exit 1` se alguma falhar. `.github/workflows/ingest.yml`: cron **06:30 UTC** (após o run 00Z do ECMWF, antes do t_issue 07:00) + `workflow_dispatch`, guard de concorrência, `DATABASE_URL` via GitHub Secret. **Validado ao vivo no runner do GitHub** (dispatch manual): 4 fontes OK. Secret `DATABASE_URL` configurado (pooled). Falta: observar 3+ dias sem intervenção.

- [x] **Previsões persistidas + predict diário** ✅. Migração 0006: `pred.predictions` (insert-only, PK inclui `quantile`; `issued_at` nunca mutado) + `pred.backtest_predictions` (fold-wise, reescrevível). `src/models/predict.py`: retrain-on-emit (treino ~seg), emite consumo D+1 (LightGBM + 2 baselines, modelos de 1ª classe) → `pred.predictions` via `ON CONFLICT DO NOTHING`. `src/db/repositories/predictions.py`. **Validado ao vivo**: 72 linhas escritas, idempotência insert-only provada (re-emissão mantém a 1ª). Backtest persistido em `pred.backtest_predictions` (5112 linhas; MAE realizado na BD = 165/354/664, confere). `.github/workflows/predict.yml`: cron **07:05 UTC**. `make_consumption_model` partilhado backtest↔serving (sem skew). 100 testes verdes.

- [x] **MLflow tracking + cron semanal de backtest** ✅. `src/models/tracking.py` (DagsHub se configurado, senão fallback local `./mlruns`; **nunca no serving**). `src/models/run_backtest.py`: backtest → persiste `pred.backtest_predictions` (durável no Neon) → regista params/métricas/feature-importance no MLflow. `.github/workflows/backtest.yml`: cron **domingos 05:00 UTC** (secrets MLflow opcionais → remoto; senão local efémero + persistência DB). Validado ao vivo (4104 linhas + run MLflow). **Falta config do utilizador:** criar repo DagsHub + `MLFLOW_TRACKING_URI/USERNAME/PASSWORD` no `.env` e GitHub Secrets para tracking remoto persistente. 100 testes verdes.

- [x] **Fase 2 — modelo de preço quantílico P10/P50/P90 + gate PASSA** 🎯. `src/features/build_features.py::build_price_features` (lags {24,48,168h} — 24h é legal no preço; agregados do dia D; ES + spread lag-24; proxies renováveis) + `price_target` + baselines (persistência-24h, sazonal-168h). `src/models/price_model.py`: 3 regressores LightGBM `objective=quantile`, `PreloadedRepo` estendido com preço, `rolling_origin_price_backtest`. **Resultado (71 folds OOS, D+1 MIBEL PT): P50 MAE 13.24 €/MWh vs persistência 15.98 vs sazonal 21.30 → ACEITE.** Descoberta da avaliação de cobertura: config profundo dava intervalo P10-P90 sub-disperso (48%); modelos **shallow/regularizados** (num_leaves=10, min_child=800) baixaram o MAE **e** subiram a cobertura para **74.9%** (alvo 80% — últimos pontos pedem calibração conformal, registado). 108 testes verdes. **Falta wiring do emit diário de preço** (P10/P50/P90 → `pred.predictions`) + persistência do backtest de preço.

- [x] **Emit diário de preço ligado + bug de PK cross-target corrigido** ✅. `predict.py` refatorado: emite **ambos** os targets (consumo point + preço P10/P50/P90 + baselines) partilhando um `PreloadedRepo`; `run_backtest.py` corre e persiste os dois backtests. Os crons `predict`/`backtest` já emitiam `--target both` por defeito → Fase 2 autónoma sem tocar nos workflows. **Bug real apanhado na verificação ao vivo:** a PK de `pred.predictions` não incluía `target_name`, e `seasonal_168h`/`point` existe para ambos os targets → a seasonal de preço colidia com a de consumo e era largada. **Migração 0007** mete `target_name` na PK de ambas as tabelas `pred.*`; teste de regressão adicionado. `pred.backtest_predictions`: consumo 5112 + preço 8520 linhas.

- [x] **Calibração conformal (CQR) do intervalo de preço** ✅. `conformal_correction` + `calibrated_price_triplet` (Romano et al. 2019): P50 no treino completo (MAE 13.24 inalterado); P10/P90 treinados no `fit`, corrigidos por Q = quantil (1−α) da não-conformidade numa fatia de calibração (últimas 3 semanas do treino). Usado no backtest **e** no emit (mesma função → sem skew). Cobertura 74.9% → **76.1%**; o gap para 80% é a não-estacionariedade de regime (quebra a permutabilidade do CQR) — reportado honestamente. Teste unitário do `conformal_correction`. 110 testes verdes.

- [x] **Serving: API FastAPI + dashboard Streamlit + monitorização** ✅. `src/api` (read-only, engine lazy, role RO quando configurado): `/forecast/{consumption|price}`, `/backtest/{target}`, `/performance/{target}`, `/monitoring/freshness`, `/monitoring/error/{target}` (pydantic v2, target validado por `Literal`). `dashboard/app.py` (Streamlit + Altair, **palette dataviz validada** — modelo azul, realizado laranja, baselines aqua/amarelo; banda P10-P90; legenda + tooltip; grelha recessiva): KPIs + tabs consumo/preço + expander de estado. `src/monitoring/watchdog.py`: `data_freshness` (as 4 fontes, flag stale > 30h) + `realised_error` (MAE das previsões live vs realizado, acumula com os dias). `Dockerfile` (API → HF Spaces, porta 7860) + secção de deploy no README + `dashboard/requirements.txt` (Streamlit Cloud). **Validado ao vivo:** freshness OK (crons a alimentar), forecast/performance corretos. 115 testes verdes. streamlit/altair num grupo `dashboard` (fora do CI).

- [x] **DEPLOY LIVE — sistema inteiro público, custo zero** 🚀 (ADR-012). API no **Render** (web service Docker free, `render.yaml` blueprint, role read-only `api_ro` no Neon criado + verificado, env var `DATABASE_URL_RO`): **https://energia-forecast-api.onrender.com** (`/health` `/docs` confirmados ao vivo). Dashboard no **Streamlit Community Cloud** (secret `API_BASE_URL`): **https://energia-forecast-bwwhirmyetaphmsk84dkqg.streamlit.app/**. Pivot de HF Spaces→Render porque o HF passou a exigir PRO para Docker Spaces (ADR-012). Cold start ~30-60s (free tier adormece; dashboard com timeout 75s tolera). **O sistema completo — ingestão→features→modelos→pred→API→dashboard→monitorização — está no ar e autónomo.**

- [x] **Hardening: `ops.dq_log` + backup semanal + fix REN dia-parcial** ✅. **`ops.dq_log`** (migração 0008): a ingestão diária persiste o resultado de cada fonte por execução (severidade info/warning/error, linhas, janela) — fim dos logs efémeros. Exposto em `/monitoring/dq` + tabela "últimas execuções" no dashboard (tolerante a version skew). **Backup semanal `pg_dump`** (`.github/workflows/backup.yml`, domingos 04:00 UTC): instala o cliente PG18, dump por ligação **direta** (derivada da pooled tirando `-pooler` → sem secret novo), gzip, artifact 30 dias, guarda contra dump vazio. **Validado ao vivo no runner** (dump ~12 MB). **Fix REN:** o `dq_log` tornou visível que a REN falhava o dia corrente (array truncado, incompleto) todos os dias → `allow_partial` aceita o prefixo do dia em progresso (só o dia de hoje; passados continuam estritos; o caso perigoso `n_slots > esperado` continua rejeitado). Hoje já ingere intradiário. **118 testes verdes**, migrações **0001-0008**.

- [x] **Dashboard v2 — redesign multipage narrativo (inglês)** ✅. Auditoria completa (o backtest de 10 semanas estava invisível; zero proveniência; atribuição CC-BY em falta = compliance; cold start 33s medido; sem links GitHub/API). Reestruturado com `st.navigation` em 4 páginas (`dashboard/views/`): **Forecasts** (hero com pico/preço de amanhã, D+1, "today: forecast vs actual" live, KPIs com delta vs baseline), **Performance** (história simulada das 10 semanas com realizado, cobertura empírica P10-P90, barras MAE com % de melhoria), **How it works** (timeline do ciclo diário em Altair, explicação anti-leakage, tabela de fontes com atribuição CC BY 4.0, arquitetura, limites honestos), **System status** (freshness, erro live com moldura de amostra pequena, `dq_log`, safety nets). Hierarquia visual: modelo/realizado grossos, baselines finas tracejadas. Footer com atribuição + links em todas as páginas. Novo endpoint **`/history/{target}`** (previsões live emparelhadas com realizado; `realised_hourly` público). Smoke test headless via `AppTest` (4 páginas sem exceções contra API local). **119 testes verdes.**
- [x] **Ops: keepalive + retry tickets de cron** ✅. Workflow `keepalive.yml` (ping /health a cada 12 min, 06-23 UTC) mata o cold start do Render para visitas diurnas. **Descoberta operacional:** o GitHub atrasou os crons ~3h30 em 2026-07-09 (predict 07:05→10:27) → `late_issue=True` correto mas fora do headline; como as features são as-of 07:00 e `pred.predictions` é insert-only, **entradas extra de cron são retries idempotentes seguros** (ingest +07:45; predict +07:50, +08:35 — a primeira a aterrar ganha).

- [x] **Fecho do ciclo de deploy + registo sóbrio** ✅ (2026-07-09, fim do dia). API redeployada no Render (`/history` + `/monitoring/dq` verificados live). **Auto-deploy resolvido por Deploy Hook** (não GitHub App): workflow `deploy-api.yml` chama o hook (secret `RENDER_DEPLOY_HOOK`) em pushes ao main filtrados a ficheiros da imagem da API — validado live (deploy id devolvido). **Dashboard + README passados a registo sóbrio/profissional** a pedido do utilizador (sem emojis, sem taglines "LLM-like", títulos descritivos, status em texto simples, sidebar com bloco de identidade; preferência gravada na memória). Bug apanhado e corrigido: testes do `run_daily` poluíam o `ops.dq_log` de produção (seam da BD agora stubado; 40 linhas falsas limpas do Neon). Utilizador decidiu NÃO rodar o role `api_ro`.

- [x] **Triggers migrados para cron-job.org (ADR-013)** ✅ (2026-07-10). Medições: o GitHub atrasava TODOS os eventos schedule ~3h20 (retry tickets inúteis — os 3 aterraram 10:25/10:39/11:28) e descartava ~90% dos de alta frequência (keepalive 5×/dia → Render adormecia); a emissão de 07-10 às 10:26 UTC falhou até o leilão. Setup novo: conta cron-job.org do utilizador + fine-grained PAT (Actions RW, só este repo) → 3 jobs: keepalive GET /health */10 24h (750h Render/mês chegam), dispatch ingest 06:30 UTC, dispatch predict 07:05 UTC (timezone dos jobs = UTC, à prova de DST). **Validado ao vivo**: test runs → workflow_dispatch chegaram em segundos, ambos success; API quente (0,14s). Schedules do GH mantidos como fallback (idempotência torna duplicados inofensivos).

- [x] **Dashboard v3 — identidade visual + análise a sério** ✅ (2026-07-10). Tema Streamlit (`.streamlit/config.toml`) + CSS de marca injetado: sidebar navy (identidade CV/site), hero card navy na página Forecasts (kicker "Tomorrow · ..." + headline com números), KPIs como cartões com spine de acento, gráficos em containers com borda. Página Performance ganhou: banda P10-P90 sobre a história do backtest, cobertura empírica rolling 7d vs alvo 80%, **MAE por hora de entrega** (modelo vs melhor baseline — onde vivem os 53%), barras corrigidas (Weekly seasonal estava cortada) com labels de valor. Cross-validation ENTSO-E no mesmo dia (MAE 1.3 MW, corr 0.99994 — ver acima). Smoke headless: demand 3 charts, price 4, forecasts 2, zero exceções. Validado no cloud.

### A seguir (retomar aqui)
- [ ] **Amanhã (07-11): 1ª emissão pontual de sempre** — cron-job.org dispara 06:30/07:05 UTC → `late_issue=False` → painel "Today: forecast vs actuals" começa a encher. Daí em diante: 3 dias de autonomia pontual = critério W2 cumprido.
- [ ] **Drift monitoring a solo pelo autor** — spec em `C:\dev\spec-drift-monitoring.md`; Claude só faz review no fim.
- [ ] **Dashboard backlog (grupo B/C)** — evolução do erro live + arquivo de previsões (quando houver track record); feature importance, bilingue, mobile (decisão do autor).
- [ ] **Hardening restante** — drift, ENTSO-E cross-validation (opcional), config DagsHub para MLflow remoto.
- [x] **Write-up publicado** ✅ (2026-07-10): "The model was the easy part" — draft do Claude, editado e validado pelo autor; live em diogogs.github.io/posts/the-model-was-the-easy-part/ + cópia canónica em `docs/posts/`. Divulgação (LinkedIn) fica para a fase final do plano de carreira.
- [ ] **Dashboard P2** (backlog): navegação temporal (arquivo de previsões), evolução do erro live, feature importance, bilingue.
- [ ] **Contexto paralelo:** site pessoal de portfólio em construção em `C:\dev\diogogs.github.io` (Astro + GitHub Pages; decisões e perfil na memória do Claude) — o write-up deste projeto será a peça fundadora do site.
