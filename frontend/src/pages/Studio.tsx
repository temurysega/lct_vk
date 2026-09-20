import { useRef, useState, type FormEvent } from "react";
import {
  ArrowLeft,
  ArrowUpRight,
  Check,
  ChevronRight,
  FileSliders,
  FileText,
  Layers3,
  LogOut,
  Plus,
  Sparkles,
  Upload,
  X,
} from "lucide-react";
import { Brand } from "../components/Brand";
import { DeckResult } from "../components/DeckResult";
import { useStudio, validateFile } from "../hooks/useStudio";
import type { User } from "../types";

export default function Studio({
  user,
  onLogout,
}: {
  user: User;
  onLogout: () => Promise<void>;
}) {
  const state = useStudio(user);
  const [templateFile, setTemplateFile] = useState<File | null>(null);
  const [contentFile, setContentFile] = useState<File | null>(null);
  const [dragging, setDragging] = useState(false);
  const [leaving, setLeaving] = useState(false);
  const uploadInput = useRef<HTMLInputElement>(null);
  const disabled = Boolean(state.busy);
  const deck = state.decks[state.active];
  function upload(event: FormEvent) {
    event.preventDefault();
    void state.upload(templateFile);
  }
  function generate(event: FormEvent) {
    event.preventDefault();
    void state.generate(contentFile);
  }
  function dropped(file: File) {
    try {
      validateFile(file, ["pptx", "pdf"], 100);
      setTemplateFile(file);
    } catch (error) {
      state.notify((error as Error).message, true);
    }
  }
  const modelNote =
    state.modelConfigured === null
      ? "Проверяем настройки модели…"
      : !state.modelConfigured
        ? "Модель пока не подключена. Добавьте готовый текст — доступна сборка без модели."
        : state.offline
          ? "Без модели: распределяем готовые материалы по слайдам."
          : "Модель спланирует содержание по вашим материалам.";
  return (
    <div className="studio-page">
      <a className="skip-link" href="#workspace">
        К рабочей области
      </a>
      <header className="studio-header">
        <Brand />
        <span className="studio-breadcrumb">
          <ChevronRight size={16} />
          Студия презентаций
        </span>
        <div className="studio-account">
          <span className="avatar">
            {user.username.slice(0, 1).toUpperCase()}
          </span>
          <span className="account-name" title={user.position}>
            {user.username}
          </span>
          <button
            id="logout"
            className="icon-button"
            aria-label="Выйти"
            title="Выйти"
            disabled={leaving}
            onClick={async () => {
              setLeaving(true);
              try {
                await onLogout();
              } catch (error) {
                state.notify((error as Error).message, true);
                setLeaving(false);
              }
            }}
          >
            <LogOut size={18} />
          </button>
        </div>
      </header>
      <main className="studio-shell" id="workspace" aria-busy={disabled}>
        <div className="studio-title">
          <div>
            <a href="/" className="back-link">
              <ArrowLeft size={14} />
              На главную
            </a>
            <h1>
              Новая презентация<span>.</span>
            </h1>
            <p>Ваши материалы. Ваш стиль. Три варианта.</p>
          </div>
          <span className="private-note">
            <Check size={14} /> Видно только вам
          </span>
        </div>
        <div className="studio-layout">
          <aside className="studio-controls">
            <fieldset disabled={disabled}>
              <section className="control-section">
                <div className="control-heading">
                  <span>01</span>
                  <h2>Ваш шаблон</h2>
                  <FileSliders size={18} />
                </div>
                <form id="upload" onSubmit={upload}>
                  <label
                    className={`file-drop ${dragging ? "dragging" : ""} ${templateFile ? "has-file" : ""}`}
                    onDragOver={(event) => {
                      event.preventDefault();
                      if (!disabled) setDragging(true);
                    }}
                    onDragLeave={() => setDragging(false)}
                    onDrop={(event) => {
                      event.preventDefault();
                      setDragging(false);
                      if (!disabled && event.dataTransfer.files[0])
                        dropped(event.dataTransfer.files[0]);
                    }}
                  >
                    <span className="upload-icon">
                      <Upload size={24} />
                    </span>
                    <strong>
                      {templateFile
                        ? templateFile.name
                        : "Перетащите шаблон сюда"}
                    </strong>
                    <span>
                      {templateFile
                        ? `${(templateFile.size / 1024 / 1024).toFixed(1)} МБ · файл выбран`
                        : "или нажмите, чтобы выбрать"}
                    </span>
                    <small>PPTX, PDF · до 100 МБ</small>
                    <input
                      ref={uploadInput}
                      id="templateFile"
                      type="file"
                      accept=".pptx,.pdf"
                      onChange={(event) =>
                        setTemplateFile(event.target.files?.[0] || null)
                      }
                      aria-label="Фирменный шаблон"
                    />
                  </label>
                  <button
                    id="uploadButton"
                    className="button secondary full"
                    type="submit"
                  >
                    {state.busy === "upload"
                      ? "Изучаем шаблон…"
                      : "Добавить шаблон"}
                    <Plus size={16} />
                  </button>
                </form>
                <label htmlFor="template">Мои шаблоны</label>
                <select
                  id="template"
                  value={state.templateId}
                  onChange={(event) => state.setTemplateId(event.target.value)}
                >
                  {!state.templates.length && (
                    <option value="">Загрузите первый шаблон</option>
                  )}
                  {state.templates.map((template) => (
                    <option
                      key={template.template_id}
                      value={template.template_id}
                    >
                      {template.name || template.source_file}
                    </option>
                  ))}
                </select>
              </section>
              <section className="control-section">
                <div className="control-heading">
                  <span>02</span>
                  <h2>Ваше содержание</h2>
                  <FileText size={18} />
                </div>
                <form id="generate" onSubmit={generate}>
                  <label htmlFor="content">Тема, факты и ключевые тезисы</label>
                  <textarea
                    id="content"
                    rows={6}
                    maxLength={100000}
                    value={state.content}
                    onChange={(event) => state.setContent(event.target.value)}
                    placeholder="Для кого презентация? Что важно рассказать? Добавьте факты и выводы."
                  />
                  <label className="attach-file" htmlFor="contentFile">
                    <Plus size={16} />
                    <span>{contentFile?.name || "Прикрепить материалы"}</span>
                    <input
                      id="contentFile"
                      type="file"
                      accept=".md,.txt,.csv,.json,.pptx,.pdf,.docx,.xlsx"
                      onChange={(event) =>
                        setContentFile(event.target.files?.[0] || null)
                      }
                    />
                  </label>
                  {contentFile && (
                    <button
                      className="remove-file"
                      type="button"
                      onClick={() => {
                        setContentFile(null);
                        const input = document.getElementById(
                          "contentFile",
                        ) as HTMLInputElement;
                        input.value = "";
                      }}
                    >
                      <X size={12} /> Убрать файл
                    </button>
                  )}
                  <p className="field-hint">
                    До 50 МБ. Если прикреплён файл, содержание берётся из него.
                  </p>
                  <div className="generation-options">
                    <label htmlFor="count">
                      Слайдов
                      <input
                        id="count"
                        type="number"
                        min={3}
                        max={100}
                        value={state.count}
                        onChange={(event) =>
                          state.setCount(Number(event.target.value))
                        }
                        required
                      />
                    </label>
                    <label className="checkbox-label">
                      <input
                        id="offline"
                        type="checkbox"
                        checked={state.offline}
                        disabled={!state.modelConfigured}
                        onChange={(event) =>
                          state.setOffline(event.target.checked)
                        }
                      />
                      Без модели
                    </label>
                  </div>
                  <p id="modelNote" className="model-note">
                    {modelNote}
                  </p>
                  <button
                    id="generateButton"
                    className="button primary full"
                    type="submit"
                  >
                    <Sparkles size={17} />
                    {state.busy === "generate"
                      ? "Создаём варианты…"
                      : "Создать 3 варианта"}
                    <ArrowUpRight size={17} />
                  </button>
                </form>
              </section>
            </fieldset>
          </aside>
          <section className="studio-results" aria-labelledby="resultsTitle">
            <div className="results-heading">
              <h2 id="resultsTitle">Варианты презентации</h2>
              <span
                className={`status-badge ${disabled ? "working" : ""}`}
                id="statusBadge"
              >
                <i />
                {disabled ? "В работе" : "Готов к работе"}
              </span>
            </div>
            <div
              id="status"
              className={state.error ? "studio-status error" : "studio-status"}
              role="status"
              aria-live="polite"
            >
              {state.status}
            </div>
            {state.progress !== null && (
              <progress
                id="progress"
                value={state.progress}
                max={100}
                aria-label="Готовность презентации"
              />
            )}
            {state.history.length > 0 && (
              <div id="history" className="history-select">
                <label htmlFor="batches">Сохранённые результаты</label>
                <select
                  id="batches"
                  value={state.batchId}
                  disabled={disabled}
                  onChange={(event) => void state.loadBatch(event.target.value)}
                >
                  <option value="">Выберите результат</option>
                  {state.history.map((batch) => (
                    <option key={batch.batch_id} value={batch.batch_id}>
                      {state.templates.find(
                        (template) =>
                          template.template_id === batch.template_id,
                      )?.source_file || "Презентация"}{" "}
                      · {batch.batch_id.slice(-17)}
                    </option>
                  ))}
                </select>
              </div>
            )}
            {state.decks.length > 0 && (
              <nav
                id="variants"
                className="result-variants"
                aria-label="Варианты презентации"
              >
                {state.decks.map((item, index) => (
                  <button
                    key={item.presentation_id}
                    className={state.active === index ? "active" : ""}
                    aria-pressed={state.active === index}
                    disabled={disabled}
                    onClick={() => state.setActive(index)}
                  >
                    {item.variant?.label || "Исправленная версия"}
                  </button>
                ))}
              </nav>
            )}
            {deck ? (
              <DeckResult
                key={deck.presentation_id}
                deck={deck}
                busy={disabled}
                onRepair={(ids) => void state.repair(ids)}
              />
            ) : (
              <div className="studio-empty" id="empty">
                <div className="empty-object" aria-hidden="true">
                  <i />
                  <i />
                  <i>
                    <Layers3 size={46} strokeWidth={1.2} />
                  </i>
                </div>
                <h3>
                  Здесь начинается
                  <br />
                  ваша история.
                </h3>
                <p>
                  Добавьте шаблон и материалы.
                  <br />
                  Мы соберём три варианта — выбор за вами.
                </p>
                <div className="empty-formats">
                  <span>PPTX</span>
                  <span>PDF</span>
                  <span>HTML</span>
                </div>
              </div>
            )}
          </section>
        </div>
        <footer className="studio-footer">
          <span>predel · Ваш смысл. Ваша форма.</span>
          <span>Проверка вёрстки не заменяет проверку фактов.</span>
        </footer>
      </main>
    </div>
  );
}
