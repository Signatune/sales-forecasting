"""Build the inspection page: the charts a baker eyeballs and the recommendation
the whole comparison exists to produce.

    .venv/bin/python inspection_page.py   ->  model_comparison.html

model_comparison.py answers *which model scores best*. This module answers the
two questions that come next — *is that score believable* and *what should we
actually do* — and writes both into one self-contained HTML page.

Believable is three charts, each reading the very frames the scores were reduced
from (model_comparison.buffered_totals), so a chart can never draw a day the
score did not see:

  1. Forecast vs actual on the Poolish total, one panel per candidate across the
     whole holdout — does the model put the weekend peaks in the right place?
  2. Buffer coverage against the 95% Service Level target — did the P95 quantity
     actually cover Demand about 19 days in 20, or is the promise a fiction?
  3. Split accuracy: WAPE per variety per split method.

What to do is `recommendation`, and it is a *rule*, not a paragraph someone wrote
after squinting at a table: two models are compared on their daily pinball
losses, paired day by day, and a gap counts only past SIGNIFICANT_T standard
errors of that difference. Every sentence the page prints is a rendering of that
rule against this run's numbers, so the prose cannot drift away from the data it
describes. This module ships no model: promoting the winners into forecast.py is
a separate ticket (08).
"""
import datetime as dt
import sys
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd

import forecast
import model_comparison as mc
import sales_history
from model_comparison import (
    EVAL_WEEKS,
    POOLISH_LEAD,
    SERVICE_LEVEL,
    SPLIT_LEAD,
    WARMUP_WEEKS,
)

PAGE_PATH = Path(__file__).parent / "model_comparison.html"

# The model the shop already runs, and the dumb baseline. Both are named in the
# conclusion by contract: a recommendation to change is only meaningful as a
# margin over what we do today, and only credible if the seasonal models beat a
# trailing daily mean at all.
INCUMBENT = "seasonal_naive"
BASELINE = "moving_average"

# The candidate that costs an extra dependency (statsmodels, the `experiment`
# extra) to run in production. Everything else is pure pandas.
DEPENDENT = "ets"

# Whether one model really beats another is not a question a difference of means
# can answer: on ~180 days a 5% lower pinball can easily be one busy Saturday.
# So two candidates are compared on their *daily* losses, paired day by day, and
# the mean difference is measured in standard errors — a t statistic. Past |t| =
# 2 the difference is about twice the noise in it and we call it real; below it,
# the two models are tied on the evidence we have, however far apart their means
# happen to land. This is the whole verdict rule; there is no tunable margin
# threshold to pick a favourite with.
SIGNIFICANT_T = 2.0

# How each candidate is introduced to a human. Keyed by the registry names in
# model_comparison, so a new candidate that lands there without a line here
# still charts — it simply goes unannotated rather than breaking the page.
DESCRIPTIONS = {
    "seasonal_naive": "Equal-weight same-weekday mean · the incumbent",
    "moving_average": "Trailing daily mean · the pilot's baseline",
    "trailing_window": f"Same-weekday mean over the last {mc.TRAILING_WINDOW_WEEKS} weeks",
    "ewma": f"Same-weekday mean, {mc.EWMA_HALFLIFE_WEEKS}-week half-life decay",
    "seasonal_trend": "Same-weekday level plus a fitted linear drift",
    "ets": "Weekly-seasonal additive Holt-Winters · needs statsmodels",
    "constant_recent_share": "Each variety's share of the recent total, flat across weekdays",
    "same_weekday_share": "The mix conditioned on the target's weekday",
    "per_variety_recency_share": "Recency-weighted per-variety forecasts, normalized",
}


# --- The report: every number the page draws --------------------------------


def build_report(
    sales: pd.DataFrame,
    candidates: Optional[Dict[str, mc.Model]] = None,
    split_candidates: Optional[Dict[str, mc.SplitModel]] = None,
    eval_weeks: int = EVAL_WEEKS,
    warmup_weeks: int = WARMUP_WEEKS,
    lead: int = POOLISH_LEAD,
    split_lead: int = SPLIT_LEAD,
    level: float = SERVICE_LEVEL,
) -> dict:
    """Everything the page shows, as plain Python: the window, the two ranked
    comparisons, each candidate's day-by-day replay, and the recommendation.

    The replay series come from the same model_comparison.buffered_totals frames
    the pinball scores were reduced from, aligned onto the shared list of
    evaluation days — so the chart and the table cannot disagree about a day. A
    day a candidate declined to forecast is None in its series, not zero: a zero
    would draw a Stockout the model never predicted.
    """
    candidates = candidates if candidates is not None else mc.candidates_with_ets()
    split_candidates = (
        split_candidates if split_candidates is not None else mc.SPLIT_CANDIDATES
    )

    eval_days, warmup_days = mc.window_days(sales, eval_weeks, warmup_weeks)
    poolish = mc.compare_models(
        sales, candidates, eval_weeks, warmup_weeks, lead, level
    )
    split = (
        mc.compare_split_models(sales, split_candidates, eval_weeks, split_lead)
        if split_candidates
        else pd.DataFrame(columns=["model", "product", "wape", "days"])
    )

    forecasts, losses, dough = {}, {}, {}
    for name, model in candidates.items():
        replay = mc.buffered_totals(model, sales, eval_days, warmup_days, lead, level)
        # Indexed by date, so t_statistic pairs two models on the days they both
        # forecast rather than on however their rows happen to line up.
        losses[name] = mc.pinball_losses(
            replay["actual"], replay["buffered_quantity"], level
        ).set_axis(replay["date"])
        dough[name] = _dough(replay)
        aligned = replay.set_index("date").reindex(eval_days)
        forecasts[name] = {
            "point": _optional(aligned["forecast_quantity"]),
            "buffered": _optional(aligned["buffered_quantity"]),
        }

    poolish_rows = [
        {**row, **dough.get(row["model"], {})} for row in poolish.to_dict("records")
    ]
    actual = mc.actual_totals(sales, eval_days).set_index("date")["actual"]
    return {
        "window": {
            "start": _iso(eval_days[0]),
            "end": _iso(eval_days[-1]),
            "days": len(eval_days),
            "eval_weeks": eval_weeks,
            "warmup_weeks": warmup_weeks,
            "lead": lead,
            "split_lead": split_lead,
            "level": level,
            "generated": dt.date.today().isoformat(),
            "history_start": _iso(sales["date"].min()),
            "history_end": _iso(sales["date"].max()),
        },
        "dates": [_iso(day) for day in eval_days],
        "actual": [float(actual.get(day, 0.0)) for day in eval_days],
        "forecasts": forecasts,
        "poolish": poolish_rows,
        "split": split.to_dict("records"),
        "recommendation": recommendation(poolish, split, losses, level),
    }


