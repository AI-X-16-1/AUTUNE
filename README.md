# AUTUNE (오튠)

> **Auto + Attune** — 어긋난 소통을 자동으로 조율하는 회의 인텔리전스
>
> 회의 녹음 하나로, 누가 뭘 하기로 했는지 자동 추적하고, 놓친 논의까지 잡아줍니다.

---

## 무슨 문제를 푸나

크로스펑셔널 회의는 세 가지 이유로 돈을 태웁니다.

**① 정리가 안 됩니다.** 회의에서 "제가 다음 주까지 올리겠습니다"라고 한 것들이
노트에 묻힙니다. 가장 말단 직원이 서기를 도맡아 30분~1시간을 쓰는데도 빠지는 게
생기고, 다음 회의는 "그거 했나요?"로 시작합니다.

**② 빠진 채로 넘어갑니다.** "검색 개인화 하겠습니다"를 결정했지만 성능 요구사항도,
콜드스타트 처리도 아무도 안 물어봤습니다. 2주 뒤 개발 착수할 때 "이거 정해진 게
없는데요?"가 터집니다.

**③ 맥락이 끊깁니다.** 같은 주제를 3주 연속 회의하는데 지난번에 뭘 결정했는지
기억이 안 납니다. 원래 "실시간 개인화"로 합의했는데 어느새 "인기순 정렬"로 바뀌어
있고, 언제 누가 바꿨는지 아무도 모릅니다.

기존 도구(Otter.ai, Clova Note, Fireflies.ai, Notion AI)는 전부 **"이번 회의"에서
멈춥니다.** 전사 + 요약까지가 끝이고, 회의 간 연결·결정 추적·갭 탐지는 전부 수작업입니다.

## Autune이 하는 일

회의의 **전체 생애주기**를 커버합니다.

| 시점    | 하는 일                                                                                           |
| ------- | ------------------------------------------------------------------------------------------------- |
| 회의 전 | 자료 분석 → 어젠다 초안 → 프리미팅 브리프 _(Phase 2)_                                             |
| 회의 중 | 실시간 전사 + 화자 분리 + 중간 요약                                                               |
| 회의 후 | 액션아이템 추출·추적, 갭 탐지, 과거 회의 맥락 연결, 개인 발언 비중 피드백, Slack·Notion·Jira 전송 |
| 축적 후 | 결정 계보 추적, 팀 커뮤니케이션 대시보드, 미스얼라인먼트 예측                                     |

**핵심 차별점:** 회의가 쌓일수록 맥락 연결·갭 패턴·예측 정확도가 올라가는
데이터 네트워크 효과. 그리고 개인정보를 설계 단계에서 보호하는 유일한 도구입니다.

## 개인정보 보호 — 설계 원칙

회의 녹음은 회사가 만드는 데이터 중 가장 민감한 축에 속합니다. 이건 나중에 붙이는
정책이 아니라 **코드 레벨 제약**으로 강제됩니다.

- **원본 음성 즉시 삭제** — 전사가 끝나면 녹음 파일은 바로 삭제됩니다. 성공하든
  실패하든 남지 않고, 복원할 수 없습니다.
- **개인정보 자동 마스킹** — 전화번호·이메일·주민번호·계좌번호를 정규식과 NER로
  이중 탐지해서, **DB에 쓰기 전에** 마스킹합니다. 마스킹 안 된 원문은 어디에도
  저장되지 않습니다.
- **발언 비중은 본인에게만** — 각자의 발언 비율은 본인에게 Slack DM으로만 갑니다.
  팀장도, 관리자도, 우리도 남의 발언 비중을 볼 수 없습니다. 집계 저장도 안 합니다.
  감시 도구로 쓰이지 않게 하려는 제품 정의 자체의 제약입니다.
- **보관 기간 제한** — 분석 결과는 기본 90일 후 자동 삭제. 사용자가 언제든 직접
  삭제할 수 있습니다.
- **참석자 동의** — 녹음 시작 시 전원에게 알림. 동의하지 않은 참석자의 발화는
  분석에서 제외할 수 있습니다.

상세 구현 규칙: [`docs/architecture/privacy.md`](docs/architecture/privacy.md)

