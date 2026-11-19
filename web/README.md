# 인간시대의끝이도래했다

React · TypeScript · Vite로 만든 정적 리더보드다. Observable Plot으로 비용과 정답률을 그리고, SUIT 가변 글꼴과 Lucide 아이콘을 사용한다.

```sh
cd web
pnpm install --frozen-lockfile
pnpm dev
```

`pnpm build`는 타입을 검사하고 `dist/`를 만든다. GitHub Pages의 게시 소스를 GitHub Actions로 지정하면 `main`의 웹 변경을 배포한다. 결과 JSON과 글꼴을 함께 배포하므로 방문자의 브라우저는 모델 API를 호출하지 않는다. 푸터는 GitHub Pages 빌드에 전달된 현재 저장소를 가리킨다. 로컬에서는 `VITE_REPOSITORY_URL`로 저장소 주소를 지정할 수 있다.

첫 방문에는 다크 테마를 적용하고, 헤더에서 고른 테마는 다음 방문에도 적용한다. 기본 강조색은 블리츠크랭크의 황동색이다. 파비콘은 Riot Games [자산 사용 정책](https://www.riotgames.com/en/legal)에 따라 [블리츠크랭크 초상화](https://ddragon.leagueoflegends.com/cdn/16.23.1/img/champion/Blitzcrank.png)를 사용한다.

모델명 옆의 로고는 [Lobe Icons](https://github.com/lobehub/lobe-icons)의 단색 SVG를 번들에 포함한다. [아이콘 라이선스](public/model-icons-LICENSE.txt)

## 모서리 반경

[공통 스타일](src/styles.css)의 세 변수를 데스크톱과 모바일에 함께 쓴다.

| 변수 | 반경 | 적용 대상 |
| --- | ---: | --- |
| `--radius-panel` | 12px | 상태 안내, 응시 명단, 순위표, 그래프 카드, 대화상자 |
| `--radius-control` | 8px | 독립 버튼, 선택 상자, 여러 버튼을 묶는 바깥 테두리 |
| `--radius-inset` | 4px | 버튼 그룹 안의 선택 항목, 툴팁, 막대그래프 끝 |

버튼 그룹은 안쪽 여백도 4px로 둬서 안팎의 곡선이 맞도록 한다. 산점도와 범례에서 원·사각형·마름모는 제공사를 구별하는 기호다.

## 시험일 상태

기본 주소는 2027학년도 성적표를 연다. 2026학년도 성적표는 학년도 선택이나 `?year=2026` 링크에서 볼 수 있다. 공개 전 성적표는 영역별로 확인된 시험지 묶음과 채점을 끝낸 모델 수를 표시한다. 상단 타이머는 다음 시험지 공개 예정 시각까지 남은 시간을 표시한다. 풀이·채점과 성적 공개 진행률은 각 모델의 전체 응시 예정 묶음을 합산한 수를 기준으로 한다. 공개 성적표의 정답률 막대는 0~100% 축을 사용한다.

저장소 루트에서 `python -m suneung_benchmark.web_status`를 실행해 `web/public/data/2027/csat/benchmark.json`을 갱신한다. 첫 과목의 시험지 파일이 들어오거나 채점 기록이 생기면 진행 중으로 바뀐다. 공식 공개를 먼저 확인했을 때는 `--released`로 전환할 수 있다. 준비·실행·채점 명령도 기본 2027학년도 경로에서 이 집계를 갱신한다.

현재 실행 설정의 모든 묶음이 채점되면 결과가 열린다. 일부 모델을 미루고 마감할 때는 `python -m suneung_benchmark.web_status --finalize`를 사용한다. 응시 예정 범위에 속하지만 아직 비교할 수 없는 모델은 표 하단에 표시한다. 공개 JSON 변경을 배포하면 열려 있는 페이지도 1분마다 상태를 다시 읽는다. `configs/2027/csat/planned-models.json`은 추가 응시 예정 명단이다.

## 2026학년도 자료

`python -m suneung_benchmark.export_web`으로 2026학년도 측정 결과에서 공개 집계를 다시 만든다. `?year=2026`으로 직접 열 수 있다. 표와 그래프는 선택한 묶음을 모두 실행한 모델을 비교하고, 정답률 분모에서 미응답을 제외한다. 비용은 재시도를 포함하며 무료 호출은 기록된 유료 단가 상당액으로 환산한다. 산점도 우측 상단에서 현재 그래프를 SVG로 저장할 수 있다.

[OpenRouter 순위표](https://openrouter.ai/rankings)의 범위 선택, [Artificial Analysis](https://artificialanalysis.ai/models)의 비용 대비 성능 비교, [Text Arena](https://arena.ai/leaderboard/chat/text)의 모델별 순위 표시를 참고했다. [SUIT](https://github.com/sun-typeface/SUIT)의 라이선스는 [fonts/LICENSE](public/fonts/LICENSE)에 있다.
