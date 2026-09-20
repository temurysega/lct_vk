import { useRef, useState, type FormEvent } from "react";
import {
  motion,
  useReducedMotion,
  useScroll,
  useTransform,
} from "motion/react";
import {
  ArrowDown,
  ArrowRight,
  ArrowUpRight,
  Check,
  CheckCheck,
  FileSliders,
  Layers3,
  Menu,
  Plus,
  Sparkles,
  X,
} from "lucide-react";
import type { Draft, User } from "../types";
import { Brand } from "../components/Brand";
import { DemoSlide } from "../components/DemoSlide";

const examples = [
  {
    title: "Квартальный отчёт",
    brief:
      "Подготовить квартальный отчёт для руководства: результаты, ключевые показатели, выводы и план на следующий квартал. Использовать подтверждённые цифры из моих материалов.",
  },
  {
    title: "Новый продукт",
    brief:
      "Презентация нового продукта для команды: проблема пользователя, наше решение, сценарии использования, преимущества и этапы запуска.",
  },
  {
    title: "Предложение клиенту",
    brief:
      "Коммерческое предложение: задача клиента, наше решение, этапы работы, ожидаемые результаты и следующий шаг.",
  },
];
const variants = ["Сбалансированный", "Несколько блоков", "Единый акцент"];

