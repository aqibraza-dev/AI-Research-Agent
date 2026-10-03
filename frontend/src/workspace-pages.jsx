import { useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { DateTime } from "luxon";
import { Calendar, Activity, Layers, ShieldCheck } from "lucide-react";
import { api, models, modelNames } from "./client";
import { useApp } from "./context";
import {
  Heading,
  Card,
  Field,
  ModelSelect,
  ErrorBox,
  Status,
  Empty,
  Pager,
  Exports,
  JobProgress,
  useResource,
  date,
  number,
} from "./ui";

const newForm = () => ({
  title: "",
  topic: "",
  depth: "standard",
  model: models[0],
  frequency: "daily",
  timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
  start_at: DateTime.local().plus({ hours: 1 }).toFormat("yyyy-MM-dd'T'HH:mm"),
  end_at: "",
  weekdays: [1],
  paused: false,
});
export function SchedulesPage() {
  const [form, setForm] = useState(newForm),
    [editing, setEditing] = useState(null),
    [preview, setPreview] = useState(null),
    [page, setPage] = useState(1),
    [selected, setSelected] = useState(null),
    [runsPage, setRunsPage] = useState(1),
    [busy, setBusy] = useState(false);
  const { notify } = useApp();
  const state = useResource("/schedules?page=" + page, 10000),
    runs = useResource(
      selected ? `/schedules/${selected}/runs?page=${runsPage}` : null,
      10000,
    );
  const set = (key, value) => {
    setForm((f) => ({ ...f, [key]: value }));
    setPreview(null);
  };
  function body() {
    const convert = (value) => {
      if (!value) return null;
      const d = DateTime.fromISO(value, { zone: form.timezone });
      if (!d.isValid || d.toFormat("yyyy-MM-dd'T'HH:mm") !== value)
        throw new Error(
          "Choose a valid local time; this time may fall in a daylight-saving gap.",
        );
      return d.toISO();
    };
    return {
      ...form,
      start_at: convert(form.start_at),
      end_at: convert(form.end_at),
    };
  }
  async function save(e) {
    e.preventDefault();
    setBusy(true);
    try {
      await api(editing ? "/schedules/" + editing : "/schedules", {
        method: editing ? "PUT" : "POST",
        body: body(),
      });
      notify("Schedule saved", "success");
      setEditing(null);
      setForm(newForm());
      setPreview(null);
      state.refresh();
    } catch (err) {
      notify(err.message, "error");
    } finally {
      setBusy(false);
    }
  }
  async function action(id, act) {
    try {
      if (act === "delete") {
        if (
          !confirm(
            "Delete this schedule and its run history? Existing reports are kept.",
          )
        )
          return;
        await api("/schedules/" + id, { method: "DELETE" });
        if (selected === id) setSelected(null);
      } else await api(`/schedules/${id}/${act}`, { method: "POST" });
      notify("Schedule updated", "success");
      state.refresh();
      runs.refresh();
    } catch (e) {
      notify(e.message, "error");
    }
  }
  function edit(s) {
    const f = {};
    Object.keys(newForm()).forEach((k) => (f[k] = s[k]));
    f.start_at = DateTime.fromISO(s.start_at)
      .setZone(s.timezone)
      .toFormat("yyyy-MM-dd'T'HH:mm");
    f.end_at = s.end_at
      ? DateTime.fromISO(s.end_at)
          .setZone(s.timezone)
          .toFormat("yyyy-MM-dd'T'HH:mm")
      : "";
    setForm(f);
    setEditing(s.id);
    window.scrollTo({ top: 0, behavior: "smooth" });
  }
  return (
    <>
      <Heading title="Research, on your schedule">
        Set a rhythm for discovery. Reports arrive in your history and
        notifications.
      </Heading>
      <div className="schedule-layout">
        <Card>
          <h2>{editing ? "Edit schedule" : "Create a schedule"}</h2>
          <form onSubmit={save}>
            <Field
              label="Schedule name"
              required
              maxLength={200}
              value={form.title}
              onChange={(e) => set("title", e.target.value)}
            />
            <Field label="Research topic">
              <textarea
                required
                minLength={3}
                maxLength={2000}
                rows={3}
                value={form.topic}
                onChange={(e) => set("topic", e.target.value)}
              />
            </Field>
            <div className="two-col">
              <Field label="Frequency">
                <select
                  value={form.frequency}
                  onChange={(e) => set("frequency", e.target.value)}
                >
                  {["once", "daily", "weekly", "monthly"].map((s) => (
                    <option key={s}>{s}</option>
                  ))}
                </select>
              </Field>
              <Field label="Depth">
                <select
                  value={form.depth}
                  onChange={(e) => set("depth", e.target.value)}
                >
                  <option>standard</option>
                  <option>deep</option>
                </select>
              </Field>
            </div>
            {form.frequency === "weekly" && (
              <div className="days">
                {["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"].map(
                  (d, i) => (
                    <label key={d}>
                      <input
                        type="checkbox"
                        checked={form.weekdays.includes(i + 1)}
                        onChange={(e) =>
                          set(
                            "weekdays",
                            e.target.checked
                              ? [...form.weekdays, i + 1]
                              : form.weekdays.filter((x) => x !== i + 1),
                          )
                        }
                      />
                      {d}
                    </label>
                  ),
                )}
              </div>
            )}
            <Field
              label="Timezone (IANA name)"
              required
              value={form.timezone}
              onChange={(e) => set("timezone", e.target.value)}
            />
            <Field
              label="Start (in selected timezone)"
              type="datetime-local"
              required
              value={form.start_at}
              onChange={(e) => set("start_at", e.target.value)}
            />
            <Field
              label="End (optional)"
              type="datetime-local"
              value={form.end_at}
              onChange={(e) => set("end_at", e.target.value)}
            />
            <ModelSelect value={form.model} onChange={(v) => set("model", v)} />
            <p className="hint">
              Best-effort delivery. Sleeping hosts may start late. Missed runs
              are combined into the latest occurrence.
            </p>
            {preview && (
              <p className="context-note">
                Next run:{" "}
                {preview.next_run_at
                  ? DateTime.fromISO(preview.next_run_at)
                      .setZone(form.timezone)
                      .toLocaleString(DateTime.DATETIME_FULL)
                  : "No future occurrence"}
              </p>
            )}
            <div className="actions">
              <button
                type="button"
                onClick={async () => {
                  try {
                    setPreview(
                      await api("/schedules/preview", {
                        method: "POST",
                        body: body(),
                      }),
                    );
                  } catch (e) {
                    notify(e.message, "error");
                  }
                }}
              >
                Preview
              </button>
              <button className="primary" disabled={busy}>
                Save schedule
              </button>
              {editing && (
                <button
                  type="button"
                  onClick={() => {
                    setEditing(null);
                    setForm(newForm());
                  }}
                >
                  Cancel edit
                </button>
              )}
            </div>
          </form>
        </Card>
        <div>
          <ErrorBox error={state.error} />
          {state.data?.items.map((s) => (
            <Card key={s.id} className="schedule-card">
              <div className="row">
                <div className="round-icon">
                  <Calendar size={19} />
                </div>
                <h2>{s.title}</h2>
                <Status
                  value={
                    s.paused ? "paused" : s.next_run_at ? "active" : "finished"
                  }
                />
              </div>
              <p>{s.topic}</p>
              <div className="schedule-meta">
                <span>
                  {s.frequency} · {s.timezone}
                </span>
                <strong>Next: {date(s.next_run_at)}</strong>
              </div>
              <div className="actions wrap">
                <button onClick={() => edit(s)}>Edit</button>
                <button
                  onClick={() => action(s.id, s.paused ? "resume" : "pause")}
                >
                  {s.paused ? "Resume" : "Pause"}
                </button>
                <button onClick={() => action(s.id, "run-now")}>Run now</button>
                <button onClick={() => action(s.id, "duplicate")}>
                  Duplicate
                </button>
                <button
                  onClick={() => {
                    setSelected(s.id);
                    setRunsPage(1);
                  }}
                >
                  Run history
                </button>
                <button onClick={() => action(s.id, "delete")}>Delete</button>
              </div>
            </Card>
          ))}
          {!state.data?.items.length && (
            <Card>
              <Empty>
                {state.loading
                  ? "Loading schedules…"
                  : "Your recurring research starts here."}
              </Empty>
            </Card>
          )}
          <Pager
            page={page}
            setPage={setPage}
            hasMore={state.data?.items.length === 20}
          />
          {selected && (
            <Card>
              <h2>Execution history</h2>
              <ErrorBox error={runs.error} />
              {runs.data?.items.map((r) => (
                <div className="list-row" key={r.id}>
                  <span>{date(r.occurrence_at)}</span>
                  <Status value={r.status} />
                </div>
              ))}
              {!runs.data?.items.length && (
                <Empty>No scheduled executions yet.</Empty>
              )}
              <Pager
                page={runsPage}
                setPage={setRunsPage}
                hasMore={runs.data?.items.length === 20}
              />
            </Card>
          )}
        </div>
      </div>
    </>
  );
}
export function AnalyticsPage() {
  const [days, setDays] = useState(30),
    [page, setPage] = useState(1);
  const { data, error, loading } = useResource(
    `/analytics?days=${days}&page=${page}`,
    15000,
  );
  const t = data?.totals;
  const max = Math.max(1, ...(data?.daily || []).map((d) => Number(d.tokens)));
  return (
    <>
      <Heading
        title="Every token, accounted for"
        action={
          <select
            aria-label="Analytics period"
            value={days}
            onChange={(e) => {
              setDays(Number(e.target.value));
              setPage(1);
            }}
          >
            <option value={7}>Last 7 days</option>
            <option value={30}>Last 30 days</option>
            <option value={90}>Last 90 days</option>
          </select>
        }
      >
        Usage, performance, and confirmed costs across your workspace.
      </Heading>
      <ErrorBox error={error} />
      <div className="four-col">
        {[
          ["Tokens charged", number(t?.tokens)],
          ["Model requests", number(t?.requests)],
          ["Known model cost", "$" + Number(t?.known_cost || 0).toFixed(4)],
          ["Research & chat jobs", number(data?.jobs.total)],
        ].map(([label, value]) => (
          <Card key={label}>
            <span className="eyebrow">{label}</span>
            <strong className="metric">{value}</strong>
          </Card>
        ))}
      </div>
      <div className="two-col">
        <Card>
          <h2>Daily token history</h2>
          <div className="chart">
            {data?.daily.map((d) => (
              <div
                className="bar-col"
                key={d.day}
                title={`${d.day}: ${number(d.tokens)} tokens`}
              >
                <div
                  className="bar"
                  style={{
                    height: Math.max(2, (Number(d.tokens) / max) * 160) + "px",
                  }}
                />
                <small>{d.day.slice(5)}</small>
              </div>
            ))}
          </div>
          {!data?.daily.length && (
            <Empty>{loading ? "Loading usage…" : "No model usage yet."}</Empty>
          )}
        </Card>
        <Card>
          <h2>Usage by model and feature</h2>
          {data?.breakdown.map((b, i) => (
            <div className="list-row" key={i}>
              <span>
                {b.model}
                <small>
                  {b.feature} · {b.requests} calls
                </small>
              </span>
              <strong>{number(b.tokens)}</strong>
            </div>
          ))}
          <p className="hint">
            {number(t?.estimated_requests)} estimated requests ·{" "}
            {number(t?.unknown_cost_requests)} requests with unknown cost.
            External target tokens do not consume the platform allowance.
          </p>
          <p className="hint">
            Reserved tokens: {number(t?.reserved)} · Failed jobs:{" "}
            {number(data?.jobs.failed)} · Average model latency:{" "}
            {(Number(t?.latency_ms || 0) / 1000).toFixed(1)}s
          </p>
        </Card>
      </div>
      <Card>
        <h2>Request ledger</h2>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>When</th>
                <th>Feature / model</th>
                <th>Input / output</th>
                <th>Charged</th>
                <th>Cost</th>
                <th>Accounting</th>
              </tr>
            </thead>
            <tbody>
              {data?.events.map((e) => (
                <tr key={e.id}>
                  <td>{date(e.created_at)}</td>
                  <td>
                    {e.feature}
                    <small>{e.model}</small>
                  </td>
                  <td>
                    {e.input_tokens ?? "—"} / {e.output_tokens ?? "—"}
                  </td>
                  <td>{number(e.charged_tokens)}</td>
                  <td>
                    {e.cost_usd === null
                      ? "Unknown"
                      : "$" + Number(e.cost_usd).toFixed(5)}
                  </td>
                  <td>
                    <Status value={e.status} />
                    {e.external && <small>External target</small>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <Pager
          page={page}
          setPage={setPage}
          hasMore={data?.events.length === 20}
        />
      </Card>
    </>
  );
}
export function ModelsPage() {
  const { data, error } = useResource("/models");
  return (
    <>
      <Heading title="Good models. One simple workspace.">
        Platform-managed free models for your research and conversations.
      </Heading>
      <ErrorBox error={error} />
      <div className="three-col">
        {data?.items.map((m, i) => (
          <Card key={m.id}>
            <div className="round-icon">
              <Layers size={22} />
            </div>
            <h2>{modelNames[i] || m.id}</h2>
            <p className="model-id">{m.id}</p>
            <Status value={m.default ? "default" : "available"} />
            <p>Free inference · Platform API key</p>
            <small>Provider capacity and rate limits apply.</small>
          </Card>
        ))}
      </div>
      <Card>
        <h2>Managed for you</h2>
        <p>
          Choose a model when starting research or chat. Credentials stay on the
          backend. Models cannot be added, edited, or removed from your account.
        </p>
        <p>
          Custom endpoints are available only in{" "}
          <Link to="/redteam">red-team testing</Link>.
        </p>
      </Card>
    </>
  );
}
export function NotificationsPage() {
  const [page, setPage] = useState(1);
  const navigate = useNavigate();
  async function openJob(id) {
    try {
      const job = await api("/jobs/" + id);
      navigate(
        job.report_id
          ? "/research/" + job.report_id
          : job.conversation_id
            ? "/chat/" + job.conversation_id
            : "/redteam?job=" + job.id,
      );
    } catch (e) {
      notify(e.message, "error");
    }
  }
  const state = useResource("/notifications?page=" + page, 15000);
  const { notify } = useApp();
  return (
    <>
      <Heading title="Your workspace inbox">
        Research completions, scheduled reports, and job updates.
      </Heading>
      <Card>
        <ErrorBox error={state.error} />
        {state.data?.items.map((n) => (
          <div className={"notification " + (n.read ? "read" : "")} key={n.id}>
            <div>
              <h3>{n.title}</h3>
              <p>{n.message}</p>
              <small>{date(n.created_at)}</small>
            </div>
            {n.job_id && (
              <button onClick={() => openJob(n.job_id)}>View result</button>
            )}
            {!n.read && (
              <button
                onClick={() =>
                  api(`/notifications/${n.id}/read`, { method: "POST" })
                    .then(state.refresh)
                    .catch((e) => notify(e.message, "error"))
                }
              >
                Mark read
              </button>
            )}
          </div>
        ))}
        {!state.data?.items.length && <Empty>No notifications yet.</Empty>}
        <Pager
          page={page}
          setPage={setPage}
          hasMore={state.data?.items.length === 20}
        />
      </Card>
    </>
  );
}

export function RedTeamPage() {
  const [searchParams] = useSearchParams();
  const [target, setTarget] = useState("platform"),
    [model, setModel] = useState(models[0]),
    [customModel, setCustomModel] = useState(""),
    [url, setUrl] = useState(""),
    [key, setKey] = useState(""),
    [authorized, setAuthorized] = useState(false),
    [suites, setSuites] = useState(["xpia"]),
    [maxTests, setMaxTests] = useState(4),
    [selected, setSelected] = useState(searchParams.get("job")),
    [busy, setBusy] = useState(false),
    [page, setPage] = useState(1);
  const { notify } = useApp();
  const history = useResource("/jobs?kind=redteam&page=" + page, 10000),
    run = useResource(selected ? "/jobs/" + selected : null, 3000);
  const results = (run.data?.result || run.data?.checkpoint)?.results || [];
  async function submit(e) {
    e.preventDefault();
    setBusy(true);
    try {
      const r = await api("/redteam", {
        method: "POST",
        body: {
          target,
          model: target === "platform" ? model : customModel,
          suites,
          max_tests: maxTests,
          ...(target === "custom"
            ? { base_url: url, api_key: key, authorized }
            : {}),
        },
      });
      setSelected(r.id);
      setKey("");
      history.refresh();
    } catch (err) {
      notify(err.message, "error");
    } finally {
      setBusy(false);
    }
  }
  return (
    <>
      <Heading eyebrow="MODEL RESILIENCE" title="Test the boundaries">
        Run controlled adversarial tests and inspect the actual responses.
      </Heading>
      <div className="two-col">
        <Card>
          <h2>Configure a test run</h2>
          <form onSubmit={submit}>
            <Field label="Target">
              <select
                value={target}
                onChange={(e) => setTarget(e.target.value)}
              >
                <option value="platform">
                  Platform model + safety instructions
                </option>
                <option value="custom">My OpenAI-compatible API</option>
              </select>
            </Field>
            {target === "platform" ? (
              <ModelSelect value={model} onChange={setModel} />
            ) : (
              <>
                <Field
                  label="HTTPS base URL (including /v1 if required)"
                  type="url"
                  required
                  value={url}
                  onChange={(e) => setUrl(e.target.value)}
                  placeholder="https://api.example.com/v1"
                />
                <Field
                  label="Target model"
                  required
                  value={customModel}
                  onChange={(e) => setCustomModel(e.target.value)}
                />
                <Field
                  label="API key · encrypted, expires in 1 hour"
                  type="password"
                  autoComplete="off"
                  required
                  value={key}
                  onChange={(e) => setKey(e.target.value)}
                />
                <label className="check">
                  <input
                    type="checkbox"
                    required
                    checked={authorized}
                    onChange={(e) => setAuthorized(e.target.checked)}
                  />
                  I own this endpoint or have permission to test it.
                </label>
              </>
            )}
            <fieldset>
              <legend>Test categories</legend>
              {[
                ["jailbreak", "Direct jailbreak"],
                ["xpia", "Cross-prompt injection"],
                ["crescendo", "Multi-turn escalation"],
                ["authority", "Authority manipulation"],
              ].map(([value, label]) => (
                <label className="check" key={value}>
                  <input
                    type="checkbox"
                    checked={suites.includes(value)}
                    onChange={(e) =>
                      setSuites(
                        e.target.checked
                          ? [...suites, value]
                          : suites.filter((x) => x !== value),
                      )
                    }
                  />
                  {label}
                </label>
              ))}
            </fieldset>
            <Field
              label="Maximum tests (1–12)"
              type="number"
              min={1}
              max={12}
              required
              value={maxTests}
              onChange={(e) => setMaxTests(Number(e.target.value))}
            />
            <button className="primary" disabled={busy || !suites.length}>
              {busy ? "Starting…" : "Run selected tests"}
            </button>
          </form>
        </Card>
        <Card>
          <h2>Previous runs</h2>
          <ErrorBox error={history.error} />
          {history.data?.items.map((j) => (
            <button
              className={"run-row " + (selected === j.id ? "selected" : "")}
              key={j.id}
              onClick={() => setSelected(j.id)}
            >
              <span>{date(j.created_at)}</span>
              <Status value={j.status} />
            </button>
          ))}
          {!history.data?.items.length && <Empty>No test runs yet.</Empty>}
          <Pager
            page={page}
            setPage={setPage}
            hasMore={history.data?.items.length === 20}
          />
          <p className="hint">
            Results are heuristic screening. A refusal is not proof of security,
            and a network error is not a successful block.
          </p>
        </Card>
      </div>
      {selected && (
        <>
          <JobProgress job={selected} />
          <Card>
            <div className="row">
              <h2>Test results</h2>
              <Exports kind="redteam" id={selected} />
            </div>
            <ErrorBox error={run.error} />
            {results.map((r, i) => (
              <details className="test-result" key={i}>
                <summary>
                  <strong>{r.suite}</strong>
                  <Status value={r.outcome} />
                  <small>{r.duration_s}s</small>
                </summary>
                <p>{r.reason}</p>
                <h4>Prompt</h4>
                <pre>{r.prompt}</pre>
                <h4>Response</h4>
                <pre>{r.response || "No response"}</pre>
              </details>
            ))}
            {!results.length && <Empty>Results appear as tests finish.</Empty>}
          </Card>
        </>
      )}
    </>
  );
}
export function AdminPage() {
  const { profile } = useApp();
  if (profile?.role !== "admin")
    return <ErrorBox error="Administrator access required." />;
  return <AdminContent />;
}
function AdminContent() {
  const { notify } = useApp();
  const [q, setQ] = useState(""),
    [page, setPage] = useState(1),
    [auditPage, setAuditPage] = useState(1),
    [detail, setDetail] = useState(null);
  const overview = useResource("/admin/overview", 15000),
    users = useResource("/admin/users?" + new URLSearchParams({ q, page })),
    audit = useResource("/admin/audit?page=" + auditPage),
    jobs = useResource("/admin/jobs", 10000),
    health = useResource("/admin/health");
  async function changeUser(u, body) {
    try {
      await api("/admin/users/" + u.id, { method: "PATCH", body });
      users.refresh();
      overview.refresh();
      audit.refresh();
      notify("User settings updated", "success");
    } catch (e) {
      notify(e.message, "error");
    }
  }
  async function limit(u) {
    const value = prompt(
      "Daily token limit. Leave blank to use the global default.",
      u.daily_limit ?? "",
    );
    if (value === null) return;
    if (value !== "" && (!/^\d+$/.test(value) || Number(value) > 10000000)) {
      notify("Enter a whole number from 0 to 10,000,000", "error");
      return;
    }
    changeUser(u, { daily_limit: value === "" ? null : Number(value) });
  }
  async function globalSettings() {
    const value = prompt(
      "Default daily token limit",
      overview.data?.settings.default_daily_limit,
    );
    if (value === null) return;
    try {
      await api("/admin/settings", {
        method: "PUT",
        body: {
          default_daily_limit: Number(value),
          search_monthly_limit: overview.data.settings.search_monthly_limit,
        },
      });
      overview.refresh();
      audit.refresh();
    } catch (e) {
      notify(e.message, "error");
    }
  }
  return (
    <>
      <Heading eyebrow="ADMINISTRATION" title="A clear view of your platform">
        Manage access, allowances, and operational health.
      </Heading>
      <ErrorBox error={overview.error} />
      <div className="four-col">
        {[
          ["Users", overview.data?.users.total],
          ["Suspended", overview.data?.users.suspended],
          ["Tokens charged", overview.data?.usage.tokens],
          ["Uncertain calls", overview.data?.usage.estimated],
        ].map(([label, val]) => (
          <Card key={label}>
            <span className="eyebrow">{label}</span>
            <strong className="metric">{number(val)}</strong>
          </Card>
        ))}
      </div>
      <Card>
        <div className="row">
          <h2>Global controls</h2>
          <button disabled={!overview.data} onClick={globalSettings}>
            Change default allowance
          </button>
        </div>
        <p>
          Default: {number(overview.data?.settings.default_daily_limit)}{" "}
          tokens/day · Monthly search cap:{" "}
          {number(overview.data?.settings.search_monthly_limit)} credits · Known
          model cost: ${Number(overview.data?.usage.known_cost || 0).toFixed(4)}
        </p>
        <ErrorBox error={health.error} />
        <pre className="json">{JSON.stringify(health.data, null, 2)}</pre>
        <button onClick={health.refresh}>Refresh service health</button>
      </Card>
      <Card>
        <div className="row">
          <h2>User management</h2>
          <input
            aria-label="Search users"
            placeholder="Search name or email…"
            value={q}
            onChange={(e) => {
              setQ(e.target.value);
              setPage(1);
            }}
          />
        </div>
        <ErrorBox error={users.error} />
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>User</th>
                <th>Today / reserved</th>
                <th>Limit</th>
                <th>Features</th>
                <th>Controls</th>
              </tr>
            </thead>
            <tbody>
              {users.data?.items.map((u) => (
                <tr key={u.id}>
                  <td>
                    {u.display_name || "Researcher"}
                    <small>
                      {u.email} · {u.role}
                    </small>
                    <Status value={u.suspended ? "suspended" : "active"} />
                  </td>
                  <td>
                    {number(u.today_tokens)} / {number(u.reserved)}
                  </td>
                  <td>
                    <button onClick={() => limit(u)}>
                      {u.daily_limit === null
                        ? "Default"
                        : number(u.daily_limit)}
                    </button>
                  </td>
                  <td>
                    {Object.entries(u.features).map(([k, v]) => (
                      <label className="check" key={k}>
                        <input
                          type="checkbox"
                          checked={v}
                          onChange={(e) =>
                            changeUser(u, {
                              features: { [k]: e.target.checked },
                            })
                          }
                        />
                        {k}
                      </label>
                    ))}
                  </td>
                  <td>
                    <div className="actions wrap">
                      <button
                        onClick={() =>
                          changeUser(u, { suspended: !u.suspended })
                        }
                      >
                        {u.suspended ? "Restore access" : "Suspend"}
                      </button>
                      <button
                        onClick={() =>
                          api(`/admin/users/${u.id}/pause-schedules`, {
                            method: "POST",
                          })
                            .then(() => {
                              notify("Schedules paused");
                              audit.refresh();
                            })
                            .catch((e) => notify(e.message, "error"))
                        }
                      >
                        Pause schedules
                      </button>
                      <button
                        onClick={() =>
                          api(`/admin/users/${u.id}/analytics`)
                            .then((r) => setDetail({ email: u.email, ...r }))
                            .catch((e) => notify(e.message, "error"))
                        }
                      >
                        Usage details
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <Pager
          page={page}
          setPage={setPage}
          hasMore={users.data?.items.length === 20}
        />
      </Card>
      {detail && (
        <Card>
          <div className="row">
            <h2>Usage: {detail.email}</h2>
            <button onClick={() => setDetail(null)}>Close</button>
          </div>
          <pre className="json">{JSON.stringify(detail, null, 2)}</pre>
        </Card>
      )}
      <Card>
        <h2>Recent jobs</h2>
        <ErrorBox error={jobs.error} />
        {jobs.data?.items.map((j) => (
          <div className="list-row" key={j.id}>
            <span>
              {j.kind} · {date(j.created_at)}
              <small>
                {j.user_id} · {j.error || j.stage}
              </small>
            </span>
            <Status value={j.status} />
            {["queued", "running"].includes(j.status) && (
              <button
                onClick={() =>
                  api(`/admin/jobs/${j.id}/cancel`, { method: "POST" })
                    .then(() => {
                      jobs.refresh();
                      audit.refresh();
                    })
                    .catch((e) => notify(e.message, "error"))
                }
              >
                Cancel
              </button>
            )}
          </div>
        ))}
      </Card>
      <Card>
        <h2>Administrative audit trail</h2>
        <ErrorBox error={audit.error} />
        {audit.data?.items.map((a) => (
          <details key={a.id} className="audit">
            <summary>
              {date(a.created_at)} · {a.action} ·{" "}
              {a.target_id || "Global settings"}
            </summary>
            <pre className="json">{JSON.stringify(a, null, 2)}</pre>
          </details>
        ))}
        <Pager
          page={auditPage}
          setPage={setAuditPage}
          hasMore={audit.data?.items.length === 30}
        />
      </Card>
    </>
  );
}
