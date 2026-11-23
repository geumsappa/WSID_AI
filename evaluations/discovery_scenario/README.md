# 사실·계획·검증 기반 품질 평가

대상은 사용자 상황 파악과 시나리오 생성입니다. 서비스 함수를 호출하므로 FE/Spring 통신·인증은 별도 검증 대상입니다.

## 실행

WSID_AI 터미널에서 실행합니다.

```powershell
# 목록만 확인
.\.venv\Scripts\python.exe -m evaluations.discovery_scenario.runner --list

# 도구 작동 확인, API 비용 없음. 실제 의미 품질 점수는 아님
.\.venv\Scripts\python.exe -m evaluations.discovery_scenario.runner

# 지인이 계산한 사례만 실제 AI로 확인 (비용 발생)
.\.venv\Scripts\python.exe -m evaluations.discovery_scenario.runner --live --case actor-peer-payment

# 행동 주체만 바꾼 대조 사례 비교
.\.venv\Scripts\python.exe -m evaluations.discovery_scenario.runner --live --case actor-peer-payment --case actor-self-payment

# 전체 28개 사례 × 3회 (비용 발생)
.\.venv\Scripts\python.exe -m evaluations.discovery_scenario.runner --live --repeats 3
```

기존 .env의 LLM_PROVIDER/LLM_MODEL/LLM_API_KEY를 사용합니다. --live가 없으면 외부 LLM을 사용하지 않습니다.
결과는 Git 제외된 `evaluation-results/<UTC 시각>/` 아래 summary.md, results.json, manual-review.csv에 저장합니다.

## 기준과 한계

현재 프로토콜은 `candidate-selection-v1`입니다. 과거 활동별 보정 규칙 평가와 기준이 달라 이전 성공률과 그대로 비교하면 안 됩니다.

| 항목 | 기준 |
| --- | --- |
| fact_contains / fact_excludes | 현재 초점의 사실 표현에 필요한 문자열 포함/제외 |
| event_actor | 확정 이벤트의 증거에 기대 표현이 있고 행동 주체가 사용자/타인이라는 정답과 일치 |
| fact_type / scenario_type | 지정한 코드 또는 허용 코드 목록과 일치. 유형을 고정하지 않은 사례는 채점하지 않음 |
| readiness / turn_readiness | 해당 입력 단계에 검증된 계획이 있는지 기대값과 비교 |
| no_exact_duplicate_facts | 동일 문자열 사실이 중복되지 않음. 의미가 같은 다른 문장까지 잡는 지표는 아님 |
| followup_present / no_exact_repeated_question | 준비 안 된 경우 질문 존재, 동일 문자열 질문 반복 없음 |
| scenario_schema / scenario_context | DTO로 파싱 가능, 지정한 표현이 실제 화면 내용에 포함됨 |
| execution | 서비스 호출에 예외 없음 |

성공률은 통과 수 / 해당 평가 수입니다. 예외·미준비로 미완료한 적용 항목도 실패로 셉니다.
정확한 단어 비교는 동의어·요약으로 오판할 수 있으므로 결과를 읽어 확인해야 합니다.
활동 category는 호환용 메타데이터이며 생성 유형을 고정하거나 품질 정답으로 쓰지 않습니다.
빠른 맥락 준비 판정이 false면 생성 API를 호출하지 않습니다. true면 생성 시점에 상세 추출·계획·검증을 수행합니다.
사실·행동 주체 정답은 복사된 대화 입력으로 평가하지 않고, 상세 검증과 생성이 성공한 뒤 검증된 그래프로 평가합니다.

results.json의 `reviews`는 검증기의 판단 기록이며 실제 정확도나 사람 평가 점수가 아닙니다.
생성기의 승인 자체를 정답으로 삼지 않고 별도 정답인 행동 주체와 인용 근거, 수동 검토를 함께 봅니다.

## 시간·호출 수

