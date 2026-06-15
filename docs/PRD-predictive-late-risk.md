# PRD: Predictive Late-Risk Detection

**Status:** Draft · v1 scope
**Author:** Jackson McCarthy
**Last updated:** June 2026
**Project:** DispatchIQ

---

## 1. Problem & context

DispatchIQ already flags orders at risk of missing their delivery window. Today that flag is a rule-of-thumb calculation: orders remaining × time left × available drivers per zone. When the arithmetic says a window can't physically clear, the agent raises an exception.

That rule works, and it's honest about what it is — a capacity check. But it only catches the problem once the *math* has already broken. By the time orders-remaining outpaces time-left, the window is often already lost; the flag tells the ops manager something they're about to find out anyway. It also treats every order as interchangeable. In reality, a 22-item order going to the far edge of the Downtown zone in the rain is a fundamentally different risk than a 3-item order two blocks from the warehouse, and the current model can't tell them apart until they're both in the "remaining" bucket.

What's missing is *anticipation*. The ops managers I worked alongside developed an intuition for which orders would slip — they could look at a board and say "that one's going to be late" twenty minutes before the rule-based math would agree. That intuition came from pattern memory: this driver, this zone, this size, this weather, this time of day. The opportunity is to give the agent that same pattern memory, so the flag moves from *"this window is now mathematically impossible"* to *"this specific order is likely to be late — here's why, and here's the window to act."*

This is a delivery-ops problem, but the underlying shape is general to frontline operations: the difference between a system that reports failures and one that predicts them is the difference between a dashboard and a decision tool.

## 2. Goals & success metrics

The feature succeeds if it surfaces real late risk early enough to act on, without generating so much noise that ops managers stop trusting it. Concretely:

- **Lead time.** A late-risk prediction should fire meaningfully earlier than the current rule-based flag would for the same order — the target is a usable head start before dispatch, not a simultaneous alert.
- **Precision over recall, deliberately.** When the agent says an order is at risk, it should usually be right. A flag that's wrong often gets ignored, and an ignored flag is worse than no flag. We accept missing some genuine late orders in exchange for the flags we do raise being credible. (See §6 for why this direction, not the reverse.)
- **Actionability.** Every prediction comes with a reason and a suggested action, not just a risk score. "Likely late — reassign to a driver" beats "78% risk."
- **Measured, not asserted.** Success is defined by a holdout evaluation against historical outcomes (§9), with an honest baseline. The bar for v1 is "beats the rule-based flag on lead time at comparable precision," not a headline accuracy number.

What we are explicitly *not* optimizing for: a high raw accuracy figure. A model can hit 95% accuracy by predicting "not late" for everything in a system where most orders aren't late. That number would be worthless here, and chasing it would be the wrong instinct.

## 3. Non-goals

- **Not a replacement for the rule-based capacity check.** The existing window math stays. It catches a real failure mode — genuine capacity shortfalls — that a predictive model shouldn't be trusted to override. The two coexist (§6).
- **Not driver performance scoring.** This feature predicts *order* outcomes. It may use driver-level signal as an input, but it does not rank, rate, or surface judgments about individual drivers. That's a separate feature with its own sensitivities, deliberately out of scope.
- **Not a routing or assignment engine.** It predicts risk and recommends an action; it does not auto-reassign orders or re-sequence routes.
- **Not real-time GPS tracking.** v1 reasons from order attributes, zone, staffing, time, and weather — not live driver location. Live tracking is a much larger build and a separate data dependency.
- **Not multi-location.** Single-warehouse, Manhattan-zone scope, matching the rest of DispatchIQ today.

## 4. User & the moment

The user is the ops manager in the 30 minutes before and during a dispatch window — the same person the rest of DispatchIQ serves. The specific moment this feature owns: an order has been picked or is in the queue, and the manager has a narrow window where reassigning it, escalating it, or proactively notifying the customer is still cheap. After that window closes, every option gets worse and more expensive.

The feature's job is to put the right order in front of that person *while they can still do something about it*, with enough reasoning that they trust the call and enough specificity that the action is obvious.

## 5. Proposed v1

