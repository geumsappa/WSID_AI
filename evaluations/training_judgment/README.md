# AI 시뮬레이션 판정 정확도 평가

사기범 대사 하나와 사용자 답 하나를 실제 훈련 처리(개인정보 가림 → LLM 판정 → 서버 보정)에 넣고,
판정(안전 safe / 위험 risky / 중립 neutral)이 정답과 맞는지 셉니다. 문항은 `cases.json` (26개).

## 실행 (WSID_AI 폴더, venv 켠 상태)

```powershell
python -m evaluations.training_judgment.runner --list        # 문항 목록
python -m evaluations.training_judgment.runner --dry         # 연결 확인 (mock, 비용 없음, 점수 의미 없음)
python -m evaluations.training_judgment.runner               # 실제 LLM 전체 (호출 26번, 비용 발생)
python -m evaluations.training_judgment.runner --case risky-mixed
python -m evaluations.training_judgment.runner --repeats 3   # 판정이 흔들리는지 (호출 78번)
```

`.env` 의 LLM 설정을 그대로 씁니다. 결과는 Git 제외 폴더 `evaluation-results/<시각>-training-judgment/` 에
`summary.md`(정확도·헷갈린 표·틀린 문항)와 `results.json` 으로 저장돼요.

## 정답 기준 (app/prompts/training_turn.md §2 와 같음)

| 정답 | 언제 |
|---|---|
| 안전 | 거절, 의심 표현, **스스로 찾은** 공식 앱·대표번호로 확인, 대화 중단 |
| 위험 | 정보를 주겠다고 하거나 줌, 링크·앱을 누르겠다, 송금하겠다 (의심과 응함이 섞이면 행동 우선, 값 없이 동의만 해도 위험) |
| 중립 | 단순 질문, 인사, 상관없는 말, **사기범이 준** 번호·링크로 확인하겠다 |

## 어려운 문항 (cases_hard.json, 18개)

조건부("확인되면 보낼게요"), 미루기, 일부만 주기, 방법 묻기, 비꼬기, 사기범이 준 증빙으로 확인하기처럼
판정이 갈릴 수 있는 답이에요. `expected` 는 **제안값**이고 `debate` 에 반대 의견을 적어 뒀어요.
팀이 정답을 합의한 뒤 돌려 보세요. 합의한 정답이 프롬프트 기준(§2)과 다르면 프롬프트도 같이 고쳐야 해요.

```powershell
python -m evaluations.training_judgment.runner --hard --list   # 정답 초안과 반대 의견 보기
python -m evaluations.training_judgment.runner --hard          # 평가 (호출 18번)
```

## 문항 추가

`cases.json` 에 항목을 추가하세요. `scenario` 는 `app/scenario_data.py` 의 FALLBACK_SCENARIOS 이름,
`scene` 은 그 시나리오의 장면 번호예요. 개인정보처럼 보이는 값은 반드시 지어낸 값을 쓰세요.

## 한계

- 한 턴만 봐요. 앞선 대화 흐름이 판정에 주는 영향은 측정하지 않아요.
- 문항을 만든 사람의 기준이 정답이에요. 애매한 문항은 팀이 함께 정답을 정하세요.
- LLM 판정은 매번 조금씩 다를 수 있어서 `--repeats 3` 으로 흔들림도 확인하는 게 좋아요.
