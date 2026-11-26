messages의 사용자 발언만 근거로 context를 작성한다.
- USER는 한 명, 타인·조직·집단은 별도 엔티티다. 역할로 주체가 명시되면 actorId를 보존하고, 주체 자체가 불명확할 때만 null이다.
- events에 주체·행동·대상·참여자를 기록한다. 확정 CONFIRMED, 불확실 UNCERTAIN, 부정 NEGATED를 구분한다. 질문·예시·추측을 사실로 바꾸지 않는다.
- evidence는 해당 turn에서 주체와 행동을 포함한 원문을 정확히 인용한다(200자 이하). ID는 actor_user/event_a/quote_a처럼 짧게 쓴다.
- focusEventIds는 현재 초점의 CONFIRMED 사건만 참조한다. 같은 경험에 추가된 역할·상호작용을 누락하거나 다른 주제로 바꾸지 않는다. 불확실·부정 사건은 보존하되 확정 앵커로 쓰지 않는다.
- previousContext의 유효한 사실·ID를 유지하고 명시적 정정만 최신 증거로 갱신한다. confirmedTopics의 turn/quote도 원문과 대조한다.
- PERSON_NAME의 확인된 이름·별명·가족 호칭을 해당 PERSON.label에 그대로 쓴다. 질문 맥락으로 인물을 구별하고 임의 이름·관계를 만들지 않는다.
- category는 호환 메타데이터다. 인사·거절·모름을 활동으로 만들지 않는다. realConcern은 사용자가 실제 금전·개인정보 피해를 명시했을 때만 true다.
