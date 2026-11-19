import type { CSSProperties } from "react";
import { percent, providerStyle, type Model, type Result } from "./data";
import { ModelName } from "./ModelName";
import type { Theme } from "./theme";
import "./accuracy.css";

type AccuracyMark = Pick<Result, "id" | "model" | "accuracy" | "coverageNote" | "points"> & {
  answered?: number;
  count?: number;
};

const ticks = [100, 75, 50, 25, 0];

export function AccuracyChart({
  rows,
  unpublished,
  pending,
  theme,
}: {
  rows: Result[];
  unpublished: [string, Model][];
  pending: boolean;
  theme: Theme;
}) {
  const marks: AccuracyMark[] = [
    ...rows.filter((row) => row.accuracy !== null),
    ...unpublished.map(([id, model]) => ({ id, model, accuracy: null })),
  ];
  if (!marks.length) return null;

  return (
    <section className="accuracy-card" aria-labelledby="accuracy-title">
      <header className="accuracy-heading">
        <h2 id="accuracy-title">모델별 정답률</h2>
        <p>
          {pending ? "성적 공개 후 표시됩니다." : "정답률 미응답 제외"}
          {marks.some((mark) => mark.points) && " · 원점수/만점"}
          {!pending && unpublished.length > 0 && " · 빗금은 진행 예정"}
        </p>
      </header>
      <div className="accuracy-chart">
        <div className="accuracy-axis" aria-hidden="true">
          {ticks.map((tick) => (
            <span key={tick} style={{ top: (100 - tick) + "%" }}>{tick}%</span>
          ))}
        </div>
        <div className="accuracy-scroll">
          <ol className="accuracy-series" role="list">
            {marks.map(({ id, model, accuracy, answered, count, coverageNote, points }) => {
              const placeholder = accuracy === null;
              const style = {
                "--score": (accuracy ?? 0) + "%",
                "--score-ratio": (accuracy ?? 0) / 100,
                "--series-color": providerStyle(model.provider, theme).color,
              } as CSSProperties;
              return (
                <li
                  className="accuracy-mark"
                  data-pending={placeholder || undefined}
                  key={id}
                  style={style}
                >
                  <ModelName id={id} name={model.name} note={coverageNote} />
                  <div className="accuracy-track" aria-hidden="true">
                    <div className={placeholder ? "pending-fill" : "accuracy-bar"} />
                  </div>
                  <strong className="accuracy-value" title={placeholder ? "진행 예정" : undefined}>
                    {placeholder ? "—" : percent(accuracy)}
                  </strong>
                  {answered !== undefined && (
                    <div className="accuracy-stats">
                      {points && (
                        <span className="accuracy-points" title="원점수 / 만점">
                          {points.earned}/{points.max}점
                        </span>
                      )}
                      <span className="accuracy-coverage" title="응답 문항 / 전체 문항">
                        {answered}/{count} 응답
                      </span>
                    </div>
                  )}
                </li>
              );
            })}
          </ol>
        </div>
      </div>
    </section>
  );
}
