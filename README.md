# WSID_AI (어떡하지 AI 서버, FastAPI)

훈련(시뮬레이션) 흐름 5단계 중 지금 들어있는 것:

| 단계 | 기능 | API | 상태 |
|---|---|---|---|
| ① 상황 입력·분석 (SIM-001/002) | 오늘 겪은 상황·받은 문자를 대화로 입력 → **채널·사칭 신분·요구 행동·위험 요소** 추출, 부족하면 추가 질문, 개인정보는 가리고 종류만 기록 | `POST /internal/ai/discovery/next` | ✅ (이미지 입력 제외) |
| ② 유형 분류·위험 분석·시나리오 (SIM-003~006) | **6분류**(메신저피싱/스미싱/대출빙자/기관사칭/투자·가상자산/기타) + 세부 유형, 판단 지점, 맞춤 시나리오, 검증 | `POST /internal/ai/scenarios/generate` | ✅ 근거 사례 DB(15건)·모방범죄 검사 포함 |
| ③ 가상 훈련 대화 | 가상 사기범이 **개인정보를 직접 요구** → 사용자가 실제로 입력했는지 기록(값은 저장 안 함), 대응 판정, 선택지 | `POST /internal/ai/simulation/reply` | ✅ |
| ④ 모의 페이지 (SIM-014) | 화면은 프론트(WSID_FE), 행동 기록은 Spring. AI 서버는 그 기록을 ⑤ 리포트에서 받아 반영 | `/internal/ai/training/evaluate`의 `mockPageEvents` | ✅ (서버 쪽) |
| ⑤ 결과 리포트 (SIM-010) | **개인정보 요구·제공 결과**, 잘한/놓친 대응, 놓친 위험 신호, 턴별 피드백, 권장 대응, 공식 연락처, 근거 사례 | `POST /internal/ai/training/evaluate` | ✅ |

---

> 💡 **이 문서의 명령어는 전부 `python -m ...` 형태로 쓰세요.** (`uvicorn`, `pip`만 단독으로 치면 Windows에서 "명령을 찾을 수 없음" 오류가 날 수 있어요)
> Mac이라면 `python` 대신 `python3`를 쓰세요.

## 1단계. 준비물 확인 (최초 1번)

터미널을 엽니다. Windows는 VS Code 아래쪽 터미널이나 PowerShell, Mac은 "터미널" 앱이에요.

```bash
python --version
```
- `Python 3.10` 이상이 나오면 OK입니다. (Mac은 `python3 --version`)
- 안 나오면 https://www.python.org/downloads 에서 설치하세요. Windows는 설치 첫 화면에서 **"Add Python to PATH" 체크**를 꼭 해야 합니다.

## 2단계. 폴더 열기

VS Code에서 `파일 → 폴더 열기`로 이 `WSID_AI` 폴더를 엽니다.
그다음 `터미널 → 새 터미널`을 열면, 터미널 위치가 이 폴더로 자동 설정됩니다.

## 3단계. 가상환경 만들기 (최초 1번)

가상환경은 **이 프로젝트 전용 파이썬 방**입니다. 다른 프로젝트와 라이브러리가 섞이지 않게 해줘요.

```bash
# Windows
python -m venv .venv
.venv\Scripts\activate

# Mac
python3 -m venv .venv
source .venv/bin/activate
```
터미널 줄 맨 앞에 `(.venv)`가 보이면 성공입니다.
> 터미널을 새로 열 때마다 `activate` 줄은 다시 실행해야 해요.
> Windows에서 "스크립트를 실행할 수 없습니다" 오류가 나면 PowerShell에 `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`를 한 번 입력하세요.

## 4단계. 라이브러리 설치 (최초 1번)

```bash
python -m pip install -r requirements.txt
```

## 5단계. 설정 파일 만들기 (최초 1번)

`.env.example` 파일을 복사해서 같은 폴더에 `.env`라는 이름으로 저장합니다.
처음에는 내용을 **그대로 두세요.** `LLM_PROVIDER=mock`이면 API 키 없이 가짜 AI로 동작합니다.

## 6단계. 서버 켜기

```bash
python -m uvicorn app.main:app --reload
```
`Uvicorn running on http://127.0.0.1:8000`이 보이면 서버가 켜진 거예요.
`--reload` 옵션 덕분에 코드를 저장하면 서버가 자동으로 다시 켜집니다. 끌 때는 `Ctrl + C`.