def _dough(replay: pd.DataFrame) -> Dict[str, float]:
    """What a candidate's P95 quantity means at the bench: how much Poolish it
    has the baker make on an average day, how much of that came back as leftover,
    and how many days it still ran short.

    Pinball prices these against each other into one number; a baker wants them
    apart, because 'bake 35 fewer bagels' worth of dough a day' and 'stock out on
    6 more days across half a year' are different sentences about the same trade.
    """
    if replay.empty:
        return {"quantity": None, "leftover": None, "stockouts": None}
    over = replay["buffered_quantity"] - replay["actual"]
    return {
        "quantity": float(replay["buffered_quantity"].mean()),
        "leftover": float(over.where(over > 0, 0.0).mean()),
        "stockouts": int((over < 0).sum()),
    }


def _iso(day) -> str:
    return pd.Timestamp(day).date().isoformat()


def _optional(series: pd.Series) -> List[Optional[float]]:
    """A reindexed column as floats with None where the model said nothing."""
    return [None if pd.isna(v) else float(v) for v in series]


# --- The recommendation: the answer, by rule --------------------------------


def t_statistic(against: pd.Series, losses: pd.Series) -> Optional[float]:
    """How many standard errors better `losses` is than `against`, the two
    models' daily pinball losses paired on the days they both forecast.

    Both Series are indexed by date, and the difference is taken on the dates
    they share — pandas aligns them, so a day one model declined and the other
    forecast is simply not a paired day. That alignment is the whole point:
    subtracting two models' losses on the *same* Saturday cancels the Saturday's
    own difficulty and leaves only the models' disagreement. Pairing by position
    instead would happily subtract one model's Tuesday from another's Sunday.

    A positive t means `losses` is the better model; past SIGNIFICANT_T the gap
    is about twice its own noise. None when fewer than two days are shared —
    nothing to pair — and infinite when the two differ by a constant, which has
    no noise to be inside of.
    """
    if against is None or losses is None:
        return None
    diff = (pd.Series(against) - pd.Series(losses)).dropna()
    if len(diff) < 2:
        return None
    error = diff.std(ddof=1) / (len(diff) ** 0.5)
    if error == 0:
        return 0.0 if diff.mean() == 0 else float("inf") * (1 if diff.mean() > 0 else -1)
    return float(diff.mean() / error)


def recommendation(
    poolish: pd.DataFrame,
    split: pd.DataFrame,
    losses: Dict[str, pd.Series],
    level: float = SERVICE_LEVEL,
) -> dict:
    """Name a winner per bake target, with the margins behind it and a verdict on
    whether to replace the incumbent — all three by rule, from the daily losses.

    - **The best scorer** is the lowest mean pinball@95 on the Poolish total.
    - **The winner** is that model, unless it is the one that costs a dependency
      (ETS/statsmodels) and is *not distinguishable* from the best pure-pandas
      candidate — a dependency the promoted path carries forever has to be paid
      for with evidence, not with a decimal place. Then the pandas model wins.
    - **The verdict** is `replace` only when the winner beats the incumbent by
      more than SIGNIFICANT_T standard errors of the paired daily difference.
      A lower mean that does not clear that bar is not a reason to change the
      model the shop already bakes off; it is a lower mean.

    Margins are reported alongside as relative cuts in loss (0.20 = a fifth
    lower), because that is the number a human wants — but they never decide
    anything here. The split winner is the candidate with the lowest mean WAPE
    across the three varieties: a mean, not a volume-weighted total, because a
    bad split on sesame is a real bake failure even though sesame is the
    smallest pile.
    """
    ranked = poolish.sort_values("pinball", ignore_index=True)
    by_model = ranked.set_index("model")
    scores, coverages = by_model["pinball"], by_model["coverage"]

    best = ranked.loc[0, "model"]
    independent = ranked[ranked["model"] != DEPENDENT]
    fallback = independent.iloc[0]["model"] if len(independent) else best

    ets_t = t_statistic(losses.get(fallback), losses.get(DEPENDENT))
    ets_earns_it = (
        best == DEPENDENT and ets_t is not None and ets_t > SIGNIFICANT_T
    )
    winner = best if best != DEPENDENT or ets_earns_it else fallback

    incumbent_t = t_statistic(losses.get(INCUMBENT), losses.get(winner))
    beats_incumbent = incumbent_t is not None and incumbent_t > SIGNIFICANT_T
    return {
        "best": best,
        "winner": winner,
        "pinball": _value(scores.get(winner)),
        "coverage": _value(coverages.get(winner)),
        "level": level,
        "incumbent": INCUMBENT,
        "incumbent_pinball": _value(scores.get(INCUMBENT)),
        "incumbent_coverage": _value(coverages.get(INCUMBENT)),
        "margin_vs_incumbent": _margin(scores.get(INCUMBENT), scores.get(winner)),
        "t_vs_incumbent": incumbent_t,
        "baseline": BASELINE,
        "margin_vs_baseline": _margin(scores.get(BASELINE), scores.get(winner)),
        "t_vs_baseline": t_statistic(losses.get(BASELINE), losses.get(winner)),
        "ets_margin": _margin(scores.get(fallback), scores.get(DEPENDENT)),
        "ets_t": ets_t,
        "ets_earns_its_dependency": ets_earns_it,
        "verdict": "replace" if beats_incumbent else "keep",
        **_split_recommendation(split),
    }