## 모듈 구성 (1인 1모듈 풀스택)

| 모듈                   | 하는 일                                                                 | 핵심 AI                              | 담당   |
| ---------------------- | ----------------------------------------------------------------------- | ------------------------------------ | ------ |
| **A. Audio Pipeline**  | 녹음 → 화자별 전사 → 개인정보 마스킹 → 원본 삭제                        | Whisper, Pyannote, Speaker Embedding | 김민경 |
| **B. 구조화 추출**     | 발화 5종 분류 → 액션아이템 카드 → 모호 동의 NLI 검증 → Notion·Jira 연동 | DeBERTa 분류기, NLI                  | 강민구 |
| **C. 갭 탐지**         | 토픽 그래프 → 참여도 매트릭스 → 템플릿 대조 → 리스크 스코어링           | spaCy NER, NetworkX, Graph Centrality | 박재경 |
| **D. 회의 맥락 엔진**  | 과거 회의 토픽 연결 → 결정 계보 추적 → 어젠다·브리프 생성               | Sentence-BERT, BM25, Cross-encoder   | 문민재 |
| **E. 회의 인텔리전스** | 품질 점수 → 갭 분류 → 예측 → 히트맵 → 주간 리포트                       | SetFit, XGBoost, Prophet             | 이승환 |

A가 만든 전사 결과를 B·C·D가 **병렬로** 소비하고, 각자 Slack·Notion·Jira로 결과를
내보냅니다. 세 모듈이 남긴 이벤트 로그를 E가 집계해 대시보드와 주간 리포트를 만듭니다.

```
  [입력]
  녹음 파일 업로드 ────────┐
  자료 업로드 (PDF 등) ────┤
  (추후) 데스크탑 앱 ──────┘
                           │
                           ▼
┌───────────────────────────────────────────────────────┐
│  [A] Audio Pipeline                                   │
│  Whisper STT + Pyannote 화자분리 + Speaker Embedding    │
│  → 개인정보 자동 마스킹 → 원본 음성 즉시 삭제                  │
│  → 통일 포맷: { speaker, timestamp, text }              │
└──────────────────────────┬────────────────────────────┘
                           │
         ┌─────────────────┼─────────────────┐
         ▼                 ▼                 ▼
    ┌──────────┐      ┌──────────┐      ┌──────────┐
    │   [B]    │      │   [C]    │      │   [D]    │
    │ 구조화    │      │ 갭 탐지    │      │ 맥락      │   ← 병렬 처리
    │ 추출      │      │          │      │ 엔진      │
    └────┬─────┘      └────┬─────┘      └────┬─────┘
         │                 │                 │
         ├→ Notion/Jira    │                 │
         ├→ Slack          ├→ Slack          ├→ Slack
         │                 │                 │
         └─────────────────┼─────────────────┘
                           │ 이벤트 로그
                           ▼
                      ┌──────────┐
                      │   [E]    │
                      │ 인텔리     │
                      │ 전스      │
                      └────┬─────┘
                           │
                           └→ Slack
```

모듈끼리는 서로를 직접 호출하지 않습니다. A의 출력은 `packages/contracts`에 정의된
공통 포맷 `{ speaker, timestamp, text }`으로만 전달되고, 전달 수단은 Celery 이벤트입니다.
덕분에 C가 죽어도 B와 D는 정상적으로 끝나고, E는 도착한 것만으로 집계합니다.

## 기술 스택

| 영역        | 기술                                                                            |
| ----------- | ------------------------------------------------------------------------------- |
| Backend     | FastAPI (Python 3.12), Celery + Redis                                           |
| Frontend    | Next.js + Tailwind (Node 22)                                                    |
| Database    | PostgreSQL + pgvector — 구조화 데이터, 임베딩 검색, 토픽 그래프, 결정 계보를 모두 담습니다 |
| 패키지 관리 | uv workspace (Python), pnpm workspace (JS)                                      |
| 연동        | Slack Bolt, Notion API, Jira REST API, Google Calendar API                      |

## 저장소 구조

`frontend/` `backend/` 2분할 대신 **역할 기반 3층 구조**를 씁니다. 목적은 폴더를
예쁘게 나누는 게 아니라 **5명이 같은 파일을 동시에 고칠 일을 없애는 것**입니다.

