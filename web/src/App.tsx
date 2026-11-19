import { ModelName } from "./ModelName";
import {
  Fragment,
  lazy,
  Suspense,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import {
  ChevronRight,
  Download,
  Moon,
  Sun,
  X,
} from "lucide-react";
import {
  areas,
  getDatasetUrl,
  dollars,
  measure,
  partIds,
  percent,
  ranking,
  scopeName,
  type Area,
  type Dataset,
  type Model,
  type Result,
} from "./data";
import { useTheme } from "./theme";
import {
  ExamNotice,
  ExamRoster,
  YearPicker,
} from "./ExamStatus";
import { AccuracyChart } from "./AccuracyChart";
import { Dropdown, DropdownGroup, DropdownOption } from "./Dropdown";
const CostPlot = lazy(() =>
  import("./CostPlot").then((m) => ({ default: m.CostPlot })),
);

function MethodDialog({ close, year }: { close: () => void; year: number }) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const dialog = ref.current!;
    dialog.showModal();
    return () => dialog.close();
  }, []);
  return (
    <dialog
      ref={ref}
      aria-labelledby="method-title"
      onCancel={close}
      onClick={(e) => {
        if (e.target === ref.current) close();
      }}
    >
      <section>
        <div className="dialog-head">
          <h2 id="method-title">평가 방법</h2>
          <button className="icon-button" aria-label="닫기" onClick={close}>
            <X size={20} />
          </button>
        </div>
        <dl className="methods">
          <dt>시험지</dt>
          <dd>
            시험지 페이지를 180 DPI 이미지로 변환해 25묶음으로 비교합니다. 같은
            묶음은 입력 해시로 대조합니다. 영어 듣기에는 공식 대본을 함께
            제공합니다.{" "}
            {year === 2027 && "Jev 1.14에는 시험지 이미지의 긴 변을 1568px로 줄여 제공했습니다."}
          </dd>
          <dt>응시 범위</dt>
          <dd>
            국영수는 국어 56문항, 수학 46문항, 영어 45문항입니다. 선택과목을
            모두 포함하고 공통 문항은 한 번만 셉니다. 전체 영역은 탐구 17과목을
            더한 487문항입니다.
          </dd>
          <dt>순위와 미응답</dt>
          <dd>
            선택한 묶음에 모두 실행 기록이 있는 모델을 비교합니다. 정답률은 정답
            수 ÷ 응답 수이며 동점은 공동 순위입니다. 응답이 하나도 없으면 순위를
            부여하지 않습니다.
          </dd>
          <dt>원점수와 만점</dt>
          <dd>
            맞힌 문항의 배점을 더해 원점수를 계산합니다. 만점은 해당 모델의 응시
            범위 전체 배점입니다.{" "}
            {year === 2027 && "Jev의 수학 점수는 객관식 기준입니다."}
          </dd>
          <dt>추정 비용</dt>
          <dd>
            각 실행에 기록한 달러 추정치에 재시도 비용을 포함합니다. 무료 호출은
            당시 유료 단가 상당액으로 환산했습니다. 모델 행을 펼치면 환산 전
            비용도 볼 수 있습니다.
          </dd>
          <dt>실험 시점</dt>
          <dd>
            {year === 2026
              ? "2026학년도 기출문제로 측정했습니다. 추론 강도와 재시도 조건은 실행별로 다르며, 학습 중 문항 노출 여부는 확인할 수 없습니다."
              : "2027학년도 공개 시험을 대상으로 합니다. 실행 설정의 자동 재시도는 0회입니다."}
          </dd>
        </dl>
      </section>
    </dialog>
  );
}

