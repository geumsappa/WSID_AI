"""Claude 연결 확인: python scripts/check_claude.py  (venv 켠 상태, WSID_AI 폴더에서)
키 값은 출력하지 않아요."""
import sys, time
sys.path.insert(0, ".")
try:
    import anthropic
    print("1) anthropic 라이브러리:", anthropic.__version__)
except ImportError:
    print("1) anthropic 라이브러리가 없어요 → pip install -r requirements.txt"); sys.exit(1)
from app.config import settings
print("2) provider:", settings.llm_provider, "/ model:", settings.llm_model, "/ key:", "있음" if settings.llm_api_key else "없음")
if settings.llm_provider != "anthropic":
    print("   → .env 의 LLM_PROVIDER=anthropic 인지 확인하세요"); sys.exit(1)
from app.llm.llm_client import anthropic_text
start = time.time()
try:
    text = anthropic_text("너는 테스트 응답기다.", '{"ping": 1} 을 받았으면 {"ok": true} 를 돌려줘.', max_tokens=50, timeout=30)
    print(f"3) 응답 성공 ({time.time() - start:.1f}초):", text[:80])
except Exception as e:
    print(f"3) 호출 실패 ({time.time() - start:.1f}초):", type(e).__name__, str(e)[:300])