## 7단계. 브라우저에서 테스트하기 ⭐

브라우저에서 **http://localhost:8000/docs** 를 엽니다. FastAPI가 자동으로 만들어 주는 테스트 화면이에요.

1. `POST /internal/ai/discovery/next` 클릭
2. **Try it out** 클릭
3. 예시 JSON이 채워져 있으니 그대로 **Execute** 클릭
4. 아래 Response body에 `nextQuestion`, `extractedFacts`가 나오면 성공 🎉

이것도 해보세요:
- `"history": []`로 비워서 보내기 → 첫 질문이 나와요
- 사용자 답변에 `010-1234-5678` 넣기 → `sensitiveDetected: true`

## 8단계. 터미널에서 직접 대화해 보기

서버는 켜둔 채로 **터미널을 하나 더** 열고(가상환경 activate 포함) 실행합니다.
```bash
python scripts/chat.py
```
**① 상황 파악 → ② 시나리오 생성 → ③ 가상 훈련 대화 → ⑤ 결과 리포트**를 한 번에 체험할 수 있어요.
③에서는 선택지 번호(1~3)를 누르거나 직접 입력하면 되고, 매 턴 판정(✅ safe / ❌ risky / ➖ neutral)과 마지막에 턴별 기록이 나와요. 종료는 `q`.

## 9단계. 자동 테스트 돌리기

코드를 고친 뒤에는 꼭 돌려보세요. 전부 `passed`면 기존 기능이 안 깨졌다는 뜻이에요.
```bash
python -m pytest
```

---

## 진짜 LLM 연결하기

팀에서 공급자를 정하면 `.env`를 이렇게 바꾸고 서버를 다시 켜세요.

```env
# OpenAI
LLM_PROVIDER=openai
LLM_API_KEY=발급받은_키
LLM_MODEL=콘솔에서_확인한_모델명

# Claude
LLM_PROVIDER=anthropic
LLM_API_KEY=발급받은_키
LLM_MODEL=콘솔에서_확인한_모델명

# OpenAI 호환 API (Gemini, Upstage Solar 등)
LLM_PROVIDER=openai
LLM_BASE_URL=공급자_문서에_나온_OpenAI_호환_주소
LLM_API_KEY=...
LLM_MODEL=...
```
- **API 키는 절대 GitHub에 올리지 마세요.** `.env`는 `.gitignore`에 이미 들어 있어요.
- 연결한 뒤에도 7~8단계로 똑같이 테스트하면 됩니다. mock일 때 붙던 `(mock)` 표시가 사라지고 진짜 AI 질문이 나와요.

---

## 파일별 역할

| 파일 | 하는 일 | 자주 고치게 될까? |
|---|---|---|
| `app/prompts/context_turn.md` | ① **상황 파악 AI 지시문** | ⭐ 제일 많이 고침 |
| `app/prompts/scenario.md` | ② **시나리오 설계 AI 지시문** | ⭐ 제일 많이 고침 |
| `app/prompts/training_turn.md` | ③ **사기범 연기 + 대응 판정 + 선택지 AI 지시문** | ⭐ 제일 많이 고침 |
| `app/fallback.py` | ① 첫 질문·기본 질문, ③ 사기범 기본 대사·기본 선택지 | 가끔 |
| `app/scenario_data.py` | ② 허용 사기 유형·채널 목록, **기본 시나리오 7종** (LLM 실패 시 사용) | 가끔 |
| `app/data/fraud_cases.yaml` | **근거 사례 DB** (공개 주의 안내 15건 요약 + 출처 링크) | 사례 추가·검토할 때 |
| `app/case_db.py` | 사례 DB 읽기 | 거의 안 고침 |
| `app/guard/sensitive.py` | 사용자가 입력한 개인정보 탐지·가림 (AI의 질문은 막지 않음) | 가끔 |
| `app/guard/scenario_guard.py` | ② 시나리오 검증: 실존 기관명·URL·전화번호·보이스피싱 차단, 근거 사례 확인, 모방범죄 검사, 개인정보 요구 장면 필수 | 가끔 (차단 표현 추가) |
| `app/services/context_turn.py` | ① 전체 흐름 | 기능 바꿀 때 |
| `app/services/scenario.py` | ② 전체 흐름 (생성 → 검사 → 재생성 → 기본 시나리오) | 기능 바꿀 때 |
| `app/services/training_turn.py` | ③ 전체 흐름 (판정 → 대사 → 가드레일 → 장면 진행 → 종료) | 기능 바꿀 때 |
| `app/prompts/report.md` | ⑤ **리포트 글 작성 AI 지시문** | ⭐ 자주 고침 |
| `app/services/report.py` | ⑤ 사실 계산(개인정보·행동·권장 대응) + AI 설명 글 + 검사 | 기능 바꿀 때 |
| `app/report_data.py` | ⑤ 공식 연락처(112·1332·118 등), 넘긴 정보별 후속 조치, 안내 문구 | 연락처 바뀔 때 |
| `app/schemas/sim.py` | Spring과 주고받는 JSON 모양 | Spring 담당과 같이 |
| `app/llm/llm_client.py` | LLM 호출, 재시도, JSON 해석 | 거의 안 고침 |
| `app/llm/mock_llm.py` | 키 없이 연습용 가짜 AI | 거의 안 고침 |
| `app/routers/sim.py` | API 주소 등록 | 새 API 추가할 때 |

