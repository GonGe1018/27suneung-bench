import { useEffect, useState } from "react";
import { ModelName } from "./ModelName";
import { ChevronRight } from "lucide-react";
import { Dropdown, DropdownOption } from "./Dropdown";
import { areas, type Area, type Dataset } from "./data";

export function YearPicker({ year }: { year: number }) {
  return (
    <Dropdown
      className="year-picker"
      label="시험 학년도"
      value={String(year)}
      align="end"
      onValueChange={(value) => {
        const url = new URL(location.href);
        url.searchParams.set("year", value);
        url.searchParams.delete("scope");
        url.hash = "";
        location.assign(url);
      }}
    >
      <DropdownOption value="2027">2027학년도</DropdownOption>
      <DropdownOption value="2026">2026학년도</DropdownOption>
    </Dropdown>
  );
}

const groups = {
  korean: "국어",
  "korean-math": "국어·수학 객관식",
  all: "전체 영역",
  science2: "국영수와 과학탐구Ⅱ",
  core: "국영수",
};

type ExamState = NonNullable<Dataset["state"]>;

const stages = [
  { state: "awaiting", label: "시험지 공개" },
  { state: "running", label: "풀이·채점" },
  { state: "complete", label: "성적 공개" },
] as const;

const messages: Record<ExamState, string> = {
  awaiting: "아직 시험지와 정답이 공개되지 않았습니다.",
  running: "벤치마크가 진행중입니다.",
  complete: "벤치마크 성적이 공개됐습니다.",
};

const releaseNoticeUrl = "https://cdn.kice.re.kr/imsi/27dae-suneung/index.html";

// The posted schedule uses each period's end time for severely visually impaired examinees.
const releaseSchedule = [
  { area: "국어", examEnd: "10:00", at: "2026-11-19T10:56:00+09:00" },
  { area: "수학", examEnd: "12:10", at: "2026-11-19T14:10:00+09:00" },
  { area: "영어", examEnd: "14:20", at: "2026-11-19T17:04:00+09:00" },
  { area: "탐구", examEnd: "16:37", at: "2026-11-19T20:15:00+09:00" },
].map((entry) => ({ ...entry, timestamp: Date.parse(entry.at) }));

function remainingTime(milliseconds: number) {
  const seconds = Math.ceil(milliseconds / 1000);
  const days = Math.floor(seconds / 86400);
  const clock = [
    Math.floor(seconds / 3600) % 24,
    Math.floor(seconds / 60) % 60,
    seconds % 60,
  ].map((value) => String(value).padStart(2, "0")).join(":");
  return days ? days + "일 " + clock : clock;
}

const progressFormat = new Intl.NumberFormat("ko-KR", {
  style: "percent",
  maximumFractionDigits: 1,
});

function StageProgress({ label, done, total }: { label: string; done: number; total: number }) {
  return (
    <div className="stage-progress">
      <div className="stage-progress-value">
        <strong>{progressFormat.format(done / total)}</strong>
        <span title="모델별 응시 예정 묶음 합계 기준">{done}/{total}묶음</span>
      </div>
      <progress aria-label={label + " 진행률"} value={done} max={total} />
    </div>
  );
}

