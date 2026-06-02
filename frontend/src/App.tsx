import { Route, Routes } from "react-router-dom";

import Layout from "./components/Layout";
import ProtectedRoute from "./components/ProtectedRoute";
import Accounts from "./pages/Accounts";
import BankCallback from "./pages/BankCallback";
import BankConnections from "./pages/BankConnections";
import Budget from "./pages/Budget";
import Cashflow from "./pages/Cashflow";
import Categories from "./pages/Categories";
import Dashboard from "./pages/Dashboard";
import Debts from "./pages/Debts";
import Goals from "./pages/Goals";
import Import from "./pages/Import";
import Login from "./pages/Login";
import Signup from "./pages/Signup";
import Transactions from "./pages/Transactions";

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route path="/signup" element={<Signup />} />
      <Route element={<ProtectedRoute />}>
        <Route element={<Layout />}>
          <Route index element={<Dashboard />} />
          <Route path="budget" element={<Budget />} />
          <Route path="cashflow" element={<Cashflow />} />
          <Route path="transactions" element={<Transactions />} />
          <Route path="accounts" element={<Accounts />} />
          <Route path="categories" element={<Categories />} />
          <Route path="debts" element={<Debts />} />
          <Route path="goals" element={<Goals />} />
          <Route path="banks" element={<BankConnections />} />
          <Route path="banks/callback" element={<BankCallback />} />
          <Route path="import" element={<Import />} />
        </Route>
      </Route>
    </Routes>
  );
}
