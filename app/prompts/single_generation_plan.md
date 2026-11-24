TASK: generation_plan
원문으로 성립하는 최소 전제의 context와 selectedPlan 하나를 만든다. 고정 유형·예시 대본·후보 비교·추가 질문은 출력하지 않는다. 계획과 본문은 이후 함께 검증한다.

{{CONTEXT_FACTS}}

{{CONTEXT_SUFFICIENCY}}

REPLAN은 recoveryFeedback의 원인이 된 previousPlan의 명분·가정을 수정한다. 실패한 본문을 사실로 삼지 말고 messages와 confirmedTopics를 유지한다.
- anchorClaims는 focusEventIds와 personalizationBasis의 확정 사건·원래 actorId를 보존한다. personalizationBasis는 실제 eventId/evidenceId와 접근에 미치는 영향을 연결하고 readinessEvidence도 원문과 대조한다.
- PERSON_NAME=KNOWN이면 name 전체(호칭 포함)를 PERSON entity.label에 그대로 쓰고 PERSON은 그 ID를 impersonatedActorId로 사용한다. SERVICE/OTHER를 임의 지인 사칭으로 바꾸거나 미완료 이름을 우회하지 않는다. recipientActorId는 사용자다.
- requestedActions는 allowedActions의 값이다. desiredOutcome은 최종 위험 요구/학습 목표, escalation은 3~4단계와 의심·거절 분기다.
- contactSetup의 발신 역할·명분·지식 전제, 실제 evidenceIds와 fictionalAssumptions를 구별한다. 이후 가상 사건은 fictionalEvents다.
- referenceUses는 실제 사용한 검색 수법의 caseId/정확한 technique/adaptationReason이다. 미사용은 [],missingInformation=[]이다.
분류·태그·대안 없이 근거와 연결을 짧게 쓴 출력 스키마 JSON만 반환한다.

{{SCENARIO_QUALITY_CRITERIA}}
