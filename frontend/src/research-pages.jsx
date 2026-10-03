import { useState, useCallback, useEffect } from "react";
import {
  Link,
  useNavigate,
  useParams,
  useSearchParams,
} from "react-router-dom";
import {
  Search,
  ArrowUpRight,
  Sparkles,
  BookOpen,
  Globe,
  ArrowRight,
} from "lucide-react";
import { api, models } from "./client";
import { useApp } from "./context";
import {
  Heading,
  Card,
  Field,
  ModelSelect,
  Markdown,
  Exports,
  ErrorBox,
  Status,
  Pager,
  Empty,
  JobProgress,
  useResource,
  date,
} from "./ui";
export function ResearchPage() {
  const [topic, setTopic] = useState(""),
    [depth, setDepth] = useState("standard"),
    [model, setModel] = useState(models[0]),
    [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const navigate = useNavigate();
  async function submit(e) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      const r = await api("/research", {
        method: "POST",
        body: { topic, depth, model },
      });
      navigate("/research/" + r.report.id);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <>
      <Heading
        eyebrow="FROM QUESTION TO CLARITY"
        title="What will you discover today?"
      >
        Explore a question. Gather live evidence. Build a report worth keeping.
      </Heading>
      <Card className="research-compose">
        <div className="row">
          <div className="round-icon">
            <Sparkles size={21} />
          </div>
          <div>
            <h2>Start a new research</h2>
            <p>Grounded in live sources, saved to your workspace.</p>
          </div>
          <span className="badge free">FREE MODELS</span>
        </div>
        <form onSubmit={submit}>
          <Field label="Your research question">
            <textarea
              required
              minLength={3}
              maxLength={2000}
              rows={4}
              value={topic}
              onChange={(e) => setTopic(e.target.value)}
              placeholder="What are the latest advances in long-duration energy storage?"
            />
          </Field>
          <div className="two-col">
            <ModelSelect value={model} onChange={setModel} />
            <Field label="Research depth">
              <select value={depth} onChange={(e) => setDepth(e.target.value)}>
                <option value="standard">
                  Standard · Draft + evidence review
                </option>
                <option value="deep">
                  Deep · Additional search + revision
                </option>
              </select>
            </Field>
          </div>
          <ErrorBox error={error} />
          <div className="compose-bottom">
            <p>Live web search · Numbered citations · Private history</p>
            <button className="primary" disabled={busy}>
              {busy ? "Starting…" : "Start research"} <ArrowRight size={16} />
            </button>
          </div>
        </form>
      </Card>
      <div className="three-col feature-cards">
        {[
          [
            Globe,
            "Follow the evidence",
            "Live search brings sources into every report.",
          ],
          [
            BookOpen,
            "Build your library",
            "Keep your findings, compare reports, and export.",
          ],
          [
            Sparkles,
            "Keep asking",
            "Continue the conversation with your research in context.",
          ],
        ].map(([Icon, title, description]) => (
          <Card key={title}>
            <Icon size={21} />
            <h3>{title}</h3>
            <p>{description}</p>
          </Card>
        ))}
      </div>
      <div className="section-head">
        <h2>Pick up where you left off</h2>
        <Link to="/history">
          View all history <ArrowUpRight size={15} />
        </Link>
      </div>
      <Recent />
    </>
  );
}
function Recent() {
  const { data, error, loading } = useResource("/research");
  return (
    <Card>
      <ErrorBox error={error} />
      {loading ? (
        <Empty>Loading recent research…</Empty>
      ) : data?.items.length ? (
        <div className="list">
          {data.items.slice(0, 4).map((r) => (
            <Link key={r.id} className="list-row" to={"/research/" + r.id}>
              <span>
                <strong>{r.title}</strong>
                <small>{date(r.created_at)}</small>
              </span>
              <Status value={r.status} />
            </Link>
          ))}
        </div>
      ) : (
        <Empty>Your first discovery starts above.</Empty>
      )}
    </Card>
  );
}
export function HistoryPage() {
  const [q, setQ] = useState(""),
    [status, setStatus] = useState(""),
    [after, setAfter] = useState(""),
    [before, setBefore] = useState(""),
    [page, setPage] = useState(1);
  const { notify } = useApp();
  const navigate = useNavigate();
  const params = new URLSearchParams({ q, status, page });
  if (after) params.set("after", new Date(after).toISOString());
  if (before)
    params.set("before", new Date(before + "T23:59:59").toISOString());
  const state = useResource("/research?" + params, 10000);
  async function rename(r) {
    const title = prompt("Report title", r.title);
    if (!title) return;
    try {
      await api("/research/" + r.id, { method: "PATCH", body: { title } });
      state.refresh();
    } catch (e) {
      notify(e.message, "error");
    }
  }
  async function remove(r) {
    if (!confirm("Delete this report? Usage history is retained.")) return;
    try {
      await api("/research/" + r.id, { method: "DELETE" });
      state.refresh();
    } catch (e) {
      notify(e.message, "error");
    }
  }
  return (
    <>
      <Heading
        title="Research history"
        action={
          <Link className="button primary" to="/research">
            + New research
          </Link>
        }
      >
        Your questions, sources, and discoveries in one place.
      </Heading>
      <Card>
        <div className="filters">
          <Field
            label="Search reports"
            placeholder="Search titles…"
            value={q}
            onChange={(e) => {
              setQ(e.target.value);
              setPage(1);
            }}
          />
          <Field label="Status">
            <select
              value={status}
              onChange={(e) => {
                setStatus(e.target.value);
                setPage(1);
              }}
            >
              <option value="">All statuses</option>
              {["queued", "running", "completed", "failed", "cancelled"].map(
                (s) => (
                  <option key={s}>{s}</option>
                ),
              )}
            </select>
          </Field>
          <Field
            label="From"
            type="date"
            value={after}
            onChange={(e) => {
              setAfter(e.target.value);
              setPage(1);
            }}
          />
          <Field
            label="Through"
            type="date"
            value={before}
            onChange={(e) => {
              setBefore(e.target.value);
              setPage(1);
            }}
          />
        </div>
        <ErrorBox error={state.error} />
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Research</th>
                <th>Created</th>
                <th>Status</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {state.data?.items.map((r) => (
                <tr key={r.id}>
                  <td>
                    <Link to={"/research/" + r.id}>{r.title}</Link>
                    <small>{r.depth}</small>
                  </td>
                  <td>{date(r.created_at)}</td>
                  <td>
                    <Status value={r.status} />
                  </td>
                  <td>
                    <div className="actions">
                      <button onClick={() => rename(r)}>Rename</button>
                      <button onClick={() => remove(r)}>Delete</button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {!state.data?.items.length && (
          <Empty>
            {state.loading
              ? "Loading reports…"
              : "No reports match your filters."}
          </Empty>
        )}
        <Pager
          page={page}
          setPage={setPage}
          hasMore={page * 20 < (state.data?.total || 0)}
        />
      </Card>
    </>
  );
}
export function ReportPage() {
  const { id } = useParams();
  const state = useResource("/research/" + id, 5000);
  const r = state.data;
  const { notify, refreshProfile } = useApp();
  const navigate = useNavigate();
  const history = useResource("/research?status=completed");
  const [other, setOther] = useState(""),
    [diff, setDiff] = useState("");
  const refreshReport = state.refresh;
  const onDone = useCallback(() => {
    refreshReport();
    refreshProfile();
  }, [refreshReport, refreshProfile]);
  async function rerun() {
    try {
      const result = await api(`/research/${id}/rerun`, { method: "POST" });
      navigate("/research/" + result.report.id);
    } catch (e) {
      notify(e.message, "error");
    }
  }
  async function compare() {
    try {
      setDiff((await api(`/research/${id}/diff?other=${other}`)).diff);
    } catch (e) {
      notify(e.message, "error");
    }
  }
  return (
    <>
      <Heading
        title={r?.title || "Research report"}
        action={<Link to="/history">← History</Link>}
      >
        {r ? `${r.depth} research · ${date(r.created_at)}` : "Loading report…"}
      </Heading>
      <ErrorBox error={state.error} />
      {r?.job && r.status !== "completed" && (
        <JobProgress job={r.job.id} onChange={onDone} />
      )}
      <div className="report-toolbar">
        <Status value={r?.status} />
        <button onClick={rerun}>Run again</button>
        {r?.status === "completed" && (
          <>
            <Link className="button" to={"/chat?report=" + id}>
              Chat about this report
            </Link>
            <Exports kind="research" id={id} />
          </>
        )}
      </div>
      {r?.content && (
        <Card>
          <Markdown>{r.content}</Markdown>
        </Card>
      )}
      {r?.sources?.length > 0 && (
        <Card className="source-card">
          <h2>Source provenance</h2>
          {r.sources.map((s) => (
            <div className="source" key={s.id}>
              <a href={s.url} target="_blank" rel="noopener noreferrer">
                [{s.id}] {s.title}
              </a>
              <small>Retrieved {date(s.retrieved_at)}</small>
              <p>{s.excerpt}</p>
            </div>
          ))}
        </Card>
      )}
      {r?.status === "completed" && (
        <Card>
          <h2>Compare reports</h2>
          <div className="row">
            <select
              aria-label="Earlier report"
              value={other}
              onChange={(e) => setOther(e.target.value)}
            >
              <option value="">Choose another report…</option>
              {history.data?.items
                .filter((x) => x.id !== id)
                .map((x) => (
                  <option value={x.id} key={x.id}>
                    {x.title} · {date(x.created_at)}
                  </option>
                ))}
            </select>
            <button disabled={!other} onClick={compare}>
              Compare
            </button>
          </div>
          {diff && <pre className="diff">{diff}</pre>}
        </Card>
      )}
    </>
  );
}
export function ChatPage() {
  const { id } = useParams();
  const [search] = useSearchParams();
  const reportId = search.get("report");
  const navigate = useNavigate();
  const { notify, refreshProfile } = useApp();
  const [text, setText] = useState(""),
    [model, setModel] = useState(models[0]),
    [busy, setBusy] = useState(false),
    [page, setPage] = useState(1),
    [listPage, setListPage] = useState(1);
  const list = useResource("/conversations?page=" + listPage),
    conv = useResource(id ? `/conversations/${id}?page=${page}` : null, 4000);
  const data = conv.data;
  useEffect(() => {
    setPage(1);
  }, [id]);
  async function create() {
    try {
      const r = await api("/conversations", {
        method: "POST",
        body: {
          title: reportId ? "Research discussion" : "New chat",
          report_id: reportId || null,
        },
      });
      list.refresh();
      navigate("/chat/" + r.id);
      return r.id;
    } catch (e) {
      notify(e.message, "error");
    }
  }
  async function send(e) {
    e.preventDefault();
    setBusy(true);
    try {
      let cid = id;
      if (!cid) {
        const r = await api("/conversations", {
          method: "POST",
          body: { title: text.slice(0, 60), report_id: reportId || null },
        });
        cid = r.id;
        navigate("/chat/" + cid);
      }
      await api(`/conversations/${cid}/messages`, {
        method: "POST",
        body: { content: text, model },
      });
      setText("");
      conv.refresh();
      list.refresh();
      refreshProfile();
    } catch (err) {
      notify(err.message, "error");
    } finally {
      setBusy(false);
    }
  }
  async function rename() {
    const title = prompt("Conversation title", data.title);
    if (title)
      try {
        await api("/conversations/" + id, { method: "PATCH", body: { title } });
        list.refresh();
        conv.refresh();
      } catch (e) {
        notify(e.message, "error");
      }
  }
  async function remove() {
    if (!confirm("Delete this conversation?")) return;
    try {
      await api("/conversations/" + id, { method: "DELETE" });
      list.refresh();
      navigate("/chat");
    } catch (e) {
      notify(e.message, "error");
    }
  }
  const active = data?.job && ["queued", "running"].includes(data.job.status);
  return (
    <>
      <Heading title="A little more clarity" eyebrow="YOUR RESEARCH COMPANION">
        Ask follow-up questions, explore ideas, and keep the conversation.
      </Heading>
      <div className="chat-layout">
        <Card className="conversation-list">
          <button className="primary" onClick={create}>
            + New conversation
          </button>
          <ErrorBox error={list.error} />
          {list.data?.items.map((c) => (
            <Link
              className={id === c.id ? "selected" : ""}
              to={"/chat/" + c.id}
              key={c.id}
            >
              {c.title}
              <small>{date(c.created_at)}</small>
            </Link>
          ))}
          <Pager
            page={listPage}
            setPage={setListPage}
            hasMore={list.data?.items.length === 20}
          />
        </Card>
        <Card className="chat-panel">
          <div className="row">
            <h2>{data?.title || "Start a conversation"}</h2>
            {id && (
              <div className="actions">
                <button onClick={rename}>Rename</button>
                <button onClick={remove}>Delete</button>
                <Exports kind="conversation" id={id} />
              </div>
            )}
          </div>
          {(data?.report_id || reportId) && (
            <p className="context-note">
              Research context attached ·{" "}
              <Link to={"/research/" + (data?.report_id || reportId)}>
                View report
              </Link>
            </p>
          )}
          <ErrorBox error={conv.error} />
          <div className="messages">
            {data?.messages.map((m) => (
              <div className={"message " + m.role} key={m.id}>
                <span className="eyebrow">
                  {m.role === "user" ? "YOU" : "RESEARCH ASSISTANT"}
                </span>
                <Markdown>{m.content}</Markdown>
                {m.role === "assistant" && <Exports kind="message" id={m.id} />}
              </div>
            ))}
            {!data?.messages.length && (
              <Empty>
                {reportId
                  ? "Ask a question about the attached research."
                  : "What would you like to understand better?"}
              </Empty>
            )}
          </div>
          {data?.messages.length >= 50 || page > 1 ? (
            <Pager
              page={page}
              setPage={setPage}
              hasMore={data?.messages.length === 50}
            />
          ) : null}
          {active && <JobProgress job={data.job.id} />}
          <ErrorBox error={data?.job?.error} />
          {data?.job && ["failed", "cancelled"].includes(data.job.status) && (
            <button
              onClick={() =>
                api(`/conversations/${id}/retry`, { method: "POST" })
                  .then(conv.refresh)
                  .catch((e) => notify(e.message, "error"))
              }
            >
              Retry last reply
            </button>
          )}
          <form className="chat-compose" onSubmit={send}>
            <textarea
              aria-label="Message"
              required
              maxLength={6000}
              rows={3}
              value={text}
              onChange={(e) => setText(e.target.value)}
              placeholder="Ask a thoughtful question…"
            />
            <div className="row">
              <ModelSelect value={model} onChange={setModel} />
              <button className="primary" disabled={busy || active}>
                {busy ? "Sending…" : "Send message ↑"}
              </button>
            </div>
          </form>
        </Card>
      </div>
    </>
  );
}
