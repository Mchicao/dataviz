"""Ejecución reproducible de forecasts ETS sobre datasets neutrales."""

from __future__ import annotations

import warnings
from typing import Any, cast

import pandas as pd
from statsmodels.tools.sm_exceptions import ConvergenceWarning
from statsmodels.tsa.exponential_smoothing.ets import ETSModel, ETSResultsWrapper

from core.compilers.render_plan import ForecastSpec
from core.contracts.dataset import Dataset

_FREQUENCIES = {
    "day": "D",
    "week": "W-MON",
    "month": "MS",
    "quarter": "QS",
    "year": "YS",
}
_SEASONS = {"day": 7, "week": 52, "month": 12, "quarter": 4, "year": 1}


class ForecastExecutionError(ValueError):
    """La serie no cumple el contrato mínimo para ajustar un modelo."""


def execute_forecast(
    dataset: Dataset,
    spec: ForecastSpec,
) -> tuple[Dataset, dict[str, Any]]:
    """Agrega una serie temporal, selecciona ETS por AIC y materializa bandas."""
    if spec.time_field not in dataset.columns or spec.value_field not in dataset.columns:
        raise ForecastExecutionError("forecast fields are absent from the query result")
    frame = pd.DataFrame(dataset.rows)
    frame[spec.time_field] = pd.to_datetime(frame[spec.time_field], errors="coerce")
    frame[spec.value_field] = pd.to_numeric(frame[spec.value_field], errors="coerce")
    frame = frame.dropna(subset=[spec.time_field, spec.value_field])
    if frame.empty:
        raise ForecastExecutionError("forecast series has no numeric dated observations")
    series = (
        frame.set_index(spec.time_field)[spec.value_field]
        .resample(_FREQUENCIES[spec.period])
        .sum(min_count=1)
        .astype(float)
    )
    if spec.fill_missing:
        series = series.interpolate(limit_direction="both")
    else:
        series = series.dropna()
    train_size = len(series) - spec.ignore_last
    if train_size < 4:
        raise ForecastExecutionError("forecast requires at least four training periods")
    train = series.iloc[:train_size]

    candidates = [_fit(train, seasonal_periods=None)]
    seasonal_periods = _SEASONS[spec.period]
    if spec.seasonal and seasonal_periods > 1 and len(train) >= seasonal_periods * 2:
        try:
            candidates.append(_fit(train, seasonal_periods=seasonal_periods))
        except ForecastExecutionError:
            pass
    fit, selected_season = min(candidates, key=lambda item: float(item[0].aic))

    prediction = fit.get_prediction(
        start=len(train),
        end=len(train) + spec.horizon - 1,
    ).summary_frame(alpha=1 - spec.confidence_level / 100)
    forecast_index = pd.date_range(
        start=train.index[-1] + pd.tseries.frequencies.to_offset(_FREQUENCIES[spec.period]),
        periods=spec.horizon,
        freq=_FREQUENCIES[spec.period],
    )
    prediction.index = forecast_index
    columns = (
        spec.time_field,
        spec.value_field,
        "Forecast Indicator",
        "Forecast Lower",
        "Forecast Upper",
    )
    records = [
        {
            spec.time_field: timestamp.isoformat(),
            spec.value_field: float(value),
            "Forecast Indicator": "Actual",
            "Forecast Lower": None,
            "Forecast Upper": None,
        }
        for timestamp, value in train.items()
    ]
    records.extend(
        {
            spec.time_field: timestamp.isoformat(),
            spec.value_field: float(row["mean"]),
            "Forecast Indicator": "Estimate",
            "Forecast Lower": (float(row["pi_lower"]) if spec.prediction_intervals else None),
            "Forecast Upper": (float(row["pi_upper"]) if spec.prediction_intervals else None),
        }
        for timestamp, row in prediction.iterrows()
    )
    result = Dataset.from_records(f"{dataset.name}__forecast", records, columns=columns)
    metadata = {
        "algorithm": "statsmodels.ETSModel",
        "selection": "minimum_aic",
        "model": "additive_error_additive_trend",
        "seasonal_periods": selected_season,
        "training_periods": len(train),
        "horizon": spec.horizon,
        "confidence_level": spec.confidence_level,
        "fidelity": "approximate",
        "limitations": [
            "Tableau auto-season model selection is proprietary.",
            "DataVIZ selects documented ETS candidates deterministically by AIC.",
        ],
    }
    return result, metadata


def _fit(
    series: pd.Series,
    *,
    seasonal_periods: int | None,
) -> tuple[ETSResultsWrapper, int | None]:
    model = ETSModel(
        series,
        error="add",
        trend="add",
        seasonal="add" if seasonal_periods else None,
        seasonal_periods=seasonal_periods,
    )
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", ConvergenceWarning)
            fit = cast(ETSResultsWrapper, model.fit(disp=False))
    except ConvergenceWarning as exc:
        raise ForecastExecutionError("ETS candidate did not converge") from exc
    return fit, seasonal_periods


__all__ = ["ForecastExecutionError", "execute_forecast"]
