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
| REN Data Hub | Consumo PT + geração por tecnologia, 15-min, desde ~2019 | API JSON pública, sem chave | **Principal (PT):** target de consumo + geração. `servicebus.ren.pt/datahubapi`, endpoint por dia (96 slots). ADR-007. |
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

## Estado atual (atualizar à medida que avança)

- [ ] Token ENTSO-E pedido (ação do autor — email a transparency@entsoe.eu, assunto "Restful API access"; aprovação ≤3 dias úteis)
- [x] Spikes de verificação dia-1: OMIE 96-períodos (parser próprio, ADR-006), REN 15-min (cross-check), Previous Runs desde 2024-04 (matriz de treino começa 2024-04-01)
- [x] Scaffold + tooling + CI
- [ ] Esquema da BD (migração alembic 001)
- [ ] Módulo OMIE + backfill 2024-01→
- [ ] Ingestão diária a correr (critério W2: 3+ dias sem intervenção)
