---
date: 2026-08-07
topic: wealth-debt-payoff
---

# Overall Wealth & Debt Payoff Optimization

## Summary

Give every debt its real repayment mechanics and its own currency, add automatic FX rates and a payoff optimizer that computes the shortest path to debt-free, and seed a realistic demo portfolio — then layer on money-lent-out as an asset, statement-anchored reconciliation, and forward wealth-trajectory scenarios, each phase shipping independently.

---

## Problem Frame

The user holds four debts across different institutions: a credit card, a bank personal loan, and two further loans — spanning four distinct repayment mechanics (revolving balance, fixed-installment amortization, flat interest charged on the original principal, and one loan where only the installment amount is visible) and multiple currencies. Coffer's current debt register models all of them identically: one balance, one APR, monthly compounding. This misstates what is actually owed, misstates payoff dates, and — critically for strategy — makes the existing avalanche/snowball planner blind to mechanics that change the right answer (extra payments against a flat-interest loan save almost nothing, yet APR-ranking may direct money there). Because register debts carry no currency, multi-currency portfolios can't even total honestly. There is also money lent *out* (a personal loan to a friend with agreed monthly interest on capital) that has no representation at all, so "overall wealth" is structurally incomplete.

---

## Key Flows

- F1. Set up a real debt
  - **Trigger:** User adds or edits a debt.
  - **Steps:** Choose repayment type → enter the terms that type needs (rate, installment, end date, promo window as applicable) → choose the debt's currency.
  - **Outcome:** The debt's projected payoff schedule and accrued interest reflect its actual contract mechanics; its balance converts honestly into the wealth view.
  - **Covered by:** R1–R6.
- F2. Find the shortest path
  - **Trigger:** User opens the payoff planner with a monthly payment capacity.
  - **Steps:** Optimizer computes the allocation minimizing time-to-debt-free / total interest → user sees the recommended plan next to avalanche, snowball, and minimums-only → user tweaks capacity or adds what-if extras and watches the plan and savings move.
  - **Outcome:** A concrete per-debt, per-month payment schedule with debt-free date and interest saved.
  - **Covered by:** R10–R14.
- F3. See it immediately
  - **Trigger:** Fresh install or demo reset (`make seed`).
  - **Steps:** Seed loads a demo portfolio mirroring the real one, with 3 months of history → user opens Dashboard / Net worth / Debts / planner.
  - **Outcome:** Every Phase-1 surface renders populated without any manual data entry.
  - **Covered by:** R15.

---

## Requirements

**Phase 1 — Debt mechanics & currency**
- R1. Every debt has a repayment type: amortized fixed-term, flat-interest-on-principal, revolving, or statement-only.
- R2. Interest accrual and payoff projection follow each type's real mechanics (amortized: fixed installment to an end date with declining interest portion; flat: interest fixed on original principal regardless of balance; revolving: interest on outstanding balance; existing promo-APR windows and minimum payments keep working where they apply).
- R3. Statement-only debts: when only installment, current balance, and end date are known, the engine derives effective mechanics and labels every derived figure as estimated.
- R4. Each debt carries its own currency.
- R5. When a debt's currency has no FX rate to the display currency, the wealth view shows the debt but excludes it from totals and flags the exclusion (same honest-conversion rule accounts follow).
- R6. The net worth view presents one overall wealth picture: converted account balances and manual valuations minus converted debt balances, with true total owed and per-debt payoff dates.

