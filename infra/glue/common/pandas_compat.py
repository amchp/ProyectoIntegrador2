from __future__ import annotations

import pandas as pd


def ensure_pandas_spark_compat() -> None:
    if not hasattr(pd.DataFrame, "iteritems"):
        pd.DataFrame.iteritems = pd.DataFrame.items  # type: ignore[attr-defined]