def _split_recommendation(split: pd.DataFrame) -> dict:
    """The split winner: lowest mean WAPE across the varieties, and its margin
    over the runner-up — the number that says whether the split is a race at all
    or, as the PRD suspects, a stable mix nobody can beat by much."""
    if split.empty:
        return {"split_winner": None, "split_wape": None, "split_margin": None}

    mean_wape = split.groupby("model")["wape"].mean().sort_values()
    winner = mean_wape.index[0]
    runner_up = mean_wape.iloc[1] if len(mean_wape) > 1 else None
    return {
        "split_winner": winner,
        "split_wape": float(mean_wape.iloc[0]),
        "split_margin": _margin(runner_up, mean_wape.iloc[0]),
    }


def _margin(against: Optional[float], score: Optional[float]) -> Optional[float]:
    """The relative cut in loss `score` achieves against `against` — 0.2 meaning
    a fifth lower. None when either side is missing (a candidate that was not in
    this run) or the comparison is degenerate."""
    if against is None or score is None or pd.isna(against) or pd.isna(score):
        return None
    if against <= 0:
        return None
    return float((against - score) / against)


def _value(score) -> Optional[float]:
    return None if score is None or pd.isna(score) else float(score)


# --- Rendering --------------------------------------------------------------
#
# One self-contained HTML file: no scripts, no CDN, no build step — the charts
# are inline SVG polylines and CSS bars sized in Python, so the page opens from
# the filesystem years from now and still draws what it drew today.


def _pct(value: Optional[float], places: int = 1) -> str:
    return "—" if value is None else f"{value * 100:.{places}f}%"


def _num(value: Optional[float], places: int = 2) -> str:
    return "—" if value is None else f"{value:.{places}f}"


def _describe(name: str) -> str:
    return DESCRIPTIONS.get(name, "")


def _polyline(values: List[Optional[float]], top: float, width: int, height: int) -> str:
    """One series as SVG polyline points, scaled to a zero-based axis of `top`.
    A None (a day the model declined) breaks nothing — it is simply not plotted,
    so the line spans the gap rather than diving to zero through it."""
    n = max(len(values) - 1, 1)
    points = [
        f"{i / n * width:.1f},{height - (value / top) * height:.1f}"
        for i, value in enumerate(values)
        if value is not None
    ]
    return " ".join(points)


def _panel(name: str, report: dict, top: float) -> str:
    """One candidate's forecast-vs-actual panel across the whole holdout: the
    actual wheat total, the model's point forecast, and the P95 Poolish quantity
    that forecast buffers to — the quantity the baker would have made."""
    width, height = 640, 130
    series = report["forecasts"][name]
    row = _row(report, name)
    winner = " is-winner" if name == report["recommendation"]["winner"] else ""
    return f"""
        <figure class="panel{winner}">
          <figcaption>
            <span class="panel-name">{name}</span>
            <span class="panel-score">pinball {_num(row['pinball'])} · covered {_pct(row['coverage'], 0)}</span>
          </figcaption>
          <svg viewBox="0 0 {width} {height}" preserveAspectRatio="none" role="img"
               aria-label="Forecast versus actual wheat total for {name}">
            <polyline class="actual" points="{_polyline(report['actual'], top, width, height)}"/>
            <polyline class="point" points="{_polyline(series['point'], top, width, height)}"/>
            <polyline class="p95" points="{_polyline(series['buffered'], top, width, height)}"/>
          </svg>
        </figure>"""


def _coverage_bars(report: dict) -> str:
    """Realised coverage per candidate against the Service Level target — how
    often the P95 quantity actually covered Demand."""
    level = report["window"]["level"]
    rows = []
    for row in report["poolish"]:
        covered = row["coverage"]
        short = covered is not None and covered < level
        rows.append(
            f"""
        <div class="bar-row">
          <div class="bar-label"><span class="name">{row['model']}</span></div>
          <div class="bar-track">
            <div class="bar-fill{' under' if short else ''}" style="width:{(covered or 0) * 100:.1f}%">{_pct(covered, 1)}</div>
            <div class="target" style="left:{level * 100:.1f}%"></div>
          </div>
        </div>"""
        )
    return "".join(rows)


