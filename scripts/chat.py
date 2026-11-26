"""터미널에서 훈련 전체(① 상황 파악 → ② 시나리오 생성 → ③ 가상 훈련 대화 → ⑤ 결과 리포트)를 체험하는 스크립트.

서버를 먼저 켠 상태에서(python -m uvicorn app.main:app --reload) 다른 터미널 창에서:
    python scripts/chat.py
종료하려면 'q' 입력. ③에서는 선택지 번호(1~3)를 누르거나 직접 입력할 수 있어요.

Spring 이 하게 될 일(대화 기록 쌓기, 사실·개인정보 기록 누적, 시나리오 저장)을 이 스크립트가 흉내냅니다.
"""
import json
import urllib.request
import uuid

BASE = "http://localhost:8000/internal/ai"


def post(path: str, payload: dict) -> dict:
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(BASE + path, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=70) as res:
        return json.loads(res.read().decode("utf-8"))


# ---------------- ① 상황 파악 대화 ----------------
def stage1(session_id: str) -> list[dict] | None:
    print("\n========== ① 상황 파악 대화 ==========")
    history: list[dict] = []
    facts: list[dict] = []

    result = post("/discovery/next", {"sessionId": session_id, "history": history})
    while True:
        print(f"\nAI: {result['nextQuestion']}")
        if result["done"]:
            break
        history.append({"role": "ai", "text": result["nextQuestion"]})

        answer = input("나: ").strip()
        if answer.lower() == "q":
            return None
        if not answer:
            history.pop()
            continue
        history.append({"role": "user", "text": answer})

        result = post("/discovery/next", {"sessionId": session_id, "history": history})
        if result["maskedText"]:
            history[-1]["text"] = result["maskedText"]  # 원문 대신 가린 글 저장
        if result["sensitiveDetected"]:
            print(f"   └ ⚠ 개인정보 입력 기록: {result['sensitiveTypes']}")
        facts.extend(result["extractedFacts"])
        print(f"   └ 추출된 사실: {result['extractedFacts']}")

    print("\n[모인 사실]")
    for f in facts:
        print(f"- [{f['category']}] {f['detail']}")
    return facts


# ---------------- ② 시나리오 생성 ----------------
def stage2(session_id: str, facts: list[dict]) -> dict:
    print("\n========== ② 시나리오 생성 중... ==========")
    res = post("/scenarios/generate", {"sessionId": session_id, "facts": facts})
    a, sc = res["analysis"], res["scenario"]
    print(f"\n[위험 유형 분류] {a['riskType']} — {a['riskTypeReason']}")
    print(f"[위험 분석] 쓴 사실: {a['usedFacts']}")
    for r in a["riskSignals"]:
        print(f"- 위험 신호: {r['signal']} ({r['reason']})")
    for j in a["judgmentPoints"]:
        print(f"- 판단 지점 {j['id']}: {j['description']}")
    print(f"\n[시나리오] {sc['title']}  ({sc['riskType']} > {sc['fraudSubtype']} / {sc['channel']})"
          + ("  ※ 기본 시나리오" if res["fallbackUsed"] else ""))
    for k in sc["keyScenes"]:
        info = f" → 요구 정보: {', '.join(k['requestedInfo'])}" if k["requestedInfo"] else ""
        print(f"  장면{k['order']} [{k['judgmentPointId']}] {k['goal']} / 시킬 행동: {k['request']}{info}")
    for c in sc["basedOnCases"]:
        print(f"  근거 사례 {c['caseId']}: {c['title']} ({c['issuer']}, {c['sourceDate'] or '날짜 미상'}) {c['sourceUrl']}")
    return res


