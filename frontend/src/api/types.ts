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
  auto_categorized: number;
}

export interface CategoryRule {
  id: number;
  pattern: string;
  category_id: number;
  priority: number;
}

export interface ApplyRulesResponse {
  rules_evaluated: number;
  transactions_updated: number;
}

export type BankConnectionStatus = "pending" | "linked" | "expired" | "revoked";

export interface BankConnection {
  id: number;
  provider: "gocardless";
  institution_id: string;
  institution_name: string;
  status: BankConnectionStatus;
  requisition_expires_at: string | null;
  created_at: string;
}

export interface InstitutionRef {
  id: string;
  name: string;
  bic: string | null;
  countries: string[];
  logo_url: string | null;
}

export interface LinkStartResponse {
  bank_connection_id: number;
  requisition_id: string;
  link_url: string;
}

export interface DiscoveredAccount {
  external_account_id: string;
  iban_last4: string | null;
  name: string | null;
  currency: string | null;
}

export interface LinkCompleteResponse {
  bank_connection_id: number;
  institution_name: string;
  accounts: DiscoveredAccount[];
}

export type SyncJobStatus = "running" | "success" | "failed";

export interface SyncJob {
  id: number;
  bank_connection_id: number;
  account_id: number | null;
  started_at: string;
  completed_at: string | null;
  status: SyncJobStatus;
  transactions_fetched: number;
  transactions_imported: number;
  error_message: string | null;
}

export interface SyncResponse {
  sync_job_ids: number[];
  queued: number;
}

export type CashflowKind = "income" | "expense";

export interface CashflowEntryIn {
  year: number;
  month: number;
  amount: string;
}

export interface CashflowLine {
  id: number;
  name: string;
  kind: CashflowKind;
  country: string;
  currency: string;
  account_id: number | null;
  category_id: number | null;
  sort_order: number;
  is_active: boolean;
  notes: string | null;
  entries: CashflowEntryIn[];
}

export interface CashflowMonth {
  year: number;
  month: number;
}

export interface CashflowMonthTotal extends CashflowMonth {
  income: string;
  expense: string;
  net: string;
}

export interface CashflowCurrencyTotals {
  currency: string;
  months: CashflowMonthTotal[];
}

export interface CashflowGrid {
  months: CashflowMonth[];
  lines: CashflowLine[];
  totals_by_currency: CashflowCurrencyTotals[];
}

export interface CashflowEntryUpsert {
  line_id: number;
  year: number;
  month: number;
  amount: number | string;
}
