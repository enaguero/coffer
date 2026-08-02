import { Route, Routes } from "react-router-dom";

import Layout from "./components/Layout";
import ProtectedRoute from "./components/ProtectedRoute";
import Accounts from "./pages/Accounts";
import Budget from "./pages/Budget";
import Cashflow from "./pages/Cashflow";
import Categories from "./pages/Categories";
import Dashboard from "./pages/Dashboard";
import Debts from "./pages/Debts";
import Forecast from "./pages/Forecast";
import Goals from "./pages/Goals";
import Import from "./pages/Import";
import Integrity from "./pages/Integrity";
import Login from "./pages/Login";
import NetWorth from "./pages/NetWorth";
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
          <Route path="forecast" element={<Forecast />} />
          <Route path="networth" element={<NetWorth />} />
          <Route path="import" element={<Import />} />
          <Route path="integrity" element={<Integrity />} />
        </Route>
      </Route>
    </Routes>
  );
}