# ---------------- ③ 가상 훈련 대화 ----------------
def stage3(session_id: str, scenario: dict) -> list[dict]:
    print("\n========== ③ 가상 훈련 대화 ==========")
    p = scenario["persona"]
    print(f"\n📱 [{scenario['channel']}] {scenario['background']}")

    history: list[dict] = []
    current_scene = 1
    turn_log: list[dict] = []  # Spring 이 세션에 쌓을 것 → ⑤ 리포트 재료
    end_reason = "user_quit"

    result = post("/simulation/reply", {"sessionId": session_id, "scenario": scenario,
                                     "currentScene": current_scene, "history": history})
    while True:
        print(f"\n{p['name']}: {result['personaMessage']}")
        if result["done"]:
            print(f"\n(훈련 종료: {result['endReason']})")
            end_reason = result["endReason"]
            break
        history.append({"role": "persona", "text": result["personaMessage"]})
        current_scene = result["currentScene"]

        for i, c in enumerate(result["choices"], start=1):
            print(f"   [{i}] {c}")
        if result["inputExpected"]:
            print(f"   ✏ 요구받은 정보: {', '.join(result['requestedInfo'])} (직접 입력할 수 있어요)")
        answer = input("나 (번호 또는 직접 입력): ").strip()
        if answer.lower() == "q":
            break
        if answer in {"1", "2", "3"} and int(answer) <= len(result["choices"]):
            answer = result["choices"][int(answer) - 1]
            print(f"나: {answer}")
        if not answer:
            history.pop()
            continue
        history.append({"role": "user", "text": answer})

        result = post("/simulation/reply", {"sessionId": session_id, "scenario": scenario,
                                         "currentScene": current_scene, "history": history})
        if result["maskedText"]:
            history[-1]["text"] = result["maskedText"]

        j = result["judgment"]
        # ⑤ 리포트 API 에 그대로 보낼 모양 (Spring 이 세션에 누적할 것)
        turn_log.append({
            "turn": sum(1 for m in history if m["role"] == "user"),
            "scene": current_scene,
            "userText": history[-1]["text"],
            "judgment": j,
            "infoDisclosure": result["infoDisclosure"],
        })
        # 실제 화면에선 판정을 숨기지만, 테스트용으로 보여줌
        mark = {"safe": "✅", "risky": "❌", "neutral": "➖"}[j["outcome"]]
        extra = f" / 🔓 개인정보 넘김: {result['infoDisclosure']['provided']}" if result["sensitiveDetected"] else ""
        print(f"   └ (판정 {mark} {j['outcome']}) {j['reason']}{extra}")
        if result["realConcern"]:
            print("   └ ⚠ 실제 피해 상황일 수 있음 → 화면에서 피해 대응 메뉴 안내")

    return turn_log, end_reason


# ---------------- ⑤ 결과 리포트 ----------------
def stage5(session_id: str, scenario: dict, turn_log: list[dict], end_reason: str):
    print("\n========== ⑤ 결과 리포트 생성 중... ==========")
    r = post("/training/evaluate", {"sessionId": session_id, "scenario": scenario, "turns": turn_log,
                         "mockPageEvents": [], "endReason": end_reason})
    mark = {"safe": "✅", "risky": "❌", "neutral": "➖"}
    print(f"\n📋 {r['headline']}")
    print(f"   {r['overview']}")
    info = r["infoProtection"]
    print(f"\n🔐 개인정보: {info['message']}")
    print(f"   요구받음 {info['requested'] or '없음'} / 넘김 {info['provided'] or '없음'} / 지킴 {info['protected'] or '없음'}")
    print("\n👍 잘한 대응")
    for g in r["goodActions"] or [{"description": "없음", "turns": []}]:
        print(f"   - {g['description']} {g['turns'] or ''}" + (" (끝내 바로잡음)" if g.get("recoveredLater") else ""))
    print("⚠️ 놓친 대응")
    for m in r["missedActions"] or [{"description": "없음", "turns": [], "recoveredLater": False}]:
        print(f"   - {m['description']} {m['turns'] or ''}" + (" (나중에 바로잡음)" if m["recoveredLater"] else ""))
    print("🚩 놓친 위험 신호")
    for s in r["missedRiskSignals"] or [{"signal": "없음", "explanation": "", "turn": None}]:
        print(f"   - {s['signal']}: {s['explanation']}")
    print("\n💬 턴별 피드백")
    for f in r["turnFeedback"]:
        given = f" [넘긴 정보: {', '.join(f['providedInfo'])}]" if f["providedInfo"] else ""
        print(f"   {f['turn']}턴 {mark[f['outcome']]}{given} {f['feedback']}")
    print("\n✅ 이렇게 하세요")
    for a in r["recommendedActions"]:
        print(f"   - {a}")
    print("\n☎ 실제로 당했다면")
    for c in r["officialContacts"]:
        print(f"   - {c['name']} {c['contact']}: {c['when']}")
    for c in r["basedOnCases"]:
        print(f"\n📎 근거 사례: {c['title']} ({c['issuer']}) {c['sourceUrl']}")
    print(f"\n{r['disclaimer']}")


def main():
    session_id = str(uuid.uuid4())
    facts = stage1(session_id)
    if facts is None:
        return
    res = stage2(session_id, facts)
    input("\n엔터를 누르면 훈련을 시작합니다...")
    turn_log, end_reason = stage3(session_id, res["scenario"])
    if turn_log:
        stage5(session_id, res["scenario"], turn_log, end_reason)


if __name__ == "__main__":
    main()
