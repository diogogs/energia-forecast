"""MLflow experiment tracking (DagsHub-hosted) — for evaluation only, NEVER in the serving path.

The daily predict job does not touch MLflow (it retrains and writes to pred.*). This module is
used by the backtest/retrain job to log params, metrics and feature importance. If no tracking
URI is configured it falls back to a local ``./mlruns`` file store, so it works out of the box;
set MLFLOW_TRACKING_URI/USERNAME/PASSWORD (DagsHub) to persist remotely.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager

import mlflow
import pandas as pd

from src.config import get_settings

EXPERIMENT = "consumption-phase1"


def configure_mlflow(experiment: str = EXPERIMENT) -> bool:
    """Point MLflow at DagsHub if configured (else local ./mlruns). Returns True if remote."""
    settings = get_settings()
    remote = bool(settings.mlflow_tracking_uri)
    if remote:
        # DagsHub authenticates via these env vars; Settings loads them from .env, not os.environ.
        if settings.mlflow_tracking_username:
            os.environ["MLFLOW_TRACKING_USERNAME"] = settings.mlflow_tracking_username
        if settings.mlflow_tracking_password:
            os.environ["MLFLOW_TRACKING_PASSWORD"] = settings.mlflow_tracking_password
        mlflow.set_tracking_uri(settings.mlflow_tracking_uri)
    mlflow.set_experiment(experiment)
    return remote


@contextmanager
def backtest_run(run_name: str, params: dict[str, object]) -> Iterator[None]:
    """A tracked backtest run: logs params on entry; metrics/artifacts logged inside."""
    configure_mlflow()
    with mlflow.start_run(run_name=run_name):
        mlflow.log_params(params)
        yield


def log_backtest_metrics(metrics: pd.DataFrame) -> None:
    """Log MAE/MAPE per model (index = model name) to the active run."""
    for model_name, row in metrics.iterrows():
        mlflow.log_metric(f"{model_name}_MAE", float(row["MAE"]))
        mlflow.log_metric(f"{model_name}_MAPE", float(row["MAPE"]))


def log_feature_importance(feature_names: list[str], importances: list[float]) -> None:
    """Log the model's feature importances as a JSON artifact + top-feature metric."""
    ranked = sorted(zip(feature_names, importances, strict=True), key=lambda kv: -kv[1])
    mlflow.log_dict({name: float(imp) for name, imp in ranked}, "feature_importance.json")
