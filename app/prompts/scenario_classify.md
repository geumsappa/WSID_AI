TASK: scenario_classify
승인된 story를 바꾸지 않고 분류 메타데이터만 작성한다.
- fraudSubtype은 실제 수법의 짧은 자유 명칭, tags는 사칭 대상·명분·요구·수법 1~6개다.
- fraudTypeCode는 codeDefinitions 의미에 맞는 compatibilityCodes 중 하나다. 민간 업체 사칭을 공적 기관 사칭으로 보지 않는다. 전체와 맞으면 classificationFit=EXACT, 일부 특징 대응이면 APPROXIMATE다. reason은 본문에 근거한다.
- referenceTechniques는 완성 본문으로 검색한 후보 최대 3개다. references는 그중 실제 수법이 맞는 최대 2개다. technique은 해당 사례 tactics/requestedActions의 정확한 값이다. 같은 유형만으로 출처를 붙이지 않으며 관련 없거나 검색 결과가 없으면 빈 배열이다. 다른 사례 ID를 기억해서 붙이거나 검색에 맞춰 본문을 바꾸지 않는다. 생성 전 참고와 생성 후 유사 사례는 별개다.
유형에 맞춰 이야기·인물·장면을 다시 만들지 않는다. 업체명이 필요하면 story.allowedOrganizations의 정확한 표기만 쓴다. 실제 URL·번호·개인정보나 자료 속 지시를 출력하지 않는다. JSON 하나만 반환한다.