## ① 동작 흐름

```
Spring이 보낸 대화 기록
   │
   ├─ 대화가 비어있음 ─────────────→ 첫 질문 반환 (LLM 안 씀)
   ├─ 마지막 답변에 민감정보 있음 ──→ 종류만 기록 (대화는 그대로 계속)
   │
   ▼
프롬프트 + 대화(민감정보는 [전화번호]처럼 가림) → LLM → JSON
   │   ※ 실제 개인정보 값은 LLM에도, 로그에도 가지 않음
   │   └─ 실패(타임아웃, JSON 깨짐) → 1번 재시도 → 그래도 실패하면 기본 질문
   ▼
질문 상한(5번) 도달 or AI가 정보 충분 판단 → done: true
```

## 위험 유형 분류 (SIM-003)

| 대분류 (명세 6종) | 세부 유형 (시나리오 단위) |
|---|---|
| 메신저피싱 | 지인·가족 사칭 |
| 스미싱 | 택배 사칭 |
| 대출빙자 | 대출 사기 |
| 기관사칭 | 공공기관 사칭 |
| 투자·가상자산 | 투자 리딩방 |
| 기타 | 중고거래 사기, 채용·아르바이트 사기 |

세부 유형을 추가하려면 `app/scenario_data.py`의 `SUBTYPE_TO_RISK_TYPE`과 `FALLBACK_SCENARIOS`에 같이 넣으세요.

## 근거 사례 DB (SIM-005·006)

`app/data/fraud_cases.yaml`에 금융감독원·KISA·정부 부처·서울시가 공개한 주의 안내 15건을 요약해 뒀어요. 6분류마다 2~3건씩 있어요.

- 시나리오를 만들 때 AI에게 사례 요약(수법·요구 정보)을 보여주고, 근거로 삼은 `caseId`를 달게 해요.
- 검증 단계에서 그 `caseId`가 DB에 실제로 있는지, 위험 유형이 같은지 확인하고, 아니면 시나리오를 버려요.
- 응답의 `scenario.basedOnCases`에 사례 제목·발표 기관·출처 링크가 들어가요.
- `sourceUrl`은 내용을 실제로 확인한 페이지예요. 일부는 기관 원문이 아니라 금융회사가 게시한 사본이나 언론 보도예요(`sourceHost`에 표시). 발표용으로 쓰기 전에 팀이 링크를 한 번씩 열어 확인하세요.
- 사례를 추가하려면 YAML에 같은 모양으로 항목을 넣으면 돼요. `summary`와 `tactics`에는 실존 기관·서비스 이름을 쓰지 마세요(AI 프롬프트에 들어가요). `python -m pytest`가 형식을 검사해 줘요.

## ② 동작 흐름

