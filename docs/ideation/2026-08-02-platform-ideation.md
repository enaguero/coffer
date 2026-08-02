---
date: 2026-08-02
topic: platform-wide-improvements
focus: all areas (UX, reliability, data quality, features) after the financial-outcomes wave
mode: repo-grounded
status: survivors 1,3,4,5,6,7 selected for implementation (2026-08-02); 2 declined; 3's mail-in path declined
---

# Ideation: Coffer platform-wide improvements (run 31cd3bd4)

## Grounding Context
Post-financial-outcomes state: import engine (UK presets, profiles, OFX/QIF), debt planner (promo-APR),
recurring+forecast, net worth from balance attestations, surplus allocator + raise capture, goal funding,
UK allowances, weekly digest, triage + rule learning, data freshness. Gaps: no backup/export at all
(statement originals live outside Postgres); migrations run unconditionally at container start; email-only
push via host cron; desktop-only UI; single-person despite multi-user auth; mixed-currency aggregates are
dishonest (seed's Chilean accounts stored as USD; most-common-currency formatting).
External: Actual Budget's restorable zip = backup bar; restore VERIFICATION unclaimed space-wide; Firefly III
restore data loss (#3107); household sharing unsolved everywhere (Firefly #372 open since 2016, YNAB Together
is the bar); ntfy/webhooks homelab norm; cmd-K table stakes; local-LLM categorization PoC-grade;
5-15 min time-to-value triples retention; blank states cause 84% first-session abandonment.

## Topic Axes
data-safety · onboarding-and-daily-ux · household-and-sharing · alerts-and-automation · intelligence

## Ranked Ideas

### 1. Verified Backups + Fearless Upgrades — **Selected**
One-artifact export (DB + statement originals + manifest), one-command restore, scheduled restore drill with
invariant checks ("last verified restore: N days ago"), pre-upgrade snapshot before pending migrations.
Basis: external (Actual bar, Firefly #3107, verification unclaimed) + direct (zero export endpoints;
`alembic upgrade head` unguarded). Confidence 90% / Medium. Status: Explored.

### 2. Event Bus + In-App Scheduler + Homelab Channels — **Declined by user**
ntfy/Telegram/webhook adapters over an events table; digest becomes a subscriber; one-click action links.
Confidence 85% / Medium. Status: Unexplored (user declined 2026-08-02).

### 3. Statement Inbox: share-sheet PWA + watch folder (mail-in declined) — **Selected (partial)**
Intake paths feeding the existing preview/confirm pipeline; nothing auto-commits. Basis: direct (desktop-only
layout; preview flow is a safe landing zone) + reasoned (upload friction is the abandonment mechanism).
Confidence 80% / Medium-High. Status: Explored.

### 4. Household Mode: Yours / Mine / Ours — **Selected**
Per-person logins, account-level shared visibility, Mine/Ours dashboard scope; read-only membership first.
Basis: external (space-wide gap; YNAB Together bar) + direct (auth exists, users are silos).
Confidence 70% / High. Status: Explored.

### 5. Statements as Ground Truth: replay + continuity + drift — **Selected**
Re-parse retained originals with the current engine and diff; per-account coverage gaps and balance-chain
discontinuities; (drift SPC deferred). Basis: direct (originals retained, engine deterministic).
Confidence 80% / Medium-High. Status: Explored.

### 6. Statement-First Onboarding: checklist + inline setup + bulk backfill — **Selected**
Guided empty states, account quick-create inside the import flow, multi-statement backfill queue.
(Playground/persona bundles deferred until the archive format exists.) Basis: external (retention evidence)
+ direct (engine does the hard parts). Confidence 85% / Medium. Status: Explored.

### 7. Real Multi-Currency — **Selected**
Per-account currency honored in aggregates; self-hosted FX table (manual/CSV rates, no API);
display-currency setting; honest "unconverted" flagging; seed fixed to CLP. Basis: direct (seed lines;
most-common heuristic mis-sums). Confidence 90% / Medium. Status: Explored.

## Rejection Summary
| Idea | Reason |
|---|---|
| cmd-K content search | second wave — below the trust/household line |
| Local-LLM rule foundry / kNN assist | ecosystem PoC-grade; shipped rule-learning already compounds |
| Rule health self-audit | fold into a later triage iteration |
| 2FA + recovery + break-glass CLI | real, narrow; cheap second-wave win |
| Scoped API tokens | rides along with inbox/headless intake later |
| Full i18n | correctness slice covered by multi-currency; translations deferred |
| In-app scheduler / preflight / action links / drift SPC / personas | folded into 1, 2, 5, 6 respectively |