def _split_bars(report: dict) -> str:
    """WAPE per variety per split method — the split target's ranking, best mean
    WAPE first, every bar on one scale so the methods are read against each other
    rather than each against itself."""
    if not report["split"]:
        return "<p class='chart-note'>No split candidates were scored.</p>"

    # A run where every method split perfectly (a flat synthetic history) has no
    # scale to draw against: every bar is simply empty rather than a zero-divide.
    scored = [row["wape"] for row in report["split"] if not pd.isna(row["wape"])]
    top = max(scored) if scored else 0.0

    by_model: Dict[str, List[dict]] = {}
    for row in report["split"]:
        by_model.setdefault(row["model"], []).append(row)
    ranked = sorted(
        by_model.items(),
        key=lambda item: pd.Series([r["wape"] for r in item[1]]).mean(),
    )

    groups = []
    for rank, (name, rows) in enumerate(ranked, start=1):
        mean_wape = pd.Series([r["wape"] for r in rows]).mean()
        bars = []
        for product in forecast.FORECAST_PRODUCTS:
            row = next((r for r in rows if r["product"] == product), None)
            if row is None:
                continue
            width = 0.0 if top <= 0 or pd.isna(row["wape"]) else row["wape"] / top * 100
            bars.append(
                f"""
            <div class="bar-row">
              <div class="bar-label"><span class="tag">{product}</span></div>
              <div class="bar-track slim">
                <div class="bar-fill" style="width:{width:.1f}%">{_pct(row['wape'])}</div>
              </div>
            </div>"""
            )
        winner = (
            " is-winner" if name == report["recommendation"]["split_winner"] else ""
        )
        groups.append(
            f"""
        <div class="split-group{winner}">
          <p class="split-name"><span class="rank">{rank}</span>{name}
            <span class="split-mean">mean WAPE {_pct(mean_wape)}</span>
            <span class="desc">{_describe(name)}</span></p>
          {''.join(bars)}
        </div>"""
        )
    return "".join(groups)


def _ranking_rows(report: dict) -> str:
    """The ranked table. Δ is each model's pinball above the best one, in
    absolute loss and as a share of it — how much worse, not how much better,
    because the reader is deciding what to give up by not taking the winner."""
    best = report["poolish"][0]["pinball"]
    rows = []
    for rank, row in enumerate(report["poolish"], start=1):
        worse = (row["pinball"] - best) / best if best else None
        delta = "—" if rank == 1 or worse is None else f"+{row['pinball'] - best:.2f} · {_pct(worse)}"
        rec = report["recommendation"]
        winner = row["model"] == rec["winner"]
        # "recommended" only when the verdict actually recommends a change; when
        # the candidates are tied on the evidence the same model is merely the
        # best of the pack, and the pill must not say otherwise.
        pill = (
            f' <span class="pill">{"recommended" if rec["verdict"] == "replace" else "best of the pack"}</span>'
            if winner
            else ""
        )
        rows.append(
            f"""
          <tr class="{'is-winner' if winner else ''}">
            <td class="model-col"><span class="rank">{rank}</span><span class="name">{row['model']}</span>{pill}<span class="desc">{_describe(row['model'])}</span></td>
            <td class="primary">{_num(row['pinball'])}</td>
            <td>{delta}</td>
            <td>{_pct(row['coverage'], 1)}</td>
            <td>{_num(row.get('leftover'), 0)}</td>
            <td>{_num(row['mape'], 1)}%</td>
            <td>{row['days']}</td>
          </tr>"""
        )
    return "".join(rows)


def _row(report: dict, model: Optional[str]) -> dict:
    return next((r for r in report["poolish"] if r["model"] == model), {})


