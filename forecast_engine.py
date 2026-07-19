"""The daily forecast engine: one morning's configuration and Sales history in,
that morning's Demand Forecast log rows out.

    run_forecasts(config, sales, as_of)
      -> DataFrame[as_of, config_version, model, target, target_date,
                   forecast_quantity]

This is the primary seam of the daily forecast log (ADR 0006). It is a *pure
function of its three arguments* — no database, no clock, no filesystem, no
model state carried between days — so all forecasting behavior is testable
without a Postgres or a scheduler, and every logged row is reproducible from the
Sales data alone. Wiring it to the database is db.py's job (ticket 04); running
it each morning is the scheduled workflow's (ticket 05).

The configuration is the versioned JSON document stored in
`forecast_configs.config`:

    {"version": 1,
     "horizon_days": 7,
     "models": {"ewma": {"halflife_weeks": 3}, "holt_winters": {}},
     "targets": {"wheat_bagels": ["everything", "plain", "sesame"],
                 "turkey_club": ["turkey club"]}}

Three things are deliberately *not* here, and belong to the analysis layer that
reads the log:

- **No lead.** The engine forecasts `as_of+1 .. as_of+horizon_days` for every
  Target, full stop. Lead is `target_date - as_of`, derived at read time, so
  "3 days out means Poolish" stays a filter over the log rather than a parameter
  frozen into the forecaster.
- **No buffering and no scoring.** Only raw point Demand Forecasts are logged;
  p95_buffer, pinball and coverage are reads over the log joined to actual Sales.
- **No bottom-up mode.** A Forecast Target is resolved top-down — its members'
  Sales summed into one series that a model is fit to. Forecasting members
  separately and summing them is a read-time aggregation over Targets that were
  each logged in their own right (CONTEXT.md).
"""
import datetime as dt
from typing import Dict, List, Sequence, Tuple

import pandas as pd

import models

LOG_COLUMNS = [
    "as_of",
    "config_version",
    "model",
    "target",
    "target_date",
    "forecast_quantity",
]


def _accepted(
    hyperparameters: Dict, model: str, accepted: Sequence[str]
) -> Dict:
    """The configured hyperparameters for `model`, rejecting any key it does not
    take.

    A misspelled key must not be tolerated. Silently falling back to the code's
    default would log a forecast under a `config_version` claiming a
    hyperparameter that never reached the model — precisely the attribution the
    log exists to provide (ADR 0006: "provenance is the `config_version`
    stamp"). Better to fail the morning's run than to record an unreproducible
    row.
    """
    unknown = sorted(set(hyperparameters) - set(accepted))
    if unknown:
        raise ValueError(
            f"Model {model!r} was configured with hyperparameter(s) {unknown}, "
            f"which it does not take. It accepts {sorted(accepted)} — fix the "
            "configuration's `models` entry"
        )
    return hyperparameters


def _ewma(
    sales: pd.DataFrame,
    as_of: dt.date,
    scope: Sequence[str],
    horizon: Tuple[int, int],
    hyperparameters: Dict,
) -> pd.DataFrame:
    """EWMA under its configured half-life, in weeks — which is also in
    same-weekday observations, since the series a half-life fades over is one
    Sale per week."""
    _accepted(hyperparameters, "ewma", ["halflife_weeks"])
    return models.ewma_forecast(
        sales,
        as_of,
        scope,
        halflife=hyperparameters.get("halflife_weeks", models.EWMA_HALFLIFE_WEEKS),
        horizon=horizon,
    )


def _holt_winters(
    sales: pd.DataFrame,
    as_of: dt.date,
    scope: Sequence[str],
    horizon: Tuple[int, int],
    hyperparameters: Dict,
) -> pd.DataFrame:
    """Holt-Winters / ETS, which takes no hyperparameters: its trend, seasonal
    and initialization choices are fixed in models.ets_forecast so that every
    logged row was fit the same way. Its config entry is therefore `{}`, and
    anything else in it is a mistake worth hearing about."""
    _accepted(hyperparameters, "holt_winters", [])
    return models.ets_forecast(sales, as_of, scope, horizon=horizon)


