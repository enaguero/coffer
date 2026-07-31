from fastapi import APIRouter

from app.api.v1 import (
    accounts,
    auth,
    banks,
    budgets,
    cashflow,
    categories,
    category_rules,
    debts,
    goals,
    imports,
    insights,
    transactions,
)

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(auth.router)
api_router.include_router(accounts.router)
api_router.include_router(categories.router)
api_router.include_router(category_rules.router)
api_router.include_router(transactions.router)
api_router.include_router(debts.router)
api_router.include_router(budgets.router)
api_router.include_router(goals.router)
api_router.include_router(imports.router)
api_router.include_router(banks.router)
api_router.include_router(cashflow.router)
api_router.include_router(insights.router)
