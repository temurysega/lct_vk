export function Brand({ light = false }: { light?: boolean }) {
  return (
    <a
      href="/"
      className={`brand ${light ? "brand-light" : ""}`}
      aria-label="predel — главная"
    >
      <span className="brand-symbol" aria-hidden="true">
        <i />
        <i />
        <i />
      </span>
      <span>predel</span>
    </a>
  );
}
