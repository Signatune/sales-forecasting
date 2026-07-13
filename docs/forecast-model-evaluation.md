# Evaluating and choosing forecasting models — field notes

Research notes for the bake forecast. Two questions:

1. What is actual standard practice, in the forecasting literature, for comparing
   forecasting models — especially when the thing you need is an **upper quantile at a
   fixed decision lead**, not a mean?
2. How do comparable businesses (bakeries, QSRs, cafés, grocery/fresh retail) *actually*
   use demand forecasts — on evidence, not vendor copy?

Everything here is read against our decision structure (`CONTEXT.md`): a **Poolish** decided
~3 days out that caps total bagels, split ~2 days out into per-variety **Bake-to Quantities**,
a 95% **Service Level** because a Stockout costs far more than a leftover, and **Sales**
that understate **Demand** during a Stockout.

## How to read this

Every claim is tagged:

- **[established]** — multiple independent primary sources, or a foundational result nobody
  disputes.
- **[contested]** — the literature genuinely disagrees, or the result is context-dependent.
- **[weak]** — one source, a preprint, or something I could only verify through an abstract
  or search index rather than full text.

Where I could only read an abstract, a repository record, or a search index — because the
paper is paywalled or the PDF did not extract — I say so rather than dressing it up. There
is an explicit ["What I could not substantiate"](#4-what-i-could-not-substantiate) section
at the end, and it is not empty.

---

## 1. How to compare forecasting models properly

### 1.1 The one idea everything else hangs off: the metric must match the functional

This is the load-bearing result for us, and it is worth stating before any list of metrics.

A point forecast is meaningless until you say *which property of the predictive distribution*
it is meant to be. Gneiting's [*Making and Evaluating Point Forecasts*](https://arxiv.org/abs/0912.0902)
(IJF 2011; arXiv:0912.0902) opens by demonstrating that the common practice of picking an
error measure and averaging it "can lead to grossly misguided inferences, unless the scoring
function and the forecasting task are carefully matched." **[established]**

The formal machinery:

- A scoring function is **consistent** for a functional *T* if the expected score is
  minimised by reporting the true *T* of the predictive distribution. It is **strictly
  consistent** if only *T* minimises it.
- A functional is **elicitable** if some scoring function is strictly consistent for it.
  Means, quantiles, ratios of expectations and expectiles are elicitable.
- The characterisation that matters here: *a scoring function is consistent for the mean
  if and only if it is a Bregman function; it is consistent for a quantile if and only if
  it is generalized piecewise linear.* (Gneiting 2011, abstract.)

Squared error is a Bregman function → **RMSE elicits the mean**. Absolute error is the
generalized-piecewise-linear score at level 0.5 → **MAE elicits the median**. The pinball
loss at level *τ* is the generalized-piecewise-linear score for the *τ*-quantile → **pinball
loss elicits the quantile**. Hyndman & Athanasopoulos state the first two directly:
"A forecast method that minimises the MAE will lead to forecasts of the median, while
minimising the RMSE will lead to forecasts of the mean"
([FPP3 §5.8](https://otexts.com/fpp3/accuracy.html)). **[established]**

Kolassa turns the same result into the practitioner's warning in
[*Why the "best" point forecast depends on the error or accuracy measure*](https://www.sciencedirect.com/science/article/abs/pii/S0169207019301359)
(IJF 36(1), 2020, 208–211; doi:10.1016/j.ijforecast.2019.02.017): different point-forecast
error measures are minimised by *different point forecasts derived from the very same density
forecast*, so "which model is best" is not a well-posed question until you fix the measure.
*(Read via abstract/repository records, not full text — paywalled.)* **[established]**

**Consequence for us.** We do not want the mean bagel count. We want the quantity that covers
Demand 19 days in 20. That is the 0.95 quantile of the predictive distribution of the wheat
total. The **only** scoring function in the standard toolkit that is strictly consistent for
it is the pinball loss at 0.95. Ranking candidates by MAPE — which `backtest.py` still does —
ranks them on how well they centre a guess we are not making. `model_comparison.py` already
gets this right; `backtest.py` does not. See §5.

### 1.2 Point accuracy metrics, and where each one breaks

From [FPP3 §5.8](https://otexts.com/fpp3/accuracy.html) (the source that owns the modern
formulation) and [Hyndman & Koehler, *Another look at measures of forecast accuracy*](https://robjhyndman.com/papers/mase.pdf)
(IJF 22(4), 2006, 679–688):

| Metric | Elicits | Scale-free? | Breaks when |
|---|---|---|---|
| MAE | median | no | comparing across series of different scale |
| RMSE | mean | no | as above; also outlier-dominated |
| MAPE | (neither cleanly) | yes | any actual is 0 or near 0 → undefined/exploding; asymmetric |
| sMAPE | — | yes | unstable near zero; can go negative despite "absolute" in the name |
| MASE / RMSSE | median / mean, scaled | yes | seasonal-naive denominator ≈ 0 |
| WAPE | ≈ median, volume-weighted | yes | total actual = 0 |

The specific MAPE indictments, verbatim from FPP3 §5.8: it is "infinite or undefined if
y_t = 0 for any t," produces "extreme values if any y_t is close to zero," and — the one
people forget — percentage errors "put a heavier penalty on negative errors than on positive
errors." **[established]** That last property is *not* a virtue for us even though we are
Stockout-averse: MAPE's asymmetry is an artefact of dividing by the actual, it is not tied to
any cost we face, and its direction (penalising forecasts *above* the actual less... or more,
depending how you sign it) is incidental. An accidental asymmetry is not a substitute for the
deliberate one.

sMAPE was proposed as the fix and is not one: Hyndman & Koehler "recommend that the sMAPE not
be used" (FPP3 §5.8, citing them). Nevertheless sMAPE was the headline metric of the M4
competition, which tells you something about the gap between what the literature recommends
and what the field does. **[established]**

**MASE** (Hyndman & Koehler 2006) scales each absolute error by the in-sample mean absolute
error of the naive (or seasonal-naive) forecast, so it is unit-free, finite when actuals are
zero, and has a built-in interpretation: **< 1 means you beat the naive benchmark, > 1 means
you lost to it.** **RMSSE** is its squared-error twin and was the basis of the M5 Accuracy
competition's WRMSSE. **[established]**

**WAPE** (what `model_comparison.wape` computes, sometimes called MAD/Mean ratio) is total
absolute error over total actual. It is the right choice over MAPE when a single small actual
would otherwise dominate, and it weights by volume — which is exactly the argument ADR 0002
makes for scoring the split. That argument is sound. Note though that WAPE is a *ratio of
sums*, so it is minimised by something closer to a volume-weighted median than a per-series
median; it is fine for ranking split methods, less fine as a target to optimise directly.
**[established]**

### 1.3 Probabilistic and quantile evaluation

**Pinball / quantile score.** [FPP3 §5.9](https://otexts.com/fpp3/distaccuracy.html) defines
the quantile score for a level-*p* quantile forecast f and observation y as
2(1−p)(f − y) when y < f, and 2p(y − f) when y ≥ f — "sometimes called the 'pinball loss
function' because a graph of it resembles the trajectory of a ball on a pinball table." At
p = 0.5 it reduces to absolute error. **[established]**

Note the factor of 2 in FPP3's convention. `model_comparison.pinball_losses` omits it
(it computes level·(y−f) and (1−level)·(f−y)). That is a constant rescaling, so it cannot
change any ranking and it is not a bug — but a reader comparing our numbers to a textbook
example should know the units differ by 2×.

The asymmetry ratio is the thing to internalise: at p = 0.95, an under-forecast is penalised
0.95/0.05 = **19×** an equal-magnitude over-forecast. `model_comparison.py` says exactly this
in its docstring, and it is correct.

**Winkler score** (FPP3 §5.9) evaluates a whole *interval*: interval width, plus a 2/α penalty
per unit the observation falls outside. Not what we want — we do not care about a lower bound,
only about the upper one. Skipping it is correct.

**CRPS.** The Continuous Ranked Probability Score integrates the quantile score over *all*
levels p ∈ (0,1); FPP3 §5.9 describes it as "a weighted absolute error computed from the
entire forecast distribution." It is the natural score if you are evaluating a full predictive
distribution. **[established]** For us it is the wrong tool: we make one decision, at one
quantile. Averaging our performance at p = 0.1 (where we will never operate) into the score
would dilute exactly the signal we need. **Pinball at 0.95 is the correct specialisation, and
CRPS is the thing we are correctly *not* using.**

**Proper scoring rules.** [Gneiting & Raftery, *Strictly Proper Scoring Rules, Prediction, and
Estimation*](https://sites.stat.washington.edu/raftery/Research/PDF/Gneiting2007jasa.pdf)
(JASA 102(477), 2007, 359–378; doi:10.1198/016214506000001437) is the foundational reference:
a scoring rule is *proper* if the forecaster's expected score is optimised by reporting their
true belief, and *strictly proper* if only the true belief does so. Pinball loss and CRPS are
both proper. This is the guarantee that a model cannot game the score by reporting something
other than its honest quantile. **[established]** *(I could not extract the PDF text; the
definitions above are the standard ones and are corroborated by
[FPP3 §5.9](https://otexts.com/fpp3/distaccuracy.html) and the
[scoringutils vignette](https://epiforecasts.io/scoringutils/articles/scoring-rules.html).)*

**Calibration, and why the score alone is not enough.** A proper score is a single number that
mixes calibration and sharpness. The standard diagnostic split comes from Gneiting, Balabdaoui
& Raftery, [*Probabilistic forecasts, calibration and sharpness*](https://sites.stat.washington.edu/raftery/Research/PDF/Gneiting2007jrssb.pdf)
(JRSS-B 69(2), 2007, 243–268): the goal is "maximizing the sharpness of the predictive
distributions subject to calibration." **Calibration** is the statistical consistency between
forecasts and observations; **sharpness** is how concentrated the forecast is, and is a
property of the forecast alone. Their operational test is the **PIT histogram**: probabilistic
calibration is essentially equivalent to uniformity of the probability-integral-transform
values. **[established]**

For a single quantile the PIT collapses to the check we actually want: **does the 95th
percentile forecast cover the actual 95% of the time?** That is empirical coverage — and
`model_comparison.coverage` computes precisely it. This is the right sanity check and the repo
already has it. Two cautions:

- Coverage on ~180 evaluation days has a binomial standard error of roughly
  √(0.95·0.05/180) ≈ 1.6pp, so an observed 92% or 98% is **not** evidence of miscalibration.
  A realised coverage anywhere in ~92–98% is consistent with a true 95%. The repo does not
  currently report an interval on coverage; it should, or people will over-read it.
- Coverage is a *necessary* check, not a sufficient one. A model can hit 95% coverage with an
  absurdly wide, useless buffer. That is why you read coverage **and** pinball together, never
  coverage alone — which is exactly the "sharpness subject to calibration" doctrine.

### 1.4 Evaluation protocol: rolling origin, at the decision's lead

**A single train/test split is not enough.** [FPP3 §5.10](https://otexts.com/fpp3/tscv.html)
describes time-series cross-validation on a rolling origin, in which "the corresponding
training set consists only of observations that occurred prior to the observation that forms
the test set," with accuracy "computed by averaging over the test sets." The whole point is
that one split gives you one draw of the noise. **[established]**

The canonical review is Tashman, [*Out-of-sample tests of forecasting accuracy: an analysis and
review*](https://www.researchgate.net/publication/223319987_Out-of-sample_tests_of_forecasting_accuracy_An_analysis_and_review)
(IJF 16(4), 2000, 437–450), which recommends rolling-origin evaluation, recalibration of
coefficients as the origin moves, and multiple test periods. **[established]**
*(Read via abstract and secondary summaries; the paper is paywalled.)*

**Horizon-specific evaluation.** FPP3 §5.10 explicitly recommends organising results *by*
horizon h "to examine how forecast error increases as the forecast horizon increases." A
number averaged over h = 2..7 is a number for a decision nobody makes. **[established]**

**And the horizon that matters is the one the decision is made at.** This is the point the
academic literature states least loudly and the operations literature states loudest: the
forecast must be produced at the lead time at which the commitment is irreversible. For us the
Poolish is committed at **h = 3** and the split at **h = 2**. A model that is wonderful at
h = 1 is worthless — the dough is already made.

`model_comparison.py` gets this exactly right and it is the single best decision in the
codebase: `POOLISH_LEAD = 3`, `SPLIT_LEAD = 2`, and `_replay_at_lead` forecasts every day
from an origin exactly `lead` days back. `backtest.py`, by contrast, replays origins spaced
6 days apart and pools h = 2..7 into one MAPE — so its headline number is an average over
five leads, only one of which anyone acts on.

**Leakage.** `forecast.history_before` is the single cutoff both the model and the baseline
respect, and `model_comparison.buffered_totals` collects the buffer's residuals from a warmup
window strictly *before* the evaluation window. That second one is a subtle trap most people
fall into — if the residual quantile that sets your buffer is computed on the days you then
score the buffer against, your coverage is guaranteed to look good and means nothing. The repo
avoids it. This is genuinely well done.

### 1.5 Baselines, and what the M-competitions found

**The rule:** every candidate must beat naive, seasonal-naive and a moving average, or it has
not earned its existence. FPP3 §5.8's MASE is literally *defined* against the seasonal-naive
benchmark for seasonal data, and its skill-score framing (§5.9) makes the benchmark explicit.
**[established]**

**M4** ([Makridakis, Spiliotis & Assimakopoulos, IJF 36(1), 2020, 54–74](https://www.sciencedirect.com/science/article/pii/S0169207019301128);
[conclusions paper](https://www.sciencedirect.com/science/article/abs/pii/S016920701930113X)),
100,000 series:

- The winner (a hybrid ES-RNN) improved sMAPE by **9.4%** over the Comb benchmark. The top 16
  methods averaged **4.49%** better than Comb.
- **12 of the 17 most accurate methods were combinations**, mostly of statistical approaches.
- Pure ML methods performed *poorly* — several did worse than the statistical benchmarks.
- The headline: individual methods, statistical or ML, are not far apart; combination is what
  buys accuracy, and the margins over dumb benchmarks are **single-digit percent**.
  **[established]**

**M5 Accuracy** ([Makridakis, Spiliotis & Assimakopoulos, IJF 38(4), 2022, 1346–1364](https://www.sciencedirect.com/science/article/pii/S0169207021001874)),
42,840 Walmart hierarchical retail series, scored on **WRMSSE**:

- The first M competition where **all top-performing methods were ML** (LightGBM, overwhelmingly)
  and beat every statistical benchmark. The top ML method was **22.4% more accurate than the
  best statistical benchmark** (exponential smoothing).
- But: **"simple methods such as exponential smoothing were still competitive, especially when
  used to produce forecasts at the product or product-store level."** The ML advantage came
  substantially from *cross-learning* — training one global model across many series — which is
  a lever you only have when you have many series. **[established]**
- The M5 papers also note the ranking is metric-dependent. **[weak]** — I read this via search
  index and secondary summaries rather than the full text; treat the specific figures with
  care.

**Read this against our situation, honestly.** We have **three** series with a clean weekly
cycle and daily counts in the hundreds. The M5 result that ML wins is a result about 42,840
series, and the mechanism (cross-learning) is unavailable to us. The M5 result that is
*transferable* is the other one: at the individual product level, exponential smoothing was
competitive. The PRD's decision to exclude gradient-boosted models as "overkill for three
series with a clean weekly cycle" is defensible and consistent with the literature — but it
should be defended on *that* basis, not as a general claim that ML doesn't work in retail. It
demonstrably does, at scale.

**M5 Uncertainty** ([Makridakis, Spiliotis, Assimakopoulos, Chen, Gaba, Tsetlin & Winkler,
IJF 38(4), 2022, 1365–1385](https://www.sciencedirect.com/science/article/pii/S0169207021001722))
is the closest thing in the literature to our problem — Walmart retail, hierarchical, and
scored on **quantiles rather than means**:

- Participants forecast **nine quantiles** (0.005, 0.025, 0.165, 0.25, 0.5, 0.75, 0.835, 0.975,
  0.995) across **12 aggregation levels**.
- The metric was the **Weighted Scaled Pinball Loss (WSPL)** — pinball loss, scaled the way
  MASE/RMSSE scale (by a naive benchmark's error), and weighted across the hierarchy levels.
- **All top 50 submissions beat the best benchmark by >12.5%; the top 10 by >20%; the winner by
  24.6%.** The winning entries "provided better calibration" as well as better WSPL.
  **[established]** *(Verified through the paper's abstract and multiple repository records;
  ScienceDirect blocked full-text retrieval.)*

The takeaway for us is not the winning method (a GBM/RNN hybrid — irrelevant at our scale). It
is that **the biggest, most serious retail forecasting competition ever run evaluated the
quantile problem with scaled pinball loss and checked calibration separately.** That is the
protocol `model_comparison.py` has independently arrived at, and it is the right one.

### 1.6 Intermittent / count demand — and why it does not apply to us

Croston's method forecasts intermittent demand by separately exponentially smoothing the demand
*size* and the *interval between demands*. It is known to be positively biased (over-forecasting
the mean); the **Syntetos–Boylan Approximation** corrects it with a (1 − α/2) multiplier and
generally beats plain Croston in both forecast and inventory terms, and **TSB**
(Teunter–Syntetos–Babai) updates a demand *probability* every period instead. **[established]**
The standard classification (Syntetos, Boylan & Croston) splits series by average demand
interval (ADI) and squared coefficient of variation (CV²) at cutoffs **ADI = 1.32** and
**CV² = 0.49**: smooth / erratic / intermittent / lumpy.

**We are in the "smooth" quadrant, and it is not close.** From `data/sales_history.parquet`
(2024-03-01 → 2026-07-09, 854 open days):

| series | mean/day | ADI | CV² |
|---|---|---|---|
| everything | 207 | 1.0 | — |
| plain | 131 | 1.0 | — |
| sesame | 124 | 1.0 | — |
| **wheat total** | **462** | **1.0** | **0.20** |

Zero rows: none. Every variety sells every open day. **Croston, SBA, TSB and the whole
intermittent-demand apparatus are irrelevant to this project** and should be explicitly
dropped rather than left as a nagging "should we look at Croston?" **[established, on our own
data]**

The one thing the intermittent-demand literature *does* buy us is the reason MAPE is a bad
default at all: Hyndman's [*Another look at forecast-accuracy metrics for intermittent
demand*](https://robjhyndman.com/papers/foresight.pdf) (Foresight, 2006) shows percentage
errors "become very large or undefined" when observations approach zero. Our totals never do —
but `backtest.py` still drops zero-actual rows to keep MAPE defined, and reports
`unscored_days` to make the drop visible. Honest, but it is a workaround for a metric we
should not be using at all.

**Note on counts:** Kolassa, [*Evaluating predictive count data distributions in retail sales
forecasting*](https://www.sciencedirect.com/science/article/abs/pii/S0169207016000315)
(IJF 32(3), 2016, 788–803) argues that at fine granularity retail data become low counts that
"can no longer be dealt with using approximative methods appropriate for continuous
probability distributions," and pushes for evaluating whole discrete predictive distributions.
At 462 bagels/day this does not bite for us. It would bite if we ever forecast a single variety
at a single store on a slow weekday. **[established, but not applicable at our volumes]**

### 1.7 The hierarchy: total Poolish → per-variety split

Our structure is a two-level hierarchy: wheat total = everything + plain + sesame. The
forecasting literature calls the strategies **bottom-up** (forecast the varieties, sum),
**top-down** (forecast the total, split by proportions), **middle-out**, and **optimal /
MinT reconciliation** ([FPP3 ch. 11](https://otexts.com/fpp3/reconciliation.html)).

FPP3's verdict on top-down is blunt: **"no top-down method satisfies this constraint, so all
top-down approaches result in biased coherent forecasts"** — i.e. top-down disaggregation
cannot preserve unbiasedness even when the base forecasts are unbiased, whereas bottom-up,
middle-out and MinT can. MinT is preferred because it "generate[s] [forecasts] using all the
information available within a hierarchical or a grouped structure." **[established]**

**This is a real, citable criticism of what `model_comparison.py` does for the split** — the
split *is* a top-down proportional disaggregation. But it is a criticism with a hard physical
answer, and the ADRs are right:

- The Poolish is **one batch**. Its size is a single decision, taken before the split exists.
  Bottom-up would tell us to make three independent buffered quantities, which does not
  correspond to any physical action the bakery can take.
- **Quantiles do not add.** This is the mathematically correct statement behind ADR 0001, and
  it is worth pinning down precisely, because "quantiles do not add" is loose. The exact
  result, from the comonotonicity literature (Dhaene, Denuit, Goovaerts, Kaas & Vyncke, *The
  concept of comonotonicity in actuarial science and finance*, Insurance: Mathematics and
  Economics 31, 2002; see also [Dhaene, Vanduffel & Goovaerts' survey](https://feb.kuleuven.be/public/u0014274/2008-Dhaene-Vanduffel-Goovaerts.pdf)):
  **the α-quantile of a sum equals the sum of the α-quantiles if and only if the components are
  comonotonic, and comonotonic dependence is the worst case** — it maximises the quantile of the
  sum. For anything less than perfectly comonotonic varieties (which ours are not),
  **Σ Q₀.₉₅(variety) > Q₀.₉₅(Σ variety)**. Buffering each variety to its own P95 and summing
  would systematically overshoot. **[established]**
- The operations version of the same fact is **risk pooling**: Eppen,
  [*Effects of Centralization on Expected Costs in a Multi-Location Newsboy Problem*](https://pubsonline.informs.org/doi/10.1287/mnsc.25.5.498)
  (Management Science 25(5), 1979, 498–501), shows a centralised (pooled) newsvendor has strictly
  lower expected holding+penalty cost than decentralised ones, with savings scaling as √n for
  identical uncorrelated demands. **Buffering once, at the pooled total, is not a compromise
  forced on us by the dough — it is the cost-optimal thing to do anyway.** **[established]**
  (Caveat, and it is a real one: the √n result depends on light-tailed demand, and pooling gains
  vanish when demands are highly positively correlated. Our three varieties almost certainly
  *are* positively correlated — a busy Saturday is busy for all three — so the pooling benefit
  is smaller than √3 would suggest. It is still positive, and the argument for one buffer at
  the total still holds.)

So: **ADR 0001 is correct, and better-supported than it currently claims.** The right framing is
not "we do top-down because the dough forces us" (defensive) but "we pool the buffer at the
total because pooling is cost-optimal, and the dough happens to agree" (offensive). Worth adding
the Eppen and comonotonicity citations to that ADR.

The honest residual weakness — which the code already half-admits — is that the split *is*
top-down and therefore, per FPP3, structurally biased. `compare_split_models` scores the split
against the **realised** wheat total, deliberately holding the total's error out. The docstring
in `bake_to_quantities` explains this well and it is the right choice for *ranking split
methods*. But it does mean the reported split WAPE is not the error a real bake sees. That is
stated in the code; it should also be stated wherever the numbers are shown to the baker.

Probabilistic reconciliation is an active research area (Panagiotelis, Gamakumara,
Athanasopoulos & Hyndman, [*Probabilistic forecast reconciliation: Properties, evaluation and
score optimisation*](https://ideas.repec.org/a/eee/ejores/v306y2023i2p693-706.html), EJOR
306(2), 2023, 693–706) — but at two levels and three bottom series, MinT would be a
cannon aimed at a mouse. Not recommended. **[contested — the method is real and better in
general; my claim that it isn't worth it *here* is a judgement call, not a citation.]**

### 1.8 "Is model A really better than model B?"

The standard tool is the **Diebold–Mariano test**: form the per-period *loss differential*
d_t = L(A_t) − L(B_t), and test H₀: E[d_t] = 0 using the mean of d_t over its standard error.

Two caveats, both important, both from the source:

1. **The variance estimator must account for serial correlation.** Multi-step-ahead forecast
   errors are autocorrelated by construction, so d_t is too, and the naive
   `std(d)/√n` standard error understates the true one — inflating the t-statistic and making
   differences look significant when they are not. DM uses a HAC / Newey–West long-run variance;
   Harvey, Leybourne & Newbold (1997) add a small-sample correction.
2. **Diebold himself says the test is routinely abused.** In
   [*Comparing Predictive Accuracy, Twenty Years Later: A Personal Perspective on the Use and
   Abuse of Diebold–Mariano Tests*](https://www.nber.org/papers/w18391) (JBES 33(1), 2015, 1–1;
   NBER w18391), he writes that the DM test "was intended for comparing forecasts; it has been,
   and remains, useful in that regard," but "was not intended for comparing *models*," and that
   much of the literature misuses it in pseudo-out-of-sample environments where "much simpler yet
   more compelling full-sample model comparison procedures exist." **[established]**

**This lands directly on `inspection_page.t_statistic`.** The repo does the right *thing* — it
pairs the two candidates' **daily pinball losses day by day** and measures the mean difference in
standard errors, with `SIGNIFICANT_T = 2.0`. Pairing on the same date to cancel that date's
intrinsic difficulty is exactly the DM construction, and the docstring's reasoning ("subtracting
two models' losses on the *same* Saturday cancels the Saturday's own difficulty") is precisely
right.

But the standard error is `diff.std(ddof=1) / sqrt(len(diff))` — the **i.i.d.** estimator. At
lead 3, with a strong weekly cycle, the loss differentials are very likely serially correlated,
so this **understates the standard error and overstates |t|**. The verdict rule is therefore
biased toward declaring "replace" — the opposite of the conservatism the module is clearly
reaching for. Fixing it is cheap (a Newey–West variance with a small lag truncation, or a
block bootstrap over weeks) and it is the highest-value correctness fix in the evaluation code.

Diebold's second caveat also applies, and cuts the other way, in our favour: he objects to using
DM to compare *models*, and prefers full-sample procedures. But our situation is the one he
endorses — we are comparing **forecasts** produced at a fixed operational lead, where the
pseudo-out-of-sample setup is not a proxy for anything, it *is* the thing. Using a DM-style
paired test here is legitimate.

### 1.9 The gap between forecast accuracy and decision quality

This is the most important section in this document.

**The classical result.** The newsvendor's optimal order quantity is the **critical fractile**:
Q* = F⁻¹( Cu / (Cu + Co) ), where Cu is the underage (lost-sale) cost and Co the overage
(leftover) cost. The optimal order is a **quantile of the demand distribution**, and the quantile
level is set entirely by the *cost ratio*. **[established — this is textbook OR, going back to
Arrow–Harris–Marschak (1951).]**

**Run it backwards, because it is the most useful thing in this document.** A 95% Service Level
target is *equivalent to* an assertion that

> Cu / (Cu + Co) = 0.95  ⟹  **Cu / Co = 19**.

The shop is asserting that **a lost bagel sale costs 19× a leftover bagel.** Nobody in the repo
has written that number down, and it is the single number the entire buffer rests on. Sanity
check it: if a bagel sells for ~$2–3 with a high gross margin, the underage cost (lost margin,
plus some goodwill/spillover) might be ~$2–3. A leftover bagel's overage cost is the marginal
ingredient + labour cost, minus any salvage (day-old sales, staff meal). For 19:1 to hold, a
leftover must cost ~$0.10–0.15 net. That is *plausible* for flour-and-water-and-salt with a
salvage channel — but it should be **checked, not assumed**, because the Poolish size is
linear-ish in it. If the true ratio is 9:1, the target is 90%, and we are systematically
over-baking. **This is a question for the owner, and it is the highest-leverage question in the
project.**

**Why RMSE/MAE ≠ cost.** The theoretical answer is §1.1: minimising RMSE gives you the mean, and
the mean is not the critical fractile unless Cu = Co. The empirical answer is a small literature
showing the two objectives genuinely diverge:

- **Syntetos, Nikolopoulos & Boylan, [*Judging the judges through accuracy-implication metrics:
  the case of inventory forecasting*](https://www.sciencedirect.com/science/article/abs/pii/S0169207009000880)
  (IJF 26(1), 2010, 134–143)** — "the efficiency of inventory systems does not relate directly to
  demand forecasting performance as measured by standard forecasting accuracy measures," and a
  forecast feeding an inventory system "should be evaluated with respect to its consequences for
  stock control," not only on accuracy. This is the cleanest peer-reviewed statement of the gap.
  **[established]** *(Read via abstract/repository records.)*
- **Ban & Rudin, [*The Big Data Newsvendor: Practical Insights from Machine Learning*](https://pubsonline.informs.org/doi/10.1287/opre.2018.1757)
  (Operations Research 67(1), 2019, 90–108)** — argues for skipping the two-step
  "estimate a demand distribution, then optimise" pipeline and **directly optimising the
  newsvendor cost**, reporting that their best feature-based one-step algorithm beat the
  best-practice benchmark by **24% in out-of-sample cost**. **[established]** The relevance for
  us is conceptual, not architectural: it is further evidence that *the loss you train and select
  on should be the loss you pay*. And note — a one-step newsvendor optimiser trained on pinball
  loss at the critical fractile **is exactly what pinball@95 model selection approximates**. We
  are already, in effect, doing the cheap version of this. That is a point in the repo's favour.
- **Ulrich, Jahnke, Langrock, Pesch & Senge, [*Distributional regression for demand forecasting in
  e-grocery*](https://www.sciencedirect.com/science/article/abs/pii/S0377221719309403)
  (EJOR 294(3), 2021, 831–842)** — a real e-grocery retailer, explicitly modelling "the extreme
  right tail of the demand distribution rather than providing point forecasts of its mean,"
  evaluated "with respect to the service level provided by the e-grocery retailer analyzed," and
  finding distributional regression (GAMLSS) gave **cost-optimal** forecasts. **[established]**
  This is the closest published analogue of our problem I found: a food retailer, forecasting an
  upper quantile, because a stockout costs more than a leftover, and selecting the model on the
  service-level/cost objective. **We are not doing anything eccentric.**
  - **One warning from that paper, and it lands on us.** They chose distributional regression *over*
    direct quantile regression because estimating an extreme upper quantile directly is imprecise —
    "the corresponding parameter estimators can be highly imprecise due to data scarcity in the
    extreme tails." Our `p95_buffer` takes an empirical 95th percentile of a pool of relative
    residuals. With ~26 weeks of warmup (~180 days), the 95th percentile is interpolated between
    roughly the 9th and 10th largest residuals. **That estimate is noisy**, and it is a single
    number that scales *everything*. This is a real fragility in the current design. See §5.
- **A caution about recent preprints.** Search surfaces several 2026 arXiv preprints on this exact
  theme — [*Beyond Accuracy: Evaluating Forecasting Models by Multi-Echelon Inventory
  Cost*](https://arxiv.org/abs/2603.16815) and [*Bridging Forecast Accuracy and Inventory KPIs*](https://arxiv.org/abs/2601.21844).
  Both are **unrefereed preprints**, they do not entirely agree with each other (the first finds
  accuracy gains *did* mostly translate to cost gains on M5 data; the second reports weak or
  negative correlations between MAE/RMSE and inventory cost), and I would not lean on either.
  I mention them only to record that the "accuracy ≠ cost" claim is *directionally* well
  supported but the *magnitude* is contested. **[contested]**

**The decision-level metric we should probably also report.** If Cu/Co can be pinned down, the
most honest score is not pinball at all — it is **expected cost per day in dollars**:
Cu·E[(D − Q)⁺] + Co·E[(Q − D)⁺], evaluated on the same rolling-origin replay. Pinball@0.95 is a
monotone rescaling of exactly this when Cu/Co = 19, so **it will produce the same ranking** — but
it will produce a number the owner can read. `model_comparison.buffered_totals` already returns
`(date, actual, forecast_quantity, buffered_quantity)`, which is everything needed to compute it.
This is a ~10-line addition with a large legibility payoff.

---

## 2. How comparable businesses actually use forecasting

The brief was: real evidence, no vendor marketing. That constraint bites hard — the majority of
search results for "bakery demand forecasting" and "restaurant labor forecasting" are SaaS
landing pages. What follows is what survived.

### 2.1 Bakery chains

**The single most on-point paper I found.** Huber & Stuckenschmidt, [*Daily retail demand
forecasting using machine learning with emphasis on calendric special days*](https://www.sciencedirect.com/science/article/abs/pii/S0169207020300224)
(IJF 36(4), 2020, 1420–1438):

- **The case is literally a bakery chain**: >100 stores, pastries produced in a centralised
  facility and delivered daily. Forecasts feed **production and ordering decisions** — the same
  decision we are making.
- They forecast **daily demand per product category per store**, compare seasonal-naive and
  exponential smoothing against feedforward NNs, LSTMs and gradient-boosted trees, and evaluate
  on **RMSE**.
- The paper's *emphasis* is on **calendric special days** — days with demand patterns wildly
  unlike normal days — which they identify as the dominant source of error.
  **[established]** *(Read via abstracts and repository records; paywalled.)*

**Two things we should take from it.** First, that a serious bakery chain frames the problem
exactly as we do: daily, per-product, feeding production. Second, and more usefully: **special
days are where the error lives.** Our history already contains closures (July 4ths,
Thanksgivings) which `forecast.py` handles by simply not having rows for them — elegant. But the
*days around* a holiday, and the holidays the shop stays open for, are almost certainly where our
worst Poolish misses are, and nothing in the current model or evaluation looks at them
specifically. A per-day error breakdown around holidays would likely be more valuable than any
further model tuning.

*(Caveat on RMSE: they optimise the mean, because their published objective is accuracy. We
should not copy that choice — see §1.1.)*

**Bakery waste, quantified.** Hübner et al., [*Machine-learning-based demand forecasting against
food waste: Life cycle environmental impacts and benefits of a bakery case study*](https://onlinelibrary.wiley.com/doi/10.1111/jiec.13528)
(Journal of Industrial Ecology, 2024) is peer-reviewed and measures the change in bakery
**returns** induced by adopting ML forecasting versus the prior conventional ordering process.
**[weak]** — I could not get past the publisher's 403 to read the methodology or the numbers, so I
will not quote a figure. The paper exists, it is refereed, and it is the right kind of evidence;
someone with institutional access should read it before we cite any waste-reduction number.

### 2.2 Grocery and fresh-food retail

- **Walmart / M5** (§1.5) is the largest public evidence base: real hierarchical retail sales,
  and the Uncertainty track shows a major grocer's problem being solved as a **quantile** problem
  scored on **pinball loss**. **[established]**
- **Corporación Favorita** (Ecuadorian grocery chain,
  [Kaggle](https://www.kaggle.com/c/favorita-grocery-sales-forecasting)) released ~125M rows of
  store-item-day sales. Notable for us: the competition metric **explicitly up-weighted perishable
  items (weight 1.25 vs 1.0)** — the operator's own encoding of "getting perishables wrong costs
  more." The 1st-place solution was a single LightGBM with a Tweedie objective. **[established
  that the competition and weighting existed; the winning-solution detail is from community
  writeups, so [weak].]**
- **Meituan / FreshRetailNet-50K** ([arXiv:2505.16319](https://arxiv.org/abs/2505.16319)) is the
  most interesting find for our **Stockout** problem: a fresh-retail operator publicly released a
  **stockout-annotated, censored-demand dataset** — ~50k series with explicit annotations of when
  a product was unavailable — precisely so that latent (true) demand recovery can be benchmarked.
  A real operator considered censored demand important enough to build a dataset around it.
  **[established that the dataset exists and is what it says; [weak] on the specific benchmark
  numbers, which I did not verify.]**
- **e-grocery distributional regression** (Ulrich et al., EJOR 2021) — covered in §1.9. A real
  e-grocer forecasting the upper tail against a service-level objective. **[established]**

### 2.3 Restaurants, cafés, canteens

Thinner than I expected, and worth saying so.

- **Recruit Restaurant Visitor Forecasting** ([Kaggle](https://www.kaggle.com/c/recruit-restaurant-visitor-forecasting))
  — 821 Japanese restaurants, 15 months of daily visitor data, with reservations, holidays and
  weather. Analysed academically in Bojer & Meldgaard, [*Kaggle forecasting competitions: An
  overlooked learning opportunity*](https://arxiv.org/abs/2009.07701) (IJF 37(2), 2021, 587–603).
  Winning approaches were **ensembles of LightGBM / XGBoost / feedforward NNs on rolling-statistic
  and lag features**; the review's cross-competition findings are that **global ensemble models
  tend to outperform local single models** and that GBDTs dominate. **[established]**
- **A Bayesian approach for predicting food and beverage sales in staff canteens and restaurants**
  ([IJF, 2021](https://www.sciencedirect.com/science/article/pii/S0169207021001011)) — POS-data-driven
  generalized additive models for daily food and beverage sales. This is the closest peer-reviewed
  analogue to a deli. **[weak]** — ScienceDirect 403'd me; I have the title, venue and approach but
  did not read the results.

### 2.4 Do weather / holiday / event regressors actually help?

**This is genuinely contested, and the honest answer is "holidays yes, weather maybe."**

- **For:** Badorf & Hoberg, [*The impact of daily weather on retail sales: An empirical study in
  brick-and-mortar stores*](https://www.sciencedirect.com/science/article/abs/pii/S0969698919303236)
  (Journal of Retailing and Consumer Services 52, 2020) find weather effects on daily sales as
  large as 23.1% by store location and 40.7% by sales theme, with non-linear effects, and report
  that **including weather forecasts improves sales forecast accuracy up to seven days ahead,
  though the improvement diminishes with horizon.** **[established]**
- **Against:** in the **Recruit Restaurant** competition — a restaurant-visitor problem, and one
  where weather data was explicitly provided — the **winner reported that using weather
  information did not help forecast performance by much, corroborated by other top finishers**
  (Bojer & Meldgaard, [arXiv:2009.07701](https://arxiv.org/abs/2009.07701)). **[established]**
- **Holidays/calendar:** unambiguously the dominant regressor in the bakery case (Huber &
  Stuckenschmidt's entire emphasis) and in essentially every retail competition. **[established]**

**Reconciling these:** weather matters most for *footfall-driven, weather-exposed, discretionary*
purchases and for *store-level totals*; it matters less once you already have a strong
weekday + trend + holiday model, because much of weather's effect is confounded with season. For
a neighbourhood deli where bagels are a habitual, planned purchase, I would expect weather to be
**second-order behind weekday, holiday and trend** — but that is my inference, not a citation.
**Recommendation: do not add weather until the holiday/special-day handling is done**, and if you
do add it, prove it earns its place on pinball@95 at lead 3 with the same rolling-origin protocol,
not on a story.

### 2.5 Censored demand (Stockouts) in practice

Our `CONTEXT.md` correctly identifies that Sales understate Demand during a Stockout, and the PRD
correctly defers it. The literature says deferring it has a cost, and names the bias:

- Trapero, Holgado de Frutos & Pedregal, [*Demand forecasting under lost sales stock policies*](https://www.sciencedirect.com/science/article/abs/pii/S0169207023000961)
  (IJF 40(3), 2024, 1055–1068) note the censored-demand forecasting literature "remains very
  limited without an accepted general solution," and propose a **Tobit Kalman filter**. That a
  2024 IJF paper still describes the area as unsettled is itself the useful fact. **[established]**
- Sachs & Minner, [*The data-driven newsvendor with censored demand observations*](https://www.sciencedirect.com/science/article/abs/pii/S092552731300203X)
  (IJPE 149, 2014, 28–36) — motivated by a large European retail chain — use the **timing of
  intraday sales** to infer unmet demand: if a product's hourly sales stop dead at 2pm, you learn
  something a daily total cannot tell you. They report that using timing observations eliminates
  ~76% of the expected-profit loss versus using only stockout-event flags. **[established]**

**This is directly actionable for us.** We pull from the Toast **Analytics** API at daily
granularity (`docs/toast-analytics-api.md`), so we currently cannot see intraday timing. But the
repo already has `toast_orders.py` hitting the Orders API, and orders carry timestamps. **If
bagels reliably sell out mid-morning on the days we stock out, intraday sales timing is a
censoring signal available to us for free**, and the Sachs & Minner result says it recovers most
of the loss. That is a strong candidate for the next effort after this one — stronger, I'd argue,
than any further model tuning.

**The direction of the bias matters for the buffer.** Censored Sales understate Demand. A model
fit on Sales therefore under-forecasts, and — critically — the **residual pool that
`p95_buffer` draws from is *also* censored**, because on the very days we stocked out, the
"actual" was clipped. So the P95 buffer is estimated from residuals that systematically
*understate* how far Demand overshot. **Both the point forecast and the buffer are biased low, in
the same direction.** Realised coverage measured against censored Sales will therefore look
*better* than the true Service Level actually is. This is a genuine, unrecorded flaw in the
current evaluation, and it deserves a line in the ADRs.

---

## 3. What the industry evidence does *not* show

Worth stating plainly, because the absence is informative:

- I found **no** credible published evidence that any bakery or QSR runs a two-stage
  pre-ferment/split decision like our Poolish → Bake-to structure. Our decomposition appears to be
  genuinely specific to our process. That is fine — it just means we cannot copy anyone's homework
  on the *structure*, only on the *evaluation*.
- I found **no** peer-reviewed field experiment measuring waste reduction from forecast-driven
  bake quantities in a single independent bakery (as opposed to a >100-store chain). Everything in
  our size class is vendor content.

---

## 4. What I could not substantiate

Listed honestly. Some of these are things I *wanted* to be true.

1. **Any specific "AI forecasting cuts food waste by X%" figure.** Every such number I found
   traced back to a vendor selling forecasting software, with no published methodology, no
   baseline definition, no control group and no sample. Examples encountered and **rejected**:
   claims of "reduce waste 30%," "95% forecast accuracy," and "15–25% better labor cost control"
   from restaurant-scheduling SaaS sites. **These are marketing, not evidence, and should not be
   cited in this repo for any purpose.** The one peer-reviewed source that plausibly *does* carry
   a real number (Hübner et al., J. Industrial Ecology 2024) I could not read past the paywall.
2. **Bakery waste percentages.** Search returns "1.5–2% for small bakeries, up to 20% for large
   ones" and "4–10% of food sales" — all from business-advice content sites with no methodology.
   **Unsubstantiated. Do not use.** If we want to know our waste rate, we should measure it; we
   have the Sales data and would need leftover counts.
3. **Peer-reviewed QSR labor-scheduling case studies driven by demand forecasts.** I searched
   hard and found essentially only vendor marketing. There is academic work on restaurant *sales*
   forecasting (§2.3) and separate academic work on workforce scheduling, but I could not find a
   credible published end-to-end case study joining them. It may exist behind paywalls I could not
   reach; I am recording that I did not find it rather than implying it does not exist.
4. **Full text of several key papers.** Hyndman & Koehler (2006), Kolassa (2020), Tashman (2000),
   Huber & Stuckenschmidt (2020), the M5 papers, Syntetos et al. (2010), Ban & Rudin (2019) and
   Sachs & Minner (2014) were all read via abstracts, repository records, or search indices —
   ScienceDirect and several publishers returned 403. The *claims* I have attributed to them are
   corroborated across at least two independent sources each, but I have not personally verified
   any verbatim quote from those papers, and I have not put quotation marks around anything I
   could not read directly.
5. **The Diebold–Mariano small-sample and HAC corrections.** I am confident in the *substance* of
   §1.8's criticism (multi-step loss differentials are autocorrelated; the i.i.d. standard error
   is too small) — this is textbook. I did not read Harvey, Leybourne & Newbold (1997) directly,
   so treat the specific attribution of the small-sample correction as **[weak]** even though the
   underlying point is **[established]**.
6. **Grocery/QSR engineering blogs with real numbers.** DoorDash publishes genuinely technical
   forecasting posts (an ensemble framework they call ELITE; and a probabilistic-ETA post that
   uses **PIT histograms to check quantile calibration** — the same diagnostic §1.3 recommends).
   Both were **403 to my fetches**, so I could only see them through the search index and will not
   quote numbers. They are also *delivery-logistics* forecasting, not perishable production, so
   their transfer to us is limited even if verified. I found **no** grocery or restaurant
   engineering blog with both real technical detail *and* verifiable numbers on production/bake
   quantity decisions.
7. **Whether weather would help *us*.** §2.4's reconciliation ("second-order behind weekday,
   holiday and trend for a habitual purchase") is my inference from conflicting evidence, not a
   finding. It should be tested, not believed.

---

## 5. Recommendations for this repo

Ordered by value, and tied to actual code.

### 5.1 What is already right — do not regress it

`model_comparison.py` independently arrived at close to the protocol the literature prescribes.
Specifically, these are correct and should be protected:

- **Pinball@0.95 as the headline metric for the Poolish total** (`pinball`, `_score_candidate`).
  This is the uniquely strictly-consistent score for the functional we actually need (§1.1). It
  is the same family of metric the M5 Uncertainty competition used (§1.5).
- **Rolling-origin replay at the true decision lead** — `POOLISH_LEAD = 3`, `SPLIT_LEAD = 2`,
  `_replay_at_lead`. Forecasting at the horizon the decision is committed at is the thing most
  projects get wrong (§1.4).
- **Coverage as a separate calibration check** (`coverage`) alongside pinball. Score + calibration,
  not score alone — the "sharpness subject to calibration" doctrine (§1.3).
- **Buffer residuals drawn from a warmup window strictly before the evaluation window**
  (`buffered_totals`, `WARMUP_WEEKS`). Avoids the trap that would make coverage look perfect and
  mean nothing (§1.4).
- **One uniform buffering mechanism across all candidates** (ADR 0002) so pinball measures forecast
  quality, not interval machinery.
- **One buffer, at the pooled total** (ADR 0001). Better-justified than the ADR currently claims —
  see 5.5.
- **A dumb baseline in the comparison** (`moving_average`) and an incumbent, so nothing wins by
  default (§1.5).

### 5.2 Fix: `backtest.py` reports the wrong metric at the wrong horizon

`backtest.py` scores on **MAPE**, pooled across **h = 2..7**. Both are wrong for this business:
MAPE elicits neither the mean nor the quantile we need, and pooling five leads produces a number
for a decision nobody takes. It also drops zero-actual rows to keep MAPE defined — an honest
workaround for a metric we shouldn't be using.

It is not harmful today (it's the *pilot's* scorer and nothing depends on it), but it is the
first thing a reader opens, and it teaches the wrong lesson. Either:
- retire it in the promotion ticket (08) once `model_comparison.py`'s winners land, or
- keep it strictly as a "familiar sanity column," which is exactly how `_score_candidate` already
  treats MAPE, and say so in its module docstring.

**Do not** let MAPE be the number anyone quotes.

### 5.3 Fix: the significance test's standard error is too small

`inspection_page.t_statistic` computes `diff.std(ddof=1) / sqrt(n)` on the paired daily pinball
loss differences. Multi-step (lead-3) forecast loss differentials are serially correlated, so this
**understates the standard error and inflates |t|** — biasing the `SIGNIFICANT_T = 2.0` verdict
rule toward "replace" (§1.8). The module's whole design intent is conservatism; this quietly
undermines it.

Fix: use a **Newey–West / HAC long-run variance** with a small lag truncation (7–14 days would
cover the weekly cycle), or a **block bootstrap over whole weeks**. Either is a contained change to
one function, and the existing paired-by-date construction is already correct — only the variance
estimator needs replacing.

### 5.4 Add: report the decision in the units of the decision

Two additions, both cheap, both high-value:

1. **Expected cost per day, in dollars.** Cu·E[(D − Q)⁺] + Co·E[(Q − D)⁺] over the same replay.
   `buffered_totals` already returns everything needed. When Cu/Co = 19 this is a monotone rescaling
   of pinball@95, so it **will not change the ranking** — but it converts "pinball 6.31" into
   "$14/day of avoidable cost," which is the only version the owner can act on (§1.9).
2. **Pin down Cu/Co explicitly.** The 95% Service Level *is* an assertion that a lost sale costs
   **19×** a leftover. That number is nowhere in the repo. Ask the owner. If the real ratio is,
   say, 9:1, the correct target is 90% and we are over-baking every single day. **This is the
   highest-leverage open question in the project** and it is a five-minute conversation, not a
   modelling effort. Record the answer in `CONTEXT.md` next to the Service Level definition.

### 5.5 Strengthen ADR 0001 with the citations it deserves

ADR 0001 currently justifies one-buffer-at-the-total defensively ("quantiles do not add"; the
baker "physically cannot bake into three separate 95% piles"). Both true. But the stronger,
offensive framing is available:

- **Comonotonicity:** the α-quantile of a sum equals the sum of α-quantiles *only* under
  comonotonic dependence, which is the worst case. For our (positively but imperfectly correlated)
  varieties, **Σ Q₀.₉₅ > Q₀.₉₅(Σ)** — buffering per variety would systematically overshoot
  (Dhaene et al. 2002).
- **Risk pooling:** [Eppen (1979)](https://pubsonline.informs.org/doi/10.1287/mnsc.25.5.498) —
  the pooled newsvendor has strictly lower expected cost than the decentralised one. **Pooling the
  buffer at the total is cost-optimal, not merely convenient.**

Also worth recording in ADR 0001 the honest counterweight from [FPP3 ch. 11](https://otexts.com/fpp3/reconciliation.html):
the split is a **top-down proportional disaggregation**, and "all top-down approaches result in
biased coherent forecasts." We accept that bias knowingly, because the physical decision structure
demands it. Saying so is better than not knowing it.

### 5.6 Watch: the P95 buffer is a single noisy number scaling everything

`p95_buffer` takes the empirical 95th percentile of a pool of relative residuals from a ~26-week
warmup (~180 days). At n ≈ 180, the 0.95 quantile is interpolated between roughly the 9th and 10th
largest residuals — **an estimate resting on a handful of observations, that then multiplies every
Poolish quantity we bake.** Ulrich et al. (EJOR 2021) chose distributional regression over direct
quantile estimation for exactly this reason: extreme-tail quantile estimators are imprecise (§1.9).

Concrete steps, cheapest first:
1. **Report the uncertainty in it.** Bootstrap the residual pool and print a CI on the P95
   multiplier. If it's ±15%, everyone should know that.
2. **Report a CI on realised coverage.** At ~180 evaluation days the binomial SE is ~1.6pp, so
   realised coverage of 92–98% is consistent with a true 95%. Presenting a bare "coverage: 93.2%"
   invites over-reading (§1.3).
3. **Consider a smoother tail estimate** — e.g. fitting a distribution to the residuals rather than
   taking a raw order statistic — but only if (1) shows the empirical P95 is genuinely unstable.
   Do not add machinery to solve a problem you have not measured.

### 5.7 Add: MASE/RMSSE as the "did we beat the naive benchmark" column

Pinball@95 is the right ranking metric but it has no natural zero — "6.31" means nothing on its
own. Add a **scaled** score against the seasonal-naive benchmark (the skill-score framing in
[FPP3 §5.9](https://otexts.com/fpp3/distaccuracy.html); this is precisely why M5 Uncertainty used
*scaled* pinball loss): report each candidate's pinball as a **ratio to the seasonal-naive
baseline's pinball**. Below 1 = better than naive. This makes "does complexity earn its keep"
readable at a glance, which is user story 7 in the PRD.

### 5.8 Drop: intermittent-demand methods, permanently

Our series are **smooth** by the standard Syntetos–Boylan–Croston classification (ADI = 1.0,
CV² = 0.20, zero zero-sales days, ~462 bagels/day — §1.6). Croston, SBA and TSB are not applicable.
Write this down once so nobody re-opens it.

### 5.9 Next effort, ranked

1. **Pin down Cu/Co** (§5.4). Five minutes, changes everything downstream.
2. **Fix the significance test's variance** (§5.3). Contained bug, currently biasing verdicts.
3. **Special/holiday days.** The bakery-chain paper's central finding is that calendric special
   days dominate the error (§2.1). We have never looked at ours. A per-day error breakdown around
   holidays is likely worth more than any further model tuning.
4. **Censored demand via intraday sales timing** (§2.5). Sachs & Minner recover ~76% of the
   censoring loss using sales *timing*, and `toast_orders.py` already reaches an API with
   timestamps. This is the one place where a real modelling advance is available to us — and note
   that censoring currently biases **both** the forecast and the buffer low, and makes realised
   coverage look better than the true Service Level.
5. **Weather** — last, and only if it survives a pinball@95 test at lead 3 (§2.4). The evidence
   that it helps food demand forecasting is genuinely mixed.

---

## Sources

Primary sources actually consulted, grouped. Where I could only read an abstract or repository
record rather than full text, that is noted in the body above.

**Forecast evaluation — foundations**
- Hyndman & Athanasopoulos, *Forecasting: Principles and Practice* (3rd ed): [§5.8 point accuracy](https://otexts.com/fpp3/accuracy.html), [§5.9 distributional accuracy](https://otexts.com/fpp3/distaccuracy.html), [§5.10 time-series cross-validation](https://otexts.com/fpp3/tscv.html), [ch. 11 reconciliation](https://otexts.com/fpp3/reconciliation.html)
- Gneiting (2011), *Making and Evaluating Point Forecasts*, IJF — [arXiv:0912.0902](https://arxiv.org/abs/0912.0902)
- Gneiting & Raftery (2007), *Strictly Proper Scoring Rules, Prediction, and Estimation*, JASA 102(477) — [PDF](https://sites.stat.washington.edu/raftery/Research/PDF/Gneiting2007jasa.pdf)
- Gneiting, Balabdaoui & Raftery (2007), *Probabilistic forecasts, calibration and sharpness*, JRSS-B 69(2) — [PDF](https://sites.stat.washington.edu/raftery/Research/PDF/Gneiting2007jrssb.pdf)
- Hyndman & Koehler (2006), *Another look at measures of forecast accuracy*, IJF 22(4) — [PDF](https://robjhyndman.com/papers/mase.pdf)
- Hyndman (2006), *Another look at forecast-accuracy metrics for intermittent demand*, Foresight — [PDF](https://robjhyndman.com/papers/foresight.pdf)
- Kolassa (2020), *Why the "best" point forecast depends on the error or accuracy measure*, IJF 36(1) — [doi](https://www.sciencedirect.com/science/article/abs/pii/S0169207019301359)
- Kolassa (2016), *Evaluating predictive count data distributions in retail sales forecasting*, IJF 32(3) — [doi](https://www.sciencedirect.com/science/article/abs/pii/S0169207016000315)
- Tashman (2000), *Out-of-sample tests of forecasting accuracy*, IJF 16(4) — [record](https://www.researchgate.net/publication/223319987_Out-of-sample_tests_of_forecasting_accuracy_An_analysis_and_review)
- Diebold (2015), *Comparing Predictive Accuracy, Twenty Years Later*, JBES 33(1) — [NBER w18391](https://www.nber.org/papers/w18391)
- Petropoulos et al. (2022), *Forecasting: theory and practice*, IJF 38(3) — [arXiv:2012.03854](https://arxiv.org/abs/2012.03854)

**Competitions**
- Makridakis, Spiliotis & Assimakopoulos (2020), *The M4 Competition: 100,000 time series and 61 forecasting methods*, IJF 36(1) — [doi](https://www.sciencedirect.com/science/article/pii/S0169207019301128)
- Makridakis, Spiliotis & Assimakopoulos (2022), *M5 accuracy competition: Results, findings, and conclusions*, IJF 38(4) — [doi](https://www.sciencedirect.com/science/article/pii/S0169207021001874)
- Makridakis, Spiliotis, Assimakopoulos, Chen, Gaba, Tsetlin & Winkler (2022), *The M5 uncertainty competition: Results, findings and conclusions*, IJF 38(4) — [doi](https://www.sciencedirect.com/science/article/pii/S0169207021001722)
- Bojer & Meldgaard (2021), *Kaggle forecasting competitions: An overlooked learning opportunity*, IJF 37(2) — [arXiv:2009.07701](https://arxiv.org/abs/2009.07701)
- [Recruit Restaurant Visitor Forecasting](https://www.kaggle.com/c/recruit-restaurant-visitor-forecasting) · [Corporación Favorita Grocery Sales Forecasting](https://www.kaggle.com/c/favorita-grocery-sales-forecasting)

**Decisions, inventory, newsvendor**
- Eppen (1979), *Effects of Centralization on Expected Costs in a Multi-Location Newsboy Problem*, Management Science 25(5) — [doi](https://pubsonline.informs.org/doi/10.1287/mnsc.25.5.498)
- Syntetos, Nikolopoulos & Boylan (2010), *Judging the judges through accuracy-implication metrics*, IJF 26(1) — [doi](https://www.sciencedirect.com/science/article/abs/pii/S0169207009000880)
- Ban & Rudin (2019), *The Big Data Newsvendor: Practical Insights from Machine Learning*, Operations Research 67(1) — [doi](https://pubsonline.informs.org/doi/10.1287/opre.2018.1757)
- Dhaene, Vanduffel & Goovaerts, *Comonotonicity* (survey) — [PDF](https://feb.kuleuven.be/public/u0014274/2008-Dhaene-Vanduffel-Goovaerts.pdf)

**Food / retail domain**
- Huber & Stuckenschmidt (2020), *Daily retail demand forecasting using machine learning with emphasis on calendric special days*, IJF 36(4) — [doi](https://www.sciencedirect.com/science/article/abs/pii/S0169207020300224)
- Ulrich, Jahnke, Langrock, Pesch & Senge (2021), *Distributional regression for demand forecasting in e-grocery*, EJOR 294(3) — [doi](https://www.sciencedirect.com/science/article/abs/pii/S0377221719309403)
- Badorf & Hoberg (2020), *The impact of daily weather on retail sales*, J. Retailing and Consumer Services 52 — [doi](https://www.sciencedirect.com/science/article/abs/pii/S0969698919303236)
- Sachs & Minner (2014), *The data-driven newsvendor with censored demand observations*, IJPE 149 — [doi](https://www.sciencedirect.com/science/article/abs/pii/S092552731300203X)
- Trapero, Holgado de Frutos & Pedregal (2024), *Demand forecasting under lost sales stock policies*, IJF 40(3) — [doi](https://www.sciencedirect.com/science/article/abs/pii/S0169207023000961)
- Hübner et al. (2024), *Machine-learning-based demand forecasting against food waste: … a bakery case study*, J. Industrial Ecology — [doi](https://onlinelibrary.wiley.com/doi/10.1111/jiec.13528) *(not read — paywalled)*
- Salinas, Flunkert & Gasthaus (2020), *DeepAR*, IJF 36(3) — [arXiv:1704.04110](https://arxiv.org/abs/1704.04110)
- FreshRetailNet-50K (Meituan), stockout-annotated censored-demand fresh-retail dataset — [arXiv:2505.16319](https://arxiv.org/abs/2505.16319)
