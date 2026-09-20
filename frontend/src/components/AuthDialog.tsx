import { useEffect, useRef, useState, type FormEvent } from "react";
import { ArrowUpRight, Eye, EyeOff, X } from "lucide-react";
import { api } from "../api";
import type { User } from "../types";
import { Brand } from "./Brand";

export function AuthDialog({
  mode,
  onMode,
  onClose,
  onSuccess,
}: {
  mode: "login" | "register" | null;
  onMode: (mode: "login" | "register") => void;
  onClose: () => void;
  onSuccess: (user: User) => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const usernameInput = useRef<HTMLInputElement>(null);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [position, setPosition] = useState("");
  const [visible, setVisible] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const register = mode === "register";
  useEffect(() => {
    setError("");
    if (mode) {
      if (!dialog.current?.open) dialog.current?.showModal();
      document.body.style.overflow = "hidden";
      usernameInput.current?.focus();
    } else {
      dialog.current?.close();
      document.body.style.overflow = "";
      setPassword("");
      setVisible(false);
    }
    return () => {
      document.body.style.overflow = "";
    };
  }, [mode]);
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy) return;
    if (!/^[\p{L}\p{N}_.-]{3,40}$/u.test(username.trim())) {
      setError(
        "Имя: от 3 до 40 букв или цифр, без пробелов. Допустимы точка, дефис и _.",
      );
      return;
    }
    setBusy(true);
    setError("");
    try {
      const user = await api<User>("/api/auth/" + mode, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          username: username.trim(),
          password,
          ...(register ? { position: position.trim() } : {}),
        }),
      });
      setPassword("");
      onSuccess(user);
    } catch (error) {
      setError(error instanceof Error ? error.message : "Не удалось войти.");
    } finally {
      setBusy(false);
    }
  }
  return (
    <dialog
      ref={dialog}
      id="authDialog"
      className="auth-dialog"
      aria-labelledby="authTitle"
      onCancel={onClose}
      onClick={(event) => {
        const r = dialog.current!.getBoundingClientRect();
        if (
          event.target === dialog.current &&
          (event.clientX < r.left ||
            event.clientX > r.right ||
            event.clientY < r.top ||
            event.clientY > r.bottom)
        )
          onClose();
      }}
    >
      <button
        className="icon-button close-auth"
        onClick={onClose}
        aria-label="Закрыть"
      >
        <X size={21} />
      </button>
      <Brand />
      <p className="eyebrow auth-eyebrow">ВАШ СЛЕДУЮЩИЙ СЛАЙД</p>
      <h2 id="authTitle">
        {register ? "Начнём с знакомства." : "Рады видеть снова."}
      </h2>
      <p className="muted">
        {register
          ? "Три поля — и ваши идеи обретают форму."
          : "Ваши материалы и презентации уже здесь."}
      </p>
      <form id="authForm" onSubmit={submit}>
        <label htmlFor="username">Имя пользователя</label>
        <input
          ref={usernameInput}
          id="username"
          autoComplete="username"
          value={username}
          onChange={(e) => setUsername(e.target.value)}
          minLength={3}
          maxLength={40}
          placeholder="Как к вам обращаться?"
          required
        />
        <label htmlFor="password">Пароль</label>
        <div className="password-field">
          <input
            id="password"
            type={visible ? "text" : "password"}
            autoComplete={register ? "new-password" : "current-password"}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            minLength={8}
            maxLength={128}
            placeholder="Не менее 8 символов"
            required
          />
          <button
            type="button"
            className="icon-button"
            aria-label={visible ? "Скрыть пароль" : "Показать пароль"}
            aria-pressed={visible}
            onClick={() => setVisible(!visible)}
          >
            {visible ? <EyeOff size={18} /> : <Eye size={18} />}
          </button>
        </div>
        {register && (
          <>
            <label htmlFor="position">Должность</label>
            <input
              id="position"
              autoComplete="organization-title"
              value={position}
              onChange={(e) => setPosition(e.target.value)}
              minLength={2}
              maxLength={120}
              placeholder="Например, менеджер продукта"
              required
            />
          </>
        )}
        <p id="authError" className="error-message" role="alert">
          {error}
        </p>
        <button id="authSubmit" className="button primary" disabled={busy}>
          {busy
            ? "Подождите…"
            : register
              ? "Создать аккаунт"
              : "Войти в студию"}
          <ArrowUpRight size={18} />
        </button>
      </form>
      <p className="auth-switch">
        {register ? "Уже есть аккаунт?" : "Первый раз здесь?"}{" "}
        <button
          type="button"
          disabled={busy}
          onClick={() => onMode(register ? "login" : "register")}
        >
          {register ? "Войти" : "Зарегистрироваться"}
        </button>
      </p>
    </dialog>
  );
}