```
Spring이 보낸 ①단계 사실 목록 (개인정보는 한 번 더 가림)
   │
   ├─ 사실이 하나도 없음 ──→ 기본 시나리오 (택배 사칭)
   ▼
프롬프트 + 사실(채널·사칭·요구·위험 요소 포함) + 사례 DB 요약 → LLM → 위험 유형 분류 + 분석 + 시나리오 JSON
   ▼
가드레일 검사
  - 모양: 허용된 세부 유형/채널/요구 행동인지, 장면 1~4개, 평가 기준 있는지
  - 분류: 대분류(riskType)는 세부 유형 표에서 자동으로 정함 → LLM이 잘못 적어도 바로잡힘
  - 안전: 실존 기관·회사 이름? 실제 URL? 전화번호? 보이스피싱(전화) 요소?
  - 근거: 시나리오가 단 caseId가 사례 DB에 실제로 있는지, 위험 유형이 같은지
  - 모방범죄: 범행 운영·회피 요령, "1단계→2단계"식 절차 안내, 지나치게 긴 대본
  - 필수: 사기범이 개인정보를 직접 요구하는 장면이 하나 이상 있는지
   │   └─ 불통과 → 1번 더 생성 → 그래도 불통과면 사실에 맞는 기본 시나리오 7종 중 하나
   ▼
분석(위험 신호, 판단 지점) + 시나리오 반환
```

## ③ 동작 흐름

```
Spring이 보낸 시나리오 + 현재 장면 + 훈련 대화 기록
   │
   ├─ 대화가 비어있음 ──→ 시나리오의 첫 메시지 + 선택지 (LLM 안 씀)
   ├─ 마지막 답변에 개인정보 있음 ──→ 값은 가리고 종류만 기록, 판정은 무조건 risky (대화는 계속)
   │     · 번호류는 패턴으로, 요구받은 인증번호 같은 짧은 숫자도 감지
   │     · 이름·주소처럼 패턴이 없는 정보는 AI 판단으로 기록하고 답변 전체를 "[이름 입력함]"으로 바꿈
   ▼
LLM 1번 호출로 3가지를 한 번에
  - 사용자 마지막 답변 판정: safe / risky / neutral + 지킨·어긴 행동(A1, A2...)
  - 사기범 다음 대사 (거절하면 1~2번 더 설득, 응하면 다음 장면)
  - 사용자 선택지 3개 (안전 / 위험 / 애매, 순서 섞음)
   ▼
개인정보 요구 보장 (훈련의 핵심)
  - 개인정보 요구 장면에 들어왔는데 사기범 대사에 요구가 없으면 → "○○ 여기에 적어 주세요"를 덧붙임
  - 사용자가 "알려드릴게요"라고만 하고 값을 안 쓰면 → 다음 장면으로 안 넘어가고 직접 입력하라고 한 번 더 요구
   ▼
가드레일
  - 사기범 대사·선택지에 실존 기관명, URL, 전화번호, 모방범죄 우려 표현 → 장면에 맞는 기본 대사로 교체
  - 사기범이 개인정보를 "요구"하는 건 막지 않음
  - 장면은 한 번에 1칸까지만 진행, 시나리오에 없는 행동 id는 버림
   ▼
종료 판단: 마지막 장면 완료 or 최대 턴(15) 도달 → done: true
```

---

## ⑤ 동작 흐름

```
Spring이 보낸 시나리오 + ③ 턴별 기록 + ④ 모의 페이지 기록
   ▼
코드가 사실 계산 (AI가 바꿀 수 없음)
  - 개인정보: 요구받은 것 / 실제로 넘긴 것 / 지킨 것, 처음 넘긴 턴
  - 잘한 대응 / 놓친 대응 (어겼다가 나중에 바로잡았는지 포함)
  - 모의 페이지 행동 요약
  - 권장 대응 = 놓친 대응 + 근거 사례의 올바른 대응 + 넘긴 정보별 후속 조치
  - 공식 연락처 (112·1332·118 등 고정값)
   ▼
LLM이 설명 글만 작성: 한 줄 요약, 총평, 턴별 피드백, 놓친 위험 신호
   │   └─ 글에 전화번호·URL이 있거나 실패하면 → 판정 이유로 만든 기본 문장
   ▼
리포트 JSON (실제 개인정보 값 없음 → 그대로 저장·PDF 가능)
```

## Spring 담당에게 전달할 API 명세

주소와 ID는 팀 API 명세(`AI-01`~`AI-05`)와 같아요. Spring 쪽 외부 API(`TRN-*`)가 아래 내부 API를 호출해요.

