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
  bank_id: string | null;
  currency: string;
  opening_balance: string;
}

export interface UkBank {
  id: string;
  name: string;
  account_types: AccountType[];
  formats: string[];
  notes: string;
}

// Mirrors backend ImportProfileConfig; column refs are header names or indexes.
export interface ImportProfileConfig {
  delimiter?: string | null;
  skip_rows?: number;
  has_header?: boolean;
  date_column: string | number;
  description_columns: Array<string | number>;
  amount_column?: string | number | null;
  debit_column?: string | number | null;
  credit_column?: string | number | null;
  external_id_column?: string | number | null;
  date_format?: string | null;
  day_first?: boolean;
  invert_amount?: boolean;
  encoding?: string;
}

export interface ImportProfile {
  id: number;
  account_id: number;
  name: string;
  source: string;
  config: ImportProfileConfig;
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
  promo_apr: string | null;
  promo_ends_on: string | null;
  minimum_payment: string | null;
  due_day_of_month: number | null;
  starts_on: string | null;
  ends_on: string | null;
  notes: string | null;
}

export interface DebtPlanDebt {
  id: number;
  name: string;
  payoff_date: string | null;
  interest_paid: string;
}

export interface PromoCliff {
  debt_id: number;
  name: string;
  promo_ends_on: string;
  balance_at_expiry: string;
  reverting_apr: string;
  extra_yearly_interest: string;
}

export interface DebtPlan {
  strategy: string;
  months: number;
  debt_free_date: string | null;
  total_interest: string;
  total_paid: string;
  monthly_budget: string;
  interest_saved_vs_minimum: string | null;
  months_saved_vs_minimum: number | null;
  debts: DebtPlanDebt[];
  balance_series: Array<{ on: string; balance: string }>;
  promo_cliffs: PromoCliff[];
  assumptions: string[];
  unpayable: boolean;
}

export interface DebtPlanCompare {
  minimum: DebtPlan;
  snowball: DebtPlan;
  avalanche: DebtPlan;
}

export interface RecurringItem {
  account_id: number;
  description: string;
  cadence: string;
  cadence_days: number;
  typical_amount: string;
  monthly_equivalent: string;
  occurrences: number;
  first_seen: string;
  last_seen: string;
  next_expected: string;
  confidence: number;
  active: boolean;
  is_income: boolean;
  category_id: number | null;
}

export interface ForecastEvent {
  on: string;
  description: string;
  amount: string;
  cadence: string;
  is_income: boolean;
}

export interface Forecast {
  start_balance: string;
  reserve: string;
  days: number;
  series: Array<{ on: string; balance: string }>;
  events: ForecastEvent[];
  due_markers: Array<{ on: string; name: string; minimum_payment: string | null }>;
  min_balance: string;
  min_balance_date: string | null;
  first_below_reserve: string | null;
  first_below_zero: string | null;
  safe_to_commit: string;
}

export interface AccountBalanceInfo {
  id: number;
  name: string;
  type: AccountType;
  currency: string;
  balance: string;
  as_of: string | null;
  source: string;
  drift: string | null;
}

export interface NetWorth {
  accounts: AccountBalanceInfo[];
  register_debts: Array<{ id: number; name: string; balance: string }>;
  assets: string;
  liabilities: string;
  net: string;
  series: Array<{ on: string; assets: string; liabilities: string; net: string }>;
}

export interface AllocationOption {
  kind: "debt" | "goal" | "runway";
  target_id: number | null;
  name: string;
  apr: string | null;
  yearly_interest_saved: string | null;
  months_earlier: string | null;
  runway_months_gained: string | null;
  note: string;
}

export interface DetectedRaise {
  description: string;
  account_id: number;
  cadence: string;
  previous_amount: string;
  new_amount: string;
  monthly_delta: string;
}

export interface Surplus {
  year: number;
  month: number;
  income: string;
  outflows: string;
  surplus: string;
  txn_count: number;
  uncategorized_count: number;
  uncategorized_amount: string;
  amount_considered: string;
  options: AllocationOption[];
  raises_detected: DetectedRaise[];
}

export interface AccountCoverage {
  account_id: number;
  name: string;
  type: AccountType;
  last_txn_on: string | null;
  txn_count: number;
  last_import_at: string | null;
  last_snapshot_on: string | null;
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
