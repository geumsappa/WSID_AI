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

# 전체 22개 사례 × 3회 (비용 발생)
.\.venv\Scripts\python.exe -m evaluations.discovery_scenario.runner --live --repeats 3
```

기존 .env의 LLM_PROVIDER/LLM_MODEL/LLM_API_KEY를 사용합니다. --live가 없으면 외부 LLM을 사용하지 않습니다.
결과는 Git 제외된 `evaluation-results/<UTC 시각>/` 아래 summary.md, results.json, manual-review.csv에 저장합니다.

## 기준과 한계

현재 프로토콜은 `deferred-discovery-v1`입니다. 과거 활동별 보정 규칙 평가와 기준이 달라 이전 성공률과 그대로 비교하면 안 됩니다.

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

상황 파악·생성 서비스 각각의 시간과 discovery_brief/intake_plan/semantic_review/scenario_generate 호출 시간을 구분합니다.
각각 호출 수·오류 수·평균·중앙값·P95를 표시합니다. 모델 호출 시간은 provider 처리와 JSON 파싱을 포함합니다.
FE/Spring 전체 대기 시간이나 순수 토큰 생성 속도는 아닙니다. P95는 오름차순 ceil(0.95 × 표본 수)번째 값이며 적은 표본에서는 참고값입니다.
표본이 없으면 null/미측정입니다. 재시도는 서비스 전체 시간에 포함됩니다.

보통 대화 1턴에 모델 1회, 시나리오 만들기에 상세 계획·계획 검증·생성·본문 검증 4회를 호출합니다. 재생성 시 늘어나므로 먼저 한 사례만 실행하세요.
기본 시나리오 대체는 제거했습니다. provider 오류나 의미 검증 거절로 끝나면 실패·미준비로 기록합니다.

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