function CostBars({
  rows,
  planned,
}: {
  rows: Result[];
  planned?: [string, Model][];
}) {
  const sorted = planned
    ? planned.map(([id, model]) => ({ id, model, cost: null, coverageNote: undefined }))
    : rows.filter((row) => row.cost !== null).sort((a, b) => a.cost! - b.cost!);
  const max = sorted.at(-1)?.cost ?? 1;
  return (
    <section className="cost-card" aria-labelledby="cost-title">
      <div className="card-heading">
        <div>
          <h2 id="cost-title">모델별 응시 비용</h2>
          <p>선택한 문항의 추정 비용 (USD)</p>
        </div>
      </div>
      <ol className="cost-list">
        {sorted.map((r) => (
          <li key={r.id}>
            <ModelName id={r.id} name={r.model.name} note={r.coverageNote} />
            <div
              className={planned ? "bar-track pending-fill" : "bar-track"}
              aria-hidden="true"
            >
              {r.cost !== null && (
                <div
                  style={{ width: (max > 0 ? (100 * r.cost) / max : 0) + "%" }}
                />
              )}
            </div>
            <strong>{planned ? "—" : dollars(r.cost)}</strong>
          </li>
        ))}
      </ol>
    </section>
  );
}

function DeferredRow({
  id,
  name,
  columns,
}: {
  id: string;
  name: string;
  columns: number;
}) {
  const [tip, setTip] = useState<"closed" | "preview" | "pinned">("closed");
  const showPreview = () =>
    setTip((mode) => (mode === "closed" ? "preview" : mode));
  return (
    <tr
      className="deferred"
      onPointerEnter={(event) => {
        if (event.pointerType === "mouse") showPreview();
      }}
      onPointerLeave={() =>
        setTip((mode) => (mode === "preview" ? "closed" : mode))
      }
    >
      <td className="rank-cell">—</td>
      <td className="model-cell">
        <ModelName id={id} name={name} />
      </td>
      <td colSpan={columns - 2}>
        <span className="hint-wrap">
          <button
            aria-label={name + " 추후 진행 예정입니다"}
            aria-describedby={"hint-" + id}
            aria-expanded={tip !== "closed"}
            onFocus={(e) => {
              if (e.currentTarget.matches(":focus-visible")) showPreview();
            }}
            onBlur={() => setTip("closed")}
            onClick={() =>
              setTip((mode) => (mode === "pinned" ? "closed" : "pinned"))
            }
            onKeyDown={(event) => {
              if (event.key === "Escape") setTip("closed");
            }}
          >
            진행 예정
          </button>
          <span
            id={"hint-" + id}
            role="tooltip"
            className={tip !== "closed" ? "show" : ""}
          >
            추후 진행 예정입니다
          </span>
        </span>
      </td>
    </tr>
  );
}

function PartialResultRow({
  id,
  model,
  parts,
  data,
  columns,
  totalParts,
}: {
  id: string;
  model: Model;
  parts: string[];
  data: Dataset;
  columns: number;
  totalParts: number;
}) {
  const [expanded, setExpanded] = useState(false);
  const detailsId = `partial-results-${id}`;
  return (
    <>
      <tr className="deferred">
        <td className="rank-cell">—</td>
        <td className="model-cell">
          <ModelName id={id} name={model.name} />
        </td>
        <td colSpan={columns - 2}>
          <button
            className="partial-result-button"
            aria-expanded={expanded}
            aria-controls={detailsId}
            aria-label={`${model.name} 부분 성적 ${parts.length}/${totalParts}묶음`}
            onClick={() => setExpanded((open) => !open)}
          >
            부분 공개 <span>{parts.length}/{totalParts}</span>
            <ChevronRight className={expanded ? "turn" : ""} size={15} />
          </button>
        </td>
      </tr>
      <tr className="detail-row" id={detailsId} hidden={!expanded}>
        <td colSpan={columns}>
          <dl className="detail-list">
            {parts.map((part) => {
              const score = model.parts[part];
              return (
                <div key={part}>
                  <dt>{data.parts[part].label}</dt>
                  <dd>
                    {score.answered ? `${score.correct} / ${score.answered}` : "미응답"}
                  </dd>
                </div>
              );
            })}
          </dl>
        </td>
      </tr>
    </>
  );
}

