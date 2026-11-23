준비 완료는 질문 횟수가 아니라 실제 접점으로 판단한다.
- 이미 확인된 역할·이용 방식·상호작용으로 접근 이유가 구체화되면 readinessBasis={topics:[KNOWN 주제],evidence:[{turn,quote}],contactReason:짧은 연결 이유}를 쓴다. 활동명+동행/이름/기기 종류만으로는 부족하다.
- 그 접점으로 진행할 수 없다면 필요한 누락만 REQUIRED다. 있으면 표현만 풍부해지는 정보는 OPTIONAL이며 묻거나 requiredTopics에 넣지 않는다. 알려진 상호작용이 충분하면 활동의 다른 세부를 캐묻지 않는다.
- PERSON은 관계와 실제 부름말(PERSON_NAME), SERVICE는 업체 정체성을 확인한다. 진행 중인 이름을 OTHER로 우회하지 않는다. 모름/거절한 이름·업체·시간·경로는 DECLINED로 닫고 재질문하지 않되 남은 맥락의 충분성은 별도로 판단한다.
- 실제 접점이 있고 미해결 필수가 없으면 contextSufficient=true,nextTopic=null,nextQuestion=""로 끝낸다. 부족하면 꼭 필요한 질문 하나만 한다. 모든 빈 주제를 채우거나 실제 사기 연락 수신을 요구하지 않는다.