| 순서 | 팀 명세 ID | 주소 | 호출하는 Spring API | 언제 호출 | 타임아웃 |
|---|---|---|---|---|---|
| ① | AI-01 | `POST /internal/ai/discovery/next` | TRN-04 상황 파악 AI 대화 | 상황 대화에서 사용자가 답할 때마다 | 25초 |
| ② | AI-03 | `POST /internal/ai/scenarios/generate` | TRN-06 개인화 시나리오 생성 | ①이 `done: true`일 때 1번 | 60초 (로딩 화면) |
| ③ | AI-04 | `POST /internal/ai/simulation/reply` | TRN-07 훈련 시작(첫 메시지), TRN-08 AI 훈련 메시지 | 훈련 대화에서 사용자가 답할 때마다 | 25초 |
| ⑤ | AI-05 | `POST /internal/ai/training/evaluate` | TRN-11 훈련 종료 및 리포트 생성 | 훈련이 끝났을 때 1번 | 60초 (로딩 화면) |
| - | - | `GET /health` | - | 서버 상태 확인 (Docker 등) | - |

- **AI-02 이미지 OCR**(`/internal/ai/ocr/extract`)은 아직 이 서버에 없어요.
- **AI-06·AI-07 피해 대응**(`/internal/ai/solutions/*`)도 아직 없어요. 같은 서버에 추가할 예정이면 `/internal/ai` 아래에 붙이면 돼요.
- TRN-10 모의 페이지 행동 기록은 Spring이 저장만 하고, 훈련 종료 때 AI-05 요청의 `mockPageEvents`로 넘기면 돼요.

모든 JSON 필드는 camelCase예요. 전체 필드 설명은 서버를 켜고 `/docs`에서도 볼 수 있어요.


**`POST /internal/ai/discovery/next`** (팀 명세 ID: AI-01 → 기능 SIM-001)

요청
```json
{
  "sessionId": "Spring이 만든 세션 ID",
  "history": [
    {"role": "ai", "text": "AI가 했던 질문"},
    {"role": "user", "text": "사용자 답변"}
  ]
}
```
- `role`은 `"ai"` 또는 `"user"`만 됩니다. `text`는 최대 1000자, `history`는 최대 30개.
- 처음 시작할 때는 `history: []` → 첫 질문을 돌려줍니다.
- 그 외에는 마지막 항목이 반드시 `user`여야 해요. 아니면 400 에러가 납니다.

응답
```json
{
  "nextQuestion": "화면에 보여줄 문장",
  "extractedFacts": [{
    "category": "택배·배송",
    "detail": "주소 불일치로 반송된다는 택배 문자를 받음",
    "channel": "문자",
    "impersonatedIdentity": "택배사",
    "requestedAction": "링크 클릭",
    "riskFactors": ["출처 불명 링크", "시간 압박"]
  }],
  "sensitiveDetected": true,
  "sensitiveTypes": ["전화번호"],
  "maskedText": "택배 문자 와서 [전화번호]로 연락했어요",
  "done": false,
  "realConcern": false,
  "fallbackUsed": false
}
```
- `extractedFacts` 항목 (SIM-002): `category`, `detail`은 항상 있고, `channel`(채널), `impersonatedIdentity`(사칭 신분), `requestedAction`(요구 행동)은 사용자가 말하지 않았으면 `null`, `riskFactors`(위험 요소)는 없으면 `[]`.

Spring이 할 일
1. `nextQuestion`을 화면에 보여주고, `history`에 `{"role":"ai"}`로 추가합니다.
2. `extractedFacts`는 세션에 **누적 저장**합니다. FastAPI는 이번 답변에서 새로 나온 사실만 돌려줘요. 모인 사실은 다음 단계인 시나리오 생성에 넘깁니다.
3. **개인정보 처리 (대화는 절대 막지 않음)**
   - history의 마지막 사용자 답변을 원문 대신 **`maskedText`로 바꿔서** 세션에 저장합니다. 그러면 실제 번호나 계좌 값이 Spring, Redis, DB 어디에도 남지 않아요.
   - `sensitiveTypes`는 참고용이에요. ①은 훈련 전 준비 대화라 **리포트의 개인정보 평가는 ③ 훈련 대화 기록(`infoDisclosure`)만으로** 해요. 그래서 ①의 기록은 따로 저장하지 않아도 돼요.
4. `done: true`면 상황 파악 단계를 마치고 시나리오 생성으로 넘어갑니다.
5. `realConcern: true`면 "실제로 피해를 당하셨나요? → 피해 대응 메뉴" 안내를 띄워주세요.
6. 타임아웃은 25초로 잡아주세요. AI 서버 내부에서 LLM 10초 × 최대 2회로 끝납니다.

