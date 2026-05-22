export type AccountType =
  | "checking"
  | "savings"
  | "credit_card"
  | "loan"
  | "overdraft"
  | "cash"
  | "other";

export type CategoryKind = "income" | "expense" | "debt_payment" | "saving";

export interface User {
  id: number;
  email: string;
  full_name: string | null;
}

export interface Account {
  id: number;
  name: string;
  type: AccountType;
  institution: string | null;
  currency: string;
  opening_balance: string;
}

export interface Category {
  id: number;
  name: string;
  kind: CategoryKind;
  color: string | null;
}

export interface Transaction {
  id: number;
  account_id: number;
  category_id: number | null;
  statement_import_id: number | null;
  posted_on: string;
  description: string;
  amount: string;
  notes: string | null;
  external_id: string | null;
}

export interface Debt {
  id: number;
  name: string;
  account_id: number | null;
  original_principal: string;
  current_balance: string;
  interest_rate_apr: string | null;
  minimum_payment: string | null;
  due_day_of_month: number | null;
  starts_on: string | null;
  ends_on: string | null;
  notes: string | null;
}

export interface DebtSummary {
  total_owed: string;
  by_debt: Debt[];
}

export interface BudgetMonthCell {
  category_id: number;
  category_name: string;
  planned: string;
  actual: string;
}

export interface BudgetMonthView {
  year: number;
  month: number;
  income_planned: string;
  income_actual: string;
  expenses_planned: string;
  expenses_actual: string;
  saving_planned: string;
  saving_actual: string;
  rows: BudgetMonthCell[];
}

export interface Goal {
  id: number;
  name: string;
  target_amount: string;
  current_amount: string;
  target_date: string | null;
  notes: string | null;
  progress: number;
}

export interface ImportResponse {
  import_id: number;
  rows_parsed: number;
  rows_imported: number;
  skipped_duplicates: number;
}
