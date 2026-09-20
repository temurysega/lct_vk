import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError, pendingJob, readDraft, saveDraft } from "../api";
import type { Batch, Deck, HistoryItem, Job, Template, User } from "../types";

const stages: Record<string, string> = {
  queued: "В очереди",
  starting: "Начинаем работу",
  template_analysis: "Изучаем шаблон",
  content_planning: "Планируем содержание",
  layout_mapping: "Выбираем макеты",
  composition: "Собираем слайды",
  quality_assurance: "Проверяем вёрстку",
  export: "Готовим файлы",
};
const aborted = (error: unknown) =>
  error instanceof DOMException && error.name === "AbortError";
function pause(signal: AbortSignal) {
  return new Promise<void>((resolve, reject) => {
    if (signal.aborted) {
      reject(new DOMException("Aborted", "AbortError"));
      return;
    }
    const cancel = () => {
      clearTimeout(timer);
      reject(new DOMException("Aborted", "AbortError"));
    };
    const timer = setTimeout(() => {
      signal.removeEventListener("abort", cancel);
      resolve();
    }, 1200);
    signal.addEventListener("abort", cancel, { once: true });
  });
}
export function validateFile(
  file: File | null,
  extensions: string[],
  megabytes: number,
) {
  if (!file) throw new Error("Выберите файл.");
  if (!extensions.includes(file.name.split(".").pop()?.toLowerCase() || ""))
    throw new Error("Неподдерживаемый формат файла.");
  if (!file.size) throw new Error("Файл пуст.");
  if (file.size > megabytes * 1024 * 1024)
    throw new Error(`Размер файла превышает ${megabytes} МБ.`);
}