---

**`POST /internal/ai/scenarios/generate`** (팀 명세 ID: AI-03 → 기능 SIM-002/004/005/006)

①에서 `done: true`가 나오면 Spring이 1번 호출합니다. 시간이 걸리므로 **화면에는 로딩 표시**를 해주세요.

요청
```json
{
  "sessionId": "Spring이 만든 세션 ID",
  "facts": [
    {"category": "택배·배송", "detail": "주소 불일치로 반송된다는 택배 문자를 받음",
     "channel": "문자", "impersonatedIdentity": "택배사",
     "requestedAction": "링크 클릭", "riskFactors": ["출처 불명 링크", "시간 압박"]}
  ]
}
```
- `facts`는 ①에서 누적한 `extractedFacts` 전체를 **받은 모양 그대로** 보내주세요 (최대 30개). 비어 있어도 됩니다 (기본 시나리오).
- 사용자가 실제로 받은 수상한 연락(채널·사칭·요구가 있는 사실)이 있으면 그 상황을 우선 반영해서 시나리오를 만들어요.

응답 (요약)
```json
{
  "analysis": {
    "riskType": "스미싱",
    "riskTypeReason": "택배사를 사칭한 문자 링크를 받은 경험이 있음",
    "usedFacts": ["주소 불일치로 반송된다는 택배 문자를 받음"],
    "riskSignals": [{"signal": "중고거래 앱 사용", "reason": "..."}],
    "judgmentPoints": [{"id": "J1", "description": "앱 밖에서 보낸 결제 링크를 열지 판단"}]
  },
  "scenario": {
    "riskType": "스미싱",
    "fraudSubtype": "택배 사칭",
    "title": "안전결제 링크를 보내온 구매자",
    "channel": "중고거래 앱 채팅",
    "persona": {"name": "김도윤", "role": "구매 희망자", "organization": "모두마켓 이용자", "tone": "..."},
    "background": "훈련 시작 화면에 보여줄 상황 설명",
    "openingMessage": "가상 인물의 첫 메시지 (첫 말풍선)",
    "keyScenes": [{"order": 1, "judgmentPointId": "J1", "goal": "...", "tactic": "...", "request": "링크 클릭", "requestedInfo": []}],
    "expectedActions": [{"id": "A1", "judgmentPointId": "J1", "description": "앱 밖 결제 링크를 열지 않는다"}],
    "mockPage": "금융",
    "basedOnCases": [{"caseId": "FC-13", "title": "안전결제 사이트를 사칭한 가짜 결제 링크",
                      "issuer": "서울특별시", "alertNo": null, "sourceHost": "서울특별시 경제분야 누리집",
                      "sourceDate": "2017-08-14", "sourceUrl": "https://news.seoul.go.kr/economy/?p=83800"}],
    "maxTurns": 15
  },
  "fallbackUsed": false
}
```
전체 필드 설명은 서버를 켜고 `/docs`에서 볼 수 있어요.

Spring이 할 일
1. 응답 전체(`analysis` + `scenario`)를 훈련 세션에 저장합니다. 개인정보가 없는 데이터라 저장해도 됩니다.
2. 화면에 `background`(상황 설명)를 보여주고, `openingMessage`를 가상 인물의 첫 말풍선으로 띄웁니다. `channel`에 따라 문자, 메신저, 중고거래 앱 채팅 화면 스타일을 고릅니다.
3. 저장한 `scenario`는 ③ 훈련 대화 API에 매 턴 그대로 넘기고, `analysis`와 `expectedActions`는 ⑤ 리포트 생성에 넘깁니다.
4. `mockPage`가 있으면 ④ 모의 페이지 템플릿(금융/공공민원/채용)을 준비합니다.
   `basedOnCases`(근거 사례와 출처 링크)는 리포트 화면에서 "이 훈련은 이런 실제 사례를 바탕으로 했어요"로 보여주면 좋아요.
5. 타임아웃은 **60초**로 잡아주세요. AI 서버 내부에서 25초 × 최대 2회로 끝납니다.

---

**`POST /internal/ai/simulation/reply`** (팀 명세 ID: AI-04 → 기능 SIM-007/008/009/011)

훈련 채팅에서 사용자가 답할 때마다 1번 호출합니다. (타임아웃 25초)

