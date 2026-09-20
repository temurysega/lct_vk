/* Presentation workflow. All content is rendered with textContent, never HTML. */
(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  let decks = [];
  let busy = false;
  let userId = "";
  let lockedControls = [];
  let modelConfigured = false;
  const api = (...args) => window.predel.api(...args);

  function node(tag, text, className) {
    const element = document.createElement(tag);
    if (text !== undefined) element.textContent = text;
    if (className) element.className = className;
    return element;
  }
  function status(message, error = false) {
    $("status").textContent = message;
    $("status").className = error ? "error" : "";
  }
  function setBusy(value) {
    busy = value;
    if (value) {
      lockedControls = [
        ...document.querySelectorAll(
          ".controls input, .controls select, .controls textarea, .controls button, #batches",
        ),
      ].filter((control) => !control.disabled);
      lockedControls.forEach((control) => {
        control.disabled = true;
      });
    } else {
      lockedControls.forEach((control) => {
        control.disabled = false;
      });
      lockedControls = [];
    }
    $("statusBadge").textContent = value ? "В работе" : "Готов к работе";
    $("workspace").setAttribute("aria-busy", String(value));
  }
  async function templates(selected) {
    const list = await api("/v1/templates");
    $("template").replaceChildren();
    if (!list.length) {
      const placeholder = node("option", "Загрузите первый шаблон");
      placeholder.value = "";
      $("template").append(placeholder);
    }
    for (const template of list) {
      const option = node("option", template.name || template.source_file);
      option.value = template.template_id;
      $("template").append(option);
    }
    if (selected) $("template").value = selected;
  }

  function validateFile(file, extensions, megabytes) {
    if (!file) throw new Error("Выберите файл.");
    if (!extensions.includes(file.name.split(".").pop().toLowerCase()))
      throw new Error("Неподдерживаемый формат файла.");
    if (file.size > megabytes * 1024 * 1024)
      throw new Error("Размер файла превышает " + megabytes + " МБ.");
    if (!file.size) throw new Error("Выбранный файл пуст.");
  }
  $("upload").addEventListener("submit", async (event) => {
    event.preventDefault();
    if (busy) return;
    try {
      validateFile($("templateFile").files[0], ["pptx", "pdf"], 100);
    } catch (error) {
      status(error.message, true);
      return;
    }
    setBusy(true);
    status("Изучаем цвета, шрифты и макеты вашего шаблона…");
    try {
      const data = new FormData();
      data.append("file", $("templateFile").files[0]);
      data.append("offline", String($("offline").checked));
      const template = await api("/v1/templates/analyze", {
        method: "POST",
        body: data,
      });
      await templates(template.template_id);
      status(
        "Шаблон готов: " +
          template.slide_count +
          " слайдов, " +
          template.pattern_count +
          " вариантов макета.",
      );
    } catch (error) {
      status(error.message, true);
    } finally {
      setBusy(false);
    }
  });

  const drop = $("templateDrop");
  ["dragenter", "dragover"].forEach((type) =>
    drop.addEventListener(type, (event) => {
      event.preventDefault();
      if (!busy) drop.classList.add("dragging");
    }),
  );
  ["dragleave", "drop"].forEach((type) =>
    drop.addEventListener(type, (event) => {
      event.preventDefault();
      drop.classList.remove("dragging");
    }),
  );
  drop.addEventListener("drop", (event) => {
    if (busy || !event.dataTransfer.files.length) return;
    try {
      validateFile(event.dataTransfer.files[0], ["pptx", "pdf"], 100);
      const transfer = new DataTransfer();
      transfer.items.add(event.dataTransfer.files[0]);
      $("templateFile").files = transfer.files;
      status("Файл выбран. Нажмите «Добавить шаблон».");
    } catch (error) {
      status(error.message, true);
    }
  });

  $("generate").addEventListener("submit", async (event) => {
    event.preventDefault();
    if (busy) return;
    if (!$("template").value) {
      status("Сначала добавьте фирменный шаблон.", true);
      $("templateFile").focus();
      return;
    }
    if (!$("content").value.trim() && !$("contentFile").files.length) {
      status("Добавьте текст или файл с материалами.", true);
      $("content").focus();
      return;
    }
    if ($("contentFile").files.length) {
      try {
        validateFile(
          $("contentFile").files[0],
          ["md", "txt", "csv", "json", "pptx", "pdf", "docx", "xlsx"],
          50,
        );
      } catch (error) {
        status(error.message, true);
        return;
      }
    }
    setBusy(true);
    $("progress").hidden = false;
    $("progress").value = 0;
    status("Создаём три варианта презентации…");
    try {
      const data = new FormData();
      data.append("template_id", $("template").value);
      data.append("content", $("content").value.trim());
      data.append("slide_count", $("count").value);
      data.append("offline", String($("offline").checked));
      data.append("variants", "true");
      data.append("export_all", "true");
      if ($("contentFile").files.length)
        data.append("content_file", $("contentFile").files[0]);
      const job = await api("/v1/presentations/jobs", {
        method: "POST",
        body: data,
      });
      sessionStorage.setItem("predel:job:" + userId, job.job_id);
      if (window.matchMedia("(max-width: 760px)").matches)
        $("resultsTitle").scrollIntoView({
          behavior: "smooth",
          block: "start",
        });
      await poll(job.job_id);
    } catch (error) {
      status(error.message, true);
    } finally {
      setBusy(false);
      $("progress").hidden = true;
    }
  });

  const stageLabels = {
    queued: "В очереди",
    starting: "Начинаем работу",
    template_analysis: "Разбираем шаблон",
    content_planning: "Планируем содержание",
    layout_mapping: "Выбираем макеты",
    composition: "Собираем слайды",
    quality_assurance: "Проверяем вёрстку",
    export: "Готовим PDF и HTML",
  };
  async function poll(id) {
    let failures = 0;
    for (;;) {
      let job;
      try {
        job = await api("/v1/jobs/" + encodeURIComponent(id));
        failures = 0;
      } catch (error) {
        if (error.status === 404)
          sessionStorage.removeItem("predel:job:" + userId);
        if (error.status === 401 || error.status === 404 || ++failures > 4)
          throw error;
        status("Соединение прервалось. Пытаемся восстановить…");
        await new Promise((resolve) => setTimeout(resolve, 2000));
        continue;
      }
      $("progress").value = Number(job.progress) || 0;
      const stage = (job.stage || "").split(":").pop();
      status(
        (stageLabels[stage] || "Обрабатываем материалы") +
          " · " +
          (Number(job.progress) || 0) +
          "%",
      );
      if (job.status === "completed" || job.status === "failed") {
        sessionStorage.removeItem("predel:job:" + userId);
        if (job.batch?.variants?.length) {
          decks = job.batch.variants;
          showTabs();
          await loadHistory();
          $("batches").value = job.batch.batch_id;
          if (job.status === "failed")
            status(
              "Презентации собраны, но есть замечания к проверке или экспорту. Подробности ниже.",
              true,
            );
          else if (job.batch.diversity_status === "passed")
            status("Три варианта готовы. Выберите подходящий.");
          else
            status(
              "Варианты готовы. В этом шаблоне мало различающихся макетов — проверьте различия.",
            );
          return;
        }
        throw new Error(
          job.error ||
            "Не удалось завершить генерацию. Проверьте шаблон и материалы.",
        );
      }
      await new Promise((resolve) => setTimeout(resolve, 1500));
    }
  }

  function showTabs(active = 0) {
    if (!decks.length) return;
    $("empty").hidden = true;
    $("variants").replaceChildren();
    decks.forEach((deck, index) => {
      const button = node(
        "button",
        deck.variant?.label || "Исправленная версия",
        index === active ? "active" : "",
      );
      button.type = "button";
      button.setAttribute("aria-pressed", String(index === active));
      button.onclick = () => showTabs(index);
      $("variants").append(button);
    });
    showDeck(decks[active]);
  }

  function showDeck(deck) {
    const container = $("deck");
    container.replaceChildren();
    const id = encodeURIComponent(deck.presentation_id);
    const qa = deck.qa || { status: "unknown", issues: [] };
    const issues = qa.issues || [];
    const downloads = node("div", undefined, "downloads");
    const formats = [
      "pptx",
      ...Object.keys(deck.exports?.artifacts || {}).filter((format) =>
        ["pdf", "html"].includes(format),
      ),
    ];
    for (const format of formats) {
      const link = node("a", format.toUpperCase() + " ↓");
      link.href = "/v1/presentations/" + id + "/download?format=" + format;
      link.download = "";
      downloads.append(link);
    }
    container.append(downloads);
    const qaLabels = {
      failed: "есть ошибки",
      warning: "есть замечания",
      passed: "пройдена",
    };
    container.append(
      node(
        "div",
        "Проверка вёрстки: " +
          (qaLabels[qa.status] || "нет результата") +
          ". Замечаний: " +
          issues.filter((issue) => issue.severity !== "info").length +
          ". Смысловая проверка моделью не проводилась." +
          (["offline", "fallback"].includes(deck.planner_mode)
            ? " Содержание распределено без модели."
            : ""),
        "qa-note",
      ),
    );
    for (const issue of deck.exports?.issues || [])
      container.append(
        node(
          "p",
          issue.code === "export_unavailable"
            ? "PDF, HTML и предпросмотр сейчас недоступны. Презентацию можно скачать в формате PPTX."
            : issue.message,
          "qa-note",
        ),
      );
    const selected = new Set();
    const repair = node("button", "Исправить выбранные замечания", "repair");
    repair.type = "button";
    repair.disabled = true;
    repair.onclick = async () => {
      if (busy || !selected.size) return;
      setBusy(true);
      repair.disabled = true;
      status("Пересобираем выбранные слайды в новой версии…");
      try {
        const fixed = await api("/v1/presentations/" + id + "/repair", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ issue_ids: [...selected] }),
        });
        decks.push(fixed);
        showTabs(decks.length - 1);
        status("Новая версия готова. Проверьте оставшиеся замечания.");
      } catch (error) {
        status(error.message, true);
      } finally {
        setBusy(false);
        repair.disabled = selected.size === 0;
      }
    };
    for (let number = 1; number <= deck.slide_count; number++) {
      const card = node("article", undefined, "slide-card");
      card.append(
        node("div", "Слайд " + String(number).padStart(2, "0"), "slide-head"),
      );
      const view = node("div", undefined, "slide-view");
      if (deck.exports?.previews?.some((preview) => preview.slide === number)) {
        const img = node("img");
        img.src = "/v1/presentations/" + id + "/preview/" + number;
        img.alt = "Слайд " + number;
        img.loading = "lazy";
        img.onerror = () =>
          view.replaceChildren(
            node("p", "Не удалось загрузить превью. Скачайте PPTX."),
          );
        view.append(img);
      } else view.append(node("p", "Предпросмотр недоступен. Скачайте PPTX."));
      card.append(view);
      const list = node("div", undefined, "issues");
      for (const issue of issues.filter(
        (item) => item.slide === number && item.severity !== "info",
      )) {
        const label = node("label", undefined, "issue " + issue.severity);
        if (issue.repairable) {
          const check = node("input");
          check.type = "checkbox";
          check.onchange = () => {
            if (check.checked) selected.add(issue.id);
            else selected.delete(issue.id);
            repair.disabled = busy || selected.size === 0;
          };
          label.append(check);
        }
        label.append(node("span", issue.message));
        list.append(label);
        if (qa.canvas?.width_inches > 0 && qa.canvas?.height_inches > 0) {
          for (const bounds of issue.bounds || []) {
            const mark = node("div", undefined, "mark");
            Object.assign(mark.style, {
              left: (100 * bounds.x) / qa.canvas.width_inches + "%",
              top: (100 * bounds.y) / qa.canvas.height_inches + "%",
              width: (100 * bounds.w) / qa.canvas.width_inches + "%",
              height: (100 * bounds.h) / qa.canvas.height_inches + "%",
            });
            view.append(mark);
          }
        }
      }
      card.append(list);
      container.append(card);
    }
    for (const issue of issues.filter(
      (item) => !item.slide && item.severity !== "info",
    ))
      container.append(node("p", issue.message, "qa-note"));
    if (
      issues.some(
        (issue) => issue.repairable && issue.slide && issue.severity !== "info",
      )
    )
      container.append(repair);
  }

  async function loadHistory() {
    const list = await api("/v1/batches");
    $("history").hidden = !list.length;
    $("batches").replaceChildren(node("option", "Выберите результат"));
    $("batches").firstChild.value = "";
    for (const batch of list) {
      const template =
        [...$("template").options].find(
          (option) => option.value === batch.template_id,
        )?.textContent || "Презентация";
      const option = node(
        "option",
        template + " · " + batch.batch_id.slice(-17),
      );
      option.value = batch.batch_id;
      $("batches").append(option);
    }
  }
  $("batches").addEventListener("change", async () => {
    if (!$("batches").value || busy) return;
    setBusy(true);
    try {
      const batch = await api(
        "/v1/batches/" + encodeURIComponent($("batches").value),
      );
      decks = batch.variants || [];
      showTabs();
      status(
        batch.status === "failed"
          ? "У этого результата есть замечания. Проверьте слайды."
          : "Загружен сохранённый результат.",
        batch.status === "failed",
      );
    } catch (error) {
      status(error.message, true);
    } finally {
      setBusy(false);
    }
  });

  function modelNote() {
    $("modelNote").textContent = modelConfigured
      ? $("offline").checked
        ? "Без модели: распределяем готовый материал по слайдам. Для работы с кратким брифом включите модель."
        : "Генерация через подключённую модель. Содержание будет спланировано по вашим материалам."
      : "Модель пока не подключена. Доступна сборка по готовым материалам — добавьте полный текст, а не только тему.";
  }
  $("offline").addEventListener("change", modelNote);
  document.addEventListener("predel:ready", async (event) => {
    userId = event.detail.user.id;
    try {
      const config = await api("/api/config");
      modelConfigured = config.model_configured;
      $("offline").checked = !modelConfigured;
      $("offline").disabled = !modelConfigured;
      modelNote();
      await templates();
      await loadHistory();
      const pending = sessionStorage.getItem("predel:job:" + userId);
      if (pending) {
        setBusy(true);
        $("progress").hidden = false;
        status("Восстанавливаем состояние генерации…");
        await poll(pending);
      }
    } catch (error) {
      status(error.message, true);
    } finally {
      setBusy(false);
      $("progress").hidden = true;
    }
  });
})();