상황 파악·생성 서비스 각각의 시간과 discovery_brief/candidate_plan/candidate_compare/semantic_review/scenario_generate/scenario_classify 호출 시간을 구분합니다.
각각 호출 수·오류 수·평균·중앙값·P95를 표시합니다. 모델 호출 시간은 provider 처리와 JSON 파싱을 포함합니다.
FE/Spring 전체 대기 시간이나 순수 토큰 생성 속도는 아닙니다. P95는 오름차순 ceil(0.95 × 표본 수)번째 값이며 적은 표본에서는 참고값입니다.
표본이 없으면 null/미측정입니다. 재시도는 서비스 전체 시간에 포함됩니다.

보통 대화 1턴에 모델 1회, 시나리오 만들기에 후보 작성·후보 비교·생성·본문 검증·사후 분류 5회를 호출합니다. 재생성 시 늘어나므로 먼저 한 사례만 실행하세요.
오류 시 자동으로 기본 시나리오로 바꾸지 않습니다. 사용자가 명시적으로 선택한 사례 기반 기본 훈련(BASIC)은 정상 흐름이며, 맞춤 생성(PERSONALIZED)과 별도로 집계합니다. provider 오류나 의미 검증 거절로 끝나면 실패·미준비로 기록합니다.

## 사람이 채점할 부분

자연스러움·개연성·사실/가상 구분을 각각 0점(문제), 1점(일부 부족), 2점(적절)으로 채점합니다.
공란은 미평가이며 자동 성공률에 합치지 않습니다. 특히 누가 누구에게 왜 접근하는지, 타인이 한 일이 사용자에게 옮겨졌는지 확인합니다.
모델·프롬프트 해시·사례 해시·프로토콜을 함께 기록해 같은 조건으로 변경 전후를 비교합니다.

준비 판정에는 ‘활동명만으로 일반적인 시나리오를 만들 수 있음’과 ‘사용자 맥락을 반영할 정보가 충분함’을 구분한다.
`terse-coffee`, `terse-job`은 추가 질문을 기대하고 `coffee-details-flow`는 짧은 입력 뒤 구체적인 답변을 통해 준비되는지 확인한다.
이 기준은 이전의 빠른 준비 판정보다 엄격하므로 과거 성공률과 그대로 비교하지 않는다.

대화 응답시간과 시나리오 만들기의 대기시간을 혼합해서 비교하지 마세요.
새 상태에서는 상세 사실/유형 추출이 생성 시점으로 옮겨져, 생성을 요청하지 않은 사례에는 해당 추출 지표를 채점하지 않습니다.
프로토콜이 다른 과거 성공률과 직접 비교하지 마세요. 생성 전 맥락 준비와 상세 검증의 승인도 별개의 판단입니다.

생성 모델은 고정 유형·예시 대본을 받지 않습니다. 현재는 생성 전에 관련 사실 수법을 검색해 선택적으로 제공하며, 사용 여부와 적합성을 따로 검증합니다. 유형/태그는 본문 승인 이후에 붙이며, 호환용 대표 코드가 이야기의 생성 조건으로 전달되지 않는지 별도 회귀 테스트합니다.
자유 세부 유형명은 고정 문자열 정답으로 채점하지 않습니다. 기존 대표 코드 정답은 해당 사례에서 의도한 의미와 일치하는지 별도로 검토하세요.
현재는 순서·불변성과 API 호환성의 자동 테스트 및 단일 실제 사례를 확인했습니다. 다양성/억지 연결 개선은 동일 조건의 반복 비교와 수동 채점으로 평가해야 합니다.

대화 준비는 모델의 자유로운 ready 판단 대신 누적된 확인 항목과 질문 목적 상태로 결정한다.
질문 문장은 목적별 공통 문장이고, 이미 답했거나 한 번 물은 목적은 다시 선택하지 않는다.
활동 종류별 정답 템플릿이 아닌 공통 상태 전이의 회귀 테스트는 tests/test_discovery_state.py에서 확인한다.