요청
```json
{
  "sessionId": "Spring이 만든 세션 ID",
  "scenario": { "② 응답의 scenario를 그대로": "..." },
  "currentScene": 1,
  "history": [
    {"role": "persona", "text": "가상 사기범 메시지"},
    {"role": "user", "text": "사용자 답변"}
  ]
}
```
- 시작할 때는 `history: []`, `currentScene: 1` → 첫 메시지를 돌려줍니다.
- `currentScene`은 **직전 응답의 값을 그대로** 보내주세요.
- `role`은 `"persona"` 또는 `"user"`입니다. ①의 `"ai"`와 달라요.

응답
```json
{
  "personaMessage": "판매대금 받으시려면 계좌 인증이 필요해요. [모의링크]",
  "currentScene": 2,
  "requestType": "개인정보 제공",
  "requestedInfo": ["계좌번호", "전화번호"],
  "inputExpected": true,
  "choices": ["그런 정보는 드릴 수 없어요", "네, 알려드릴게요", "왜 그게 필요하세요?"],
  "judgment": {
    "outcome": "risky",
    "userAction": "결제 링크를 열겠다고 함",
    "achievedActionIds": [],
    "violatedActionIds": ["A1"],
    "reason": "앱 밖에서 보낸 링크를 의심 없이 열려고 함"
  },
  "infoDisclosure": {"requested": [], "provided": [], "disclosed": false},
  "sensitiveDetected": false,
  "sensitiveTypes": [],
  "maskedText": "네 링크 눌러볼게요",
  "done": false,
  "endReason": null,
  "realConcern": false,
  "fallbackUsed": false
}
```

Spring이 할 일
1. `personaMessage`를 가상 인물 말풍선으로 띄우고 history에 `{"role":"persona"}`로 추가합니다. `currentScene`도 저장해서 다음 요청에 보냅니다.
2. `choices`를 버튼으로 보여주세요. 사용자는 버튼을 누르거나 직접 입력할 수 있어요.
3. 사용자 답변은 원문 대신 **`maskedText`로 바꿔서** history에 저장합니다.
4. **턴별 기록을 세션에 누적**합니다 → ⑤ 리포트 재료가 돼요.
   `{turn, scene, judgment(전체), infoDisclosure}`
   - `infoDisclosure`가 리포트의 핵심이에요. `requested`는 그 턴에 사기범이 요구한 개인정보, `provided`는 사용자가 실제로 입력한 종류예요. 예: `{"requested": ["계좌번호","전화번호"], "provided": ["계좌번호"], "disclosed": true}`
   - 실제 값은 응답 어디에도 없어요. 종류만 저장하면 됩니다.
   - `inputExpected: true`면 사기범이 개인정보를 직접 입력하라고 요구하는 중이에요. 이때는 선택지 버튼보다 **입력창을 눈에 띄게** 해주세요. `requestedInfo`는 지금 요구 중인 정보 종류입니다.
5. `judgment`는 **훈련 중 화면에 보여주지 않습니다.** 결과는 리포트에서 공개해요.
6. `[모의링크]`는 클릭할 수 있는 링크로 바꿔서 ④ 모의 페이지(`scenario.mockPage`)로 연결합니다. 모의 페이지에서 한 행동(열기·닫기·로그인 등)은 Spring이 따로 기록합니다 (SIM-014).
7. `done: true`면 훈련을 종료하고 ⑤ 리포트로 이동합니다. 사용자가 직접 "그만하기"를 누르면 AI 호출 없이 바로 종료하면 돼요.
8. `realConcern: true`면 "실제로 피해를 당하셨나요? → 피해 대응 메뉴" 안내를 띄워주세요.

---

**`POST /internal/ai/training/evaluate`** (팀 명세 ID: AI-05 → 기능 SIM-010, RPT-01)

훈련이 끝나면(`done: true` 또는 사용자가 "그만하기") 1번 호출합니다. 타임아웃 **60초**, 화면에 로딩 표시.

