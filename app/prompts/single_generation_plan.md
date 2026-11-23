TASK: generation_plan
사기 예방훈련의 실제 context와 selectedPlan 하나를 작성한다. 여러 방향을 고려하되 원문으로 성립하는 최소 전제의 접근을 선택한다. 고정 유형·예시 대본·후보 비교·추가 질문은 출력하지 않는다. 코드 검사 뒤 독립 검증이 계획과 본문을 함께 심사한다.

{{CONTEXT_FACTS}}

계획 계약:
- anchorClaims는 focusEventIds와 personalizationBasis에 사용한 확정 사건의 원래 actorId를 보존한다. personalizationBasis에 실제 eventId/evidenceId, 추가 맥락과 접근에 미치는 영향을 짧게 연결한다. readinessEvidence도 원문과 대조한다.
- PERSON은 확인된 이름의 인물을 impersonatedActorId로 참조한다. SERVICE/OTHER를 임의 지인 사칭으로 바꾸거나 미완료 이름 확인을 우회하지 않는다. recipientActorId는 사용자다.
- requestedActions는 allowedActions의 값이다. desiredOutcome은 중간 확인 이후의 위험 요구/학습 목표, escalation은 3~4단계와 의심·거절 분기를 간결히 계획한다.
- contactSetup은 필수다. senderRole/contactPremise/knowledgePremise, 실제 evidenceIds와 새 가정 fictionalAssumptions를 구분한다. 앞으로의 가상 사건은 fictionalEvents에 쓴다.
- referenceUses는 실제 참고한 검색 수법만 caseId/정확한 technique/adaptationReason으로 연결한다. 미사용은 빈 배열이다. missingInformation=[]이다.
- 설명은 필요한 근거와 연결만 짧게 쓴다. 분류·태그·대안 목록 없이 출력 스키마의 JSON 하나만 반환한다.

{{SCENARIO_QUALITY_CRITERIA}}