results.json의 `intake_decisions`에 두 후보와 공통 근거를, `candidate_comparisons`에 후보별 검토·점수·차이 판정을 기록합니다. 이는 내부 모델 판단이며 독립적인 품질 정답이나 다양성 성공률로 집계하지 않습니다. 본문 검토는 맥락이 전개에 실제 쓰였는지와 일반 패턴 여부도 검사합니다. 과거 훈련 전체와 비교하는 중복 검사는 아닙니다.


## 이름 확인·기본 훈련 평가 (candidate-selection-v2-explicit-basic)

28개 사례 중 다음 네 사례를 추가했습니다. 모두 서비스 호출용 합성 입력이며 실제 개인정보가 아닙니다.

- `friend-nickname-flow`: 별명 질문 전 준비 미완료, 별명 확인 후 준비 완료, 표시 이름과 첫 대사에 같은 별명 사용.
- `explicit-basic-choice`: 정보 제공을 중단한 뒤 기본 훈련을 직접 선택하면 BASIC 생성. 가상 설정을 사용자 sourceFacts에 넣지 않고, 선택한 사례의 출처를 유지.
- `decline-basic-choice`: 제안을 거절하면 준비 완료로 처리하지 않음.
- `solo-coding-purpose`: 충분히 구체적인 단독 작업은 친구 이름 없이 준비 가능.

```powershell
.\.venv\Scripts\python.exe -m evaluations.discovery_scenario.runner --live --case friend-nickname-flow --case explicit-basic-choice --case decline-basic-choice
```

`generation_modes`에서 맞춤/기본 각각의 요청 수, 생성 성공 수·성공률, 전체 기능 기준 통과 수·비율, 생성 대기시간을 확인합니다. 준비 미완료·생성 예외도 요청 수에서 빠지지 않으며, 기본 훈련 성공을 맞춤 생성 성공으로 집계하지 않습니다. 오류가 있거나 검사 결과가 하나도 없는 행도 기능 통과로 집계하지 않습니다.

이름이 제목에만 들어간 경우는 이름 개인화 통과가 아닙니다. persona.name 정확한 일치와 openingMessage의 이름 포함을 각각 검사합니다. 현재 질문 주제는 이번 intakeTurn에 실제 생성된 질문만 검사해 이전 턴의 이름 질문을 재사용한 상태가 통과하지 않도록 했습니다.

기본 훈련의 출처 지표는 선택 ID·생성 전 참고 ID·실제 사용 ID와 출처 표시의 기계적 일관성입니다. 실제로 수법을 적절하게 활용했는지, 도입 문장의 자연스러움, 인과관계의 개연성을 자동으로 보증하는 지표가 아닙니다. 해당 항목은 `manual-review.csv`에서 사람이 채점합니다.

보고서에는 프롬프트·사례 목록 외에 실행 코드·스키마·참고 사례 JSON의 해시도 기록합니다. 결과는 기존 Git 제외 `evaluation-results/`에 저장합니다. 기존 v1 결과와 v2의 종합 통과율을 동일 기준으로 비교하지 마세요.

`requested`는 생성까지 기대한 평가 사례 수, `attempted`는 실제 생성 함수를 호출한 사례 수, `blocked_before_generation`은 준비 미완료 등으로 호출 전 멈춘 수입니다. `generation_success_rate`는 생성 완료/기대 사례, `attempt_success_rate`는 생성 완료/실제 생성 호출이며 호출이 없으면 null입니다. 추가 질문으로 준비되지 않은 사례를 AI API 오류로 해석하지 마세요. 코드·스키마·사례·프롬프트 해시는 긴 외부 호출 전에 수집합니다.

질문 필요성 검토 결과는 각 턴의 facts.questionAssessments와 question_need_reviews에 저장됩니다. semantic_review_attempts와 scenario_draft_attempts에는 거절된 시도도 포함되므로 최종 반환 성공만으로 생성 품질을 판정하지 마세요. 단계별 시간에서 discovery_question_review를 별도로 확인할 수 있습니다. 이름 확인·준비도 성공과 이후 본문 생성 검증 성공은 서로 다른 항목입니다. 대기 안내 문구 반복은 실제 questionHistory에 있는 질문 반복과 구분합니다.

