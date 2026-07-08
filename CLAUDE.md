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
| Open-Meteo | Previsões meteo horárias (vento 100m, radiação, temperatura) + arquivo de previsões passadas | Sem chave; CC-BY | Substitui o IPMA (ADR-001). Previous Runs p/ treino; Forecast API p/ produção; modelo pinado. |
| Energy-Charts (Fraunhofer ISE) | Load + geração de ES (features) | API pública, sem chave; CC-BY 4.0 | ES como feature (a REN é só PT). `api.energy-charts.info` — `/public_power`, `/price`. ADR-007. |
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

**Última atualização:** 2026-07-08 (fim do dia 2, Semana 1).

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

### A seguir (retomar aqui)
- [ ] **Módulo Energy-Charts** — load/geração de ES (features). Mesmo padrão: fetch+parse testado → upsert idempotente → backfill.
- [ ] **Camadas clean + features** — grelha horária, pivot das séries REN, AsOfRepo.
- [ ] **Camadas clean + features**, baselines, backtesting (Semanas 3-4).
- [ ] **Ingestão diária automática** (critério de saída W2: 3+ dias sem intervenção).
- [ ] Token ENTSO-E: aguardar aprovação (~3 dias úteis) — depois gerar em My Account Settings e adicionar a `.env`/Secrets (opcional, validação cruzada).
