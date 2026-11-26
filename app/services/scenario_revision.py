"""Apply a bounded narrative correction to an existing, fact-bound draft."""
from copy import deepcopy
from app.schemas.context_graph import GeneratedScenario
from app.schemas.scenario_revision import ScenarioRevision
from app.services.context_graph import ContextIntegrityError


def apply_revision(draft: GeneratedScenario, revision: ScenarioRevision) -> GeneratedScenario:
    result = deepcopy(draft.model_dump())
    edits = revision.scenario.model_dump(exclude_none=True, exclude_defaults=True)
    clear_page = edits.pop("clearMockPage", False)
    persona = edits.pop("persona", None)
    if persona:
        result["scenario"]["persona"].update(persona)
    result["scenario"].update(edits)
    if clear_page:
        result["scenario"]["mockPage"] = None
    if revision.analysis is not None:
        result["analysis"] = revision.analysis.model_dump()
    if (draft.scenario.keyScenes[-1].request != "대화 지속"
            and result["scenario"]["keyScenes"][-1]["request"] == "대화 지속"):
        raise ContextIntegrityError("repair_removed_terminal_risk")
    if result == draft.model_dump():
        raise ContextIntegrityError("empty_scenario_revision")
    # Revalidate merged fields. preservedClaims come from the original draft only.
    return GeneratedScenario.model_validate(result)
