TASK: discovery_question_review
proposedDecision의 추가 질문을 원문과 대조한다. 초기 경험 질문은 상세 사유 검토 대상이 아니다. 제안을 정답으로 보지 말고 초점·행동 주체를 보존한다.

{{INTAKE_SUFFICIENCY}}

- 다른 주제에 이미 답이 있어도 재사용한다. 새 사실은 만들지 않는다. 필요한 정보가 없을 때만 MISSING이며 구체적인 missingDetail/scenarioImpact를 쓴다. 기존 답이 있으면 ANSWERED와 정확한 turn/quote, 없어도 진행 가능하면 UNNECESSARY/OPTIONAL이다.
- 같은 사실·닫힌 주제를 다시 묻지 않는다. 남은 필수가 없으면 준비 완료, 있으면 questionTargets 안의 질문 하나만 존댓말로 한다.
DiscoveryOutput JSON만 반환한다. updates(새 확인만),personalizationTarget,requiredTopics,focusChanged=false,questionAssessments와 action을 쓴다.
action은 ASK={type,nextTopic,nextQuestion},VERIFY_READY={type,readinessBasis},STOP={type,evidence:{turn,quote}},OFFER_BASIC={type}이다. ASK의 주제·질문은 null·빈값 없이 필수다. STOP은 현재 중단 발언을 인용하고 OFFER_BASIC은 허용된 때만 쓴다.
주제는 ACTIVITY/ACTIVITY_DETAIL/SETTING/SERVICE/PEOPLE/PERSON_NAME/TIME/PURPOSE/ACCESS/INTERACTION/TRANSACTION/ROLE다. assessments는 {topic,status,necessity,missingDetail,scenarioImpact,reason,evidence:[{turn,quote}]}이며 status=MISSING/ANSWERED/UNNECESSARY,necessity=REQUIRED/OPTIONAL이다. updates는 실제 turn/quote와 KNOWN/PARTIAL/DECLINED/NOT_APPLICABLE, 확인된 PERSON_NAME이면 name을 쓴다. 인용·질문 180자 이하, readinessBasis는 {topics,evidence:[{turn,quote}],contactReason}이다.

상태별 필드는 스키마를 따른다. name은 PERSON_NAME=KNOWN만, MISSING은 missingDetail/scenarioImpact/necessity, ANSWERED는 원문 evidence, UNNECESSARY는 reason만 쓴다. 다른 상태의 필드는 넣지 않는다.
