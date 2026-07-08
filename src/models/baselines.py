"""Target-relative persistence baselines — first-class models the real model must beat.

They read only columns produced by build_features (the legal lags), so they run through exactly
the same as-of pipeline as any model (CLAUDE.md). A model that cannot beat both on the same
folds is not shipped.
"""

from __future__ import annotations

import pandas as pd

# Persistence: tomorrow's hour looks like the same hour 48h ago (the freshest LEGAL consumption
# lag — the 24h lag is leakage). Seasonal-weekly: the same hour one week ago.
PERSISTENCE_LAG_H = 48
SEASONAL_WEEKLY_LAG_H = 168


def persistence_consumption(features: pd.DataFrame) -> pd.Series:
    """Predict each delivery hour as consumption 48h earlier."""
    return features[f"cons_lag_{PERSISTENCE_LAG_H}h"].rename("y_hat")


def seasonal_weekly_consumption(features: pd.DataFrame) -> pd.Series:
    """Predict each delivery hour as consumption 168h (one week) earlier."""
    return features[f"cons_lag_{SEASONAL_WEEKLY_LAG_H}h"].rename("y_hat")