요청
```json
{
  "sessionId": "...",
  "scenario": { "② 응답의 scenario 그대로": "..." },
  "turns": [
    {
      "turn": 3, "scene": 2,
      "userText": "계좌는 [계좌번호]요",
      "judgment": { "③ 응답의 judgment 그대로": "..." },
      "infoDisclosure": {"requested": ["계좌번호","전화번호"], "provided": ["계좌번호"], "disclosed": true}
    }
  ],
  "mockPageEvents": [
    {"action": "링크 열기"},
    {"action": "정보 입력", "inputTypes": ["계좌번호"]}
  ],
  "endReason": "scenario_complete"
}
```
- `turns`: ③에서 턴마다 누적한 기록. `userText`는 ③ 응답의 `maskedText`(개인정보 가려진 것)예요.
- `mockPageEvents`: ④ 모의 페이지에서 한 행동. `action`은 `링크 열기 | 페이지 닫기 | 로그인 시도 | 정보 입력 | 공식 채널 확인`. 입력값은 보내지 말고 `inputTypes`(어떤 칸이었는지)만.
- `endReason`: `scenario_complete | max_turns | user_quit`

응답 (요약)
```json
{
  "headline": "개인정보를 넘긴 장면이 있었어요",
  "overview": "총평 2~3문장",
  "infoProtection": {
    "requested": ["계좌번호","전화번호"], "provided": ["계좌번호"], "protected": ["전화번호"],
    "disclosed": true,
    "events": [{"turn": 3, "scene": 2, "requested": ["계좌번호","전화번호"], "provided": ["계좌번호"]}],
    "message": "3번째 답변에서 처음으로 개인정보를 넘겼어요. 넘긴 정보: 계좌번호 / 지킨 정보: 전화번호"
  },
  "goodActions": [{"actionId": "A3", "description": "...", "turns": [4], "recoveredLater": false}],
  "missedActions": [{"actionId": "A1", "description": "...", "turns": [1], "recoveredLater": false}],
  "missedRiskSignals": [{"signal": "...", "explanation": "...", "turn": 1}],
  "turnFeedback": [{"turn": 3, "scene": 2, "outcome": "risky", "userAction": "...", "providedInfo": ["계좌번호"], "feedback": "..."}],
  "mockPage": {"actions": ["링크 열기","정보 입력"], "inputTypes": ["계좌번호"], "risky": true, "message": "..."},
  "recommendedActions": ["..."],
  "officialContacts": [{"name": "경찰청", "contact": "112", "when": "..."}],
  "basedOnCases": [{"caseId": "FC-13", "title": "...", "sourceUrl": "..."}],
  "fraudType": {"riskType": "기타", "subtype": "중고거래 사기"},
  "endReason": "scenario_complete",
  "disclaimer": "...",
  "fallbackUsed": false
}
```

**사실은 코드가 계산하고, 글만 AI가 써요.** `infoProtection`, `goodActions`, `missedActions`, `recommendedActions`, `officialContacts`는 기록에서 코드가 정해서 AI가 틀린 말을 해도 바뀌지 않아요. 공식 연락처는 AI가 만들지 않고 `app/report_data.py`의 고정값이에요.

Spring이 할 일
1. 응답을 그대로 리포트 화면에 그리면 돼요. 맨 위에 `headline` + **`infoProtection.message`** (가장 중요)를 크게 보여주세요.
2. 로그인 사용자가 저장하면(RPT-001) 이 응답 JSON을 저장하고, **훈련 대화 원문은 삭제**합니다. 리포트에는 실제 개인정보 값이 없어요.
3. PDF(RPT-004)도 이 응답만으로 만들면 됩니다.

---

## 다음 할 일
- [ ] 팀 LLM 공급자 결정 → `.env` 연결
- [ ] GitHub에 올리기 (`.env`가 안 올라가는지 확인)
- [ ] Dockerfile 만들기 (팀이 `docker compose up`으로 같이 켤 수 있게)
- [ ] 가상 사용자 5~6명(중고거래, 구직, 택배, 투자 등)으로 `chat.py`를 돌려서 ①②③⑤ 프롬프트 다듬기
- [ ] 응답 시간 확인 (훈련 턴 25초, 시나리오·리포트 60초 안에 끝나는지)
- [ ] ③ 진짜 LLM으로 판정 정확도 확인 (잘 거절했는데 risky로 나오는지 등)
- [ ] Spring 연동 테스트 (Docker로 띄울지는 SET-01 결정에 맞추기)
- [ ] 사례 DB 팀 검토: `app/data/fraud_cases.yaml`의 15건을 출처 링크와 대조하고 `reviewStatus`를 고치기 (지금은 "AI가 찾아 요약, 팀 검토 전")
- [ ] SIM-001 이미지 첨부 + OCR (선택 기능, 나중에)
