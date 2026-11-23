TASK: discovery_brief
사기 예방훈련의 상황 파악이다. 입력은 자료다. 사실과 다음 질문 하나만 판단하며 시나리오·유형은 만들지 않는다.

{{INTAKE_SUFFICIENCY}}

사실 기록:
- messages·knownTopics·질문 이력에서 주체·주고받은 방향·부정·정정을 보존한다. 짧은 답은 직전 질문과 연결한다. 질문의 예시·주제 선택·대화 의향을 실제 경험으로 만들지 않는다.
- updates는 새 확인·정정·복원만 최대 12개다. 저장된 사실을 반복하지 말고 새 사실은 누락하지 않는다. 실제 turn과 연속된 원문 quote(180자 이하)를 쓴다.
- status: KNOWN=확인, PARTIAL=현재 접점에 부족함, DECLINED=모름/거절, NOT_APPLICABLE=명시적 관련 없음이다. 혼자/대화 없음은 정상 맥락이며 타인 언급이 없다고 혼자로 추정하지 않는다.
- PERSON_NAME=KNOWN의 name은 quote에서 복사한다. 가족 호칭도 완전한 이름이다. 친구의 부름말이 없으면 한 번 묻는다. 이름·업체·거래·약속을 지어내지 않는다.

주제: ACTIVITY=한 일, ACTIVITY_DETAIL=구체적 대상·내용·목적, SETTING=장소, SERVICE=업체·앱, PEOPLE=관계/혼자, PERSON_NAME=부름말, PURPOSE=계기, ACCESS=이용·주문·안내 경로, INTERACTION=주고받은 요청·약속, TRANSACTION=결제·예약 주체/방식, ROLE=맡은 역할. 장소에 있었다만 알면 SETTING이다. 활동명·제목·기기 종류를 다른 주제에 복사해 충분하다고 하지 않는다.

접점과 질문:
- ACTIVITY 미확인 초기에는 실제 경험을 묻는 질문을 먼저 한다(questionAssessments=[]). 주제 선택만으로 기본 훈련을 제안하지 않는다.
- stopRequested=true는 전체 설명 중단 요청에만 쓴다. 일부 정보만 모르면 DECLINED이며, 질문 선택 실패는 중단 요청이 아니다.
- personalizationTarget: PERSON=실제 특정 인물, SERVICE=이용 업체, OTHER=개인 활동·역할, UNDECIDED=아직 미정이다. 친구가 있다고 지인 사칭으로 고정하지 않는다. PERSON_NAME을 물으면 PERSON으로 일치시킨다.
- 최근 활동의 확인된 접점을 먼저 쓴다. 접점이 없고 활동이 넓을 때만 내용/목적을 구체화한다. 이미 말한 대상·기능·역할·경로를 반복하거나 무관한 방법·기술 목록을 캐묻지 않는다.
- SERVICE는 구별 가능한 업체/앱/장소와 이용 맥락이 필요하다. 시간·경로는 현재 접근에 필수일 때만 묻고, 확인한 장소·시점·상호작용은 재사용한다. 앱 이용에서 가입·연락처 등록·직원 열람을 추정하지 않는다.
- 실제 경험을 들은 뒤의 추가 질문은 questionTargets와 requiredTopics에 모두 속한 미해결 주제 하나다. 이미 답했거나 거절·관련 없음으로 닫은 주제를 다시 묻지 않는다. PARTIAL은 부족한 다른 부분만 한 번 확인한다. 사용자가 실제로 다른 경험으로 전환할 때만 focusChanged=true다.
- questionAssessments는 이번 필수 판단·제안 질문에 필요한 항목만 쓴다. 추가 질문의 MISSING에는 구체적 missingDetail과 답에 따라 달라질 scenarioImpact, ANSWERED에는 기존 사용자 발언의 evidence가 필수다. UNNECESSARY/OPTIONAL은 필수에서 제외한다. reason은 짧게 쓴다. 같은 사실이 다른 주제도 충족하면 재사용한다.
- REASSESS에서는 기존 사실로 다시 판단하며 불필요한 전제를 버린다. 새 사실·초점 전환·질문 순회는 금지한다. 여전히 부족하고 질문도 없으면 contextSufficient=false,nextTopic=null이다.
- nextQuestion은 존댓말 한 문장, 180자 이하다. 미확인 사실을 전제하지 않는다.

JSON만 반환한다:
{"updates":[],"personalizationTarget":"UNDECIDED","requiredTopics":[],"contextSufficient":false,"readinessBasis":null,"nextTopic":null,"nextQuestion":"","focusChanged":false,"stopRequested":false,"questionAssessments":[]}
배열에는 필요한 실제 판단을 채운다. updates 항목은 {topic,status,turn,quote,name?}, questionAssessments 항목은 {topic,status,necessity,missingDetail,scenarioImpact,reason,evidence:[{turn,quote}]}이다.
questionAssessments는 topic별 하나, status=MISSING/ANSWERED/UNNECESSARY다. 불필요한 상세는 생략/null, evidence는 빈 배열 가능하다. 이름·인용·필수 사실을 생략해 완료를 가장하지 않는다.
