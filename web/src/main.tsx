import { createRoot } from "react-dom/client";
import App from "./App";
import { getDatasetUrl, type Dataset } from "./data";
import "./styles.css";
import "./status.css";

const root = createRoot(document.getElementById("root")!);
const year = new URLSearchParams(location.search).get("year") === "2026" ? 2026 : 2027;
let displayed = false;
let loading = false;
async function refresh() {
  if (loading) return;
  loading = true;
  try {
    const response = await fetch(getDatasetUrl(year), { cache: "no-cache" });
    if (!response.ok) throw new Error("Dataset unavailable");
    const data: Dataset = await response.json();
    root.render(<App data={data} />);
    displayed = true;
  } catch {
    if (!displayed)
      root.render(
        <main className="load-error">
          <h1>성적을 불러올 수 없습니다.</h1>
          <button onClick={refresh}>다시 시도</button>
        </main>,
      );
  } finally {
    loading = false;
  }
}
void refresh();
if (year === 2027) {
  window.setInterval(() => {
    if (!document.hidden) void refresh();
  }, 60000);
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) void refresh();
  });
}
