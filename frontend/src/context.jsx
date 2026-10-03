import {
  createContext,
  useContext,
  useState,
  useEffect,
  useCallback,
} from "react";
import { supabase, api } from "./client";
const Context = createContext(null);
export const useApp = () => useContext(Context);
export function Provider({ children }) {
  const [session, setSession] = useState(null),
    [loading, setLoading] = useState(true),
    [profile, setProfile] = useState(null),
    [notes, setNotes] = useState([]),
    [profileError, setProfileError] = useState("");
  const notify = useCallback((message, type = "info") => {
    const id = crypto.randomUUID();
    setNotes((n) => [...n, { id, message, type }]);
    setTimeout(() => setNotes((n) => n.filter((x) => x.id !== id)), 6000);
  }, []);
  const refreshProfile = useCallback(async () => {
    try {
      setProfile(await api("/profile"));
      setProfileError("");
    } catch (e) {
      setProfileError(e.message);
    }
  }, []);
  useEffect(() => {
    let alive = true;
    supabase.auth
      .getSession()
      .then(({ data }) => {
        if (alive) {
          setSession(data.session);
          setLoading(false);
        }
      })
      .catch(() => setLoading(false));
    const { data } = supabase.auth.onAuthStateChange((event, s) => {
      if (event === "SIGNED_OUT") {
        setLoading(true);
        window.location.replace("/login");
        return;
      }
      setSession(s);
      setLoading(false);
      if (event === "PASSWORD_RECOVERY")
        window.location.assign("/account?recovery=1");
    });
    return () => {
      alive = false;
      data.subscription.unsubscribe();
    };
  }, []);
  useEffect(() => {
    if (session) refreshProfile();
    else {
      setProfile(null);
      setProfileError("");
    }
  }, [session, refreshProfile]);
  return (
    <Context.Provider
      value={{
        session,
        loading,
        profile,
        profileError,
        refreshProfile,
        notify,
      }}
    >
      {children}
      <div className="toasts" role="status">
        {notes.map((n) => (
          <div key={n.id} className={`toast ${n.type}`}>
            {n.message}
            <button
              aria-label="Dismiss notification"
              onClick={() => setNotes((x) => x.filter((y) => y.id !== n.id))}
            >
              ×
            </button>
          </div>
        ))}
      </div>
    </Context.Provider>
  );
}
