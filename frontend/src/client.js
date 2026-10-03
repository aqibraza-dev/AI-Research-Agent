import { createClient } from "@supabase/supabase-js";
const url = import.meta.env.VITE_SUPABASE_URL,
  key = import.meta.env.VITE_SUPABASE_PUBLISHABLE_KEY;
export const configured = Boolean(url && key);
export const supabase = configured ? createClient(url, key) : null;
const base = (
  import.meta.env.VITE_API_URL || "http://localhost:8000/api/v1"
).replace(/\/$/, "");
export async function api(path, options = {}) {
  const session = await supabase.auth.getSession();
  const token = session.data.session?.access_token;
  if (!token) throw new Error("Your session has expired. Sign in again.");
  const res = await fetch(base + path, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${token}`,
      ...options.headers,
    },
    body: options.body === undefined ? undefined : JSON.stringify(options.body),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.error?.message || `Request failed (${res.status})`);
  }
  return options.blob ? res.blob() : res.json();
}
export async function download(kind, id, format) {
  const blob = await api(`/exports/${kind}/${id}?format=${format}`, {
    blob: true,
  });
  const url = URL.createObjectURL(blob),
    a = document.createElement("a");
  a.href = url;
  a.download = `${kind}-${id}.${format === "pdf" ? "pdf" : "md"}`;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
export const models = [
  "openrouter/free",
  "qwen/qwen3.8-27b:free",
  "nvidia/nemotron-3.5-lightning:free",
];
export const modelNames = [
  "Free model router",
  "Qwen3.8 27B",
  "Nemotron 3.5 Lightning",
];