export function useStudio(user: User) {
  const [draft] = useState(readDraft);
  const [content, setContent] = useState(draft.content);
  const [count, setCount] = useState(draft.count);
  const [templates, setTemplates] = useState<Template[]>([]);
  const [templateId, setTemplateId] = useState("");
  const [modelConfigured, setModelConfigured] = useState<boolean | null>(null);
  const [offline, setOffline] = useState(true);
  const [history, setHistory] = useState<HistoryItem[]>([]);
  const [batchId, setBatchId] = useState("");
  const [decks, setDecks] = useState<Deck[]>([]);
  const [active, setActive] = useState(0);
  const [status, setStatus] = useState("");
  const [error, setError] = useState(false);
  const [busy, setBusy] = useState<
    "loading" | "upload" | "generate" | "repair" | "history" | null
  >("loading");
  const [progress, setProgress] = useState<number | null>(null);
  const controller = useRef<AbortController | null>(null);
  const working = useRef(false);
  useEffect(() => {
    saveDraft({ content, count });
  }, [content, count]);
  const notify = useCallback((message: string, failed = false) => {
    setStatus(message);
    setError(failed);
  }, []);

  const poll = useCallback(
    async (id: string, signal: AbortSignal) => {
      let failures = 0;
      while (!signal.aborted) {
        let job: Job;
        try {
          job = await api<Job>("/v1/jobs/" + encodeURIComponent(id), {
            signal,
          });
          failures = 0;
        } catch (error) {
          if (aborted(error)) throw error;
          if (error instanceof ApiError && error.status === 404)
            pendingJob(user.id, null);
          if (
            (error instanceof ApiError && [401, 404].includes(error.status)) ||
            ++failures > 4
          )
            throw error;
          notify("Восстанавливаем соединение…");
          await pause(signal);
          continue;
        }
        if (signal.aborted) return;
        setProgress(job.progress || 0);
        notify(
          (stages[job.stage?.split(":").pop() || ""] ||
            "Обрабатываем материалы") + ` · ${job.progress || 0}%`,
        );
        if (["completed", "failed"].includes(job.status)) {
          pendingJob(user.id, null);
          if (!job.batch?.variants?.length)
            throw new Error(
              job.error ||
                "Не удалось завершить генерацию. Проверьте материалы.",
            );
          setDecks(job.batch.variants);
          setActive(0);
          setBatchId(job.batch.batch_id);
          setHistory(await api<HistoryItem[]>("/v1/batches", { signal }));
          notify(
            job.status === "failed"
              ? "Варианты собраны, но есть замечания к проверке или экспорту. Подробности ниже."
              : job.batch.diversity_status === "passed"
                ? "Три варианта готовы. Выберите свой."
                : "Варианты готовы. Проверьте различия: в шаблоне может быть недостаточно разных макетов.",
            job.status === "failed",
          );
          return;
        }
        await pause(signal);
      }
    },
    [notify, user.id],
  );

  useEffect(() => {
    const abort = new AbortController();
    controller.current = abort;
    async function initialize() {
      try {
        const [config, list, batches] = await Promise.all([
          api<{ model_configured: boolean }>("/api/config", {
            signal: abort.signal,
          }),
          api<Template[]>("/v1/templates", { signal: abort.signal }),
          api<HistoryItem[]>("/v1/batches", { signal: abort.signal }),
        ]);
        if (abort.signal.aborted) return;
        setModelConfigured(config.model_configured);
        setOffline(!config.model_configured);
        setTemplates(list);
        setTemplateId(list[0]?.template_id || "");
        setHistory(batches);
        const pending = pendingJob(user.id);
        if (pending) {
          working.current = true;
          setBusy("generate");
          setProgress(0);
          await poll(pending, abort.signal);
        }
      } catch (error) {
        if (!aborted(error))
          notify(
            error instanceof Error
              ? error.message
              : "Не удалось открыть студию.",
            true,
          );
      } finally {
        if (!abort.signal.aborted) {
          working.current = false;
          setBusy(null);
          setProgress(null);
        }
      }
    }
    initialize();
    return () => {
      abort.abort();
      working.current = false;
    };
  }, [user.id, notify, poll]);

  async function action(
    kind: Exclude<typeof busy, null>,
    task: (signal: AbortSignal) => Promise<void>,
  ) {
    if (working.current || busy) return;
    working.current = true;
    setBusy(kind);
    setError(false);
    const abort = new AbortController();
    controller.current = abort;
    try {
      await task(abort.signal);
    } catch (error) {
      if (!aborted(error))
        notify(
          error instanceof Error
            ? error.message
            : "Не удалось выполнить запрос.",
          true,
        );
    } finally {
      working.current = false;
      if (!abort.signal.aborted) {
        setBusy(null);
        setProgress(null);
      }
    }
  }
  useEffect(() => () => controller.current?.abort(), []);

  async function upload(file: File | null) {
    try {
      validateFile(file, ["pptx", "pdf"], 100);
    } catch (error) {
      notify((error as Error).message, true);
      return;
    }
    await action("upload", async (signal) => {
      notify("Изучаем цвета, шрифты и макеты вашего шаблона…");
      const body = new FormData();
      body.append("file", file!);
      body.append("offline", String(offline));
      const template = await api<Template>("/v1/templates/analyze", {
        method: "POST",
        body,
        signal,
      });
      setTemplates(await api<Template[]>("/v1/templates", { signal }));
      setTemplateId(template.template_id);
      notify(
        `Шаблон готов: ${template.slide_count} слайдов, ${template.pattern_count} вариантов макета.`,
      );
    });
  }
  async function generate(file: File | null) {
    if (!templateId) {
      notify("Сначала добавьте фирменный шаблон.", true);
      return;
    }
    if (!content.trim() && !file) {
      notify("Добавьте текст или файл с материалами.", true);
      return;
    }
    if (file)
      try {
        validateFile(
          file,
          ["md", "txt", "csv", "json", "pptx", "pdf", "docx", "xlsx"],
          50,
        );
      } catch (error) {
        notify((error as Error).message, true);
        return;
      }
    await action("generate", async (signal) => {
      setProgress(0);
      notify("Создаём три варианта…");
      const body = new FormData();
      body.append("template_id", templateId);
      body.append("content", content.trim());
      body.append("slide_count", String(count));
      body.append("offline", String(offline));
      body.append("variants", "true");
      body.append("export_all", "true");
      if (file) body.append("content_file", file);
      const job = await api<Job>("/v1/presentations/jobs", {
        method: "POST",
        body,
        signal,
      });
      pendingJob(user.id, job.job_id);
      await poll(job.job_id, signal);
    });
  }
  async function loadBatch(id: string) {
    if (!id) return;
    await action("history", async (signal) => {
      const batch = await api<Batch>("/v1/batches/" + encodeURIComponent(id), {
        signal,
      });
      setDecks(batch.variants);
      setActive(0);
      setBatchId(id);
      notify("Загружен сохранённый результат.");
    });
  }
  async function repair(issueIds: string[]) {
    const deck = decks[active];
    if (!deck || !issueIds.length) return;
    await action("repair", async (signal) => {
      notify("Исправляем выбранные слайды в новой версии…");
      const fixed = await api<Deck>(
        "/v1/presentations/" +
          encodeURIComponent(deck.presentation_id) +
          "/repair",
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ issue_ids: issueIds }),
          signal,
        },
      );
      setActive(decks.length);
      setDecks([...decks, fixed]);
      notify("Новая версия готова. Проверьте оставшиеся замечания.");
    });
  }
  return {
    content,
    setContent,
    count,
    setCount,
    templates,
    templateId,
    setTemplateId,
    modelConfigured,
    offline,
    setOffline,
    history,
    batchId,
    decks,
    active,
    setActive,
    status,
    error,
    busy,
    progress,
    upload,
    generate,
    loadBatch,
    repair,
    notify,
  };
}