function ResultsTable({
  rows,
  data,
  parts,
  unpublished,
  pending,
}: {
  rows: Result[];
  data: Dataset;
  parts: string[];
  unpublished: [string, Model][];
  pending: boolean;
}) {
  const [view, setView] = useState<"rank" | "matrix">("rank");
  const [order, setOrder] = useState("rank");
  const [expanded, setExpanded] = useState<string | null>(null);
  const ordered = [...rows];
  if (order === "cost")
    ordered.sort((a, b) => (a.cost ?? Infinity) - (b.cost ?? Infinity));
  if (order === "coverage") ordered.sort((a, b) => b.answered - a.answered);
  const areaKeys = (Object.keys(areas) as Area[]).filter((a) =>
    parts.some((p) => data.parts[p].area === a),
  );
  const columns = view === "rank" ? 7 : 2 + areaKeys.length;
  return (
    <section className="records" aria-labelledby="results-title">
      <div className="records-head">
        <div className="title-count">
          <h2 id="results-title">모델 순위</h2>
        </div>
        <div className="segmented">
          <button
            aria-pressed={view === "rank"}
            onClick={() => setView("rank")}
          >
            순위표
          </button>
          <button
            aria-pressed={view === "matrix"}
            onClick={() => setView("matrix")}
          >
            영역별 성적
          </button>
        </div>
      </div>
      <div className="table-note">
        <span>
          {pending
            ? "성적 공개 후 순위와 점수가 표시됩니다."
            : view === "rank"
              ? "미응답 제외, 동점은 공동 순위"
              : "각 칸은 정답 수 / 응답 수입니다."}
        </span>
        {!pending && (
          <Dropdown
            className="sort-picker"
            label="모델 정렬"
            value={order}
            onValueChange={setOrder}
            align="end"
          >
            <DropdownOption value="rank">정답률순</DropdownOption>
            <DropdownOption value="cost">비용 낮은 순</DropdownOption>
            <DropdownOption value="coverage">응답 많은 순</DropdownOption>
          </Dropdown>
        )}
      </div>
      <div
        className="table-scroll"
        role="region"
        aria-label="모델별 성적표"
        tabIndex={0}
      >
        <table className={view === "rank" ? "rank-table" : "matrix-table"}>
          <thead>
            <tr>
              <th className="rank-cell" scope="col">
                순위
              </th>
              <th scope="col" className="model-cell">
                모델
              </th>
              {view === "rank" ? (
                <>
                  <th>정답률</th>
                  <th className="secondary-cell">정답 / 응답</th>
                  <th className="secondary-cell">미응답</th>
                  <th className="secondary-cell">추정 비용</th>
                  <th className="secondary-cell">추론 강도</th>
                </>
              ) : (
                areaKeys.map((a) => <th key={a}>{areas[a]}</th>)
              )}
            </tr>
          </thead>
          <tbody>
            {ordered.map((r) => (
              <Fragment key={r.id}>
                <tr>
                  <td className={"rank-cell " + (r.rank === 1 ? "first" : "")}>
                    {r.rank === null ? "—" : r.rank}
                  </td>
                  <td className="model-cell">
                    <div className="model-actions">
                      <ModelName id={r.id} name={r.model.name} note={r.coverageNote} />
                      <button
                        className="model-button"
                        aria-label={r.model.name + " 상세 성적"}
                        title="상세 성적"
                        aria-expanded={expanded === r.id}
                        onClick={() => setExpanded(expanded === r.id ? null : r.id)}
                      >
                        <ChevronRight
                          className={expanded === r.id ? "turn" : ""}
                          size={15}
                        />
                      </button>
                    </div>
                  </td>
                  {view === "rank" ? (
                    <>
                      <td className="accuracy">{percent(r.accuracy)}</td>
                      <td className="secondary-cell">
                        {r.correct} / {r.answered}
                      </td>
                      <td className="secondary-cell">{r.count - r.answered}</td>
                      <td className="secondary-cell">{dollars(r.cost)}</td>
                      <td className="secondary-cell effort">
                        {r.effort}
                      </td>
                    </>
                  ) : (
                    areaKeys.map((a) => {
                      const score = measure(
                        r.id,
                        r.model,
                        parts.filter((p) => data.parts[p].area === a),
                        data,
                      )!;
                      return (
                        <td
                          key={a}
                          className={
                            score.answered && score.correct === score.answered
                              ? "full-score"
                              : ""
                          }
                        >
                          {score.correct} / {score.answered}
                        </td>
                      );
                    })
                  )}
                </tr>
                {expanded === r.id && (
                  <tr className="detail-row">
                    <td colSpan={columns}>
                      <dl className="detail-list">
                        <div>
                          <dt>정답 / 응답</dt>
                          <dd>
                            {r.correct} / {r.answered}
                          </dd>
                        </div>
                        <div>
                          <dt>미응답</dt>
                          <dd>{r.count - r.answered}문항</dd>
                        </div>
                        {r.points && (
                          <div>
                            <dt>원점수 / 만점</dt>
                            <dd>{r.points.earned} / {r.points.max}점</dd>
                          </div>
                        )}
                        <div>
                          <dt>추론 강도</dt>
                          <dd>{r.effort}</dd>
                        </div>
                        <div>
                          <dt>추정 비용</dt>
                          <dd>{dollars(r.cost)}</dd>
                        </div>
                        <div>
                          <dt>환산 전 추정 비용</dt>
                          <dd>{dollars(r.billed)}</dd>
                        </div>
                      </dl>
                    </td>
                  </tr>
                )}
              </Fragment>
            ))}
            {unpublished.map(([id, model]) => {
              const availableParts = parts.filter((part) => model.parts[part]);
              if (availableParts.length) {
                return (
                  <PartialResultRow
                    key={id}
                    id={id}
                    model={model}
                    parts={availableParts}
                    data={data}
                    columns={columns}
                    totalParts={parts.length}
                  />
                );
              }
              if (!pending) {
                return (
                  <DeferredRow key={id} id={id} name={model.name} columns={columns} />
                );
              }
              return (
                <tr className="pending-row" key={id}>
                  <td className="rank-cell">—</td>
                  <td className="model-cell">
                    <ModelName id={id} name={model.name} />
                  </td>
                  {view === "rank" ? (
                    <>
                      <td>—</td>
                      <td className="secondary-cell">—</td>
                      <td className="secondary-cell">—</td>
                      <td className="secondary-cell">—</td>
                      <td className="secondary-cell effort">{model.effort}</td>
                    </>
                  ) : areaKeys.map((area) => <td key={area}>—</td>)}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function coversParts(model: Model, parts: string[]) {
  const expected = model.expectedParts;
  return expected !== undefined && parts.every((part) => expected.includes(part));
}

export default function App({ data }: { data: Dataset }) {
  const repositoryUrl = import.meta.env.VITE_REPOSITORY_URL;
  const pending = data.state === "awaiting" || data.state === "running";
  const readScope = () => {
    const value = new URLSearchParams(location.search).get("scope");
    if (value && partIds(data, value).length) return value;
    const candidates = [
      "core",
      "math",
      "korean",
      "english",
      "science2",
      "science1",
      "social",
      ...Object.keys(data.parts).map((id) => "part/" + id),
    ];
    return (
      candidates.find((candidate) =>
        ranking(data, partIds(data, candidate)).some((r) => r.answered > 0),
      ) ?? "core"
    );
  };
  const [scope, setScope] = useState(readScope);
  const [method, setMethod] = useState(false);
  const { theme, toggleTheme } = useTheme();
  useEffect(() => {
    const back = () => setScope(readScope());
    back();
    window.addEventListener("popstate", back);
    return () => window.removeEventListener("popstate", back);
  }, [data]);
  const parts = useMemo(() => partIds(data, scope), [data, scope]);
  const rows = useMemo(() => ranking(data, parts), [data, parts]);
  const waitingForScores = pending && rows.length === 0;
  const publishedIds = new Set(rows.map((row) => row.id));
  const unpublished = Object.entries(data.models).filter(
    ([id, model]) => coversParts(model, parts) && !publishedIds.has(id),
  );
  if (pending) unpublished.sort(([, a], [, b]) => a.name.localeCompare(b.name));
  const planned = waitingForScores ? unpublished : undefined;
  const partialCount = unpublished.filter(([, model]) =>
    parts.some((part) => model.parts[part]),
  ).length;
  const plannedCount = unpublished.length - partialCount;
  const modelCount = waitingForScores
    ? `${unpublished.length}개 모델`
    : unpublished.length
      ? [
          `${rows.length}개 공개`,
          partialCount ? `${partialCount}개 부분 공개` : null,
          plannedCount ? `${plannedCount}개 예정` : null,
        ].filter(Boolean).join(" · ")
      : `${rows.length}개 모델`;
  const count = parts.reduce((n, k) => n + data.parts[k].count, 0);
  const name = scopeName(data, scope);
  function choose(value: string) {
    if (!value || value === scope) return;
    const url = new URL(location.href);
    url.searchParams.set("scope", value);
    history.pushState(null, "", url);
    setScope(value);
  }
  return (
    <>
      <a className="skip" href="#main">
        본문으로 이동
      </a>
      <header className="site-head">
        <div className="container header-inner">
          <a
            href={import.meta.env.BASE_URL}
            className="brand"
            aria-label="홈"
          >
            <img
              className="brand-face"
              src={import.meta.env.BASE_URL + "favicon.png"}
              alt=""
              width={48}
              height={48}
            />
          </a>
          <nav aria-label="주 탐색">
            <button onClick={() => setMethod(true)}>평가 방법</button>
          </nav>
          <a
            className="download"
            aria-label="데이터 다운로드"
            href={getDatasetUrl(data.year)}
            download={"csat-" + data.year + ".json"}
          >
            <Download size={16} />
            <span>데이터 다운로드</span>
          </a>
          <button
            type="button"
            className="theme-toggle icon-button"
            title={theme === "dark" ? "밝은 테마로 전환" : "어두운 테마로 전환"}
            aria-label="어두운 테마"
            aria-pressed={theme === "dark"}
            onClick={toggleTheme}
          >
            {theme === "dark" ? <Sun size={18} /> : <Moon size={18} />}
          </button>
        </div>
      </header>
      <main className="container" id="main">
        <div className="masthead">
          {data.year === 2027 && (
            <p className="site-slogan">
              파도를 막으려 모래성을 높이는 동안, 바다는 이미 발목을 넘었다.
            </p>
          )}
          <h1>
            <strong>{data.year}학년도 대학수학능력시험</strong> 성적표
          </h1>
          <YearPicker year={data.year} />
        </div>
        {data.state && (
          <ExamNotice
            state={data.state}
            completion={data.completion}
            progress={pending ? data.progress : undefined}
          />
        )}
        {data.year === 2026 && (
          <p className="status-panel archive-notice">테스트를 위해 일부 모델만 평가했습니다.</p>
        )}
        <div className="scope-controls">
          <div className="scope-left">
            <div className="scope-label">응시 범위</div>
            <div className="scope-pills">
              {["core", "all"].map((key) => (
                <button
                  className={scope === key ? "selected" : ""}
                  aria-pressed={scope === key}
                  key={key}
                  onClick={() => choose(key)}
                >
                  {key === "core" ? "국영수" : "전체 영역"}
                </button>
              ))}
            </div>
            <Dropdown
              className="subject-picker"
              label="영역 또는 세부 과목 선택"
              value={["core", "all"].includes(scope) ? "" : scope}
              onValueChange={choose}
              placeholder="과목 선택"
            >
              <DropdownGroup label="영역">
                {Object.entries(areas).map(([key, label]) => (
                  <DropdownOption key={key} value={key}>
                    {label}
                  </DropdownOption>
                ))}
              </DropdownGroup>
              {(Object.keys(areas) as Area[])
                .filter((area) => area !== "english")
                .map((area) => (
                  <DropdownGroup label={areas[area] + " 세부 과목"} key={area}>
                    {Object.entries(data.parts)
                      .filter(([, part]) => part.area === area)
                      .sort(([, a], [, b]) => a.label.localeCompare(b.label, "ko"))
                      .map(([id, part]) => (
                        <DropdownOption key={id} value={"part/" + id}>
                          {part.label}
                        </DropdownOption>
                      ))}
                  </DropdownGroup>
                ))}
            </Dropdown>
          </div>
          <p className="scope-total" aria-live="polite">
            {modelCount} <span>/</span> {count}문항
          </p>
        </div>
        <AccuracyChart
          rows={rows}
          unpublished={unpublished}
          pending={waitingForScores}
          theme={theme}
        />
        <ResultsTable
          key={data.year}
          rows={rows}
          parts={parts}
          data={data}
          unpublished={unpublished}
          pending={waitingForScores}
        />
        {planned?.length || rows.length ? (
          <div className="chart-grid">
            {waitingForScores ? (
              <section className="analysis-plot" aria-labelledby="pending-plot-title">
                <div className="card-heading">
                  <div>
                    <h2 id="pending-plot-title">비용과 정답률</h2>
                    <p>성적과 비용이 공개되면 모델별로 표시됩니다.</p>
                  </div>
                </div>
                <div className="pending-plot pending-fill" aria-hidden="true" />
              </section>
            ) : (
              <Suspense
                fallback={
                  <div className="analysis-plot" role="status">
                    그래프를 불러오는 중입니다.
                  </div>
                }
              >
                <CostPlot rows={rows} scope={data.year + " 수능 · " + name} theme={theme} />
              </Suspense>
            )}
            <CostBars rows={rows} planned={planned} />
          </div>
        ) : (
          <p className="empty-scope">
            {pending
              ? "이 범위에는 응시 예정 모델이 없습니다."
              : "이 범위에는 아직 집계된 모델 성적이 없습니다."}
          </p>
        )}
        {pending && <ExamRoster data={data} />}
      </main>
      <footer className="site-footer">
        <div className="container footer-inner">
          <p>
            시험지 출처{" "}
            <a
              href="https://www.suneung.re.kr/boardCnts/list.do?boardID=1500234&amp;m=0403&amp;s=suneung&amp;searchStr="
              target="_blank"
              rel="noopener noreferrer"
            >
              한국교육과정평가원
            </a>
          </p>
          <div className="footer-actions">
            <button type="button" onClick={() => setMethod(true)}>
              평가 방법
            </button>
            {repositoryUrl && (
              <a href={repositoryUrl} target="_blank" rel="noopener noreferrer">
                GitHub 저장소
              </a>
            )}
          </div>
        </div>
        <div className="container footer-notice">
          <p>
            개발자의 사비로 측정된 벤치마크입니다. 비용 상 측정하지 못한 부분이 많습니다.
          </p>
          <p>
            혹시나 다른 모델들을 테스트할 수 있으신 분은 연락 부탁드립니다.
            repo와 사이트 내에 제공자 명단에 닉네임을 기입해드릴 수 있습니다. 감사합니다.
          </p>
          <p className="footer-contact">discord : neuro_sama</p>
        </div>
      </footer>
      {method && (
        <MethodDialog year={data.year} close={() => setMethod(false)} />
      )}
    </>
  );
}
