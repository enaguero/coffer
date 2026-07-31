"""Pure analytics over Coffer's data: debt payoff simulation, recurring
detection, cashflow forecasting, net worth, and surplus/raise insights.

Modules here take plain values (rows already fetched by the API layer) and do
arithmetic — no DB sessions, no FastAPI. That keeps every financial calculation
unit-testable without fixtures.
"""
