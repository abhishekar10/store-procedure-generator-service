export default function ThemeToggle({ theme, onToggle }) {
  const isDark = theme === "dark";
  return (
    <button className="theme-toggle" onClick={onToggle} title={isDark ? "Switch to light theme" : "Switch to dark theme"}>
      {isDark ? "☀️" : "🌙"}
    </button>
  );
}