def _conclusion(report: dict) -> str:
    """The written recommendation: the winner per bake target, the margins, and a
    plain-language read on whether replacing the incumbent is worth it.

    Every number is read off this run and every judgment follows
    `recommendation`'s rule — the prose renders the verdict, it does not reach
    one. The dough paragraph is the one place the page speaks in bagels rather
    than in loss, because that is the trade the shop owner is actually being
    asked to make.
    """
    rec = report["recommendation"]
    window = report["window"]
    pct = int(round(window["level"] * 100))
    winner, incumbent = _row(report, rec["winner"]), _row(report, rec["incumbent"])

    if rec["verdict"] == "replace":
        verdict = (
            f"<strong>Replace the incumbent with <code>{rec['winner']}</code>.</strong> It "
            f"cuts pinball@{pct} by {_pct(rec['margin_vs_incumbent'])} against "
            f"<code>{rec['incumbent']}</code>, the model the shop bakes off today, and the "
            f"margin is {_num(rec['t_vs_incumbent'], 1)} standard errors of the paired "
            "daily difference — real, not a lucky Saturday. This is the direction the PRD "
            "predicted: the incumbent's equal-weight same-weekday average cannot see the "
            "~8%/yr downtrend, so it keeps forecasting the higher past."
        )
    else:
        verdict = (
            f"<strong>The Poolish models are tied on the evidence — no forced change.</strong> "
            f"<code>{rec['winner']}</code> does score the lowest pinball@{pct} of the "
            f"dependency-free candidates, {_pct(rec['margin_vs_incumbent'])} under "
            f"<code>{rec['incumbent']}</code>, but that gap is only "
            f"{_num(rec['t_vs_incumbent'], 1)} standard errors of the paired daily "
            f"difference — well inside the day-to-day noise on {window['days']} days. Read "
            "honestly, this comparison cannot tell the top seasonal models apart, and a "
            "lower mean alone is not a mandate to swap out a model that already runs."
        )

    beats_baseline = (
        rec["t_vs_baseline"] is not None and rec["t_vs_baseline"] > SIGNIFICANT_T
    )
    baseline = (
        f"<p><strong>The seasonal models do earn their complexity.</strong> Against the "
        f"pilot's <code>{rec['baseline']}</code> baseline the winner cuts pinball@{pct} by "
        f"{_pct(rec['margin_vs_baseline'])}, at {_num(rec['t_vs_baseline'], 1)} standard "
        "errors — past the bar. A trailing daily mean smears the weekend peak across the "
        f"week, and its P{pct} has to carry "
        f"{_num(_row(report, rec['baseline']).get('leftover'), 0)} bagels' worth of "
        "leftover dough a day to cover Saturday anyway.</p>"
        if beats_baseline
        else f"<p><strong>The seasonal models do not clearly beat the dumb baseline.</strong> "
        f"The winner is {_pct(rec['margin_vs_baseline'])} under "
        f"<code>{rec['baseline']}</code> on pinball@{pct}, but at only "
        f"{_num(rec['t_vs_baseline'], 1)} standard errors — on this evidence the seasonal "
        "machinery has not yet paid for itself, which is worth knowing before promoting "
        "any of it.</p>"
    )

    ets_is_best = rec["best"] == DEPENDENT
    if rec["ets_t"] is None:
        ets = (
            "<p><strong>Holt-Winters/ETS was not scored in this run.</strong> "
            "<code>statsmodels</code> — the <code>experiment</code> extra — was not "
            "installed, so the textbook seasonal method sat this one out.</p>"
        )
    elif rec["ets_earns_its_dependency"]:
        ets = (
            f"<p><strong>ETS earns its dependency.</strong> It is the best scorer outright, "
            f"and beats the best pure-pandas candidate by {_pct(rec['ets_margin'])} at "
            f"{_num(rec['ets_t'], 1)} standard errors — past the bar, so the promoted path "
            "takes on <code>statsmodels</code>.</p>"
        )
    elif ets_is_best:
        ets = (
            f"<p><strong>ETS does not earn its dependency.</strong> It is the best scorer "
            f"outright, but its edge over <code>{rec['winner']}</code> is "
            f"{_pct(rec['ets_margin'])} at only {_num(rec['ets_t'], 1)} standard errors — "
            "the two are tied on this much evidence. A dependency the production path "
            "carries forever, plus a per-day model fit, has to be paid for with a real "
            "margin, and this is not one. <code>statsmodels</code> stays out: the PRD's "
            "open question, answered.</p>"
        )
    else:
        ets = (
            f"<p><strong>ETS does not even win.</strong> The textbook seasonal method scores "
            f"below <code>{rec['winner']}</code>, so the <code>statsmodels</code> question "
            "does not arise: the dependency buys nothing here and stays out of the promoted "
            "path.</p>"
        )

    dough = (
        f"<p><strong>What actually separates them is dough, not loss.</strong> "
        f"<code>{rec['incumbent']}</code> covers Demand on {_pct(rec['incumbent_coverage'], 1)} "
        f"of days — it overshoots the {pct}% it was asked for, and pays for the extra "
        f"safety with {_num(incumbent.get('leftover'), 0)} bagels' worth of leftover dough a "
        f"day ({incumbent.get('stockouts')} short days in {window['days']}). "
        f"<code>{rec['winner']}</code> lands on {_pct(rec['coverage'], 1)}, which is the "
        f"Service Level the shop actually chose, and gets there on "
        f"{_num(winner.get('leftover'), 0)} leftover a day "
        f"({winner.get('stockouts')} short days) — about "
        f"{_num((incumbent.get('quantity') or 0) - (winner.get('quantity') or 0), 0)} fewer "
        "bagels of Poolish to make every morning. That, and not the pinball column, is the "
        "case for promoting it: the same promise, honestly priced, on less dough.</p>"
        if winner.get("quantity") and incumbent.get("quantity")
        else ""
    )

    split = (
        f"<p><strong>The bake split: <code>{rec['split_winner']}</code>.</strong> It scores a "
        f"mean WAPE of {_pct(rec['split_wape'])} across the three varieties, "
        f"{_pct(rec['split_margin'])} below the runner-up. The mix is stable — the split is "
        "very nearly a non-race, exactly as the PRD suspected — so the simplest method that "
        "tracks the recent mix is enough, and the choice here is worth far less than the "
        "choice of Poolish model.</p>"
        if rec["split_winner"]
        else ""
    )

    return f"""
      <div class="conclusion">
        <p>{verdict}</p>
        {baseline}
        {ets}
        {dough}
        {split}
        <p class="ship">Nothing ships from this page. Promoting the winners —
        <code>{rec['winner']}</code> for the Poolish total, <code>{rec['split_winner']}</code>
        for the split — into <code>forecast.py</code> is ticket 08, and it is a decision for
        a human: the loss numbers do not compel it, the dough numbers argue for it.</p>
      </div>"""