```
autune/
├── packages/          # 공유 — 모두가 의존, 자주 안 바뀜 (전원 합의)
│   ├── contracts/     #   모듈 간 데이터 계약 (Pydantic → TS 타입 생성)
│   ├── core/          #   DB 세션, 설정, 인증, 로깅, 공통 엔티티
│   └── integrations/  #   Notion / Jira / Calendar / Slack 래퍼
│
├── modules/           # 소유 — 각자 자기 것만 만짐 (1인 1모듈)
│   ├── audio/         #   A
│   ├── extraction/    #   B
│   ├── gap/           #   C
│   ├── context/       #   D
│   └── intelligence/  #   E
│
├── apps/              # 조립 — 얇은 껍데기, 로직 없음 (거의 안 건드림)
│   ├── api/           #   FastAPI — 라우터 자동 등록만
│   ├── worker/        #   Celery — 태스크 자동 등록만
│   ├── web/           #   Next.js
│   └── bot/           #   Slack Bolt
│
├── infra/             # docker-compose, alembic, 배포 설정
└── docs/              # 개발 문서 (영문)
```

프론트엔드도 같은 축으로 자릅니다. 담당자가 백엔드 `modules/gap/`과 프론트
`apps/web/src/features/gap/`을 함께 소유합니다.

**핵심 규칙 3가지:**

1. **모듈끼리 서로 import하지 않습니다.** 통신은 `packages/contracts`의 타입과
   Celery 이벤트로만. CI에서 import-linter가 강제합니다.
2. **모듈 테이블은 접두사 필수** — `aud_`, `ext_`, `gap_`, `ctx_`, `intel_`.
   공통 엔티티(`meetings`, `utterances` 등)는 A만 쓰고 나머지는 읽습니다.
3. **alembic은 모듈별 독립 브랜치.** `down_revision` 충돌이 안 생깁니다.
   적용은 `alembic upgrade heads` (복수형).

## 시작하기

```bash
git clone <repo> && cd autune

cp .env.example .env                                 # 필요한 값 채우기
docker compose -f infra/docker-compose.yml up -d     # postgres(pgvector), redis
uv sync --all-packages
pnpm install
uv run alembic -c infra/alembic.ini upgrade heads

# 실행
uv run uvicorn apps.api.main:app --reload                              # API :8000
uv run celery -A apps.worker.celery_app worker -Q default,cpu_heavy    # 워커
pnpm --filter @autune/web dev                                          # 웹 :3000
```

`--all-packages`는 생략하면 안 됩니다. 워크스페이스 루트는 가상 패키지라
(`package = false`) 그냥 `uv sync`만 하면 개발 도구만 깔리고 `autune_core`
import이 바로 실패합니다.

자기 모듈만 작업한다면 `uv sync --package autune-gap`으로 해당 모듈 의존성만
설치할 수 있습니다 (A의 수 GB짜리 ML 휠을 안 받아도 됩니다). 단 이 경우 다른
모듈은 설치되지 않아 전체 테스트는 돌릴 수 없습니다 — `uv run pytest`를 돌리려면
`uv sync --all-packages`를 하거나, `uv run pytest modules/gap`처럼 자기 모듈로
범위를 좁히세요.

상세: [`docs/engineering/environments.md`](docs/engineering/environments.md)

## 문서

**개발 문서는 전부 영어로 작성합니다.** AI 에이전트와 도구가 참조하는 문서이고,
저장소는 영구적인 공유 산출물이기 때문입니다. **팀원 간 대화와 리뷰 코멘트는
한국어로** 합니다. 이 README는 프로젝트를 처음 접하는 한국인 독자를 위한
예외입니다.

