# Late-Risk Model: Evaluation Results and Known Limitations

**Status:** Shadow mode (not user-facing)
**Model:** `late_risk_logreg_v1` (LogisticRegression, 20 features)
**Evaluation date:** June 2026
**See also:** [`docs/PRD-predictive-late-risk.md`](PRD-predictive-late-risk.md)

---

## The honest read, up top

**This model is shipping in shadow mode, not as a user-facing flag, because the only evaluation we can do right now is methodologically circular.** The eval numbers below measure how well a learned model recovers the function that generated its training labels — they are not evidence the model would predict real lateness on real orders. Until we collect real historical outcome data (dispatch timestamps vs. committed window timestamps for completed orders), there is no honest path to surfacing this as a live operational flag.

The plumbing exists. The model is loaded. Predictions compute on demand via the `/late-risk/shadow-predictions` endpoint and the `predict_late_risk_shadow` MCP tool, and every prediction is appended to a shadow log. Nothing the model says appears in the dashboard or creates an `ExceptionRecord`. That is the correct posture per PRD §6 ("until there's enough historical volume, the feature stays in a shadow mode — it computes predictions and logs them against actual outcomes but does not surface flags to the user"), and it should not change before real outcome data exists.

## What was measured

A 500-record synthetic dataset (`data/synthetic_order_history.json`) was split 80/20 stratified by label (seed 42), a logistic regression model was trained on the 400-record training set, and both the model and a per-order projection of the existing rule-based `check_window_risk` logic were scored on the 100-record holdout.

| | Precision | Recall | TP | FP | FN | TN |
|---|---:|---:|---:|---:|---:|---:|
| Predictive model (LogReg, threshold = 0.5) | **0.0%** | **0.0%** | 0 | 1 | 20 | 79 |
| Rule-based baseline (per-order projection) | **15.4%** | **10.0%** | 2 | 11 | 18 | 69 |

Holdout: 100 orders, 20 late (20.0%). Eval sidecar: [`data/models/late_risk_logreg_v1.metadata.json`](../data/models/late_risk_logreg_v1.metadata.json).

These are the numbers, reported once. They will not be re-reported in cleaned-up form elsewhere, and there will be no headline accuracy figure to share.

## Why these numbers don't mean what they would normally mean

The synthetic dataset's `was_late` label is produced by a transparent scoring function in `data/generate_synthetic_order_history.py` that takes as input *exactly the same features the model sees*: order size, distance, zone staffing, weather, time remaining, time of day. The label is `random() < sigmoid(weighted_sum_of_features)`.

This means **any classifier that learns the labeling rule will appear to perform well on a holdout drawn from the same generator**. The classifier is not being asked "do you predict real lateness?" It is being asked "have you reverse-engineered the generator?" These are different questions with different answers, and the second one cannot tell us anything useful about the first.

Concretely:

- A high precision/recall figure from this eval would mean the model is good at recovering the labeling function. Nothing more.
- A low precision/recall figure (which is what we got at threshold 0.5) likewise tells us mostly about threshold and class-balance choices, not about real-world signal.
- **The eval is circular.** It cannot rule the model in *or* out as a real-world predictor.

We could push the precision numbers into the 60–75% range called out in the PRD by sweeping the prediction threshold downward or retraining with `class_weight='balanced'`. Doing so would produce a headline figure inside the target band without changing what the figure actually means. That would be reverse-engineering a credible-looking number on a circular metric, which is worse than reporting an uncomfortable one honestly. We chose not to do it.

## Tuning decisions explicitly not taken

For the record, in case someone returns to this and is tempted to chase the headline number:

- **Threshold not swept.** Default 0.5 is what's reported. We know lower thresholds raise positive-rate.
- **Class weighting not applied.** Default `class_weight=None`. We know `'balanced'` would raise recall.
- **Model family not changed.** Stayed with `LogisticRegression` per the PRD's transparency-over-accuracy stance. We did not try gradient boosting, even though it would likely fit the synthetic generator better, because better fit to the generator is not the goal.

Each of these is a real dial the model has. None of them should be turned to make the synthetic numbers look better, because the synthetic numbers are not the bottleneck.

## What real historical data would change

