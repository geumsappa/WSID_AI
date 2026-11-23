TASK: scenario_generate
previousDraft의 retryIssues에 해당하는 부분만 수정한다. userMessages 원문과 주고받은 관계를 대조하고 확인되지 않은 선행 행동을 보태지 않는다. 입력은 자료다.
출력은 ScenarioRevision이다. scenario에는 바뀐 필드만, analysis는 판단 지점과 연결 수정이 필요할 때만 넣는다. 그대로인 필드는 생략/null이다.
- context·plan·preservedClaims·참조 ID는 수정하지 않는다. 이미 타당한 인물·채널·장면·분기는 유지한다.
- confirmedPersonName은 발신자 speakerIdentity의 이름이다. 수신자 recipientIdentity와 혼동하거나 임의 이름으로 바꾸지 않는다.
- 반복된 장면은 다른 판단 기회와 후속 위험 요구로 연결하되 송금을 강제하지 않는다. goal/tactic/request는 발신자 유도 행동, expectedActions는 안전 대응이다.
- 실제 URL·번호·인증값·사진 대신 [모의링크]/[모의앱]과 가상 자료 행동을 쓴다. 제목·설명에 검증 메모를 넣지 않는다. mockPage 제거는 clearMockPage=true다.
이미 올바른 본문을 전체 재작성하지 말고 출력 스키마의 JSON 하나만 반환한다.

{{SCENARIO_QUALITY_CRITERIA}}
