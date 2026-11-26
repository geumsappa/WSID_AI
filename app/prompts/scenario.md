TASK: scenario_generate
context·plan과 userMessages를 대조해 금융사기 예방훈련을 만든다. 미확인 과거 사실로 계획을 정당화하지 말고 retryIssues와 출력 스키마를 지킨다.
- preservedClaims는 plan.anchorClaims의 eventId·actorId를 보존한다. channel/requestedActions와 contactSetup의 발신 역할·접근·지식 전제를 유지한다.
- speakerIdentity는 발신자,recipientIdentity는 사용자다. confirmedPersonName은 persona.name에 유지하고 사용자 호칭으로 쓰지 않는다. 자기소개에 쓰면 발신자임을 명확히 하며 첫 대사에 이름 반복은 필수가 아니다. 모르는 수신자 이름은 생략한다. confirmedServiceNames를 persona/background/openingMessage에 활용하고 실제 기관명은 allowedOrganizations만 쓴다.
- title은 자연스러운 사건명(8~20자 권장),background는 도착한 연락·용건 1~2문장이다. 원문 나열·검증 메모·교육 해설·사기 정답·BASIC 접두어는 빼고 상대 신분·말은 상대 주장으로 쓴다.
- keyScenes는 desiredOutcome/escalation에 따른 3~4개 판단 기회다. goal/tactic/request는 발신자 유도,analysis.judgmentPoints와 expectedActions는 안전 대응이며 judgmentPointId를 맞춘다.
- onCompliance=협조 후 전개,onSuspicion=의심/별도 확인,onRefusal=거절 후 전개다. 마지막에 최종 위험 행동·판단을 둔다. 자동 종료·최소 턴·사용자의 기응답을 선언하지 않는다.
- 링크=[모의링크],설치=[모의앱],사진=가상 자료 행동이다. 실제 촬영·업로드·OTP 수신·설치 조작법은 안내하지 않는다. mockPage는 금융/공공민원/채용/null이다.
- 메신저·SNS·앱 채팅·이메일 첫 연락은 인사·용건 뒤 답장을 기다린다. 문자는 짧은 안내·모의 링크가 가능하다. 담당자 전환은 contactSetup에 있을 때만 goal에 쓴다.
참고 수법은 장면에 적용하되 출처 ID·실제 사례를 대사에 넣지 않는다. 분류·태그는 별도 단계가 붙인다. 자체 점검 설명 없이 JSON만 반환한다.

{{SCENARIO_QUALITY_CRITERIA}}
