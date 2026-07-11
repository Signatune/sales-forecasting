"""Locks the inspection page's two jobs: the replay data every chart draws, and
the recommendation the whole comparison exists to produce.

The charts themselves are SVG/CSS and not asserted pixel by pixel — what is
pinned is that the page carries the numbers the scores were taken from, names a
winner per bake target with its margins, and reaches its verdict by a rule
rather than by whoever wrote the prose.
"""
import pandas as pd
import pytest

import forecast
import inspection_page
from inspection_page import build_report, recommendation, render, t_statistic
from tests.test_model_comparison import sales, varieties


def comparison(rows) -> pd.DataFrame:
    """A ranked Poolish comparison frame, as compare_models returns."""
    return pd.DataFrame(
        rows, columns=["model", "pinball", "coverage", "mape", "days"]
    ).sort_values("pinball", ignore_index=True)


def split_comparison(rows) -> pd.DataFrame:
    """A split comparison frame, as compare_split_models returns."""
    return pd.DataFrame(rows, columns=["model", "product", "wape", "days"])


def wapes(model: str, wape: float, days: int = 100):
    """One split candidate scoring the same WAPE on each of the three varieties."""
    return [(model, product, wape, days) for product in forecast.FORECAST_PRODUCTS]


class TestBuildReport:
    def test_charts_the_days_the_models_were_scored_on(self):
        # A flat history every candidate forecasts exactly: the page's series
        # must cover each open day of the 2-week window once, with the actual,
        # the point forecast and the P95 the score itself was taken from.
        history = sales(varieties("2026-05-29", 42, (5.0, 3.0, 2.0)))

        report = build_report(history, eval_weeks=2, warmup_weeks=2)

        assert len(report["dates"]) == 14
        assert report["dates"][-1] == "2026-07-09"
        assert report["actual"] == pytest.approx([10.0] * 14)
        for name in report["forecasts"]:
            series = report["forecasts"][name]
            assert series["point"] == pytest.approx([10.0] * 14)
            assert series["buffered"] == pytest.approx([10.0] * 14)

    def test_carries_the_ranking_the_charts_are_labelled_from(self):
        history = sales(varieties("2026-05-29", 42, (5.0, 3.0, 2.0)))

        report = build_report(history, eval_weeks=2, warmup_weeks=2)

        ranked = [row["model"] for row in report["poolish"]]
        assert set(ranked) == set(report["forecasts"])
        assert "seasonal_naive" in ranked
        assert all(row["coverage"] == pytest.approx(1.0) for row in report["poolish"])
        assert {row["product"] for row in report["split"]} == set(
            forecast.FORECAST_PRODUCTS
        )

    def test_a_day_a_model_declined_leaves_a_gap_rather_than_a_zero(self):
        # A model that never forecast a day has no quantity for it; charting a
        # zero there would draw a Stockout the model never actually predicted.
        history = sales(varieties("2026-05-29", 42, (5.0, 3.0, 2.0)))
        silent = lambda s, as_of: pd.DataFrame(
            columns=["product", "date", "forecast_quantity"]
        )

        report = build_report(
            history,
            candidates={"silent": silent},
            split_candidates={},
            eval_weeks=2,
            warmup_weeks=2,
        )

        assert report["forecasts"]["silent"]["point"] == [None] * 14
        assert report["poolish"][0]["days"] == 0

    def test_names_the_window_it_scored(self):
        history = sales(varieties("2026-05-29", 42, (5.0, 3.0, 2.0)))

        report = build_report(history, eval_weeks=2, warmup_weeks=2)

        assert report["window"]["start"] == "2026-06-26"
        assert report["window"]["end"] == "2026-07-09"
        assert report["window"]["days"] == 14
        assert report["window"]["level"] == 0.95
        assert report["window"]["lead"] == 3


def days_from(start: str, days: int) -> pd.DatetimeIndex:
    return pd.date_range(start, periods=days, freq="D")


def steady(loss: float, days: int = 100, start: str = "2026-01-09") -> pd.Series:
    """A model that loses the same amount every day — a difference against it
    carries no noise at all, so any gap is real by construction. Indexed by date,
    as the daily losses the evaluator hands over are."""
    return pd.Series([loss] * days, index=days_from(start, days))


