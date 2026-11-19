"""FastAPI DTO에서 공유용 OpenAPI 생성: python scripts/export_assist_openapi.py"""
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.main import app  # noqa: E402

if __name__ == "__main__":
    target = ROOT / "docs" / "assist-openapi.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(app.openapi(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(target.relative_to(ROOT))
