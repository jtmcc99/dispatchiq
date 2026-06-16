# `data/` — synthetic training data

This folder is **separate from `backend/data/`** (which holds the live demo's operational state — orders, drivers, exceptions, CS notifications). Everything here is **synthetic** and exists only to support model prototyping for the predictive late-risk feature described in [`docs/PRD-predictive-late-risk.md`](../docs/PRD-predictive-late-risk.md).

## Files

| File | What it is |
|------|------------|
| `generate_synthetic_order_history.py` | Deterministic generator (seed = 42). Single source of truth for the labeling rules. |
| `synthetic_order_history.json` | 500-record labeled dataset produced by the generator. |

## ⚠️  Synthetic, not real

Every record in `synthetic_order_history.json` is fabricated. Labels are produced by a transparent scoring rule in `generate_synthetic_order_history.py` — they are *not* observed outcomes from real deliveries. Any model trained on this data tells you about the generator's rules, not about real DispatchIQ operations.

This dataset exists because:

1. The live demo (`backend/data/orders.json`) contains a single shift with no `was_late` outcome labels.
2. The PRD's v1 hypothesis — that order/zone/staffing/weather features predict lateness better than the rule-based baseline — is testable with synthetic data, *as a scaffolding exercise*. Real validation requires real history.

The synthetic dataset is intended for: wiring up the training pipeline, sanity-checking model code, building the eval harness. It is **not** intended for: claims about model accuracy on real operations, demos that imply real performance numbers, or any production use.

## Regenerating

```bash
python3 data/generate_synthetic_order_history.py
```

Running with the default seed reproduces the committed dataset byte-for-byte. Change `SEED` in the script (or the labeling coefficients in `_late_probability`) only if you want a different sample.