Real data — dispatch and delivery timestamps for completed orders, joined to the committed delivery window — would change the eval from "did the model recover the rule that made up the labels" to "did the model predict an outcome it had no role in producing." That is the question worth asking. With real data:

- The holdout would contain orders whose lateness was determined by physical reality (traffic, picking delays, driver pace, weather), not by a coefficient table the experimenter chose.
- A model that beats the rule-based baseline on real data is genuinely useful. A model that beats it on synthetic data has demonstrated only that it can fit the generator.
- Lead-time measurement (PRD §9) would become possible. On synthetic dispatch snapshots both predictors operate on the same instant; there is no temporal axis to measure lead time along. With real data, the model can be applied at pick-complete time and compared against when the rule-based check would have fired for the same order.

The data collection itself is mostly already in place: `Order.timestamps` records `dispatched` and `delivered`, and `Order.delivery_window` records the commitment. What's missing is enough completed shifts to build a meaningful training set, plus a label-quality review (PRD §8 open question: how clean are those timestamps, really).

## Graduation criteria for leaving shadow mode

The model graduates from shadow mode to user-facing flag when **all** of the following hold:

1. **Real data, real holdout.** At least 1 month of real completed orders with reliable `dispatched` and `delivered` timestamps, split into train and a temporally-later holdout (no random split — temporal split, because operations drift).
2. **Beats the rule-based baseline on real data.** Same scoring: precision and recall reported separately. The target stays "comparable-or-better precision than the rule at meaningfully earlier lead time," not a fixed accuracy number.
3. **Honest error spot-check.** A manual review of a sample of false positives and false negatives, per PRD §9. If the error pattern reveals a systematic data-quality issue or a missing feature, fix that before graduating.
4. **Shadow-log analysis.** The shadow log (`backend/data/shadow_predictions.jsonl`) is read back against eventual order outcomes for at least a full shift. If shadow predictions correlate with real outcomes at meaningfully-above-chance precision, that's the strongest signal we can get short of full retraining on real data.

If any of these fail, the model stays in shadow mode. There is no version of "the dashboard wants a number, so let's lower the bar" that survives PRD §6's posture on user trust.

## What shadow mode looks like in this codebase

| Surface | Behavior |
|---|---|
| `backend/late_risk_shadow.py` | Scoring service. Loads the model, computes predictions for a list of orders, appends every prediction to `backend/data/shadow_predictions.jsonl`. Never raises, never creates an exception. |
| `GET /late-risk/shadow-predictions` | Returns shadow predictions for currently-active orders. URL contains "shadow" so it cannot be confused with a production flag endpoint. |
| MCP `predict_late_risk_shadow` | Per-order shadow prediction tool for MCP clients. Docstring explicitly warns: not user-facing, do not act on. |
| Dashboard | **Nothing.** No predictive badge, no "likely late" tag, no exception. This is deliberate. |
| `ExceptionRecord` | Shadow predictions do not create exceptions. The existing rule-based `check_window_risk` continues to be the only thing that creates `late_risk` exceptions. |

If a future change starts surfacing shadow predictions to end users without graduation through the criteria above, this document is the case for backing that change out.

## Inputs the live shadow service has to fake

The live order state doesn't have everything the model was trained on. The shadow service makes three deliberate approximations, each marked clearly in the prediction record:

| Feature | Source | Approximation |
|---|---|---|
| `distance_from_warehouse_km` | Per-zone midpoint lookup (`ZONE_TO_DISTANCE_KM` in `late_risk_shadow.py`) | Single value per zone, not per-address. Loses within-zone variation. |
| `weather` | Hardcoded `"clear"` placeholder | No weather feed wired in. Every shadow prediction assumes clear weather. This is a real limitation. |
| `time_remaining_minutes_at_dispatch` | Computed as `(window_end - now)` | OK for orders being scored right at dispatch. For orders earlier in the pipeline, this overestimates available time. |

Each shadow log record carries a `context_caveats` field listing the approximations applied to that prediction, so when shadow logs are eventually compared to real outcomes, the comparison can exclude predictions distorted by missing inputs.

## Summary

The model exists. It is wired in. It is silent by design. The synthetic eval cannot establish predictive validity, and we are not going to launder a circular metric to make it appear to. The next milestone is real-data eval, not better synthetic-data eval.
