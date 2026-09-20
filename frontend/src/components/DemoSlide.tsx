import { ArrowUpRight, Sparkles } from "lucide-react";

export function DemoSlide({
  variant = 0,
  compact = false,
}: {
  variant?: number;
  compact?: boolean;
}) {
  return (
    <div
      className={`demo-slide variant-${variant} ${compact ? "compact-slide" : ""}`}
      aria-hidden={compact || undefined}
    >
      <div className="demo-slide-top">
        <span>predel / brand stories</span>
        <span>2026</span>
      </div>
      {variant === 0 ? (
        <>
          <div className="demo-title">
            Большие идеи.
            <br />
            <span>Общий язык.</span>
          </div>
          <div className="demo-sculpture" aria-hidden="true">
            <i />
            <i />
            <i />
            <i />
          </div>
          <div className="demo-slide-foot">
            <span>Создаём то, что объединяет.</span>
            <ArrowUpRight size={22} />
          </div>
        </>
      ) : variant === 1 ? (
        <>
          <div className="demo-title">
            От идеи
            <br />к действию.
          </div>
          <div className="demo-process">
            {["Смысл", "Форма", "Результат"].map((word, index) => (
              <div key={word}>
                <b>0{index + 1}</b>
                <span>{word}</span>
              </div>
            ))}
          </div>
          <div className="demo-slide-foot">
            <span>Три шага. Одна история.</span>
            <span>02 / 10</span>
          </div>
        </>
      ) : (
        <>
          <div className="demo-title">
            Фокус на том,
            <br />
            <span>что важно.</span>
          </div>
          <div className="demo-orbit" aria-hidden="true">
            <Sparkles />
            <i />
            <i />
          </div>
          <div className="demo-slide-foot">
            <span>Меньше деталей. Больше смысла.</span>
            <span>03 / 10</span>
          </div>
        </>
      )}
    </div>
  );
}
