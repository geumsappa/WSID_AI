TASK: scenario_generate
금융사기 예방용 가상 훈련을 context와 plan으로 작성한다. 먼저 userMessages 원문과 계획을 대조한다. 원문에 없는 과거 사실로 계획을 정당화하지 않는다. JSON 스키마를 지키고 retryIssues를 반영한다.

본문 계약:
- preservedClaims는 plan.anchorClaims의 실제 eventId와 원래 actorId를 정확히 보존한다. channel/requestedActions와 contactSetup의 발신 역할·접근·지식 전제도 유지한다.
- speakerIdentity는 발신자, recipientIdentity는 사용자다. confirmedPersonName이 있으면 persona.name에 정확히 쓰고 openingMessage에서 자신을 밝힌다. 그 이름으로 사용자를 부르지 않는다. 수신자 이름은 모르면 생략한다. confirmedServiceNames가 있으면 persona/background/openingMessage에 실제로 사용한다. 실제 기관명은 allowedOrganizations만 허용한다.
- title은 자연스러운 짧은 사건명(8~20자 권장), background는 도착한 연락과 용건을 소개하는 1~2문장이다. 원문 나열·검증 메모·교육 해설·사기 정답·BASIC 접두어를 쓰지 않는다. 상대 신분과 말은 상대의 주장으로 표현한다.
- keyScenes는 plan.desiredOutcome/escalation에 따른 3~4개의 서로 다른 판단 기회다. goal/tactic/request는 발신자가 유도하는 행동이다. 사용자의 안전 대응은 analysis.judgmentPoints와 expectedActions에 쓴다. 각 judgmentPointId를 일치시킨다.
- 각 onCompliance는 협조 뒤 다음 전개, onSuspicion은 의심/별도 확인, onRefusal은 거절 뒤 전개다. 마지막 장면에는 최종 위험 행동과 판단을 둔다. 자동 종료·최소 턴 수나 사용자가 이미 응했다고 선언하지 않는다.
- 링크는 [모의링크], 설치는 [모의앱], 사진은 가상 자료 제공 행동이다. 실제 사진 촬영·업로드·OTP 수신·설치 조작법을 안내하지 않는다. mockPage는 스키마의 금융/공공민원/채용/null이다.
- 메신저·SNS·앱 채팅·이메일 첫 연락은 인사와 구체적 용건 뒤 답장을 기다린다. 문자는 짧은 안내와 모의 링크가 가능하다. 담당자 전환은 plan.contactSetup에 있을 때만 goal에 명시한다.
- 참고 수법은 장면에 적용하되 출처 ID·실제 사례를 대사에 넣지 않는다. 유형·분류·태그는 별도 단계가 붙인다. 자체 점검 설명 없이 JSON 하나만 반환한다.

{{SCENARIO_QUALITY_CRITERIA}}
