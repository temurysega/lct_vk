/* Shared account flow; no passwords or session tokens are kept in browser storage. */
(() => {
  "use strict";
  const byId = (id) => document.getElementById(id);
  const isStudio = document.body.classList.contains("studio");
  const dialog = byId("authDialog");
  let user = null;
  let mode = "register";
  let submitting = false;

  async function api(url, options = {}) {
    let response;
    try {
      response = await fetch(url, { credentials: "same-origin", ...options });
    } catch {
      throw new Error(
        "Не удалось связаться с сервером. Проверьте соединение и попробуйте ещё раз.",
      );
    }
    let data = null;
    if (response.status !== 204) {
      try {
        data = await response.json();
      } catch {
        /* Handle non-JSON gateway responses below. */
      }
    }
    if (!response.ok) {
      let message =
        typeof data?.detail === "string"
          ? data.detail
          : "Не удалось выполнить запрос. Попробуйте ещё раз.";
      if (response.status === 422)
        message = url.startsWith("/api/auth/")
          ? "Проверьте поля: имя — от 3 символов без пробелов, пароль — от 8 символов, должность — от 2 символов."
          : "Проверьте формат файлов и параметры презентации: от 3 до 100 слайдов.";
      const error = new Error(message);
      error.status = response.status;
      if (response.status === 401 && isStudio) {
        sessionStorage.setItem(
          "predel:draft",
          JSON.stringify({
            content: byId("content").value,
            count: byId("count").value,
          }),
        );
        location.assign("/?auth=login");
      }
      throw error;
    }
    return data;
  }
  window.predel = { api };

  function setMode(nextMode) {
    mode = nextMode;
    const register = mode === "register";
    byId("authTitle").textContent = register
      ? "Начнём с знакомства"
      : "С возвращением";
    byId("authDescription").textContent = register
      ? "Три поля — и можно создавать."
      : "Ваши идеи и презентации ждут в студии.";
    byId("positionField").hidden = !register;
    byId("position").required = register;
    byId("password").autocomplete = register
      ? "new-password"
      : "current-password";
    byId("authSubmit").textContent = register
      ? "Создать аккаунт ↗"
      : "Войти в студию ↗";
    byId("authSwitchText").textContent = register
      ? "Уже есть аккаунт?"
      : "Ещё нет аккаунта?";
    byId("switchAuth").textContent = register ? "Войти" : "Зарегистрироваться";
    byId("authError").textContent = "";
  }

  function openAuth(nextMode) {
    if (user) {
      location.assign("/studio");
      return;
    }
    setMode(nextMode);
    dialog.showModal();
    document.body.style.overflow = "hidden";
    byId("username").focus();
  }

  if (dialog) {
    document
      .querySelectorAll("[data-auth]")
      .forEach((button) =>
        button.addEventListener("click", () => openAuth(button.dataset.auth)),
      );
    byId("closeAuth").addEventListener("click", () => dialog.close());
    dialog.addEventListener("close", () => {
      document.body.style.overflow = "";
      byId("password").value = "";
    });
    dialog.addEventListener("click", (event) => {
      const rect = dialog.getBoundingClientRect();
      if (
        event.target === dialog &&
        (event.clientX < rect.left ||
          event.clientX > rect.right ||
          event.clientY < rect.top ||
          event.clientY > rect.bottom)
      )
        dialog.close();
    });
    byId("switchAuth").addEventListener("click", () => {
      if (!submitting) setMode(mode === "register" ? "login" : "register");
    });
    byId("togglePassword").addEventListener("click", () => {
      const show = byId("password").type === "password";
      byId("password").type = show ? "text" : "password";
      byId("togglePassword").textContent = show ? "Скрыть" : "Показать";
      byId("togglePassword").setAttribute(
        "aria-label",
        show ? "Скрыть пароль" : "Показать пароль",
      );
      byId("togglePassword").setAttribute("aria-pressed", String(show));
    });
    byId("authForm").addEventListener("submit", async (event) => {
      event.preventDefault();
      if (submitting) return;
      const body = {
        username: byId("username").value.trim(),
        password: byId("password").value,
      };
      if (!/^[\p{L}\p{N}_.-]{3,40}$/u.test(body.username)) {
        byId("authError").textContent =
          "В имени используйте от 3 до 40 букв, цифр, точку, дефис или _.";
        byId("username").focus();
        return;
      }
      if (mode === "register") body.position = byId("position").value.trim();
      submitting = true;
      byId("authSubmit").disabled = true;
      byId("switchAuth").disabled = true;
      byId("authSubmit").textContent = "Подождите…";
      byId("authError").textContent = "";
      try {
        await api("/api/auth/" + mode, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        });
        byId("password").value = "";
        location.assign("/studio");
      } catch (error) {
        byId("authError").textContent = error.message;
      } finally {
        submitting = false;
        byId("authSubmit").disabled = false;
        byId("switchAuth").disabled = false;
        byId("authSubmit").textContent =
          mode === "register" ? "Создать аккаунт ↗" : "Войти в студию ↗";
      }
    });
    byId("quickStart").addEventListener("submit", (event) => {
      event.preventDefault();
      if (!byId("brief").value.trim()) {
        byId("brief").focus();
        return;
      }
      sessionStorage.setItem(
        "predel:draft",
        JSON.stringify({
          content: byId("brief").value.trim(),
          count: byId("quickCount").value,
        }),
      );
      openAuth("register");
    });
    document.querySelectorAll("[data-prompt]").forEach((button) =>
      button.addEventListener("click", () => {
        byId("brief").value = button.dataset.prompt;
        byId("brief").focus();
      }),
    );
  }

  async function initialize() {
    try {
      const response = await fetch("/api/auth/me", {
        credentials: "same-origin",
      });
      if (response.ok) user = await response.json();
      else if (response.status !== 401)
        throw new Error("Сервис временно недоступен. Обновите страницу.");
    } catch (error) {
      if (isStudio) {
        byId("studioLock").replaceChildren();
        const message = document.createElement("p");
        message.textContent = error.message || "Не удалось открыть студию.";
        const retry = document.createElement("button");
        retry.className = "button";
        retry.textContent = "Повторить";
        retry.onclick = () => location.reload();
        byId("studioLock").append(message, retry);
        return;
      }
    }
    if (isStudio) {
      if (!user) {
        location.replace("/?auth=login");
        return;
      }
      byId("userName").textContent = user.username;
      byId("userName").title = user.position;
      byId("logout").hidden = false;
      byId("logout").addEventListener("click", async () => {
        byId("logout").disabled = true;
        try {
          await api("/api/auth/logout", { method: "POST" });
          sessionStorage.removeItem("predel:draft");
          sessionStorage.removeItem("predel:job:" + user.id);
          location.assign("/");
        } catch (error) {
          byId("status").textContent = error.message;
          byId("status").className = "error";
          byId("logout").disabled = false;
        }
      });
      try {
        const draft = JSON.parse(
          sessionStorage.getItem("predel:draft") || "null",
        );
        if (draft) {
          byId("content").value =
            typeof draft.content === "string" ? draft.content : "";
          byId("count").value = Math.min(
            100,
            Math.max(3, Number(draft.count) || 10),
          );
          sessionStorage.removeItem("predel:draft");
        }
      } catch {
        sessionStorage.removeItem("predel:draft");
      }
      byId("studioLock").hidden = true;
      byId("workspace").inert = false;
      document.dispatchEvent(
        new CustomEvent("predel:ready", { detail: { user } }),
      );
    } else if (user) {
      document.querySelector('[data-auth="login"]').textContent = user.username;
      document.querySelector('[data-auth="login"]').title = user.username;
      document.querySelector('[data-auth="register"]').textContent = "Студия ↗";
    } else if (new URLSearchParams(location.search).get("auth") === "login") {
      openAuth("login");
      history.replaceState(null, "", "/");
    }
  }

  const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
  if (!reducedMotion.matches && "IntersectionObserver" in window) {
    document.documentElement.classList.add("js-motion");
    const observer = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          if (entry.isIntersecting) {
            entry.target.classList.add("visible");
            observer.unobserve(entry.target);
          }
        });
      },
      { threshold: 0.08 },
    );
    document
      .querySelectorAll(".reveal")
      .forEach((section) => observer.observe(section));
    const columns = document.querySelectorAll(".slide-column");
    let frame = null;
    window.addEventListener(
      "scroll",
      () => {
        if (frame || !columns.length || reducedMotion.matches) return;
        frame = requestAnimationFrame(() => {
          const offset = Math.min(window.scrollY, 900);
          columns.forEach((column, i) => {
            column.style.transform = `translateY(${-offset * (0.025 + i * 0.02)}px)`;
          });
          frame = null;
        });
      },
      { passive: true },
    );
  }
  initialize();
})();