# The models the log may contain, keyed by the name that is stored in the
# `forecasts.model` column and written in the config's `models` map. Each entry
# adapts that model's configured hyperparameters — named in the config in the
# shop's terms ("halflife_weeks"), not the callable's — onto the one definition
# of the model in models.py, and rejects keys that model does not take. A name
# absent here is a loud error rather than a silently skipped model: a config
# naming a model that does not run would leave a gap in the log that looks,
# later, exactly like a failed run.
#
# Named MODEL_RUNNERS, not MODELS, because this module also `import models`:
# these are the engine's adapters, not the model definitions themselves.
MODEL_RUNNERS = {
    "ewma": _ewma,
    "holt_winters": _holt_winters,
}


def _target_series(
    sales: pd.DataFrame, target: str, member_products: Sequence[str]
) -> pd.DataFrame:
    """One Forecast Target's Sales as a single series relabeled with the Target
    name: its member Products' quantities summed per date.

    Sum-of-one is the lone-Product case — no special path — so what a model
    receives is the same shape whether the Target is a group or a single
    Product. An unknown member is a loud error, mirroring
    forecast.unexpected_products: a Target quietly missing a member would log a
    total that silently understates the group it claims to be. A Target with no
    members at all is the same mistake in its extreme — it would forecast
    nothing while looking, in the log, like a model that failed to run.
    """
    if not len(member_products):
        raise ValueError(
            f"Forecast Target {target!r} names no Products. A Target is a group "
            "of one or more Products summed into one series — give it members, "
            "or remove it from the configuration"
        )

    unknown = sorted(set(member_products) - set(sales["product"]))
    if unknown:
        raise ValueError(
            f"Forecast Target {target!r} names Product(s) {unknown}, which are "
            f"not in the Sales history. It holds "
            f"{sorted(set(sales['product']))} — fix the Target's members in the "
            "configuration, or check that the Sales history is complete"
        )

    members = sales[sales["product"].isin(member_products)]
    summed = members.groupby("date", as_index=False)["quantity"].sum()
    summed.insert(0, "product", target)
    return summed


def run_forecasts(
    config: Dict, sales: pd.DataFrame, as_of: dt.date
) -> pd.DataFrame:
    """Every configured model's point Demand Forecasts for every configured
    Forecast Target, across `as_of+1 .. as_of+config["horizon_days"]`.

    Each Target is resolved to one summed series (see _target_series) and each
    model is pointed at it by a lone `[target]` scope, so one definition of each
    model serves both the baked varieties and any Target the owner configures.
    Models read only Sales strictly before `as_of` (forecast.history_before), so
    a run — or a replayed past origin — never sees the days it forecasts. Every
    row is stamped with `config["version"]`, which is the provenance the
    write-once log is keyed and read by.

    The horizon is the *span asked for*, not a guarantee of row count. A model
    emits no row for a target date it has no evidence for — a Target that has
    never sold on a Tuesday yields no Tuesday forecast (see
    models._same_weekday_reduce) — and the engine does not fabricate one, for
    the same reason forecast.forecast_demand does not: a made-up zero would be
    scored later as a confident forecast of nothing rather than as the silence
    it really is. On the shop's actual Targets, which sell essentially every
    open day, every target date is covered; a sparsely-sold Target logs the days
    it can support. Callers wanting to know about the gaps should compare
    against forecast.sparse_weekday_counts rather than trust the row count.
    """
    horizon = (1, int(config["horizon_days"]))
    version = config["version"]

    unknown_models = sorted(set(config["models"]) - set(MODEL_RUNNERS))
    if unknown_models:
        raise ValueError(
            f"Configuration names model(s) {unknown_models}, which this engine "
            f"cannot run. It knows {sorted(MODEL_RUNNERS)} — add the model to "
            "forecast_engine.MODEL_RUNNERS, or fix the configuration"
        )

    records: List[dict] = []
    for target, member_products in config["targets"].items():
        series = _target_series(sales, target, member_products)
        for name, hyperparameters in config["models"].items():
            forecasts = MODEL_RUNNERS[name](
                series, as_of, [target], horizon, hyperparameters
            )
            for row in forecasts.itertuples():
                records.append(
                    {
                        "as_of": as_of,
                        "config_version": version,
                        "model": name,
                        "target": target,
                        "target_date": row.date.date(),
                        "forecast_quantity": float(row.forecast_quantity),
                    }
                )

    log = pd.DataFrame(records, columns=LOG_COLUMNS)
    log["forecast_quantity"] = log["forecast_quantity"].astype(float)
    return log.sort_values(
        ["model", "target", "target_date"], ignore_index=True
    )
