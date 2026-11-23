"""AI-08 긴급 대응 도우미 말."""
import pytest

import app.services.solution_reply as solution_reply
from app.api_errors import AiApiError
from app.schemas.solution_reply import SolutionReplyRequest

REQUEST = SolutionReplyRequest(
    facts={"transferredMoney": "YES", "contactContinues": "YES"},
    cards=[{"title": "지급정지 요청", "priority": "URGENT", "actions": ["송금한 은행에 지급정지를 요청해요"]}],
    userText="아직 연락하고 있어요", kind="update")


def test_사실과_카드를_보내고_도우미_말을_돌려줌(monkeypatch):
    seen = {}

    def fake(system, user, **kwargs):
        seen["user"] = user
        return {"message": "돈을 보내셨다면 지금은 시간이 가장 중요해요. 먼저 송금한 은행에 지급정지를 요청하세요."}

    monkeypatch.setattr(solution_reply, "generate_json", fake)
    result = solution_reply.reply(REQUEST)
    assert result.message.startswith("돈을 보내셨다면")
    assert "지급정지 요청" in seen["user"] and "돈을 보냈는지: 예" in seen["user"]
    assert "아직 연락하고 있어요" in seen["user"]


def test_전화번호나_링크가_섞이면_쓰지_않음(monkeypatch):
    monkeypatch.setattr(solution_reply, "generate_json", lambda *a, **k: {"message": "1588-1234 로 전화하세요"})
    with pytest.raises(AiApiError):
        solution_reply.reply(REQUEST)


def test_사용자_문장의_개인정보는_가려서_보냄(monkeypatch):
    seen = {}
    monkeypatch.setattr(solution_reply, "generate_json",
                        lambda system, user, **k: seen.setdefault("user", user) and {"message": "확인했어요."})
    solution_reply.reply(REQUEST.model_copy(update={"userText": "제 계좌 110-123-456789로 보냈어요"}))
    assert "456789" not in seen["user"]