| 문서                                                   | 내용                                                                            |
| ------------------------------------------------------ | ------------------------------------------------------------------------------- |
| [`CLAUDE.md`](CLAUDE.md)                               | **여기부터.** 절대 규칙 11개, 소유권 맵, 작업별 문서 매핑                       |
| [`docs/README.md`](docs/README.md)                     | 전체 문서 인덱스와 읽는 순서                                                    |
| [`docs/product/prd.md`](docs/product/prd.md)           | 제품 요구사항 (엔지니어링 관점)                                                 |
| [`docs/product/glossary.md`](docs/product/glossary.md) | 도메인 용어 사전                                                                |
| [`docs/architecture/`](docs/architecture/)             | 모노레포 구조, 모듈 경계, 데이터 계약, 데이터 모델, 비동기 파이프라인, 개인정보 |
| [`docs/engineering/`](docs/engineering/)               | 협업 워크플로우, 코드 컨벤션, 마이그레이션, 의존성, 테스트, 환경 설정           |
| [`docs/modules/`](docs/modules/)                       | 모듈 A~E 상세                                                                   |
| [`docs/decisions/`](docs/decisions/)                   | ADR — 왜 이렇게 만들었는지                                                      |

새로 합류했다면 이 순서로 30분: `CLAUDE.md` → `docs/product/prd.md` →
`docs/product/glossary.md` → `docs/architecture/monorepo.md` →
`docs/engineering/environments.md`

## 협업 규칙

- 브랜치는 `<모듈>/<작업>` (예: `gap/topic-graph`). `main` 직접 push 금지, PR만.
  클론 직후 `git config core.hooksPath .githooks` 를 한 번 실행하면 실수로 `main`에
  push하는 걸 막아줍니다. (레포가 Free 플랜 Private이라 GitHub 브랜치 보호는 못 씁니다)
- 승인은 **내 모듈만 고쳤으면 아무나 1명**, **남의 모듈을 건드렸으면 그 소유자**.
  **리뷰어는 직접 지정해야 합니다** — 무료 플랜 private 저장소에서는 CODEOWNERS가
  리뷰어를 자동 배정하지 않습니다. 파일은 "누가 승인해야 하는지"를 적어둔 지도입니다.
- 자기 소유가 아닌 파일은 건드리지 않습니다. 필요하면 소유자에게 요청.
- `packages/contracts`는 W1에 확정, 이후 **추가만**. 필드 삭제·이름 변경은 Slack
  공지 + 관련 모듈 담당자 전원 승인.
- lockfile 충돌은 병합하지 말고 재생성 (`uv lock` / `pnpm install`).

- 작업은 [프로젝트 보드](https://github.com/orgs/AI-X-16-1/projects/9)에서 추적합니다.
  이슈에서 브랜치를 만들고, PR 본문에 `Closes #번호` 를 쓰면 머지 시 카드가 자동으로 Done이 됩니다.

상세: [`docs/engineering/workflow.md`](docs/engineering/workflow.md)

## 로드맵 (6주)

| 주차 | 핵심                                                 | 마일스톤             |
| ---- | ---------------------------------------------------- | -------------------- |
| W1   | 공통 인프라(2일) + 개인 PoC(3일)                     | 각 모듈 PoC 동작     |
| W2   | AI 파이프라인 + API 서빙. **A 최우선 완성**          | API로 각 모듈 동작   |
| W3   | 모듈 간 연결 + 프론트 착수. Notion/Jira 연동         | 녹음→파이프라인 관통 |
| W4   | 핵심 UI (실시간 전사, 액션 보드, 갭 리포트, 맥락 뷰) | 비개발자 사용 가능   |
| W5   | 대시보드 + 내부 베타 (실제 회의 5~10개)              | 실제 회의 E2E 검증   |
| W6   | 버그 수정, 성능, 랜딩 페이지, 데모 영상              | 배포 가능 MVP        |

A가 크리티컬 패스입니다. `TranscriptReady`가 실제로 나오기 전까지 B·C·D는 통합할
수 없으므로 A가 먼저 완성됩니다.

## 목표 지표 (6주)

| 지표                   | 목표                   |
| ---------------------- | ---------------------- |
| 액션아이템 추출 F1     | 0.80+                  |
| 화자 분리 DER          | 15% 이하               |
| 갭 탐지 정밀도         | 0.70+                  |
| 토픽 연결 정확도       | 0.75+                  |
| 개인정보 마스킹 재현율 | 0.95+                  |
| 처리 시간              | 녹음 길이의 1.5배 이내 |