A late-risk prediction exposed as an agent capability (and as an MCP tool, consistent with DispatchIQ's existing architecture) that, for a given order, returns a risk classification, the primary factors driving it, and a recommended action.

**Inputs (all already in the system or cheaply derivable):**
- Order size and weight (already flagged for the biker-vs-driver decision)
- Destination zone and rough distance from warehouse
- Current zone staffing and driver/biker mix
- Time remaining in the delivery window
- Weather conditions (already available via the ShiftReady-style data pattern, or a lightweight feed)
- Time of day / window position

**Model approach for v1:** Start simple and honest. A transparent model — logistic regression or a small gradient-boosted tree over the features above, trained on historical order outcomes (late / not late). The point of v1 is not model sophistication; it's establishing that the signal exists and is measurable. A simple model that we can explain ("this order is flagged because it's large, far, and the zone is short a driver") is worth more here than a black box, both for user trust and because the *reason* is part of the output.

**Output:** A three-level classification — high / moderate / low risk — rather than a raw probability, because ops managers act on categories, not decimals. High-risk orders surface in the dashboard exception flow alongside the existing rule-based flags, visually distinguished as *predictive* (anticipated) versus *capacity* (confirmed). Each carries its top one or two contributing factors and a recommended action drawn from DispatchIQ's existing playbook (reassign to driver, escalate, notify customer).

## 6. Key decisions & tradeoffs

**The false-positive / false-negative call.** This is the central decision. A false positive is an order flagged as risky that would have been fine — the cost is a wasted reassignment or an unnecessary customer notification, and, cumulatively, eroded trust in the flag. A false negative is a genuinely at-risk order the model misses — the cost is a late delivery the manager didn't get a chance to prevent. These pull in opposite directions and you cannot minimize both.

We tune toward **precision** — fewer, more-credible flags — for one reason specific to this user: an ops manager checks DispatchIQ in a 30-second window under pressure. A tool that cries wolf gets mentally filed as noise within a week, and then it's dead regardless of how good the model is underneath. A flag that's right most of the time earns the glance. We would rather miss some late orders (which the manager might still catch through the existing rule-based check, or through experience) than train the user to ignore us. This is a reversible call — the threshold is a dial, and once we have real usage data on how managers respond to flags, we can revisit it. But v1 starts conservative.

**Coexistence with the rule-based flag.** The predictive flag does not replace or override the capacity math. They answer different questions: the rule-based check says "is this window physically possible," the predictive model says "is this specific order likely to slip." An order can be flagged by one and not the other, and that's informative — a predictive flag on an order in a window the math says is fine is exactly the early-warning case this feature exists for. The dashboard shows them as distinct flag types so the manager knows whether they're looking at a hard capacity wall or a probabilistic warning. We deliberately do not blend them into a single score, because that would hide the distinction that makes each one actionable.

**The cold-start problem.** A predictive model needs history, and a fresh DispatchIQ deployment (or a new zone, or a new season) has none. Two-part handling: until there's enough historical volume, the feature stays in a shadow mode — it computes predictions and logs them against actual outcomes but does not surface flags to the user. It only goes live once it clears the precision bar on real data. In the meantime, the rule-based flag carries the load exactly as it does today. This avoids the worst failure mode: a confident-looking prediction built on no evidence.

**Transparency over accuracy.** Choosing an explainable model over a marginally more accurate black box is a deliberate tradeoff. The reason is part of the product — "flagged because large + far + short-staffed zone" is what makes the manager trust and act on it. A 2% accuracy gain isn't worth losing that.

## 7. Cut from v1, and why

- **Auto-reassignment.** The model could, in principle, not just flag risk but execute the fix. Cut, because trusting a v1 predictive model to take an irreversible operational action is exactly backwards from the precision-first, trust-building posture above. Recommend, don't act. Revisit once the flag has earned trust.
- **Live GPS / driver-location input.** Would improve predictions, but it's a heavy new data dependency and integration. The v1 hypothesis — that order attributes + zone + staffing + weather + time carry enough signal — is testable without it, and proving that first is the cheaper path.
- **A continuous risk score / probability in the UI.** Cut in favor of three categories. Managers act on high/moderate/low; a 0.73 invites false precision and slows the 30-second glance.
- **Cross-zone or systemwide risk rollups.** Interesting for a regional view, but v1 is order-level and single-warehouse. Aggregation is a later layer.
- **Driver-level risk attribution surfaced to the user.** Even if driver signal feeds the model, exposing "this order is risky because of *this driver*" crosses into performance-scoring territory with real workforce sensitivities. Kept as a possible model input, deliberately not surfaced as a user-facing reason in v1.

## 8. Risks & open questions

- **Is there enough historical signal?** The core hypothesis is that the available features predict lateness better than the rule-based baseline. If they don't — if lateness is mostly driven by factors we don't capture — the feature doesn't earn its place. The shadow-mode rollout (§6) is designed to answer this before anything ships to users.
- **Outcome label quality.** "Late" needs a clean definition (missed the committed window, presumably) and reliable historical records of it. If the historical data is noisy about when orders actually arrived, the training signal degrades. Open question: how clean is the existing outcome data?
- **Distribution shift.** A model trained on past patterns can quietly go stale when operations change — a new zone, a seasonal demand shift, a change in the driver pool. Needs a plan for periodic re-evaluation, not train-once-and-forget.
- **The trust dynamic is hard to measure pre-launch.** We're optimizing for a behavioral outcome (managers keep trusting the flag) that we can't fully observe until it's in real use. v1 should instrument flag-response behavior — do managers act on high-risk flags? — so we learn this fast.

## 9. How we'd measure success

Consistent with the evaluation approach used elsewhere in this portfolio (CareLog's eval harness), success is defined by measurement against a holdout, not by assertion.

- **Offline, before launch.** Split historical orders into train and holdout sets. Train the model on the train set, then evaluate predictions against actual outcomes on the holdout. Report precision and recall *separately* — not a blended accuracy figure — and explicitly report lead time: how much earlier the predictive flag fires versus when the rule-based flag would have for the same order.
- **Honest baseline.** The baseline to beat is the existing rule-based flag, scored the same way. The bar for v1 is "beats the rule-based flag on lead time at comparable-or-better precision." A realistic first-pass precision target is in the 60–75% range, not 95% — credibility comes from honest measurement, and an inflated number on a problem this noisy would be a red flag, not a selling point.
- **Spot-check the failures.** Manually review a sample of false positives and false negatives to understand *why* the model misses — whether it's a data problem, a missing feature, or genuine unpredictability. The pattern in the errors is more useful than the aggregate score.
- **Online, after launch.** Instrument flag-response: when a high-risk flag fires, does the manager act, and does acting change the outcome? This closes the loop between "the model is accurate" and "the feature is useful," which are not the same thing.

---

*This PRD intentionally scopes a narrow, measurable v1 over a more ambitious version. The discipline is deliberate: prove the signal exists and earn user trust with a flag that's right, then expand. A predictive feature that ships broad and wrong is worse than no feature, because it spends trust that's expensive to win back.*
