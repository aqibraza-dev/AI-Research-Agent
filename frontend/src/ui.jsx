import { useCallback, useEffect, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { api, download, models, modelNames } from "./client";
import { useApp } from "./context";
export function useResource(path, interval = 0) {
  const [data, setData] = useState(null),
    [error, setError] = useState(""),
    [loading, setLoading] = useState(true);
  const refresh = useCallback(async () => {
    if (!path) return;
    try {
      setData(await api(path));
      setError("");
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, [path]);
  useEffect(() => {
    let active = true;
    let timer = null;
    setData(null);
    setLoading(true);
    setError("");
    async function load() {
      if (!path) {
        setLoading(false);
        return;
      }
      try {
        const r = await api(path);
        if (active) {
          setData(r);
          setError("");
          if (["completed", "failed", "cancelled"].includes(r.status))
            clearInterval(timer);
        }
      } catch (e) {
        if (active) setError(e.message);
      } finally {
        if (active) setLoading(false);
      }
    }
    load();
    timer = interval
      ? setInterval(() => {
          if (!document.hidden) load();
        }, interval)
      : null;
    return () => {
      active = false;
      clearInterval(timer);
    };
  }, [path, interval]);
  return { data, error, loading, refresh };
}
export function Heading({ eyebrow = "WORKSPACE", title, children, action }) {
  return (
    <div className="page-head">
      <div>
        <span className="eyebrow">{eyebrow}</span>
        <h1>{title}</h1>
        <p>{children}</p>
      </div>
      {action}
    </div>
  );
}
export function Card({ children, className = "" }) {
  return <section className={`card ${className}`}>{children}</section>;
}
export function Empty({ children = "Nothing here yet." }) {
  return <div className="empty">{children}</div>;
}
export function ErrorBox({ error }) {
  return error ? (
    <div className="error" role="alert">
      {error}
    </div>
  ) : null;
}
export function Status({ value }) {
  return (
    <span className={`badge ${value}`}>{value?.replaceAll("_", " ")}</span>
  );
}
export function Field({ label, children, ...props }) {
  return (
    <label className="field">
      <span>{label}</span>
      {children || <input {...props} />}
    </label>
  );
}
export function ModelSelect({ value, onChange }) {
  return (
    <Field label="Model">
      <select value={value} onChange={(e) => onChange(e.target.value)}>
        {models.map((m, i) => (
          <option key={m} value={m}>
            {modelNames[i]}
          </option>
        ))}
      </select>
    </Field>
  );
}
export function Markdown({ children }) {
  return (
    <div className="markdown">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          a: (props) => (
            <a {...props} target="_blank" rel="noopener noreferrer" />
          ),
        }}
      >
        {children || ""}
      </ReactMarkdown>
    </div>
  );
}
export function Exports({ kind, id }) {
  const { notify } = useApp();
  return (
    <div className="actions">
      {["markdown", "pdf"].map((format) => (
        <button
          key={format}
          className="small"
          onClick={() =>
            download(kind, id, format).catch((e) => notify(e.message, "error"))
          }
        >
          {format === "pdf" ? "↓ PDF" : "↓ Markdown"}
        </button>
      ))}
    </div>
  );
}
export function Pager({ page, setPage, hasMore = true }) {
  return (
    <div className="pager">
      <button disabled={page === 1} onClick={() => setPage(page - 1)}>
        Previous
      </button>
      <span>Page {page}</span>
      <button disabled={!hasMore} onClick={() => setPage(page + 1)}>
        Next
      </button>
    </div>
  );
}
export function JobProgress({ job, onChange }) {
  const state = useResource(job ? `/jobs/${job}` : null, 3000);
  const { notify } = useApp();
  const row = state.data;
  useEffect(() => {
    if (row && ["completed", "failed", "cancelled"].includes(row.status))
      onChange?.(row);
  }, [row, onChange]);
  if (!job) return null;
  return (
    <div className="progress">
      <ErrorBox error={state.error} />
      <div className="row">
        <Status value={row?.status || "connecting"} />
        <strong>{row?.stage || "Connecting to backend…"}</strong>
        {row && ["queued", "running"].includes(row.status) && (
          <button
            className="small"
            onClick={() =>
              api(`/jobs/${job}/cancel`, { method: "POST" })
                .then(state.refresh)
                .catch((e) => notify(e.message, "error"))
            }
          >
            Cancel
          </button>
        )}
      </div>
      <p>
        {row?.error || "Progress is saved. You can return to this job later."}
      </p>
    </div>
  );
}
export const date = (value) => (value ? new Date(value).toLocaleString() : "—");
export const number = (value) => Number(value || 0).toLocaleString();
