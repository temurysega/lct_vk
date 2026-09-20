import { lazy, Suspense, useEffect, useState } from "react";
import { MotionConfig } from "motion/react";
import { api, ApiError, saveDraft } from "./api";
import type { Draft, User } from "./types";
import { AuthDialog } from "./components/AuthDialog";
import { Landing } from "./pages/Landing";

const Studio = lazy(() => import("./pages/Studio"));

export function App() {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [mode, setMode] = useState<"login" | "register" | null>(null);
  const [path, setPath] = useState(location.pathname);
  const studio = path === "/studio";
  const navigate = (next: string) => {
    history.pushState(null, "", next);
    setPath(next);
    window.scrollTo(0, 0);
  };
  useEffect(() => {
    const controller = new AbortController();
    api<User>("/api/auth/me", { signal: controller.signal })
      .then(setUser)
      .catch((error) => {
        if (error instanceof ApiError && error.status !== 401)
          setError(error.message);
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    const pop = () => setPath(location.pathname);
    const expired = () => {
      setUser(null);
      setMode("login");
    };
    window.addEventListener("popstate", pop);
    window.addEventListener("predel:unauthorized", expired);
    return () => {
      controller.abort();
      window.removeEventListener("popstate", pop);
      window.removeEventListener("predel:unauthorized", expired);
    };
  }, []);
  useEffect(() => {
    document.title = studio
      ? "Студия — predel"
      : "predel — слайды с вашим характером";
    if (
      !loading &&
      !user &&
      (studio ||
        new URLSearchParams(location.search).get("auth") === "login") &&
      !error
    )
      setMode("login");
  }, [studio, loading, user, error]);
  function start(nextMode: "login" | "register" = "register", draft?: Draft) {
    if (draft) saveDraft(draft);
    if (user) navigate("/studio");
    else setMode(nextMode);
  }
  async function logout() {
    await api<void>("/api/auth/logout", { method: "POST" });
    saveDraft({ content: "", count: 10 });
    setUser(null);
    navigate("/");
  }
  return (
    <MotionConfig reducedMotion="user">
      {studio ? (
        loading ? (
          <div className="page-loading" id="studioLock" role="status">
            Открываем студию…
          </div>
        ) : user ? (
          <Suspense
            fallback={
              <div className="page-loading" id="studioLock">
                Загружаем студию…
              </div>
            }
          >
            <Studio user={user} onLogout={logout} />
          </Suspense>
        ) : (
          <div className="page-loading" id="studioLock">
            <h1>{error ? "Не удалось открыть студию" : "Войдите в predel"}</h1>
            <p>{error || "Ваши материалы доступны только вам."}</p>
            <button
              className="button primary"
              onClick={() => (error ? location.reload() : setMode("login"))}
            >
              {error ? "Повторить" : "Войти"}
            </button>
            <a href="/">На главную</a>
          </div>
        )
      ) : (
        <Landing user={user} onStart={start} />
      )}
      <AuthDialog
        mode={mode}
        onMode={setMode}
        onClose={() => setMode(null)}
        onSuccess={(user) => {
          setUser(user);
          setMode(null);
          navigate("/studio");
        }}
      />
    </MotionConfig>
  );
}
