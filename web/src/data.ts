import type { Theme } from "./theme";

export const getDatasetUrl = (year: number) =>
  import.meta.env.BASE_URL +
  (year === 2026
    ? "data/2026/csat/pilot.json"
    : "data/2027/csat/benchmark.json");

export const areas = {
  korean: "국어",
  math: "수학",
  english: "영어",
  science1: "과학탐구Ⅰ",
  science2: "과학탐구Ⅱ",
  social: "사회탐구",
} as const;

export type Area = keyof typeof areas;
export type Provider = string;
type Part = { label: string; area: Area; count: number };
type Score = {
  effort?: string;
  answered: number;
  correct: number;
  cost: number | null;
  billedEstimate: number | null;
};
export type Model = {
  name: string;
  short: string;
  provider: Provider;
  effort: string;
  scope?: string;
  expectedParts?: string[];
  parts: Record<string, Score>;
};
type AreaProgress = {
  readyParts: number;
  totalParts: number;
  scoredModels: number;
  plannedModels: number;
};

export type Dataset = {
  year: number;
  exam: string;
  state?: "awaiting" | "running" | "complete";
  progress?: Partial<Record<Area, AreaProgress>>;
  completion?: {
    plannedBundles: number;
    scoredBundles: number;
    publishedBundles: number;
  };
  parts: Record<string, Part>;
  models: Record<string, Model>;
};
export type Result = {
  id: string;
  effort: string;
  model: Model;
  answered: number;
  correct: number;
  count: number;
  cost: number | null;
  billed: number | null;
  accuracy: number | null;
  rank: number | null;
};

export const scopeNames: Record<string, string> = {
  core: "국영수",
  all: "전체 영역",
  ...areas,
};
const core = new Set<Area>(["korean", "math", "english"]);

export function partIds(data: Dataset, scope: string): string[] {
  return Object.entries(data.parts)
    .filter(
      ([id, p]) =>
        scope === "all" ||
        (scope === "core" && core.has(p.area)) ||
        p.area === scope ||
        scope === "part/" + id,
    )
    .map(([id]) => id);
}

export function scopeName(data: Dataset, scope: string) {
  return scopeNames[scope] ?? data.parts[scope.slice(5)]?.label ?? "국영수";
}

export function measure(
  id: string,
  model: Model,
  parts: string[],
  data: Dataset,
): Result | null {
  if (!parts.length || parts.some((part) => !model.parts[part])) return null;
  let correct = 0,
    answered = 0,
    count = 0;
  let cost: number | null = 0,
    billed: number | null = 0;
  for (const part of parts) {
    const score = model.parts[part];
    correct += score.correct;
    answered += score.answered;
    count += data.parts[part].count;
    cost = cost === null || score.cost === null ? null : cost + score.cost;
    billed =
      billed === null || score.billedEstimate === null
        ? null
        : billed + score.billedEstimate;
  }
  return {
    id,
    model,
    correct,
    answered,
    count,
    cost,
    billed,
    effort: [...new Set(parts.map((part) => model.parts[part].effort ?? model.effort))].join(" / "),
    accuracy: answered ? (100 * correct) / answered : null,
    rank: null,
  };
}

export function ranking(data: Dataset, parts: string[]) {
  const results = Object.entries(data.models)
    .map(([id, model]) => measure(id, model, parts, data))
    .filter((r): r is Result => r !== null)
    .sort(
      (a, b) =>
        (b.accuracy ?? -1) - (a.accuracy ?? -1) ||
        a.model.name.localeCompare(b.model.name),
    );
  let last: Result | undefined;
  results.forEach((r, i) => {
    if (r.answered) r.rank = last?.accuracy === r.accuracy ? last.rank : i + 1;
    last = r;
  });
  return results;
}

export const dollars = (v: number | null) =>
  v === null ? "미집계" : "$" + v.toFixed(2);
export const percent = (v: number | null) =>
  v === null ? "미응답" : v.toFixed(2) + "%";
const providerStyles: Record<
  string,
  {
    light: string;
    dark: string;
    symbol: "circle" | "diamond" | "square" | "triangle" | "cross" | "star";
  }
> = {
  OpenAI: { light: "#966900", dark: "#f3c264", symbol: "circle" },
  Anthropic: { light: "#a54933", dark: "#ff9d83", symbol: "diamond" },
  Google: { light: "#216995", dark: "#85c5f1", symbol: "square" },
  Qwen: { light: "#734fa2", dark: "#c9a8f0", symbol: "triangle" },
  DeepSeek: { light: "#365ca8", dark: "#9eafff", symbol: "cross" },
  "Moonshot AI": { light: "#784b32", dark: "#e6b699", symbol: "diamond" },
  "Z.ai": { light: "#9a4578", dark: "#eba4c9", symbol: "star" },
  Muse: { light: "#317167", dark: "#8ccbc1", symbol: "square" },
};
export function providerStyle(name: string, theme: Theme) {
  const style = providerStyles[name] ?? { light: "#525961", dark: "#d6d9df", symbol: "circle" };
  return { color: style[theme], symbol: style.symbol };
}
