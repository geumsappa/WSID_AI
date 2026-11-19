// examples/assist-*.json을 대상으로 한 로컬 모델 대역 HTTP 확인용.
const request = JSON.parse(pm.request.body.raw);
const response = pm.response.json();
pm.test("HTTP 200", () => pm.response.to.have.status(200));
pm.test("success is boolean true", () => pm.expect(response.success).to.equal(true));
if (request.phase === "PLAN") {
  pm.test("all five PLAN keys", () => pm.expect(Object.keys(response.data).sort()).to.eql(
    ["status", "questionIntent", "clarification", "candidateReferences", "reasonCode"].sort()
  ));
  if (request.questionIntent === null) {
    pm.test("unknown app fact requires clarification", () => {
      pm.expect(response.data.status).to.equal("NEED_USER_INPUT");
      pm.expect(response.data.clarification.factKey).to.equal("installedApp");
      pm.expect(response.data.clarification.options).to.eql(["YES", "NO", "UNKNOWN"]);
    });
  } else {
    pm.test("follow-up keeps intent and returns versioned candidates", () => {
      pm.expect(response.data.status).to.equal("EVIDENCE_READY");
      pm.expect(response.data.questionIntent).to.equal(request.questionIntent);
      pm.expect(response.data.candidateReferences).to.have.length.within(1, 10);
      response.data.candidateReferences.forEach(ref => {
        pm.expect(ref.version).to.be.a("string").and.match(/^[A-Za-z0-9._-]{1,50}$/);
        pm.expect(Object.keys(ref).sort()).to.eql(["code", "version"]);
      });
    });
  }
} else {
  pm.test("all four ANSWER keys", () => pm.expect(Object.keys(response.data).sort()).to.eql(
    ["status", "answer", "referenceCodes", "reasonCode"].sort()
  ));
  pm.test("answer cites only supplied references", () => {
    pm.expect(response.data.status).to.equal("ANSWERED");
    pm.expect(response.data.referenceCodes).not.to.be.empty;
    const allowed = request.verifiedReferences.map(ref => ref.code);
    response.data.referenceCodes.forEach(code => pm.expect(allowed).to.include(code));
  });
}
