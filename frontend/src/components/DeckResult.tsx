import { useState } from "react";
import {
  AlertCircle,
  CheckCheck,
  Download,
  FileSliders,
  Wrench,
} from "lucide-react";
import type { Deck } from "../types";

export function DeckResult({
  deck,
  busy,
  onRepair,
}: {
  deck: Deck;
  busy: boolean;
  onRepair: (ids: string[]) => void;
}) {
  const [selected, setSelected] = useState<string[]>([]);
  const [failedImages, setFailedImages] = useState<number[]>([]);
  const id = encodeURIComponent(deck.presentation_id);
  const qa = deck.qa || { status: "unknown", issues: [] };
  const issues = (qa.issues || []).filter((issue) => issue.severity !== "info");
  const previews = deck.exports?.previews || [];
  const formats = [
    "pptx",
    ...Object.keys(deck.exports?.artifacts || {}).filter((format) =>
      ["pdf", "html"].includes(format),
    ),
  ];
  return (
    <div className="deck-result" id="deck">
      <div className="deck-toolbar">
        <div>
          <strong>{deck.slide_count} слайдов</strong>
          <span>
            Редактируемый результат
            {deck.visuals
              ? ` · схем: ${deck.visuals.diagrams} · пиктограмм: ${deck.visuals.pictograms} · изображений: ${deck.visuals.images}`
              : ""}
          </span>
        </div>
        <div className="downloads">
          {formats.map((format) => (
            <a
              key={format}
              href={`/v1/presentations/${id}/download?format=${format}`}
              download
            >
              <Download size={15} />
              {format.toUpperCase()}
            </a>
          ))}
        </div>
      </div>
      <div
        className={`qa-summary ${qa.status === "failed" ? "has-errors" : ""}`}
      >
        {qa.status === "passed" ? (
          <CheckCheck size={20} />
        ) : (
          <AlertCircle size={20} />
        )}
        <div>
          <strong>
            Проверка вёрстки:{" "}
            {qa.status === "passed"
              ? "пройдена"
              : qa.status === "failed"
                ? "есть ошибки"
                : qa.status === "warning"
                  ? "есть замечания"
                  : "нет результата"}
          </strong>
          <p>
            Замечаний: {issues.length}. Смысловая проверка моделью не
            проводилась.
            {["offline", "fallback"].includes(deck.planner_mode || "")
              ? " Содержание распределено без модели."
              : ""}
          </p>
        </div>
      </div>
      {(deck.exports?.issues || []).map((issue, index) => (
        <p className="export-note" key={index}>
          <AlertCircle size={16} />
          {issue.code === "export_unavailable"
            ? "PDF, HTML и предпросмотр сейчас недоступны. Вы можете скачать PPTX."
            : issue.message}
        </p>
      ))}
      {!previews.length && (
        <div className="no-preview">
          <FileSliders size={40} strokeWidth={1.2} />
          <h3>Презентация собрана.</h3>
          <p>
            Для просмотра слайдов откройте файл в PowerPoint.
            <br />
            Здесь появится предпросмотр, когда будет доступен рендер.
          </p>
        </div>
      )}
      <div
        className={
          !previews.length ? "compact-slide-list" : "rendered-slide-list"
        }
      >
        {Array.from({ length: deck.slide_count }, (_, index) => index + 1).map(
          (number) => {
            const slideIssues = issues.filter(
              (issue) => issue.slide === number,
            );
            const hasPreview =
              previews.some((preview) => preview.slide === number) &&
              !failedImages.includes(number);
            return (
              <article className="slide-result" key={number}>
                <div className="slide-heading">
                  <span>Слайд {String(number).padStart(2, "0")}</span>
                  <span>
                    {slideIssues.length
                      ? `${slideIssues.length} замечаний`
                      : "Нет замечаний к вёрстке"}
                  </span>
                </div>
                {hasPreview && (
                  <div className="slide-view">
                    <img
                      src={`/v1/presentations/${id}/preview/${number}`}
                      alt={`Слайд ${number}`}
                      loading="lazy"
                      onError={() =>
                        setFailedImages((previous) => [...previous, number])
                      }
                    />
                    {qa.canvas &&
                      qa.canvas.width_inches > 0 &&
                      qa.canvas.height_inches > 0 &&
                      slideIssues.flatMap((issue) =>
                        (issue.bounds || []).map((bounds, index) => (
                          <span
                            key={issue.id + index}
                            className="issue-mark"
                            style={{
                              left: `${(100 * bounds.x) / qa.canvas!.width_inches}%`,
                              top: `${(100 * bounds.y) / qa.canvas!.height_inches}%`,
                              width: `${(100 * bounds.w) / qa.canvas!.width_inches}%`,
                              height: `${(100 * bounds.h) / qa.canvas!.height_inches}%`,
                            }}
                          />
                        )),
                      )}
                  </div>
                )}
                {failedImages.includes(number) && (
                  <p className="export-note">
                    Не удалось загрузить превью. Скачайте PPTX.
                  </p>
                )}
                {slideIssues.map((issue) => (
                  <label
                    className={`audit-issue ${issue.severity}`}
                    key={issue.id}
                  >
                    {issue.repairable && (
                      <input
                        type="checkbox"
                        disabled={busy}
                        checked={selected.includes(issue.id)}
                        onChange={(event) =>
                          setSelected((previous) =>
                            event.target.checked
                              ? [...previous, issue.id]
                              : previous.filter((id) => id !== issue.id),
                          )
                        }
                      />
                    )}
                    <span>{issue.message}</span>
                  </label>
                ))}
              </article>
            );
          },
        )}
      </div>
      {issues
        .filter((issue) => !issue.slide)
        .map((issue) => (
          <p key={issue.id} className="export-note">
            {issue.message}
          </p>
        ))}
      {issues.some((issue) => issue.repairable && issue.slide) && (
        <button
          className="button primary repair-button"
          disabled={busy || !selected.length}
          onClick={() => onRepair(selected)}
        >
          <Wrench size={16} />
          Исправить выбранные ({selected.length})
        </button>
      )}
    </div>
  );
}