def noisy(loss: float, swing: float, days: int = 100, start: str = "2026-01-09") -> pd.Series:
    """A model whose daily loss swings +/- `swing` about `loss` — the day-to-day
    spread a mean difference has to be read against."""
    return pd.Series(
        [loss + swing, loss - swing] * (days // 2), index=days_from(start, days)
    )


class TestTStatistic:
    def test_a_constant_gap_has_no_noise_to_be_inside_of(self):
        assert t_statistic(steady(12.0), steady(10.0)) == float("inf")

    def test_a_small_mean_gap_under_a_wide_swing_is_not_evidence(self):
        # The better model wins by 0.5 a day while both swing +/-10 around their
        # means: the paired difference is 10.5 one day and -9.5 the next, a mean
        # of 0.5 against a standard error near 1 — well under two.
        t = t_statistic(noisy(10.5, 10.0), steady(10.0))

        assert t == pytest.approx(0.5, abs=0.05)

    def test_pairs_on_the_days_the_two_models_share(self):
        # The rival declined the first 50 days. Only the days both forecast are
        # paired — 50 of them, each a constant 2.0 apart — so the days it sat out
        # neither dilute the difference nor misalign it against another weekday.
        incumbent = steady(12.0, days=100)
        rival = steady(10.0, days=50, start="2026-02-28")

        assert t_statistic(incumbent, rival) == float("inf")

    def test_two_models_that_share_no_day_cannot_be_paired(self):
        assert t_statistic(steady(12.0, days=10), steady(10.0, days=10, start="2027-01-01")) is None


class TestRecommendation:
    def test_names_the_winner_and_its_margin_over_the_incumbent_and_baseline(self):
        # ewma 10 vs the incumbent's 12.5 (a 20% cut) and the moving-average
        # baseline's 20 (a 50% cut), and every day the same — no noise to hide in.
        ranked = comparison(
            [
                ("ewma", 10.0, 0.94, 20.4, 100),
                ("seasonal_naive", 12.5, 0.97, 22.7, 100),
                ("moving_average", 20.0, 0.99, 48.3, 100),
            ]
        )
        losses = {
            "ewma": steady(10.0),
            "seasonal_naive": steady(12.5),
            "moving_average": steady(20.0),
        }

        result = recommendation(
            ranked, split_comparison(wapes("same_weekday", 0.1)), losses
        )

        assert result["winner"] == "ewma"
        assert result["margin_vs_incumbent"] == pytest.approx(0.20)
        assert result["margin_vs_baseline"] == pytest.approx(0.50)
        assert result["verdict"] == "replace"

    def test_a_lower_mean_inside_the_daily_noise_is_not_a_reason_to_replace(self):
        # ewma's mean is 4.8% under the incumbent's — but the two disagree by
        # +10.5 one day and -9.5 the next, so the gap is half a standard error.
        # A lower mean is a lower mean; it is not evidence, and the model the
        # shop already bakes off stays.
        ranked = comparison(
            [
                ("ewma", 10.0, 0.949, 20.4, 100),
                ("seasonal_naive", 10.5, 0.983, 22.7, 100),
                ("moving_average", 20.0, 0.949, 48.3, 100),
            ]
        )
        losses = {
            "ewma": steady(10.0),
            "seasonal_naive": noisy(10.5, 10.0),
            "moving_average": steady(20.0),
        }

        result = recommendation(
            ranked, split_comparison(wapes("same_weekday", 0.1)), losses
        )

        assert result["winner"] == "ewma"
        assert result["margin_vs_incumbent"] == pytest.approx(0.048, abs=1e-3)
        assert result["t_vs_incumbent"] == pytest.approx(0.5, abs=0.05)
        assert result["verdict"] == "keep"

    def test_ets_must_out_argue_the_pandas_models_to_earn_its_dependency(self):
        # ETS is the best scorer, but its edge over ewma is inside the daily
        # noise — so it has not paid for statsmodels, and the recommendation
        # falls back to the pure-pandas model (the PRD's open question).
        ranked = comparison(
            [
                ("ets", 9.5, 0.95, 19.1, 100),
                ("ewma", 10.0, 0.94, 20.4, 100),
                ("seasonal_naive", 12.5, 0.97, 22.7, 100),
                ("moving_average", 20.0, 0.99, 48.3, 100),
            ]
        )
        losses = {
            "ets": noisy(9.5, 10.0),
            "ewma": steady(10.0),
            "seasonal_naive": steady(12.5),
            "moving_average": steady(20.0),
        }

        result = recommendation(
            ranked, split_comparison(wapes("same_weekday", 0.1)), losses
        )

        assert result["best"] == "ets"
        assert result["winner"] == "ewma"
        assert result["ets_margin"] == pytest.approx(0.05)
        assert result["ets_t"] == pytest.approx(0.5, abs=0.05)
        assert result["ets_earns_its_dependency"] is False

    def test_a_clear_ets_win_does_earn_its_dependency(self):
        ranked = comparison(
            [
                ("ets", 8.0, 0.95, 16.0, 100),
                ("ewma", 10.0, 0.94, 20.4, 100),
                ("seasonal_naive", 12.5, 0.97, 22.7, 100),
                ("moving_average", 20.0, 0.99, 48.3, 100),
            ]
        )
        losses = {
            "ets": steady(8.0),
            "ewma": steady(10.0),
            "seasonal_naive": steady(12.5),
            "moving_average": steady(20.0),
        }

        result = recommendation(
            ranked, split_comparison(wapes("same_weekday", 0.1)), losses
        )

        assert result["winner"] == "ets"
        assert result["ets_earns_its_dependency"] is True
        assert result["margin_vs_incumbent"] == pytest.approx(0.36)
        assert result["verdict"] == "replace"

    def test_names_the_split_winner_on_mean_wape_across_the_varieties(self):
        ranked = comparison(
            [
                ("ewma", 10.0, 0.94, 20.4, 100),
                ("seasonal_naive", 12.5, 0.97, 22.7, 100),
                ("moving_average", 20.0, 0.99, 48.3, 100),
            ]
        )
        splits = split_comparison(
            wapes("constant_recent_share", 0.12)
            + wapes("same_weekday_share", 0.10)
            + wapes("per_variety_recency_share", 0.11)
        )

        result = recommendation(
            ranked, splits, {"ewma": steady(10.0), "seasonal_naive": steady(12.5)}
        )

        assert result["split_winner"] == "same_weekday_share"
        assert result["split_wape"] == pytest.approx(0.10)
        # 0.10 against the 0.11 runner-up: a 9.1% cut in split error.
        assert result["split_margin"] == pytest.approx(1 / 11, rel=1e-3)


class TestRender:
    def test_the_page_answers_the_question_it_was_built_to_answer(self):
        history = sales(varieties("2026-05-29", 42, (5.0, 3.0, 2.0)))
        report = build_report(history, eval_weeks=2, warmup_weeks=2)

        page = render(report)

        assert page.startswith("<!doctype html>")
        assert page.rstrip().endswith("</html>")
        # The three charts the ticket asks a baker to eyeball, and the verdict.
        assert "Forecast vs actual" in page
        assert "Buffer coverage" in page
        assert "Split accuracy" in page
        assert "Recommendation" in page
        for row in report["poolish"]:
            assert row["model"] in page
        for name in {row["model"] for row in report["split"]}:
            assert name in page

    def test_the_page_draws_the_scored_days_it_reports(self):
        history = sales(varieties("2026-05-29", 42, (5.0, 3.0, 2.0)))
        report = build_report(history, eval_weeks=2, warmup_weeks=2)

        page = render(report)

        assert "2026-06-26" in page  # the window it names
        assert "2026-07-09" in page
        # One forecast-vs-actual panel per candidate, each an SVG polyline.
        assert page.count('<figure class="panel') == len(report["forecasts"])
        assert "<svg" in page

    def test_a_tied_winner_is_never_labelled_recommended(self):
        # The page must not hand the baker a "recommended" model in one panel and
        # tell him the models are tied in the next. When the verdict is `keep`,
        # the best scorer is the best of the pack and nothing more.
        tied = recommendation(
            comparison(
                [
                    ("ewma", 10.0, 0.949, 20.4, 100),
                    ("seasonal_naive", 10.5, 0.983, 22.7, 100),
                    ("moving_average", 20.0, 0.949, 48.3, 100),
                ]
            ),
            split_comparison(wapes("same_weekday_share", 0.1)),
            {
                "ewma": steady(10.0),
                "seasonal_naive": noisy(10.5, 10.0),
                "moving_average": steady(20.0),
            },
        )
        report = {
            **build_report(
                sales(varieties("2026-05-29", 42, (5.0, 3.0, 2.0))),
                eval_weeks=2,
                warmup_weeks=2,
            ),
            "recommendation": tied,
        }

        page = render(report)

        assert tied["verdict"] == "keep"
        assert "recommended" not in page
        assert "best of the pack" in page
        assert "tied with the incumbent" in page

    def test_a_dependency_free_winner_is_reported_as_such(self):
        # The prose must follow the rule, not the other way round: an ETS win
        # inside the daily noise is reported as not earning its dependency, and
        # the page names the pandas model instead.
        report = {
            **build_report(
                sales(varieties("2026-05-29", 42, (5.0, 3.0, 2.0))),
                eval_weeks=2,
                warmup_weeks=2,
            ),
            "recommendation": recommendation(
                comparison(
                    [
                        ("ets", 9.5, 0.95, 19.1, 100),
                        ("ewma", 10.0, 0.94, 20.4, 100),
                        ("seasonal_naive", 12.5, 0.97, 22.7, 100),
                        ("moving_average", 20.0, 0.99, 48.3, 100),
                    ]
                ),
                split_comparison(wapes("same_weekday_share", 0.1)),
                {
                    "ets": noisy(9.5, 10.0),
                    "ewma": steady(10.0),
                    "seasonal_naive": steady(12.5),
                    "moving_average": steady(20.0),
                },
            ),
        }

        page = render(report)

        assert "does not earn its dependency" in page
        assert "statsmodels" in page
        assert "ewma" in page


class TestMain:
    def test_writes_the_page_from_the_sales_history(self, tmp_path, monkeypatch):
        history_path = tmp_path / "sales_history.parquet"
        page_path = tmp_path / "model_comparison.html"
        monkeypatch.setattr(inspection_page, "SALES_HISTORY_PATH", history_path)
        monkeypatch.setattr(inspection_page, "PAGE_PATH", page_path)
        monkeypatch.setattr(inspection_page, "EVAL_WEEKS", 2)
        monkeypatch.setattr(inspection_page, "WARMUP_WEEKS", 2)
        sales(varieties("2026-05-29", 42, (5.0, 3.0, 2.0))).to_parquet(
            history_path, index=False
        )

        inspection_page.main()

        page = page_path.read_text(encoding="utf-8")
        assert "Recommendation" in page
        assert "seasonal_naive" in page
