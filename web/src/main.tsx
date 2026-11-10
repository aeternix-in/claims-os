import React from 'react';
import ReactDOM from 'react-dom/client';
import { BrowserRouter, Navigate, Route, Routes, useLocation } from 'react-router-dom';
import './index.css';
import Shell, { NAV } from './components/Shell';
import { SessionProvider, useSession } from './lib/session';
import Login from './screens/Login';
import { Desk, Preauth, Discharge, Cash } from './screens/Desk';
import QueryDesk from './screens/QueryDesk';
import BankMatch from './screens/BankMatch';
import Leakage from './screens/Leakage';
import Reports from './screens/Reports';
import Setup from './screens/Setup';
import Overview from './screens/Overview';
import Payers from './screens/Payers';
import Admit from './screens/Admit';
import CaseView from './screens/CaseView';

function Placeholder() {
  const name = useLocation().pathname.slice(1);
  const n = NAV.find((x) => x.name === name);
  return <div><h1 className="font-serif text-[28px]">{n?.label}</h1><p className="text-ink-3">{n?.hint}. Being connected to the backend.</p></div>;
}

function App() {
  const { session, profile, loading } = useSession();
  if (loading) return <div className="grid min-h-screen place-items-center text-ink-3">Loading…</div>;
  if (!session) return <Login />;
  if (!profile) return <div className="grid min-h-screen place-items-center px-4 text-center text-ink-2">Your account isn't linked to a hospital yet. Ask an admin to add you.</div>;
  return (
    <Routes>
      <Route element={<Shell />}>
        <Route index element={<Navigate to="/overview" replace />} />
        <Route path="overview" element={<Overview />} />
        <Route path="desk" element={<Desk />} />
        <Route path="preauth" element={<Preauth />} />
        <Route path="queries" element={<QueryDesk />} />
        <Route path="discharge" element={<Discharge />} />
        <Route path="cash" element={<Cash />} />
        <Route path="bank" element={<BankMatch />} />
        <Route path="leakage" element={<Leakage />} />
        <Route path="reports" element={<Reports />} />
        <Route path="setup" element={<Setup />} />
        <Route path="payers" element={<Payers />} />
        <Route path="admit" element={<Admit />} />
        <Route path="case/:id" element={<CaseView />} />
        {NAV.filter((n) => !('ready' in n)).map((n) => <Route key={n.name} path={n.name} element={<Placeholder />} />)}
      </Route>
    </Routes>
  );
}

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode><BrowserRouter><SessionProvider><App /></SessionProvider></BrowserRouter></React.StrictMode>,
);