**Phase 1 — Automatic FX rates**
- R7. FX rates can refresh automatically from an external source, behind an explicit user-enabled setting (off by default — this is the app's first outbound network dependency).
- R8. A manually-set rate always overrides a fetched rate for that currency.
- R9. When a fetch fails or the instance is offline, last-known rates remain in use and their staleness is visible; nothing else degrades.

**Phase 1 — Payoff optimizer**
- R10. Given a monthly payment capacity, the optimizer computes the allocation across all debts that minimizes time-to-debt-free and total interest, respecting each debt's mechanics and constraints (minimum payments, promo windows expiring, flat-interest loans where prepayment saves nothing).
- R11. Output is a concrete plan: per-debt per-month payments, debt-free date, total interest paid, and interest saved versus the minimums-only baseline and versus avalanche and snowball.
- R12. The plan is denominated in the display currency; per-debt figures also show in the debt's own currency.
- R13. What-if extras (one-off or recurring additional amounts) re-run the optimization and show the delta.
- R14. All output is presented as computed arithmetic with its assumptions visible — comparisons and schedules, never framed as financial advice.

**Phase 1 — Seed data**
- R15. The demo seed includes a debt portfolio mirroring the user's real shape — a revolving credit card, an amortized bank loan, a flat-interest loan, and a statement-only loan, spanning at least two currencies — with 3 months of payment history and balance snapshots, so Dashboard, Net worth, Debts, and the optimizer all render populated. The existing seed invariant (demo minimums sum to 40% of the demo salary) is preserved.

**Phase 2 — Money lent out (receivables)**
- R16. A debt can be directional: money the user has lent, with the user as creditor, counted as an asset in overall wealth.
- R17. Receivable terms support the agreed arrangement: a monthly interest percentage on outstanding capital; recording an incoming payment reduces capital and tracks interest received separately.
- R18. Receivables convert into the wealth view under the same honest-conversion rule; they are displayed as expected inflows but are not part of payoff optimization.

**Phase 3 — Statement-anchored debts**
- R19. A debt linked to an account can derive its balance, payments, and actual interest charged from that account's imported statements instead of manual updates.
- R20. When actual interest charged diverges from what the debt's model predicts, the divergence is flagged — the signal that the model or the bank's terms are wrong.
- R21. For statement-only debts, observed history refines the estimated mechanics over time.

**Phase 4 — Wealth trajectory**
- R22. Net worth can be projected forward under a chosen payoff plan — asset balances and debt payoff combined over time, in the display currency.
- R23. Trajectories for different scenarios can be compared side by side.

---

## Acceptance Examples

- AE1. **Covers R5.** Given a debt in a currency with no saved or fetched FX rate, when the user opens Net worth, the debt is listed, the total excludes it, and its currency appears in the excluded list.
- AE2. **Covers R8.** Given both a fetched and a manually-entered rate for the same currency, when anything converts, the manual rate is used.
- AE3. **Covers R9.** Given FX auto-refresh enabled and the fetch failing, when the user views converted figures, last-known rates apply and their age is visible.
- AE4. **Covers R2, R10.** Given a flat-interest loan and a revolving card with similar balances and spare capacity, when the optimizer runs, extra payments go to the card, and the plan states why the flat loan doesn't benefit from prepayment.
- AE5. **Covers R3.** Given a debt entered with only installment, balance, and end date, when its schedule renders, the derived rate and projections are labeled as estimates.
- AE6. **Covers R15.** Given a fresh `make seed`, when the user signs in as the demo user, Dashboard, Net worth, Debts, and the optimizer all show populated, coherent 3-month history.
- AE7. **Covers R16, R17.** Given a receivable with 1%-monthly-on-capital terms, when an incoming payment is recorded, capital drops by the capital portion, interest received accrues separately, and net worth's asset side updates.
- AE8. **Covers R20.** Given a statement-linked debt whose bank charged more interest than the model predicts, when statements import, the debt is flagged with the divergence.

---

## Success Criteria

- The user can enter all four real debts with their true mechanics and currencies and see, in one view, an accurate total owed and believable payoff dates — numbers they'd trust over the bank apps.
- The optimizer's recommended plan never loses to avalanche, snowball, or minimums-only on total interest for the same capacity, and shows the saving explicitly.
- Immediately after seeding, every Phase-1 surface demonstrates the feature with 3 months of realistic history — no manual setup needed to evaluate it.
- Handoff quality: `ce-plan` can plan Phase 1 from this document without inventing product behavior, scope, or success measures.

---

## Scope Boundaries

- Live bank connections / Open Banking — statements remain the only ingestion path.
- Financial advice or recommendations beyond computed comparisons (no "you should refinance" logic).
- Automatic FX remains off until the user enables it — no outbound calls by default.
- Receivables do not feed the payoff optimizer's capacity (display and tracking only, Phase 2).
- No speculative asset modeling (market returns, property appreciation) in trajectory projections — Phase 4 projects from known mechanics only.

---

## Key Decisions

- Upgrade the existing debt ledger rather than statement-first or sandbox-first: mechanics correctness is the prerequisite for every other layer; reconciliation and trajectory layer on top. (Chosen over both alternatives in dialogue.)
- Debts gain a currency column, deliberately breaking the "register figures are display-denominated" convention for debts only — multi-currency portfolios can't total honestly otherwise.
- Automatic FX is opt-in with manual override winning: keeps the privacy default (no outbound calls) while giving the user the convenience they asked for.
- Receivables are directional debts, not a separate module: same machinery, opposite sign, minimizing carrying cost.
- The optimizer is framed as arithmetic, not advice: it reports schedules and savings with assumptions visible.
- Phased delivery in the user's priority order: mechanics + FX + optimizer + seed first; receivables, reconciliation, trajectory follow independently.

---

## Dependencies / Assumptions

- Assumption (unconfirmed): the user currently tracks these debts ad hoc (bank apps / spreadsheet); no export exists to migrate.
- Assumption: exact contract terms (rates, end dates) are obtainable for at least the amortized and flat loans; the statement-only path covers the rest.
- Phase 3 depends on the loan institutions providing importable statements (CSV/PDF/OFX).
- Phase 1 seed work must preserve the demo-user invariant documented in the repo (minimums = 40% of demo salary).

---

## Outstanding Questions

### Deferred to Planning

- [Affects R7][Needs research] Which FX rate source: reliability, licensing, currency coverage, and fetch cadence for a self-hosted app.
- [Affects R10][Technical] Optimizer algorithm: whether exact search over allocation orderings is tractable at 4–10 debts or a greedy-with-corrections approach suffices — and how promo-window expiries interact with flat-interest ranking.
- [Affects R3][Technical] Inference method for statement-only debts (solving for effective rate from installment/balance/term) and how estimate uncertainty is displayed.
- [Affects R15][Technical] Whether seeded history uses the statement-import pipeline (so integrity/coverage surfaces light up too) or direct inserts.
- [Affects R22][Technical] Whether trajectory projection extends the existing forecast engine or composes net-worth-at-date with the payoff plan.