export function ExamNotice({
  state,
  completion,
  progress,
}: {
  state: ExamState;
  completion?: Dataset["completion"];
  progress?: Dataset["progress"];
}) {
  const [now, setNow] = useState(Date.now);
  const next = releaseSchedule.find((entry) => entry.timestamp > now);
  const waitingForFirstRelease = state === "awaiting" && now < releaseSchedule[0].timestamp;
  const message = state === "awaiting" && !waitingForFirstRelease
    ? "시험지 공개를 확인 중입니다."
    : messages[state];

  useEffect(() => {
    if (!next) return;
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [next]);

  return (
    <section className="status-panel" aria-label="시험 진행 상태">
      <div className="status-overview">
        <div>
          <p className="status-message" role="status">
            {message}
          </p>
          {state === "awaiting" && (
            <p className="status-detail">시험지가 공개되는 즉시 벤치마크를 진행합니다.</p>
          )}
        </div>
        {state !== "complete" && (
          <a className="archive-link" href="?year=2026">
            2026 성적표 보기
            <ChevronRight size={16} />
          </a>
        )}
      </div>
      <ol className="exam-stages" aria-label="벤치마크 진행 단계" role="list">
        {stages.map(({ state: stage, label }) => (
          <li key={stage} aria-current={state === stage ? "step" : undefined}>
            <span className="exam-stage-label">{label}</span>
            {stage === "awaiting" && (
              <div className="release-countdown">
                {next ? (
                  <>
                    <div className="release-countdown-value">
                      <strong role="timer" aria-label={next.area + " 시험지 공개까지 남은 시간"}>
                        {remainingTime(next.timestamp - now)}
                      </strong>
                      <span>{next.area} 공개까지</span>
                    </div>
                    <p>
                      일반 시험 종료 {next.examEnd} <span aria-hidden="true">·</span>{" "}
                      <a
                        href={releaseNoticeUrl}
                        target="_blank"
                        rel="noopener noreferrer"
                        title="평가원 공개 안내"
                      >
                        공개 예정 <time dateTime={next.at}>{next.at.slice(11, 16)}</time>
                      </a>
                    </p>
                  </>
                ) : (
                  <p>
                    탐구 공개 예정 시각 경과 <span aria-hidden="true">·</span>{" "}
                    <a href={releaseNoticeUrl} target="_blank" rel="noopener noreferrer">
                      평가원 공개 안내
                    </a>
                  </p>
                )}
              </div>
            )}
            {stage !== "awaiting" && completion && completion.plannedBundles > 0 && (
              <StageProgress
                label={label}
                done={stage === "running" ? completion.scoredBundles : completion.publishedBundles}
                total={completion.plannedBundles}
              />
            )}
          </li>
        ))}
      </ol>
      {progress && <ExamProgress progress={progress} />}
    </section>
  );
}

const progressOrder: Area[] = ["math", "korean", "english", "science2", "science1", "social"];

function ExamProgress({ progress }: { progress: NonNullable<Dataset["progress"]> }) {
  return (
    <section className="exam-progress" aria-labelledby="exam-progress-title">
      <h2 id="exam-progress-title">과목별 진행 현황</h2>
      <ul className="exam-progress-list" role="list">
        {progressOrder.map((area) => {
          const item = progress[area];
          if (!item) return null;
          const { readyParts, totalParts, scoredModels, plannedModels } = item;
          const ready = readyParts === totalParts;
          const scored = plannedModels > 0 && scoredModels === plannedModels;
          return (
            <li key={area}>
              <strong>{areas[area]}</strong>
              <span data-active={readyParts > 0 || undefined}>
                {ready
                  ? "시험지 확인"
                  : readyParts > 0
                    ? `시험지 ${readyParts}/${totalParts}묶음 확인`
                    : "시험지 대기"}
              </span>
              {readyParts > 0 && plannedModels > 0 && (
                <span>{scored ? "채점 완료" : `채점 ${scoredModels}/${plannedModels}개 모델`}</span>
              )}
            </li>
          );
        })}
      </ul>
    </section>
  );
}

export function ExamRoster({ data }: { data: Dataset }) {
  const models = Object.entries(data.models);
  return (
    <section className="roster" aria-labelledby="roster-title">
      <h2 id="roster-title">
        응시 예정 모델 <small>{models.length}개</small>
      </h2>
      <div className="roster-list">
        {Object.entries(groups).map(([scope, title]) => {
          const group = models
            .filter(([, m]) => m.scope === scope)
            .sort(([, a], [, b]) => a.name.localeCompare(b.name));
          if (!group.length) return null;
          const firstModel = group[0][1];
          const questionCount = firstModel.expectedParts?.reduce(
            (sum, key) => sum + (firstModel.parts[key]?.coverage?.count ?? data.parts[key].count),
            0,
          );
          return (
            <section className="roster-group" key={scope}>
              <header>
                <h3>{title}</h3>
                {questionCount !== undefined && (
                  <span>{questionCount}문항</span>
                )}
              </header>
              <ul>
                {group.map(([id, model]) => (
                  <li key={id}>
                    <ModelName id={id} name={model.name} />
                  </li>
                ))}
              </ul>
            </section>
          );
        })}
      </div>
    </section>
  );
}
