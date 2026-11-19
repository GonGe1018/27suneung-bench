import { useEffect, useId, useRef, useState } from "react";
import * as Plot from "@observablehq/plot";
import { Download, Maximize2, X } from "lucide-react";
import { dollars, percent, providerStyle, type Result } from "./data";

import { getModelIcon } from "./ModelName";
import type { Theme } from "./theme";

const svgNS = "http://www.w3.org/2000/svg";

function savePlot(svg: SVGSVGElement, scope: string) {
  const copy = svg.cloneNode(true) as SVGSVGElement;
  const width = Number(svg.getAttribute("width")),
    height = Number(svg.getAttribute("height"));
  const exportWidth = Math.max(width, 360);
  const palette = getComputedStyle(svg);
  copy.setAttribute("viewBox", `0 0 ${exportWidth} ${height + 92}`);
  copy.setAttribute("width", String(exportWidth));
  copy.setAttribute("height", String(height + 92));
  const wrapper = document.createElementNS(svgNS, "g");
  wrapper.setAttribute("transform", `translate(${(exportWidth - width) / 2} 40)`);
  while (copy.firstChild) wrapper.append(copy.firstChild);
  const bg = document.createElementNS(svgNS, "rect");
  bg.setAttribute("width", "100%");
  bg.setAttribute("height", "100%");
  bg.setAttribute("fill", palette.getPropertyValue("--plot-surface").trim());
  const caption = (text: string, y: number) => {
    const node = document.createElementNS(svgNS, "text");
    node.setAttribute("x", "18");
    node.setAttribute("y", String(y));
    node.setAttribute("fill", palette.getPropertyValue("--ink").trim());
    node.setAttribute("text-anchor", "start");
    node.setAttribute("font-size", "15");
    node.textContent = text;
    return node;
  };
  copy.replaceChildren(
    bg,
    wrapper,
    caption(scope + " · 비용과 정답률", 25),
    caption("가로축  추정 비용 (USD)   ·   세로축  정답률 (%)", height + 58),
    caption("인간시대의끝이도래했다", height + 80),
  );
  const url = URL.createObjectURL(
    new Blob([new XMLSerializer().serializeToString(copy)], {
      type: "image/svg+xml",
    }),
  );
  const a = document.createElement("a");
  a.href = url;
  a.download = "인간시대의끝이도래했다-" + scope + ".svg";
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function addLabels(
  svg: SVGSVGElement,
  rows: Result[],
  x: (n: number) => number,
  y: (n: number) => number,
  width: number,
  height: number,
  theme: Theme,
) {
  const dots = rows.map((r) => ({
    icon: getModelIcon(r.model.name),
    name: r.model.name,
    note: r.coverageNote,
    x: x(r.cost!),
    y: y(r.accuracy!),
  }));
  const boxes = dots.map((p) => ({ x: p.x - 6, y: p.y - 6, w: 12, h: 12 }));
  const measure = document.createElement("canvas").getContext("2d")!;
  measure.font = "600 12px SUIT, sans-serif";
  for (const p of dots) {
    const iconSpace = p.icon ? 16 : 0;
    const nameWidth = Math.ceil(measure.measureText(p.name).width) + iconSpace;
    const w = Math.max(nameWidth, p.note ? Math.ceil(measure.measureText(p.note).width) : 0);
    const h = p.note ? 30 : 16;
    if (w > width - 52) continue;
    const options = [
      [0, -17],
      [0, 20],
      [w / 2 + 10, 0],
      [-w / 2 - 10, 0],
      [0, -34],
      [0, 35],
    ];
    for (const [dx, dy] of options) {
      const cx = Math.min(width - w / 2 - 5, Math.max(47 + w / 2, p.x + dx));
      const cy = p.y + dy;
      const box = { x: cx - w / 2 - 2, y: cy - h / 2, w: w + 4, h };
      if (
        box.y < 1 ||
        box.y + box.h > height - 26 ||
        boxes.some(
          (b) =>
            box.x < b.x + b.w &&
            box.x + box.w > b.x &&
            box.y < b.y + b.h &&
            box.y + box.h > b.y,
        )
      )
        continue;
      const label = document.createElementNS(svgNS, "g");
      label.setAttribute("class", "plot-label");
      const nameX = cx - nameWidth / 2 + iconSpace;
      const nameY = p.note ? cy - 6 : cy;
      const t = document.createElementNS(svgNS, "text");
      t.setAttribute("x", String(nameX));
      t.setAttribute("y", String(nameY));
      t.setAttribute("text-anchor", "start");
      t.setAttribute("dominant-baseline", "central");
      t.setAttribute("font-size", "12");
      t.setAttribute("font-weight", "600");
      t.setAttribute("fill", "currentColor");
      t.textContent = p.name;
      if (p.icon) {
        const icon = document.createElementNS(svgNS, "image");
        icon.setAttribute("href", p.icon);
        icon.setAttribute("x", String(nameX - iconSpace));
        icon.setAttribute("y", String(nameY - 6));
        icon.setAttribute("width", "12");
        icon.setAttribute("height", "12");
        icon.setAttribute("aria-hidden", "true");
        if (theme === "dark") icon.style.filter = "invert(1)";
        label.append(icon);
      }
      label.append(t);
      if (p.note) {
        const note = t.cloneNode() as SVGTextElement;
        note.setAttribute("x", String(cx));
        note.setAttribute("y", String(cy + 7));
        note.setAttribute("text-anchor", "middle");
        note.setAttribute("font-size", "10");
        note.setAttribute("fill", "var(--muted)");
        note.textContent = p.note;
        label.append(note);
      }
      svg.append(label);
      boxes.push(box);
      break;
    }
  }
}

type CostPlotProps = {
  rows: Result[];
  scope: string;
  theme: Theme;
};

function PlotCard({
  rows,
  scope,
  theme,
  expanded = false,
  onToggle,
}: CostPlotProps & { expanded?: boolean; onToggle: () => void }) {
  const titleId = useId();
  const toggleLabel = expanded ? "확대 그래프 닫기" : "그래프 확대";
  const providers = [...new Set(rows.map((r) => r.model.provider))].sort();
  const hasPoints = rows.some((r) => r.cost !== null && r.accuracy !== null);
  const host = useRef<HTMLDivElement>(null);
  const drawing = useRef<SVGSVGElement | null>(null);
  const [width, setWidth] = useState(0);

  useEffect(() => {
    const el = host.current!;
    const observer = new ResizeObserver(([entry]) =>
      setWidth(Math.floor(entry.contentRect.width)),
    );
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    const el = host.current;
    if (!el || width < 120) return;
    const usable = rows.filter((r) => r.cost !== null && r.accuracy !== null);
    if (!usable.length) {
      el.replaceChildren();
      drawing.current = null;
      return;
    }
    const lowest = Math.min(...usable.map((r) => r.accuracy!));
    const highest = Math.max(...usable.map((r) => r.accuracy!));
    const span = Math.max(1, highest - lowest);
    const floor = Math.max(0, Math.floor(lowest - span * 0.18));
    const ceiling = Math.min(100, Math.ceil(highest + span * 0.15));
    const maxCost = Math.max(0.1, ...usable.map((r) => r.cost!));
    const height = expanded
      ? Math.min(560, Math.max(330, Math.round(width * 0.58)))
      : width < 420 ? 230 : 244;
    const palette = getComputedStyle(el);
    const surface = palette.getPropertyValue("--plot-surface").trim();
    const ink = palette.getPropertyValue("--ink").trim();
    const grid = palette.getPropertyValue("--muted").trim();
    const svg = Plot.plot({
      width,
      height,
      marginLeft: 44,
      marginRight: 25,
      marginTop: 32,
      marginBottom: 29,
      style: {
        background: "transparent",
        color: ink,
        fontFamily: "SUIT, sans-serif",
        fontSize: "12px",
        overflow: "visible",
      },
      x: {
        domain: [0, maxCost * 1.06],
        nice: true,
        label: null,
        ticks: width < 430 ? 4 : 7,
        tickFormat: (d) => "$" + Number(d),
        tickSize: 0,
        tickPadding: 9,
      },
      y: {
        domain: [floor, Math.max(floor + 0.1, ceiling)],
        label: null,
        ticks: 4,
        tickFormat: (d) => Number(d) + "%",
        tickSize: 0,
        tickPadding: 8,
      },
      marks: [
        Plot.gridX({
          ticks: width < 430 ? 4 : 7,
          stroke: grid,
          strokeOpacity: 0.15,
          strokeDasharray: "2,4",
        }),
        Plot.gridY({
          ticks: 4,
          stroke: grid,
          strokeOpacity: 0.25,
          strokeDasharray: "2,4",
        }),
        ...providers.map((provider) =>
          Plot.dot(
            usable.filter((r) => r.model.provider === provider),
            {
              x: "cost",
              y: "accuracy",
              r: 6.5,
              symbol: providerStyle(provider, theme).symbol,
              fill: providerStyle(provider, theme).color,
              stroke: surface,
              strokeWidth: 2,
              title: (r) =>
                r.model.name +
                (r.coverageNote ? "\n" + r.coverageNote : "") +
                "\n" +
                percent(r.accuracy) +
                " · " +
                dollars(r.cost) +
                "\n" +
                r.correct +
                " / " +
                r.answered +
                " 정답",
            },
          ),
        ),
      ],
    }) as SVGSVGElement & ReturnType<typeof Plot.plot>;
    svg.setAttribute("role", "group");
    svg.setAttribute("aria-label", scope + " 모델별 추정 비용과 정답률");
    addLabels(
      svg,
      usable,
      (n) => svg.scale("x")!.apply!(n),
      (n) => svg.scale("y")!.apply!(n),
      width,
      height,
      theme,
    );
    el.replaceChildren(svg);
    drawing.current = svg;
    return () => {
      svg.remove();
      drawing.current = null;
    };
  }, [rows, scope, width, theme, expanded]);

  return (
    <section className="analysis-plot" aria-labelledby={titleId}>
      <div className="card-heading">
        <div>
          <h2 id={titleId}>비용과 정답률</h2>
          <p>{expanded ? scope : "선택한 범위의 모델별 추정 비용"}</p>
        </div>
        <div className="plot-actions">
          <button
            className="icon-button"
            disabled={!hasPoints}
            title="그래프 SVG 저장"
            aria-label="그래프 SVG 저장"
            onClick={() => drawing.current && savePlot(drawing.current, scope)}
          >
            <Download size={17} />
          </button>
          <button
            className="icon-button"
            disabled={!expanded && !hasPoints}
            title={toggleLabel}
            aria-label={toggleLabel}
            aria-haspopup={expanded ? undefined : "dialog"}
            autoFocus={expanded}
            onClick={onToggle}
          >
            {expanded ? <X size={19} /> : <Maximize2 size={17} />}
          </button>
        </div>
      </div>
      <div className="plot-y">정답률 (%)</div>
      {!hasPoints && <p>비용과 정답률이 모두 집계된 모델이 없습니다.</p>}
      <div className="plot-host" ref={host}></div>
      <div className="plot-foot">
        <div className="legend">
          {providers
            .filter((p) => rows.some((r) => r.model.provider === p))
            .map((p) => (
              <span key={p}>
                <i
                  className={"point " + p}
                  style={{ backgroundColor: providerStyle(p, theme).color }}
                />
                {p}
              </span>
            ))}
        </div>
        <span>추정 비용 (USD)</span>
      </div>
    </section>
  );
}

export function CostPlot(props: CostPlotProps) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [expanded, setExpanded] = useState(false);
  return (
    <>
      <PlotCard
        {...props}
        onToggle={() => {
          dialog.current?.showModal();
          setExpanded(true);
        }}
      />
      <dialog
        ref={dialog}
        className="plot-dialog"
        aria-label="비용과 정답률 확대 그래프"
        onClose={() => setExpanded(false)}
        onClick={(event) => {
          if (event.target === event.currentTarget) event.currentTarget.close();
        }}
      >
        {expanded && (
          <PlotCard {...props} expanded onToggle={() => dialog.current?.close()} />
        )}
      </dialog>
    </>
  );
}
