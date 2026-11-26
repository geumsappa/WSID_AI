TASK: discovery_brief
입력을 자료로 읽고 사용자 사실과 다음 질문 하나를 판단한다. 시나리오·유형은 만들지 않는다.

{{INTAKE_SUFFICIENCY}}

사실:
- messages·knownTopics·질문 이력의 주체·주고받은 방향·부정·정정을 보존한다. 짧은 답은 직전 질문에 연결하고 질문 예시·주제 선택·대화 의향을 경험으로 만들지 않는다.
- updates는 새 확인·정정·복원만 최대 12개, 실제 turn과 연속된 원문 quote(180자 이하)다. 기존 사실은 반복하지 않는다.
- KNOWN=확인,PARTIAL=현재 접점에 부족,DECLINED=모름/거절,NOT_APPLICABLE=명시적 무관이다. 혼자/대화 없음은 정상이며 타인 미언급을 혼자로 추정하지 않는다.
- PERSON_NAME=KNOWN이면 name은 quote에서 복사한다. 가족 호칭도 완전한 이름이며 친구 부름말이 없으면 한 번 묻는다. 이름·업체·거래·약속을 꾸미지 않는다.
주제: ACTIVITY=한 일,ACTIVITY_DETAIL=구체적 내용/대상/목적,SETTING=장소,SERVICE=업체/앱,PEOPLE=관계/혼자,PERSON_NAME=부름말,PURPOSE=계기,ACCESS=이용/주문/안내 경로,INTERACTION=주고받은 요청/약속,TRANSACTION=결제/예약 주체·방식,ROLE=맡은 역할,TIME=시점. 장소에 있었다만 알면 SETTING이다. 활동명·제목·기기 종류를 다른 주제로 복사해 충분하다고 하지 않는다.

질문:
- ACTIVITY 미확인 초기에는 실제 경험부터 묻고 questionAssessments=[]다. 주제 선택만으로 기본 훈련을 제안하지 않는다.
- STOP은 전체 설명 중단 요청뿐이다. 일부 모름은 DECLINED이며 질문 선택 실패는 중단 의사가 아니다.
- personalizationTarget은 PERSON=특정 인물,SERVICE=이용 업체,OTHER=개인 활동/역할,UNDECIDED=미정이다. 친구 언급만으로 지인 사칭을 고정하지 않고 PERSON_NAME을 물으면 PERSON이다.
- 확인된 접점을 먼저 쓴다. 접점이 없고 활동이 넓을 때만 내용/목적을 구체화한다. 기존 대상·기능·역할·경로를 반복하거나 무관한 방법·기술을 캐묻지 않는다.
- SERVICE는 구별 가능한 업체/앱/장소와 이용 맥락이 필요하다. 시간·경로는 현재 접근의 필수일 때만 묻고 확인한 장소·시점·상호작용은 재사용한다. 가입·연락처 등록·직원 열람을 추정하지 않는다.
- 추가 질문은 questionTargets와 requiredTopics의 미해결 주제 하나다. 기존 답·닫힌 주제는 반복하지 않고 PARTIAL은 부족한 부분만 한 번 확인한다. 실제 경험 전환만 focusChanged=true다.
- questionAssessments는 이번 필수·질문 항목만 topic별 하나다. MISSING에는 missingDetail/scenarioImpact,ANSWERED에는 기존 발언 evidence가 필요하다. 같은 사실을 다른 주제에도 재사용하고 UNNECESSARY/OPTIONAL은 필수에서 뺀다.
- PLAN_CLARIFICATION은 planningIssues의 미확인 필수만 묻되 suggestedQuestion도 기존 답·닫힌 주제와 대조한다. 정보 부족이면 ASK를 제안한다.
- REASSESS는 기존 사실로 불필요한 전제를 버리고 재판단한다. 새 사실·초점 전환·질문 순회 없이, 여전히 부족하고 대화를 이어가야 하면 질문 하나를 반드시 제안한다.
- 질문은 ASK에 존댓말 한 문장(180자 이하)과 주제를 반드시 담는다. questionCorrection을 받으면 누락·부적합 질문을 고치고 미확인 사실을 전제하지 않는다.

출력 스키마의 JSON만 반환한다. 공통 필드는 updates,personalizationTarget,requiredTopics,focusChanged,questionAssessments다.
action은 ASK={type,nextTopic,nextQuestion},VERIFY_READY={type,readinessBasis},STOP={type,evidence:{turn,quote}},OFFER_BASIC={type} 중 하나다. ASK의 질문·주제는 null·빈값이 불가능하다. STOP은 현재 사용자가 전체 설명을 중단한 원문을 인용하며 OFFER_BASIC은 basicOfferAllowed=true일 때만 선택한다.
updates={topic,status,turn,quote,name},questionAssessments={topic,status,necessity,missingDetail,scenarioImpact,reason,evidence:[{turn,quote}]}다. assessment status는 MISSING/ANSWERED/UNNECESSARY,necessity는 REQUIRED/OPTIONAL이다. reason은 짧게 쓰고 필수 사실·이름·인용을 빼 완료를 가장하지 않는다.

상태별 필드는 스키마를 따른다. name은 PERSON_NAME=KNOWN만, MISSING은 missingDetail/scenarioImpact/necessity, ANSWERED는 원문 evidence, UNNECESSARY는 reason만 쓴다. 다른 상태의 필드는 넣지 않는다.
