import { useState } from "react";
import { Link, useNavigate, useLocation } from "react-router-dom";
import { supabase } from "./client";
import { useApp } from "./context";
import { Heading, Card, Field, ErrorBox } from "./ui";
export function AuthPage({ signup = false, reset = false }) {
  const [email, setEmail] = useState(""),
    [password, setPassword] = useState(""),
    [name, setName] = useState(""),
    [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [message, setMessage] = useState("");
  const navigate = useNavigate(),
    loc = useLocation();
  async function submit(e) {
    e.preventDefault();
    setBusy(true);
    setError("");
    setMessage("");
    try {
      let result;
      if (reset) {
        result = await supabase.auth.resetPasswordForEmail(email, {
          redirectTo: location.origin + "/account?recovery=1",
        });
      } else if (signup) {
        result = await supabase.auth.signUp({
          email,
          password,
          options: {
            data: { display_name: name },
            emailRedirectTo: location.origin + "/auth/callback",
          },
        });
      } else
        result = await supabase.auth.signInWithPassword({ email, password });
      if (result.error) throw result.error;
      if (reset)
        setMessage(
          "If the account exists, a password reset link has been sent.",
        );
      else if (signup && !result.data.session)
        setMessage("Check your email to confirm your account, then sign in.");
      else navigate(loc.state?.from || "/research");
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="auth-wrap">
      <Card>
        <Link to="/" aria-label="Back to home" className="auth-logo">✦</Link>
        <span className="eyebrow">AI RESEARCH</span>
        <h1>
          {reset
            ? "Reset password"
            : signup
              ? "Create your workspace"
              : "Welcome back"}
        </h1>
        <p>Research with evidence. Keep every insight.</p>
        <form onSubmit={submit}>
          {signup && (
            <Field
              label="Your name"
              value={name}
              maxLength={100}
              required
              onChange={(e) => setName(e.target.value)}
            />
          )}
          <Field
            label="Email address"
            type="email"
            autoComplete="email"
            required
            value={email}
            onChange={(e) => setEmail(e.target.value)}
          />
          {!reset && (
            <Field
              label="Password"
              type="password"
              minLength={8}
              required
              autoComplete={signup ? "new-password" : "current-password"}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
          )}
          <ErrorBox error={error} />
          {message && (
            <p className="success" role="status">
              {message}
            </p>
          )}
          <button className="primary" disabled={busy}>
            {busy
              ? "Please wait…"
              : reset
                ? "Send reset link"
                : signup
                  ? "Create account"
                  : "Sign in →"}
          </button>
        </form>
        <div className="auth-links">
          <Link to={signup || reset ? "/login" : "/signup"} state={loc.state}>
            {signup || reset ? "Back to sign in" : "Create an account"}
          </Link>
          {!reset && !signup && <Link to="/reset">Forgot password?</Link>}
        </div>
      </Card>
    </div>
  );
}
export function AccountPage() {
  const { session, profile, notify } = useApp();
  const [password, setPassword] = useState(""),
    [busy, setBusy] = useState(false);
  async function change(e) {
    e.preventDefault();
    setBusy(true);
    const { error } = await supabase.auth.updateUser({ password });
    setBusy(false);
    if (error) notify(error.message, "error");
    else {
      notify("Password updated", "success");
      setPassword("");
    }
  }
  return (
    <>
      <Heading title="Your account">Manage your session and security.</Heading>
      <div className="two-col">
        <Card>
          <h2>{profile?.display_name || "Researcher"}</h2>
          <p>{session.user.email}</p>
          <p>Role: {profile?.role || "Loading…"}</p>
          <button
            onClick={async () => {
              const { error } = await supabase.auth.signOut();
              if (error) {
                notify(error.message, "error");
                return;
              }

            }}
          >
            Sign out
          </button>
        </Card>
        <Card>
          <h2>Change password</h2>
          <form onSubmit={change}>
            <Field
              label="New password"
              type="password"
              minLength={8}
              required
              autoComplete="new-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
            <button className="primary" disabled={busy}>
              Update password
            </button>
          </form>
        </Card>
      </div>
    </>
  );
}
