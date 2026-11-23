"""Expand shared criteria so generation and review receive the same rules."""
from pathlib import Path

PROMPTS = Path(__file__).resolve().parents[1] / "prompts"


def load_prompt(path: Path) -> str:
    prompt = path.read_text(encoding="utf-8")
    for token, filename in (
        ("{{CONTEXT_FACTS}}", "context_facts.md"),
        ("{{SCENARIO_QUALITY_CRITERIA}}", "scenario_quality_criteria.md"),
    ):
        if token in prompt:
            prompt = prompt.replace(token, (PROMPTS / filename).read_text(encoding="utf-8"))
    return prompt
