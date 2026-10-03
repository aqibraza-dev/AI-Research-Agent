import { useState } from "react";
import {
  BrowserRouter,
  Routes,
  Route,
  NavLink,
  Navigate,
  Outlet,
  Link,
  useLocation,
} from "react-router-dom";
import {
  Plus,
  BookOpen,
  MessageSquare,
  Calendar,
  BarChart3,
  Layers,
  Shield,
  Settings,
  Bell,
  Menu,
  X,
  Sparkles,
  LogOut,
} from "lucide-react";
import { Provider, useApp } from "./context";
import { configured, supabase } from "./client";
import { ErrorBox, number } from "./ui";
import { LandingPage } from "./landing-page";
import { AuthPage, AccountPage } from "./auth-pages";
import {
  ResearchPage,
  HistoryPage,
  ReportPage,
  ChatPage,
} from "./research-pages";
import {
  SchedulesPage,
  AnalyticsPage,
  ModelsPage,
  NotificationsPage,
  RedTeamPage,
  AdminPage,
} from "./workspace-pages";
const links = [
  ["/research", "New research", Plus],
  ["/history", "Research history", BookOpen],
  ["/chat", "Chat", MessageSquare],
  ["/schedules", "Scheduler", Calendar],
  ["/analytics", "Analytics", BarChart3],
  ["/models", "Models", Layers],
  ["/redteam", "Red teaming", Shield],
];
function Layout() {
  const { session, loading, profile, profileError, refreshProfile } = useApp();
  const [open, setOpen] = useState(false);
  const loc = useLocation();
  if (loading) return <div className="center">Restoring your session…</div>;
  if (!session)
    return <Navigate to="/login" state={{ from: loc.pathname + loc.search }} replace />;
  return (
    <div className="shell">
      {open && (
        <button
          className="overlay"
          aria-label="Close menu"
          onClick={() => setOpen(false)}
        />
      )}
      <aside className={open ? "open" : ""}>
        <Link to="/" className="brand">
          <span>
            <Sparkles size={20} />
          </span>
          AI Research
        </Link>
        <span className="nav-label">YOUR WORKSPACE</span>
        <nav>
          {links.map(([path, title, Icon]) => (
            <NavLink
              key={path}
              to={path}
              end={path === "/research"}
              onClick={() => setOpen(false)}
            >
              <Icon size={18} />
              {title}
            </NavLink>
          ))}
          {profile?.role === "admin" && (
            <NavLink to="/admin" onClick={() => setOpen(false)}>
              <Settings size={18} />
              Admin panel
            </NavLink>
          )}
        </nav>
        <div className="quota">
          <span className="eyebrow">DAILY ALLOWANCE</span>
          <strong>
            {number(profile?.usage.charged)}{" "}
            <small>/ {number(profile?.effective_limit)}</small>
          </strong>
          <progress
            max={profile?.effective_limit || 1}
            value={
              (profile?.usage.charged || 0) + (profile?.usage.reserved || 0)
            }
          />
          <p>Input + output tokens · Resets at 00:00 UTC</p>
        </div>
        <NavLink to="/account" className="account-link">
          <span className="avatar">
            {(profile?.display_name ||
              session.user.email ||
              "U")[0].toUpperCase()}
          </span>
          <span>
            {profile?.display_name || "My account"}
            <small>{profile?.role || "Researcher"}</small>
          </span>
        </NavLink>
      </aside>
      <div className="main">
        <header>
          <button
            className="mobile"
            onClick={() => setOpen(!open)}
            aria-label="Open menu"
          >
            <Menu size={20} />
          </button>
          <span className="breadcrumb">
            Workspace <span>/</span>{" "}
            {links.find((x) => x[0] === loc.pathname)?.[1] || "Details"}
          </span>
          <Link
            to="/notifications"
            className="icon-button"
            aria-label="Notifications"
          >
            <Bell size={19} />
          </Link>
        </header>
        <main>
          <ErrorBox error={profileError} />
          {profileError && (
            <button onClick={refreshProfile}>Reconnect profile</button>
          )}
          <Outlet />
        </main>
        <footer>
          AI Research <span>Evidence first. Clearer thinking.</span>
        </footer>
      </div>
    </div>
  );
}
function Router() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<LandingPage />} />
        <Route path="/login" element={<AuthPage />} />
        <Route path="/signup" element={<AuthPage signup />} />
        <Route path="/reset" element={<AuthPage reset />} />
        <Route path="/auth/callback" element={<Navigate to="/research" replace />} />
        <Route element={<Layout />}>
          <Route path="research" element={<ResearchPage />} />
          <Route path="history" element={<HistoryPage />} />
          <Route path="library" element={<Navigate to="/history" replace />} />
          <Route path="research/:id" element={<ReportPage />} />
          <Route path="chat/:id?" element={<ChatPage />} />
          <Route path="schedules" element={<SchedulesPage />} />
          <Route path="analytics" element={<AnalyticsPage />} />
          <Route path="models" element={<ModelsPage />} />
          <Route path="redteam" element={<RedTeamPage />} />
          <Route path="admin" element={<AdminPage />} />
          <Route path="notifications" element={<NotificationsPage />} />
          <Route path="account" element={<AccountPage />} />
          <Route
            path="*"
            element={
              <div className="center">
                <h1>Page not found</h1>
                <Link to="/research">Return to research</Link>
              </div>
            }
          />
        </Route>
      </Routes>
    </BrowserRouter>
  );
}
export default function App() {
  if (!configured)
    return (
      <div className="center">
        <div className="card">
          <h1>Connect your workspace</h1>
          <p>
            Set VITE_SUPABASE_URL and VITE_SUPABASE_PUBLISHABLE_KEY in the
            frontend environment, then restart or redeploy.
          </p>
          <p>See the setup guide included with this project.</p>
        </div>
      </div>
    );
  return (
    <Provider>
      <Router />
    </Provider>
  );
}
