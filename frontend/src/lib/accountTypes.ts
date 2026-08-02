import type { AccountType } from "../api/types";

/** One vocabulary for account types — the Accounts form and the Import
 * quick-create must never drift apart. */
export const ACCOUNT_TYPE_OPTIONS: { value: AccountType; label: string }[] = [
  { value: "checking", label: "Checking / current" },
  { value: "savings", label: "Savings" },
  { value: "credit_card", label: "Credit card" },
  { value: "loan", label: "Loan" },
  { value: "overdraft", label: "Overdraft" },
  { value: "cash", label: "Cash" },
  { value: "other", label: "Other (pension, property…)" },
];