export function Landing({
  user,
  onStart,
}: {
  user: User | null;
  onStart: (mode?: "login" | "register", draft?: Draft) => void;
}) {
  const [menuOpen, setMenuOpen] = useState(false);
  const [variant, setVariant] = useState(0);
  const [brief, setBrief] = useState("");
  const [count, setCount] = useState(10);
  const briefInput = useRef<HTMLTextAreaElement>(null);
  const hero = useRef<HTMLElement>(null);
  const reduced = useReducedMotion();
  const { scrollYProgress } = useScroll({
    target: hero,
    offset: ["start start", "end start"],
  });
  const y = useTransform(scrollYProgress, [0, 1], [0, 120]);
  const rotate = useTransform(scrollYProgress, [0, 1], [-7, 6]);
  const reveal = {
    initial: reduced ? (false as const) : { opacity: 0, y: 25 },
    whileInView: { opacity: 1, y: 0 },
    viewport: { once: true, amount: 0.15 },
    transition: { duration: reduced ? 0 : 0.55 },
  };
  function submit(event: FormEvent) {
    event.preventDefault();
    if (brief.trim()) onStart("register", { content: brief.trim(), count });
  }
  return (
    <div className="landing">
      <a className="skip-link" href="#main">
        К содержимому
      </a>
      <header className="floating-header">
        <div className="nav-island">
          <Brand />
          <nav aria-label="Главная навигация">
            <a href="#possibilities">Возможности</a>
            <a href="#how">Как это работает</a>
            <a href="#create">Создать</a>
          </nav>
          <button
            className="menu-toggle icon-button"
            aria-label={menuOpen ? "Закрыть меню" : "Открыть меню"}
            aria-expanded={menuOpen}
            onClick={() => setMenuOpen(!menuOpen)}
          >
            {menuOpen ? <X /> : <Menu />}
          </button>
        </div>
        <div className="account-island">
          <button
            className="login-link"
            data-auth="login"
            title={user?.username}
            onClick={() => onStart("login")}
          >
            {user?.username || "Войти"}
          </button>
          <button
            className="button ink small"
            data-auth="register"
            onClick={() => onStart()}
          >
            {user ? "В студию" : "Начать"}
            <ArrowUpRight size={16} />
          </button>
        </div>
        {menuOpen && (
          <nav className="mobile-menu" aria-label="Мобильная навигация">
            {[
              ["#possibilities", "Возможности"],
              ["#how", "Как это работает"],
              ["#create", "Создать презентацию"],
            ].map(([href, text]) => (
              <a key={href} href={href} onClick={() => setMenuOpen(false)}>
                {text}
                <ArrowUpRight size={18} />
              </a>
            ))}
          </nav>
        )}
      </header>
      <main id="main">
        <section className="hero" ref={hero}>
          <div className="hero-grid" aria-hidden="true" />
          <motion.div
            className="hero-art"
            style={reduced ? {} : { y, rotate }}
            initial={{ opacity: 0, scale: 0.88 }}
            animate={{ opacity: 1, scale: 1 }}
            transition={{ duration: reduced ? 0 : 0.85 }}
          >
            <img
              src="/assets/images/predel-keychain.png"
              alt="Объёмная связка синих и хромированных брелоков: знак predel, слайд и искра"
              width="1024"
              height="1536"
              fetchPriority="high"
            />
          </motion.div>
          <motion.div
            className="hero-copy"
            initial={{ opacity: 0, y: 22 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{
              delay: reduced ? 0 : 0.15,
              duration: reduced ? 0 : 0.65,
            }}
          >
            <p className="eyebrow">
              <span className="blue-dot" /> ИДЕЯ ВНУТРИ. ВАШ СТИЛЬ СНАРУЖИ.
            </p>
            <h1>
              Ваши идеи.
              <br />
              <span>Впечатляющая форма.</span>
            </h1>
            <p className="hero-description">
              От первого тезиса до готовой презентации.
              <br className="mobile-break" /> В вашем стиле. С помощью predel.
            </p>
            <div className="hero-actions">
              <a href="#create" className="button primary">
                Создать презентацию
                <ArrowUpRight size={19} />
              </a>
              <a href="#possibilities" className="text-link">
                Посмотреть возможности
                <ArrowDown size={15} />
              </a>
            </div>
          </motion.div>
          <div className="hero-bottom">
            <span>СОЗДАНО ДЛЯ ВАШИХ БОЛЬШИХ ИДЕЙ</span>
            <a href="#possibilities" aria-label="Прокрутить к возможностям">
              <ArrowDown size={18} />
            </a>
            <span>PPTX / PDF / HTML</span>
          </div>
        </section>

        <section className="intro-statement section-pad" id="possibilities">
          <motion.div {...reveal}>
            <p className="eyebrow">НЕ НАЧИНАЙТЕ С ПУСТОГО СЛАЙДА</p>
            <h2>
              Смысл — ваш.
              <span className="inline-shape" aria-hidden="true">
                <FileSliders />
              </span>
              <br />
              Цвета, шрифты, характер —<br />
              <span className="text-muted">тоже ваши.</span>{" "}
              <span className="inline-blue">Вёрстка — наша.</span>
            </h2>
            <p className="section-description">
              Загрузите фирменный шаблон и материалы.
              <br />
              predel соберёт три композиции, сохранив язык вашего бренда.
            </p>
          </motion.div>
        </section>

        <motion.section
          {...reveal}
          className="showcase section-pad"
          aria-labelledby="showcaseTitle"
        >
          <div className="section-heading">
            <div>
              <p className="eyebrow">ОДНА ИСТОРИЯ. ТРИ ВЗГЛЯДА.</p>
              <h2 id="showcaseTitle">
                Выбирайте форму.
                <br />
                <span className="text-muted">Сохраняйте смысл.</span>
              </h2>
            </div>
            <p>
              Не случайные стили, а разные способы
              <br />
              рассказать вашу историю.
            </p>
          </div>
          <div className="showcase-browser">
            <div className="showcase-toolbar">
              <div className="browser-dots" aria-hidden="true">
                <i />
                <i />
                <i />
              </div>
              <span>
                <span className="mini-brand">p</span> Новая презентация
              </span>
              <span className="showcase-label">Пример оформления</span>
            </div>
            <div className="showcase-body">
              <aside className="showcase-rail" aria-label="Примеры композиций">
                {variants.map((label, index) => (
                  <button
                    key={label}
                    className={variant === index ? "selected" : ""}
                    onClick={() => setVariant(index)}
                    aria-label={label}
                    aria-pressed={variant === index}
                  >
                    <span>0{index + 1}</span>
                    <DemoSlide variant={index} compact />
                  </button>
                ))}
              </aside>
              <div className="showcase-main">
                <div className="demo-stage">
                  <motion.div
                    key={variant}
                    initial={reduced ? false : { opacity: 0, y: 12 }}
                    animate={{ opacity: 1, y: 0 }}
                    transition={{ duration: 0.3 }}
                  >
                    <DemoSlide variant={variant} />
                  </motion.div>
                </div>
                <div className="showcase-status">
                  <span>
                    <CheckCheck size={15} /> Один стиль. Разные композиции.
                  </span>
                  <span>01 — 03</span>
                </div>
              </div>
            </div>
          </div>
          <div
            className="variant-switch"
            role="group"
            aria-label="Выбор примера"
          >
            {variants.map((label, index) => (
              <button
                key={label}
                onClick={() => setVariant(index)}
                className={variant === index ? "active" : ""}
                aria-pressed={variant === index}
              >
                {index === 0 ? (
                  <Layers3 size={16} />
                ) : index === 1 ? (
                  <FileSliders size={16} />
                ) : (
                  <Sparkles size={16} />
                )}
                {label}
              </button>
            ))}
          </div>
        </motion.section>

        <section className="workflow section-pad" id="how">
          <motion.div {...reveal} className="section-heading">
            <div>
              <p className="eyebrow">МЕНЬШЕ РУТИНЫ. БОЛЬШЕ ВАШЕГО.</p>
              <h2>
                От «есть идея»
                <br />
                до «можно показывать».
              </h2>
            </div>
            <a
              href="#create"
              className="round-link"
              aria-label="Создать презентацию"
            >
              <ArrowUpRight />
            </a>
          </motion.div>
          <div className="workflow-grid">
            {[
              {
                number: "01",
                title: "Задайте основу.",
                text: "Фирменный шаблон, ваши факты и нужное число слайдов. Всё начинается с ваших материалов.",
                icon: <FileSliders />,
                className: "import-visual",
              },
              {
                number: "02",
                title: "Найдите свой вариант.",
                text: "Три композиции одного содержания. Сравните их и выберите, что лучше рассказывает вашу историю.",
                icon: <Layers3 />,
                className: "layers-visual",
              },
              {
                number: "03",
                title: "Доведите до точности.",
                text: "Проверьте замечания к вёрстке, выберите исправления и скачайте редактируемый результат.",
                icon: <CheckCheck />,
                className: "audit-visual",
              },
            ].map((step, index) => (
              <motion.article
                {...reveal}
                transition={{
                  duration: reduced ? 0 : 0.5,
                  delay: reduced ? 0 : index * 0.08,
                }}
                key={step.number}
              >
                <div
                  className={`workflow-visual ${step.className}`}
                  aria-hidden="true"
                >
                  {index === 0 ? (
                    <>
                      <div className="file-object">
                        <span>PPTX</span>
                        <FileSliders size={50} />
                        <small>ваш стиль</small>
                      </div>
                      <div className="file-object second">
                        <span>БРИФ</span>
                        <i />
                        <i />
                        <i />
                      </div>
                    </>
                  ) : index === 1 ? (
                    <div className="stack-object">
                      <i />
                      <i />
                      <i>
                        <span>p</span>
                      </i>
                    </div>
                  ) : (
                    <div className="audit-object">
                      <span>
                        <Check size={16} /> Шрифт из шаблона
                      </span>
                      <span>
                        <Check size={16} /> Цвета сохранены
                      </span>
                      <span>
                        <Check size={16} /> Всё на своих местах
                      </span>
                      <b>
                        <CheckCheck size={32} />
                      </b>
                    </div>
                  )}
                </div>
                <div className="workflow-step">
                  <span>{step.number}</span>
                  {step.icon}
                </div>
                <h3>{step.title}</h3>
                <p>{step.text}</p>
              </motion.article>
            ))}
          </div>
        </section>

        <section id="create" className="create-section section-pad">
          <motion.div {...reveal} className="create-inner">
            <div className="create-title">
              <p className="eyebrow">
                <Sparkles size={14} /> ВАШ СЛЕДУЮЩИЙ СЛАЙД
              </p>
              <h2>
                А что покажете
                <br />
                <span>вы?</span>
              </h2>
              <p>
                Начните с мысли.
                <br />
                Остальное соберём в студии.
              </p>
            </div>
            <div className="brief-area">
              <form id="quickStart" onSubmit={submit}>
                <label htmlFor="brief">О чём ваша презентация?</label>
                <textarea
                  ref={briefInput}
                  id="brief"
                  rows={4}
                  maxLength={20000}
                  value={brief}
                  onChange={(event) => setBrief(event.target.value)}
                  placeholder="Новый продукт, итоги квартала, большая идея… Расскажите, что важно донести."
                  required
                />
                <div className="brief-toolbar">
                  <label className="count-select" htmlFor="quickCount">
                    <FileSliders size={17} />
                    <select
                      id="quickCount"
                      value={count}
                      onChange={(e) => setCount(Number(e.target.value))}
                      aria-label="Количество слайдов"
                    >
                      {[10, 12, 15, 20].map((n) => (
                        <option key={n} value={n}>
                          {n} слайдов
                        </option>
                      ))}
                    </select>
                  </label>
                  <button className="button ink" type="submit">
                    В студию
                    <ArrowUpRight size={18} />
                  </button>
                </div>
              </form>
              <div className="prompt-examples">
                {examples.map((example) => (
                  <button
                    type="button"
                    data-prompt
                    key={example.title}
                    onClick={() => {
                      setBrief(example.brief);
                      briefInput.current?.focus();
                    }}
                  >
                    <Plus size={13} />
                    {example.title}
                  </button>
                ))}
              </div>
              <p className="brief-note">
                Шаблон и файлы можно добавить на следующем шаге.
              </p>
            </div>
          </motion.div>
        </section>
        <section className="faq section-pad" aria-labelledby="faqTitle">
          <h2 id="faqTitle">Пара деталей.</h2>
          <div>
            {[
              [
                "Можно использовать свой шаблон?",
                "Да. Добавьте PPTX или PDF: predel извлечёт цвета, шрифты и макеты и использует их при создании слайдов. Оригинал не изменяется.",
              ],
              [
                "Можно редактировать результат?",
                "Да. PPTX содержит нативные объекты PowerPoint. При доступном экспорте также можно скачать PDF и HTML.",
              ],
              [
                "Что нужно для регистрации?",
                "Только имя пользователя, пароль и должность. Ваши шаблоны и презентации доступны только вашему аккаунту.",
              ],
            ].map(([question, answer]) => (
              <details key={question}>
                <summary>
                  {question}
                  <Plus size={19} />
                </summary>
                <p>{answer}</p>
              </details>
            ))}
          </div>
        </section>
      </main>
      <footer className="footer section-pad">
        <div className="footer-top">
          <p>
            Меньше возни со слайдами.
            <br />
            Больше пространства для идей.
          </p>
          <a href="#create" className="button ink">
            Начнём?
            <ArrowUpRight size={18} />
          </a>
        </div>
        <a className="footer-wordmark" href="/" aria-label="predel — главная">
          predel<span>↗</span>
        </a>
        <div className="footer-bottom">
          <span>© 2026 predel</span>
          <span>Ваш смысл. Ваша форма.</span>
          <a href="#main">
            Наверх
            <ArrowRight size={14} />
          </a>
        </div>
      </footer>
    </div>
  );
}