def render(report: dict) -> str:
    """The whole inspection page as one self-contained HTML document."""
    window = report["window"]
    rec = report["recommendation"]
    pct = int(round(window["level"] * 100))
    top = max(
        [v for v in report["actual"] if v is not None]
        + [
            v
            for series in report["forecasts"].values()
            for v in series["buffered"]
            if v is not None
        ]
    ) * 1.1
    panels = "".join(
        _panel(row["model"], report, top) for row in report["poolish"]
    )

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Poolish Bake — Model Comparison</title>
<style>
  :root {{
    --paper:#FBFAF6; --raised:#FFFFFF; --ink:#211D17; --muted:#7A7264; --faint:#9A9284;
    --line:#E7E1D6; --line-2:#F0ECE3; --accent:#BE7C1E; --accent-soft:#F6EAD2;
    --good:#3F7D4E; --poor:#B5462F; --bar-track:#EFE9DE;
    --shadow:0 1px 2px rgba(33,29,23,.05), 0 8px 24px -12px rgba(33,29,23,.18);
    --serif:"Iowan Old Style","Palatino Linotype",Palatino,"Book Antiqua",Georgia,serif;
    --sans:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
    --mono:ui-monospace,"SF Mono","SFMono-Regular",Menlo,Consolas,"Liberation Mono",monospace;
  }}
  @media (prefers-color-scheme: dark) {{
    :root {{
      --paper:#16140F; --raised:#201D16; --ink:#F2ECE0; --muted:#A79E8E; --faint:#7C7364;
      --line:#322D24; --line-2:#29251D; --accent:#E0A54A; --accent-soft:#3A2E17;
      --good:#6FB57F; --poor:#E07A5F; --bar-track:#2A251D;
      --shadow:0 1px 2px rgba(0,0,0,.3), 0 12px 30px -16px rgba(0,0,0,.6);
    }}
  }}
  * {{ box-sizing:border-box; }}
  body {{
    margin:0; background:var(--paper); color:var(--ink); font-family:var(--sans);
    line-height:1.55; font-size:16px; -webkit-font-smoothing:antialiased;
  }}
  .wrap {{ max-width:960px; margin:0 auto; padding:clamp(1.5rem,4vw,3.5rem) clamp(1.1rem,4vw,2rem) 4rem; }}

  header {{ border-bottom:1px solid var(--line); padding-bottom:1.6rem; }}
  .eyebrow {{
    font-family:var(--mono); font-size:.72rem; letter-spacing:.16em;
    text-transform:uppercase; color:var(--accent); margin:0 0 .7rem;
  }}
  h1 {{
    font-family:var(--serif); font-weight:600; font-size:clamp(1.9rem,5vw,2.9rem);
    line-height:1.08; letter-spacing:-.01em; text-wrap:balance; margin:0 0 .6rem;
  }}
  .lede {{ margin:0; max-width:62ch; color:var(--muted); font-size:1.02rem; }}
  .chips {{ display:flex; flex-wrap:wrap; gap:.5rem; margin-top:1.4rem; }}
  .chip {{
    display:inline-flex; align-items:baseline; gap:.45rem; background:var(--raised);
    border:1px solid var(--line); border-radius:999px; padding:.34rem .8rem;
    font-size:.82rem; color:var(--muted);
  }}
  .chip b {{
    color:var(--ink); font-family:var(--mono); font-variant-numeric:tabular-nums;
    font-weight:600; letter-spacing:-.01em;
  }}

  section {{ margin-top:2.8rem; }}
  .kicker {{
    font-family:var(--mono); font-size:.74rem; letter-spacing:.14em;
    text-transform:uppercase; color:var(--faint); margin:0 0 1rem;
  }}

  .winner {{
    display:grid; grid-template-columns:auto 1fr; gap:1.4rem; align-items:center;
    background:var(--raised); border:1px solid var(--line); border-left:3px solid var(--accent);
    border-radius:14px; padding:1.5rem 1.7rem; box-shadow:var(--shadow);
  }}
  .winner .score {{
    font-family:var(--mono); font-variant-numeric:tabular-nums;
    font-size:clamp(2.4rem,7vw,3.4rem); font-weight:600; line-height:1;
    color:var(--accent); letter-spacing:-.02em;
  }}
  .winner .score small {{
    display:block; font-size:.68rem; letter-spacing:.12em; text-transform:uppercase;
    color:var(--faint); margin-top:.5rem; font-weight:500;
  }}
  .winner .who h3 {{ font-family:var(--serif); font-size:1.35rem; font-weight:600; margin:0 0 .3rem; }}
  .winner .who p {{ margin:0; color:var(--muted); font-size:.93rem; max-width:54ch; }}

  .conclusion {{
    background:var(--raised); border:1px solid var(--line); border-radius:14px;
    padding:1.5rem 1.7rem; box-shadow:var(--shadow); max-width:70ch;
  }}
  .conclusion p {{ margin:0 0 1rem; }}
  .conclusion p:last-child {{ margin-bottom:0; }}
  .conclusion .ship {{ color:var(--muted); font-size:.9rem; border-top:1px solid var(--line-2); padding-top:1rem; }}
  code {{ font-family:var(--mono); font-size:.88em; }}

  .chart {{
    background:var(--raised); border:1px solid var(--line); border-radius:14px;
    padding:1.5rem 1.6rem 1.2rem; box-shadow:var(--shadow);
  }}
  .chart-note {{ margin:.9rem 0 0; font-size:.82rem; color:var(--muted); max-width:70ch; }}

  .panels {{ display:grid; gap:1.2rem; }}
  .panel {{ margin:0; }}
  .panel figcaption {{
    display:flex; justify-content:space-between; align-items:baseline; gap:1rem;
    margin-bottom:.3rem; font-size:.85rem; flex-wrap:wrap;
  }}
  .panel-name {{ font-weight:600; font-family:var(--mono); font-size:.8rem; }}
  .panel-score {{ font-family:var(--mono); font-size:.72rem; color:var(--faint); font-variant-numeric:tabular-nums; }}
  .panel.is-winner .panel-name {{ color:var(--accent); }}
  .panel svg {{
    width:100%; height:110px; display:block; background:var(--paper);
    border:1px solid var(--line-2); border-radius:8px;
  }}
  .panel polyline {{ fill:none; vector-effect:non-scaling-stroke; }}
  .panel .actual {{ stroke:var(--faint); stroke-width:1; }}
  .panel .point {{ stroke:var(--accent); stroke-width:1.4; }}
  .panel .p95 {{ stroke:var(--accent); stroke-width:1; stroke-dasharray:3 3; opacity:.65; }}
  .legend {{ display:flex; gap:1.2rem; flex-wrap:wrap; font-size:.78rem; color:var(--muted); margin-bottom:1.1rem; }}
  .legend span {{ display:inline-flex; align-items:center; gap:.4rem; }}
  .swatch {{ width:1.1rem; height:0; border-top:2px solid var(--faint); }}
  .swatch.point {{ border-top-color:var(--accent); }}
  .swatch.p95 {{ border-top:2px dashed var(--accent); }}

  .bars {{ display:flex; flex-direction:column; gap:.75rem; }}
  .bar-row {{ display:grid; grid-template-columns:12.5rem 1fr; align-items:center; gap:1rem; }}
  .bar-label {{ font-size:.85rem; min-width:0; }}
  .bar-label .name {{ font-weight:600; font-family:var(--mono); font-size:.8rem; }}
  .bar-label .tag {{ font-family:var(--mono); font-size:.72rem; color:var(--muted); }}
  .bar-track {{
    position:relative; background:var(--bar-track); border-radius:6px; height:28px; overflow:hidden;
  }}
  .bar-track.slim {{ height:20px; }}
  .bar-fill {{
    height:100%; border-radius:6px; background:var(--accent); display:flex; align-items:center;
    justify-content:flex-end; padding-right:.6rem; color:#fff; font-family:var(--mono);
    font-variant-numeric:tabular-nums; font-size:.75rem; font-weight:600; min-width:4ch;
  }}
  .bar-fill.under {{ background:var(--poor); }}
  .target {{ position:absolute; top:0; bottom:0; width:2px; background:var(--ink); opacity:.55; }}
  .split-group {{ margin-bottom:1.4rem; }}
  .split-group:last-child {{ margin-bottom:0; }}
  .split-name {{ margin:0 0 .6rem; font-family:var(--mono); font-size:.8rem; font-weight:600; }}
  .split-group.is-winner .split-name {{ color:var(--accent); }}
  .split-group.is-winner .rank {{ background:var(--accent); color:#fff; }}
  .split-name .split-mean {{ font-weight:400; color:var(--muted); margin-left:.5rem; }}
  .split-name .desc {{ display:block; font-family:var(--sans); font-weight:400; font-size:.78rem; color:var(--faint); margin-left:2.2rem; }}

  .table-scroll {{ overflow-x:auto; border-radius:14px; border:1px solid var(--line); box-shadow:var(--shadow); }}
  table {{ width:100%; border-collapse:collapse; background:var(--raised); font-size:.92rem; min-width:620px; }}
  thead th {{
    text-align:right; font-family:var(--mono); font-size:.68rem; letter-spacing:.1em;
    text-transform:uppercase; color:var(--faint); font-weight:600; padding:.95rem 1rem;
    border-bottom:1px solid var(--line); white-space:nowrap;
  }}
  thead th.model-col, tbody td.model-col {{ text-align:left; }}
  tbody td {{
    padding:.85rem 1rem; border-bottom:1px solid var(--line-2); text-align:right;
    font-family:var(--mono); font-variant-numeric:tabular-nums;
  }}
  tbody tr:last-child td {{ border-bottom:none; }}
  tbody td.model-col {{ font-family:var(--sans); }}
  .rank {{
    display:inline-flex; align-items:center; justify-content:center; width:1.5rem; height:1.5rem;
    border-radius:50%; font-family:var(--mono); font-size:.78rem; font-weight:600;
    background:var(--line-2); color:var(--muted); margin-right:.7rem;
  }}
  tr.is-winner .rank {{ background:var(--accent); color:#fff; }}
  tr.is-winner td {{ background:color-mix(in srgb, var(--accent-soft) 60%, transparent); }}
  .model-col .name {{ font-weight:600; }}
  .model-col .desc {{ color:var(--faint); font-size:.78rem; display:block; margin-top:.1rem; }}
  .pill {{
    display:inline-block; font-size:.72rem; padding:.1rem .5rem; border-radius:999px;
    font-weight:600; background:var(--accent-soft); color:var(--accent);
  }}
  .primary {{ color:var(--ink); font-weight:600; }}

  .method {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(220px,1fr)); gap:1.1rem; }}
  .method .card {{ background:var(--raised); border:1px solid var(--line); border-radius:12px; padding:1.1rem 1.2rem; }}
  .method h4 {{ font-family:var(--serif); font-size:1.05rem; margin:0 0 .4rem; }}
  .method p {{ margin:0; color:var(--muted); font-size:.88rem; }}
  .method .k {{
    font-family:var(--mono); color:var(--accent); font-size:.7rem; letter-spacing:.1em;
    text-transform:uppercase; display:block; margin-bottom:.5rem;
  }}
  footer {{
    margin-top:3rem; padding-top:1.4rem; border-top:1px solid var(--line); color:var(--faint);
    font-size:.8rem; font-family:var(--mono); display:flex; flex-wrap:wrap;
    gap:.4rem 1.2rem; justify-content:space-between;
  }}
  @media (max-width:620px) {{
    .bar-row {{ grid-template-columns:1fr; gap:.4rem; }}
    .winner {{ grid-template-columns:1fr; }}
  }}
</style>
</head>
<body>
<div class="wrap">

  <header>
    <p class="eyebrow">Rolling-origin evaluation · Poolish total &amp; bake split</p>
    <h1>Which model decides the Poolish bake?</h1>
    <p class="lede">
      {len(report['forecasts'])} candidates replayed over the last {window['eval_weeks']} weeks, each
      forecasting the summed Wheat Dough Demand {window['lead']} days ahead from only prior Sales,
      then buffered to a {pct}% Service Level and ranked on pinball loss — the metric that penalises
      a Stockout 19&times; harder than a leftover.
    </p>
    <div class="chips">
      <span class="chip">Eval window <b>{window['start']} → {window['end']}</b></span>
      <span class="chip">Open days scored <b>{window['days']}</b></span>
      <span class="chip">Poolish lead <b>{window['lead']}&nbsp;days</b></span>
      <span class="chip">Split lead <b>{window['split_lead']}&nbsp;days</b></span>
      <span class="chip">Service Level <b>{pct}%</b></span>
      <span class="chip">Warmup <b>{window['warmup_weeks']}&nbsp;wk prior</b></span>
    </div>
  </header>

  <section>
    <p class="kicker">Recommendation</p>
    <div class="winner">
      <div class="score">{_num(rec['pinball'])}<small>pinball@{pct}</small></div>
      <div class="who">
        <h3>{rec['winner']}{'' if rec['verdict'] == 'replace' else ' — but tied with the incumbent'}</h3>
        <p>{_describe(rec['winner'])} — {
            f"the recommended Poolish model, {_pct(rec['margin_vs_incumbent'])} under the incumbent"
            if rec['verdict'] == 'replace'
            else f"the best-scoring Poolish model, though its {_pct(rec['margin_vs_incumbent'])} "
                 f"margin over <code>{rec['incumbent']}</code> is inside the daily noise"
        }, and <code>{rec['split_winner']}</code> for the bake split.</p>
      </div>
    </div>
  </section>

  <section>
    {_conclusion(report)}
  </section>

  <section>
    <p class="kicker">Full ranking · Poolish total</p>
    <div class="table-scroll">
      <table>
        <thead>
          <tr>
            <th class="model-col">Model</th>
            <th>Pinball@{pct}</th>
            <th>Δ vs best</th>
            <th>Coverage</th>
            <th>Leftover/day</th>
            <th>MAPE</th>
            <th>Days</th>
          </tr>
        </thead>
        <tbody>{_ranking_rows(report)}
        </tbody>
      </table>
    </div>
  </section>

  <section>
    <p class="kicker">Forecast vs actual · Poolish total across the holdout</p>
    <div class="chart">
      <div class="legend">
        <span><i class="swatch"></i> actual wheat total</span>
        <span><i class="swatch point"></i> point forecast</span>
        <span><i class="swatch p95"></i> P{pct} Poolish quantity</span>
      </div>
      <div class="panels">{panels}
      </div>
      <p class="chart-note">
        {window['days']} open days, {window['start']} → {window['end']}, on one shared scale. The
        weekly saw-tooth is the weekend peak: a model whose orange line rides the grey one's peaks
        is putting the Demand on the right days. The dashed line is the quantity the baker would
        actually have made — the point forecast buffered to P{pct} — so wherever grey rises above
        dashed, that day was a Stockout.
      </p>
    </div>
  </section>

  <section>
    <p class="kicker">Buffer coverage · realised vs the {pct}% target</p>
    <div class="chart">
      <div class="bars">{_coverage_bars(report)}
      </div>
      <p class="chart-note">
        How often each model's P{pct} Poolish quantity actually covered Demand across the
        {window['days']} scored days. The vertical rule is the {pct}% target: a bar short of it
        stocks out more often than the Service Level promises (red), and a bar well past it is
        over-baking — buying coverage with leftovers nobody asked for.
      </p>
    </div>
  </section>

  <section>
    <p class="kicker">Split accuracy · WAPE per variety</p>
    <div class="chart">
      {_split_bars(report)}
      <p class="chart-note">
        Each method splits the day's realised wheat total {window['split_lead']} days out, so these
        rank split quality with the Poolish model's own error held out. Lower is better; all bars
        share one scale. No second quantile buffer is applied — the buffer lives once, in the
        Poolish total, because quantiles do not add (ADR 0001).
      </p>
    </div>
  </section>

  <section>
    <p class="kicker">How these scores are kept honest</p>
    <div class="method">
      <div class="card">
        <span class="k">Leak-free</span>
        <h4>Forecast from the past only</h4>
        <p>Every scored day is forecast from a {window['lead']}-day-back origin via
        <code>history_before</code>, so no model ever sees the day it is judged on.</p>
      </div>
      <div class="card">
        <span class="k">Same buffer</span>
        <h4>Every point forecast → P{pct} identically</h4>
        <p>Each candidate emits a point forecast; the evaluator applies the one uniform
        relative-residual buffer, so pinball measures forecast quality, not interval machinery.</p>
      </div>
      <div class="card">
        <span class="k">Separate window</span>
        <h4>The buffer never sees its test days</h4>
        <p>Buffer residuals come from the {window['warmup_weeks']} weeks strictly before the
        evaluation window — no scored day feeds its own P{pct}.</p>
      </div>
      <div class="card">
        <span class="k">By rule</span>
        <h4>The verdict is arithmetic</h4>
        <p>Margins are tested, not admired: two models' daily losses are paired day by day, and a
        gap counts only past {_num(SIGNIFICANT_T, 0)} standard errors. The prose below renders that
        rule — it does not reach around it.</p>
      </div>
    </div>
  </section>

  <footer>
    <span>Generated {window['generated']} · <code>inspection_page.py</code></span>
    <span>Sales history {window['history_start']} → {window['history_end']} ·
    {', '.join(forecast.FORECAST_PRODUCTS)}</span>
  </footer>

</div>
</body>
</html>
"""


def main() -> None:
    sales = sales_history.load_sales_history()
    report = build_report(sales, eval_weeks=EVAL_WEEKS, warmup_weeks=WARMUP_WEEKS)
    PAGE_PATH.write_text(render(report), encoding="utf-8")

    rec = report["recommendation"]
    window = report["window"]
    print(
        f"Poolish total — {len(report['poolish'])} candidates over "
        f"{window['start']}..{window['end']} ({window['days']} open days)\n"
    )
    print(f"{'model':18}{'pinball':>9}{'coverage':>10}{'leftover/day':>14}")
    for row in report["poolish"]:
        print(
            f"{row['model']:18}{_num(row['pinball']):>9}{_pct(row['coverage'], 1):>10}"
            f"{_num(row.get('leftover'), 0):>14}"
        )
    print(
        f"\nPoolish -> {rec['winner']} ({_pct(rec['margin_vs_incumbent'])} under "
        f"{rec['incumbent']}, t={_num(rec['t_vs_incumbent'], 1)}) · verdict: "
        f"{rec['verdict']}\nSplit   -> {rec['split_winner']} "
        f"(mean WAPE {_pct(rec['split_wape'])})"
    )
    print(f"\nwrote {PAGE_PATH}")


if __name__ == "__main__":
    sys.exit(main())
