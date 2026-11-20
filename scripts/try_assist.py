"""터미널에서 피해 대응 채팅(추가 질문)을 Spring처럼 흉내 내어 체험하는 스크립트.

서버를 먼저 켠 상태에서(python -m uvicorn app.main:app --reload) 다른 터미널 창에서:
    python scripts/try_assist.py              # 지금 Spring이 쓰는 /assist 흐름
    python scripts/try_assist.py --compare    # 예전 /answer 답변도 나란히 보기
    python scripts/try_assist.py --v1         # /assist에 history·fraudType을 안 보내기 (v1.1 효과 비교)
    python scripts/try_assist.py --serve      # 프론트 mock이 붙는 개발용 Spring 대역 서버 (http://localhost:8001)
                                              # 채팅 답(/assist)과 사진 OCR(/dev/ocr → /internal/ai/ocr/extract)

--serve: 프론트(mock 모드)의 VITE_AI_DEV_URL을 http://localhost:8001 로 두면, 채팅 질문이
  이 대역 서버 → /assist PLAN → (근거 승인) → ANSWER 로 가서 실제 서비스와 같은 흐름의 답을 화면에서 볼 수 있어요.
  내부 API 키는 이 서버가 붙여서 보내므로 브라우저에는 키가 없어요. 개발 PC에서만 쓰세요.

흐름 (Spring이 하는 일을 이 스크립트가 대신해요)
  1) 상황 설명 → AI-06 /extract 로 사실 4개·사기 유형 추출
  2) 질문 → /assist PLAN
       NEED_USER_INPUT : 예/아니오/모름을 물어보고 사실을 고친 뒤 PLAN 다시 (질문 원문 없이 intent로)
       EVIDENCE_READY  : 후보 근거 code를 data/references/references.jsonl 에서 찾아 /assist ANSWER
       UNABLE_TO_ANSWER: 이유 코드 출력
  3) --compare 면 같은 질문을 /answer(사용 안 함 표시된 예전 가안)에 history·fraudType과 함께 보내 비교

.env 의 AI_API_KEY 를 X-Internal-Api-Key 헤더로 보내요. 종료: q
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REFS = ROOT / "data" / "references" / "references.jsonl"
FACT_KEYS = ["transferredMoney", "sharedInformation", "installedApp", "contactContinues"]
FACT_LABEL = {"transferredMoney": "송금", "sharedInformation": "정보 제공", "installedApp": "앱 설치", "contactContinues": "연락 지속"}
ANSWER_KEYS = {"y": "YES", "예": "YES", "n": "NO", "아니오": "NO", "아니요": "NO", "m": "UNKNOWN", "모름": "UNKNOWN", "": "UNKNOWN"}


def env_key() -> str:
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith("AI_API_KEY="):
                return line.split("=", 1)[1].strip()
    return "local-dev-key"


def load_refs() -> dict[str, dict]:
    refs: dict[str, dict] = {}
    if REFS.exists():
        for line in REFS.read_text(encoding="utf-8").splitlines():
            if line.strip():
                r = json.loads(line)
                refs[r["code"]] = r
    return refs


class Client:
    def __init__(self, base: str, key: str):
        self.base = base.rstrip("/")
        self.key = key

    def post(self, path: str, payload: dict) -> tuple[int, dict]:
        req = urllib.request.Request(
            self.base + path, data=json.dumps(payload).encode("utf-8"), method="POST",
            headers={"Content-Type": "application/json", "X-Internal-Api-Key": self.key, "X-Request-Id": str(uuid.uuid4())},
        )
        try:
            with urllib.request.urlopen(req, timeout=90) as res:
                return res.status, json.loads(res.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")
            try:
                return e.code, json.loads(body)
            except ValueError:
                return e.code, {"raw": body[:300]}


def show_facts(facts: dict) -> str:
    return ", ".join(f"{FACT_LABEL[k]}={facts[k]}" for k in FACT_KEYS)


def ask_fact(content: str) -> str:
    while True:
        v = input(f"   AI 확인 질문: {content}\n   (y=예 / n=아니오 / m=모름) > ").strip().lower()
        if v in ANSWER_KEYS:
            return ANSWER_KEYS[v]


def run_assist(api: Client, refs: dict, question: str, facts: dict, asked: list[str],
               history: list[dict] | None = None, fraud_type: str | None = None) -> str | None:
    """Spring SOL-03 QUESTION 처리 흉내. 답변 글(또는 None)을 돌려줘요.
    history·fraud_type을 주면 v1.1 선택 필드로 보내요 (history는 이번 질문 앞의 대화)."""
    intent = None
    q: str | None = question
    extra: dict = {}
    if history is not None:
        extra["history"] = history[-10:]
    if fraud_type:
        extra["fraudType"] = fraud_type
    remaining = 3 - len(asked) if len(asked) < 3 else 0
    for _ in range(4):
        code, res = api.post("/internal/ai/solutions/assist", {
            "phase": "PLAN", "question": q, "questionIntent": intent, "facts": facts,
            "askedFactKeys": asked, "remainingClarifications": max(0, remaining), **extra,
        })
        if code != 200:
            print(f"   [assist PLAN 실패 {code}] {res}")
            return None
        plan = res["data"]
        intent = plan.get("questionIntent") or intent
        if plan["status"] == "NEED_USER_INPUT":
            c = plan["clarification"]
            facts[c["factKey"]] = ask_fact(c["content"])
            asked.append(c["factKey"])
            if "history" in extra:
                # Spring처럼 질문·확인 질문·대답을 대화 기록에 쌓아요 (후속 요청에서 원래 질문을 찾을 수 있게)
                if q is not None:
                    extra["history"] = (extra["history"] + [{"role": "USER", "content": q}])[-10:]
                extra["history"] = (extra["history"] + [{"role": "AI", "content": c["content"]},
                                                        {"role": "USER", "content": {"YES": "네", "NO": "아니요", "UNKNOWN": "모르겠어요"}[facts[c["factKey"]]]}])[-10:]
            remaining -= 1
            q = None  # Spring은 질문 원문을 저장하지 않아서 후속 PLAN은 intent만 보내요
            continue
        if plan["status"] == "UNABLE_TO_ANSWER":
            print(f"   [assist] 답변 불가 ({plan.get('reasonCode')})")
            return None
        cands = [c["code"] for c in plan["candidateReferences"]]
        verified = [{"code": c, "title": refs[c]["title"][:200], "excerpt": refs[c]["excerpt"][:2000]} for c in cands if c in refs]
        missing = [c for c in cands if c not in refs]
        print(f"   [assist PLAN] intent={intent} 후보 근거 {len(cands)}개" + (f" (references.jsonl에 없음: {missing})" if missing else ""))
        if not verified:
            print("   [assist] 승인할 근거가 없어 답변 불가 (Spring도 이 경우 답변 불가)")
            return None
        code, res = api.post("/internal/ai/solutions/assist", {
            "phase": "ANSWER", "question": q, "questionIntent": intent, "facts": facts, "verifiedReferences": verified, **extra,
        })
        if code != 200:
            print(f"   [assist ANSWER 실패 {code}] {res}")
            return None
        ans = res["data"]
        if ans["status"] != "ANSWERED":
            print(f"   [assist] 답변 불가 ({ans.get('reasonCode')})")
            return None
        print(f"   [근거] {', '.join(ans['referenceCodes'])}")
        return ans["answer"]
    print("   [assist] 확인 질문이 너무 많아 멈췄어요")
    return None


def plan_and_answer(api: Client, refs: dict, question: str, facts: dict,
                    history: list[dict] | None, fraud_type: str | None) -> dict:
    """확인 질문을 사람에게 묻지 않고 한 번에 처리해요 (--serve용). /answer 가안과 같은 응답 모양으로 돌려줘요."""
    extra: dict = {}
    if history:
        extra["history"] = history[-10:]
    if fraud_type:
        extra["fraudType"] = fraud_type
    asked = [k for k in FACT_KEYS if facts.get(k) != "UNKNOWN"]  # 이미 아는 사실은 다시 묻지 않게
    code, res = api.post("/internal/ai/solutions/assist", {
        "phase": "PLAN", "question": question, "questionIntent": None, "facts": facts,
        "askedFactKeys": asked, "remainingClarifications": 3, **extra,
    })
    if code != 200:
        return {"status": "UNAVAILABLE", "error": res, "httpStatus": code}
    plan = res["data"]
    if plan["status"] == "NEED_USER_INPUT":
        c = plan["clarification"]
        return {"status": "NEED_USER_INPUT", "answer": None, "referenceCodes": [],
                "clarifyFactKey": c["factKey"], "clarifyQuestion": c["content"]}
    if plan["status"] == "UNABLE_TO_ANSWER":
        return {"status": "UNABLE_TO_ANSWER", "answer": None, "referenceCodes": [], "reasonCode": plan.get("reasonCode")}
    verified = [{"code": c["code"], "title": refs[c["code"]]["title"][:200], "excerpt": refs[c["code"]]["excerpt"][:2000]}
                for c in plan["candidateReferences"] if c["code"] in refs]
    if not verified:
        return {"status": "UNABLE_TO_ANSWER", "answer": None, "referenceCodes": [], "reasonCode": "NO_EVIDENCE"}
    code, res = api.post("/internal/ai/solutions/assist", {
        "phase": "ANSWER", "question": question, "questionIntent": plan["questionIntent"], "facts": facts,
        "verifiedReferences": verified, **extra,
    })
    if code != 200:
        return {"status": "UNAVAILABLE", "error": res, "httpStatus": code}
    ans = res["data"]
    return {"status": ans["status"], "answer": ans.get("answer"), "referenceCodes": ans.get("referenceCodes", []),
            "reasonCode": ans.get("reasonCode")}


def serve(api: Client, refs: dict, port: int, origin: str) -> None:
    """프론트 mock(개발용)이 부르는 Spring 대역. /answer 가안과 같은 요청·응답 모양이라 프론트는 주소만 바꾸면 돼요."""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class Handler(BaseHTTPRequestHandler):
        def _send(self, status: int, body: dict | None = None) -> None:
            data = json.dumps(body or {}, ensure_ascii=False).encode("utf-8")
            try:
                self._write(status, data)
            except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
                # 브라우저가 먼저 기다리기를 그만둔 경우(시간 초과·새로고침). 답은 버려요
                print("[대역] 브라우저가 응답 전에 연결을 끊었어요 (프론트 시간 초과일 수 있어요)")

        def _write(self, status: int, data: bytes) -> None:
            self.send_response(status)
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *args) -> None:  # 대화 내용이 로그에 남지 않게 기본 접근 로그는 끄기
            pass

        def do_OPTIONS(self) -> None:
            self._send(204)

        def do_GET(self) -> None:
            if self.path != "/health":
                return self._send(404)
            try:
                with urllib.request.urlopen(api.base + "/health", timeout=1.5) as res:
                    return self._send(200 if res.status == 200 else 503, {"ai": res.status})
            except (urllib.error.URLError, OSError):
                return self._send(503, {"ai": "down"})

        def _ocr(self) -> None:
            """사진(multipart images)을 그대로 AI OCR(/internal/ai/ocr/extract)로 넘기고 키만 붙여요."""
            length = int(self.headers.get("Content-Length", 0))
            if length <= 0 or length > 16 * 1024 * 1024:
                return self._send(413, {"error": "too large"})
            req = urllib.request.Request(
                api.base + "/internal/ai/ocr/extract", data=self.rfile.read(length), method="POST",
                headers={"Content-Type": self.headers.get("Content-Type", ""), "X-Internal-Api-Key": api.key,
                         "X-Request-Id": str(uuid.uuid4())},
            )
            try:
                with urllib.request.urlopen(req, timeout=60) as res:
                    body = json.loads(res.read().decode("utf-8"))
                    data = body.get("data") or {}
                    print(f"[대역] OCR {data.get('imageType')} usable={data.get('usable')} 글자 {len(data.get('recognizedText') or '')}자")
                    return self._send(200, body)
            except urllib.error.HTTPError as e:
                detail = e.read().decode("utf-8", errors="replace")[:300]
                print(f"[대역] OCR 실패 {e.code} {detail}")
                return self._send(e.code, {"error": detail})
            except (urllib.error.URLError, OSError) as e:
                print(f"[대역] OCR 연결 실패 {type(e).__name__}")
                return self._send(502, {"error": "ocr unavailable"})

        def do_POST(self) -> None:
            if self.path == "/dev/ocr":
                return self._ocr()
            if self.path != "/internal/ai/solutions/answer":
                return self._send(404)
            try:
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))).decode("utf-8"))
                question = str(body.get("question") or "").strip()[:2000]
                facts = {k: (body.get("facts") or {}).get(k, "UNKNOWN") for k in FACT_KEYS}
                history = [t for t in (body.get("history") or []) if t.get("content")]
                # 프론트는 이번 질문까지 history에 넣어 보내요. assist v1.1은 '이번 질문 앞의 대화'라서 빼요
                if history and history[-1].get("role") == "USER" and history[-1]["content"].strip() == question:
                    history = history[:-1]
                history = [{"role": t["role"], "content": str(t["content"])[:2000]} for t in history if t.get("role") in ("USER", "AI")]
            except (ValueError, TypeError):
                return self._send(400, {"error": "bad request"})
            if not question:
                return self._send(400, {"error": "question required"})
            result = plan_and_answer(api, refs, question, facts, history or None, body.get("fraudType"))
            print(f"[대역] {result['status']}" + (f" {result.get('reasonCode')}" if result.get("reasonCode") else "")
                  + (f" 근거={','.join(result['referenceCodes'])}" if result.get("referenceCodes") else ""))
            if result["status"] == "UNAVAILABLE":
                return self._send(502, result)
            return self._send(200, result)

    print(f"개발용 Spring 대역: http://localhost:{port}  (AI 서버 {api.base}, 허용 출처 {origin})")
    print("프론트 .env.local 에 VITE_AI_DEV_URL=http://localhost:%d 로 두고 npm run dev 를 다시 시작하세요. 종료: Ctrl+C" % port)
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()


def run_answer(api: Client, question: str, facts: dict, fraud_type: str | None, history: list[dict]) -> str | None:
    code, res = api.post("/internal/ai/solutions/answer", {
        "facts": facts, "question": question, "fraudType": fraud_type, "history": history[-10:],
    })
    if code != 200:
        print(f"   [answer 실패 {code}] {res}")
        return None
    if res["status"] == "NEED_USER_INPUT":
        return f"(되물음) {res.get('clarifyQuestion')}"
    if res["status"] != "ANSWERED":
        return "(답변 불가)"
    return res["answer"]


def main() -> None:
    p = argparse.ArgumentParser(description="피해 대응 채팅 /assist 체험 (Spring 흉내)")
    # Windows에서 localhost는 IPv6(::1)를 먼저 시도해서 요청마다 2초쯤 늦어져요. uvicorn 기본 주소(127.0.0.1)로 바로 가요
    p.add_argument("--base", default="http://127.0.0.1:8000")
    p.add_argument("--compare", action="store_true", help="예전 /answer 답변도 함께 보기")
    p.add_argument("--v1", action="store_true", help="/assist에 history·fraudType을 보내지 않기 (계약 v1 그대로)")
    p.add_argument("--serve", action="store_true", help="프론트 mock용 개발 대역 서버 켜기")
    p.add_argument("--port", type=int, default=8001)
    p.add_argument("--origin", default="http://localhost:8443", help="프론트 개발 서버 주소 (CORS)")
    args = p.parse_args()
    api = Client(args.base, env_key())
    refs = load_refs()
    if not refs:
        sys.exit(f"근거 파일이 없어요: {REFS}")
    if args.serve:
        serve(api, refs, args.port, args.origin)
        return

    desc = input("상황을 설명해 주세요 > ").strip()
    if not desc or desc.lower() == "q":
        return
    code, res = api.post("/internal/ai/solutions/extract", {"description": desc})
    if code != 200:
        sys.exit(f"[extract 실패 {code}] {res}")
    facts = dict(res["facts"])
    fraud_type = res.get("fraudType")
    print(f"[AI-06] {show_facts(facts)} / 사기 유형={fraud_type}\n")

    asked: list[str] = []
    history: list[dict] = [{"role": "USER", "content": desc[:2000]}]
    while True:
        question = input("질문 > ").strip()
        if not question:
            continue
        if question.lower() == "q":
            break
        label = "v1: history·fraudType 없음" if args.v1 else "v1.1: history·fraudType 포함"
        print(f"\n■ /assist ({label})")
        new = run_assist(api, refs, question, facts, asked,
                         history=None if args.v1 else list(history), fraud_type=None if args.v1 else fraud_type)
        print(f"   {new}" if new else "   (답 없음)")
        if args.compare:
            print("\n■ /answer (예전 가안: history·fraudType 사용)")
            old = run_answer(api, question, facts, fraud_type, history)
            print(f"   {old}" if old else "   (답 없음)")
        print(f"\n   지금 사실: {show_facts(facts)}\n")
        history.append({"role": "USER", "content": question[:2000]})
        if new:
            history.append({"role": "AI", "content": new[:2000]})


if __name__ == "__main__":
    main()
