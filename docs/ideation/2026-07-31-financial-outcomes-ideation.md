---
date: 2026-07-31
topic: financial-outcomes-features
focus: feature gaps that most improve users' finances (debt reduction, wealth, saving)
mode: repo-grounded
status: all 6 survivors selected for implementation (2026-07-31)
---

# Ideation: Coffer financial-outcomes features (run 34389b0b)

Focus: feature gaps that most improve real outcomes (debt down, wealth up, saving more).
Mode: repo-grounded. Axes: debt-payoff / saving-and-goal-funding / spending-awareness / wealth-and-net-worth / forecasting.

## Survivors (6)

1. **Debt Payoff Planner with promo-APR cliffs and snowflake what-ifs** (debt-payoff, conf 90%, Medium)
   Snowball/avalanche/custom ordering, payoff date + total interest per strategy, extra-£ slider and one-off snowflakes, promo-rate schedules (0% until X then Y%) with expiry countdowns and balance-transfer what-ifs, cumulative interest-saved odometer. Basis: direct — Debt model already stores APR/minimum/due-day and the API's only computation is a pie-chart sum; external — Undebt.it/Monarch table stakes, promo modeling is the named differentiator, UK balance-transfer culture. Follow-on: auto-reconcile balances from imported payments (living ledger).

2. **Recurring-Transaction Engine + 60-day Forward Ledger** (forecasting, conf 80%, High but phased)
   First-class RecurringItem detected from import history; consumers: bill calendar, day-by-day balance projection from last attested balance, reserve-aware low-balance warnings, safe-to-commit number, auto-built cashflow grid (its dormant account_id/category_id hooks), pre-renewal subscription warnings. Basis: direct — zero recurring awareness today; CashflowLine docstring promises matching "later"; external — PocketSmith/Simplifi validate recurring-driven forecasting. The highest-leverage primitive: consumed by surplus close, raise capture, sinking funds, weekly digest.

3. **Monthly Close & Surplus Allocator** (saving-and-goal-funding, conf 85%, Medium)
   After import: realized surplus computed from actuals; one ranked list of destinations priced in outcomes (£ interest saved vs highest-APR debt, months moved on goals, runway gained vs survival floor); one click applies as overpayment/goal contribution. Delivered at the import moment (the debrief ritual). Basis: reasoned — misallocation (cash idle beside 25% APR debt) is the dominant household error and Coffer holds both sides on separate pages; converged independently in 5 of 6 frames.

4. **Raise Capture / Auto-Escalation** (saving-and-goal-funding, conf 75%, Low after #2)
   Detect sustained salary step-ups from recurring income; propose pre-committed split of the raise to debt/goals before lifestyle absorbs it; bake accepted commitments into budget/cashflow lines. Basis: external — Save More Tomorrow (Thaler & Benartzi): 3.5%→13.6% savings-rate lift; the best-evidenced behavioral intervention in the space; no self-hosted tool ships it.

5. **Net Worth from Statement Balance Attestations** (wealth-and-net-worth, conf 85%, Medium)
   Capture the closing/running balance UK statements already contain (currently discarded by parsers) as BalanceSnapshot at import; drift check vs transaction-derived balance catches missing data; manual valuation ledger with staleness ages for property/pension/ISA (wrapper-tagged); monthly net-worth trend + runway. Basis: direct — parsers discard Balance columns, no balance-history model exists; external — Firefly III punts here, property valuation absent in all reviewed UK apps. Milestone ("base camp") framing is a design variant. Unlocks UK allowance meter later.

6. **Post-Import Triage + Correction-to-Rule Learning + Coverage Sentinel** (spending-awareness, conf 90%, Low-Medium)
   Keyboard triage queue of uncategorized/low-confidence rows after confirm; every recategorization offers a generated CategoryRule (applied retroactively); per-account "imported through" dates with gap detection and provisional badges on dashboard figures. Basis: direct — rules are hand-authored regex, nothing drains uncategorized residue, nothing distinguishes "spent nothing" from "haven't uploaded"; external — Copilot's ~8s/txn review loop. The data-integrity floor every other survivor stands on.

Suggested sequencing: 6 → 1 (independent, highest ROI/loc) → 2 → 3 → 4, with 5's snapshot capture built alongside 2 (shared primitive).

## Rejections
| Idea | Reason |
|---|---|
| Living debt ledger | absorbed into #1 as its reconciliation layer |
| Goal funding schedules | largely subsumed by #3 (standing contributions are an allocation type); revisit in its brainstorm |
| JIT sinking funds | strong variant of goal funding; brainstorm branch of #3 after #2 exists |
| Statement debrief ritual | merged into #3's delivery moment |
| Net worth base camps | presentation variant inside #5 |
| UK tax-year allowance meter | high-value niche; needs #5's wrapper tagging first — second wave |
| Payday-anchored budget periods | schema-invasive period abstraction; expensive vs current value |
| Self-drafting budget + burn-rate | lower outcome-leverage; burn-rate flags fold into #3's debrief |
| Weekly wire email digest | channel not capability; valuable after #1/#2 create deadline alerts |
| Survival floor & runway | input to #3's ranking and #5's runway, not standalone |