## 2026-11-23: 접점 기반 준비 완료와 단일 계획 생성

새 맞춤 생성 경로는 `generation_plan → scenario_generate → semantic_review → scenario_classify`의 정상 호출 4회다. 장문 후보 2개 작성·비교를 대신해 계획 하나를 작성하고, 독립 검증은 `CONTEXT_PLAN_AND_DRAFT` 범위에서 원래 사용자 발언·계획·본문을 모두 검사한다. 인용·주체·참조 ID·확인된 이름과 사례 수법 검사는 여전히 코드로 먼저 수행한다. 기존 저장 계획/호환 경로와 BASIC은 기존 후보 비교 경로를 유지한다. 재시도는 호출 수를 늘릴 수 있다.

상황 파악의 `readinessBasis`는 실제 인용과 접점의 연결을 보존하며, 질문의 `necessity=OPTIONAL`은 필수에서 제외한다. PERSON의 이름·관계, SERVICE의 정체성 조건은 이 표시로 건너뛸 수 없다. 접점 근거의 미확인 인용도 거절한다.

프로토콜은 `single-plan-joint-review-v3`이며 새로운 프롬프트와 로더 해시도 보존한다. 이전 v2 결과와 같은 조건의 평가인 것처럼 합치지 않는다. 호출 수 감소는 로컬 회귀 검사로 확인했으며 실제 생성 지연시간의 감소는 아직 측정하지 않았다.

생성·수정·검증의 `userMessages`에는 보존된 원래 사용자 발언을 함께 전달한다. 선택한 context의 인용만으로 대화를 대체하지 않는다. 개인정보 마스킹은 유지하며, 원문이 없는 이전 형식은 `sourceScope=LEGACY_FACTS` 또는 `RETAINED_QUOTES`, 사례 기반 기본 훈련은 `FICTIONAL_CONTEXT`로 구분한다. 원문과 계획·본문 사이의 제공자/수혜자·지불자·부탁 방향을 공통 품질 기준에서 대조한다. 이 보완은 입력 보존과 검사 기준의 강화이며 실제 AI의 의미 판단이 항상 정확함을 보장하지 않는다.

LLM에 전달하는 JSON 스키마는 `response_schema()`로 문서용 title/description/examples/$comment만 제거한다. 실제 필드명(title/description 포함), 타입·필수 여부·enum·길이/개수·참조·기본값과 Pydantic 검증은 유지한다. 원래 API 스키마와 사실 데이터는 변경하지 않는다. 필드 사이 JSON 공백도 줄이되 문자열 내부 공백은 보존한다. 이 감소량은 입력 글자 수 기준이며 실제 토큰/비용 감소율은 별도 측정해야 한다.

상황 파악 지침도 중복을 줄였다. 기본 지침은 약 11,500자에서 약 3,100자로 축약하고 새로 확인한 사실과 현재 질문에 필요한 판단만 작성하도록 했다. 원문·이름/주체 보존·필수/선택 정보 구분·질문 종료·질문 재검토와 출력 검사는 유지한다. 대화 기록을 잘라 줄이거나 재시도 제한을 변경한 것이 아니며, 실제 응답시간 개선은 별도 실측 대상이다.

사후 분류의 유사 사례도 완성된 제목·도입·대사·장면으로 `scenario_patterns.json`을 검색해 최대 3개만 전달한다. 기존 분류용 20개 전체 목록은 새 분류 경로에서 보내지 않는다. 검색 밖 ID·없는 수법·중복 출처 선택을 거절하며 무관한 자료를 강제하지 않는다. 유형/태그는 검색 결과가 없어도 생성하고 출처는 비워 둔다. `classification_attempts`에 후보 ID와 코퍼스/검색기 정보를 보존한다. `basedOnCases`는 사후 유사 사례, `generationEvidence`는 생성 전 실제 사용 근거로 계속 구분한다.
