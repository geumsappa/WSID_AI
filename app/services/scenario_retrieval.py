"""Local factual-pattern retrieval; no user data leaves the process for search."""
from collections import Counter
from functools import lru_cache
import hashlib
import json
import math
from pathlib import Path
import re
import unicodedata

PATH = Path(__file__).resolve().parents[1] / "data" / "scenario_patterns.json"
INDEX_VERSION = "char-tfidf-v1"
MIN_SCORE = 0.12


def _terms(text):
    text = re.sub(r"알아보고|기다리고|있어요|있어|했어요|했어|봤어요|봤어|하려고|하고|싶어요|싶어", " ", text)
    words = re.findall(r"[가-힣a-z0-9]+", unicodedata.normalize("NFKC", text).lower())
    return Counter(part for word in words if len(word) >= 2
                   for n in (2, 3) for i in range(len(word) - n + 1)
                   for part in [word[i:i + n]])


@lru_cache(maxsize=1)
def _index():
    raw = PATH.read_bytes()
    data = json.loads(raw)
    records = data["records"]
    ids = [row["id"] for row in records]
    if len(ids) != len(set(ids)) or data["schemaVersion"] != 2 or not records:
        raise ValueError("Invalid factual pattern registry")
    docs = []
    for row in records:
        if row["country"] != "KR" or row["review"]["status"] != "LICENSE_AND_FACTS_CHECKED" or not row["source"]["url"].startswith("https://"):
            raise ValueError("Unreviewed source in active factual pattern registry")
        grant = row["source"].get("license", {})
        if (grant.get("code") != "KOGL-1" or grant.get("scope") != "TEXT_ONLY"
                or not grant.get("observedNotice") or grant.get("evidenceUrl") != row["source"]["url"]
                or not row["source"].get("attributionText") or not row["source"].get("textHash")):
            raise ValueError("Missing explicit licence or attribution for factual pattern")
        docs.append(_terms(" ".join([row["context"], row["description"], row["targetRole"],
            *row["claimedRoles"], *row["keywords"]])))
    df = Counter(term for doc in docs for term in doc)
    idf = {term: math.log((len(docs) + 1) / (freq + 1)) + 1 for term, freq in df.items()}
    vectors = [{term: (1 + math.log(freq)) * idf[term] for term, freq in doc.items()} for doc in docs]
    norms = [math.sqrt(sum(value * value for value in vector.values())) for vector in vectors]
    return data, hashlib.sha256(raw).hexdigest(), idf, vectors, norms


def retrieve_patterns(text, *, limit=3):
    data, digest, idf, vectors, norms = _index()
    counts = _terms(text[-6000:])
    # Out-of-vocabulary terms still count toward query length: one accidental match
    # in otherwise unrelated input must not receive a perfect similarity score.
    unknown_idf = math.log(len(vectors) + 1) + 1
    query = {term: (1 + math.log(freq)) * idf.get(term, unknown_idf) for term, freq in counts.items()}
    norm = math.sqrt(sum(value * value for value in query.values()))
    ranked = []
    if norm:
        for record, vector, doc_norm in zip(data["records"], vectors, norms):
            shared = query.keys() & vector.keys()
            if len(shared) < 2 or not doc_norm:
                continue
            score = sum(query[term] * vector[term] for term in shared) / (norm * doc_norm)
            if score >= MIN_SCORE:
                ranked.append({**record, "score": round(score, 6)})
    ranked.sort(key=lambda row: (-row["score"], row["id"]))
    return {"corpusVersion": data["version"], "corpusHash": digest, "retrieverVersion": INDEX_VERSION,
            "scoreMeaning": "lexical cosine similarity, not a probability",
            "status": "RETRIEVED" if ranked else "NO_RELEVANT_PATTERN", "patterns": ranked[:max(0, min(limit, 3))]}


def prompt_patterns(trace):
    # Do not inject original bodies or URLs into generation prompts.
    return [{key: row[key] for key in ("id", "recordKind", "description", "context", "targetRole",
        "claimedRoles", "requestedActions", "mechanisms", "limits", "adaptationNote")} for row in trace["patterns"]]


def validate_uses(uses, trace):
    from app.services.context_graph import ContextIntegrityError
    available = {row["id"]: row for row in trace["patterns"]}
    seen = set()
    for use in uses:
        row = available.get(use.caseId)
        if row is None or use.caseId in seen:
            raise ContextIntegrityError("unretrieved_pattern_reference")
        if use.technique not in [*row["mechanisms"], *row["requestedActions"]]:
            raise ContextIntegrityError("unsupported_pattern_technique")
        seen.add(use.caseId)


def generation_evidence(plan):
    trace = plan._retrieval_trace
    rows = {row["id"]: row for row in trace.get("patterns", [])}
    validate_uses(plan.referenceUses, {"patterns": list(rows.values())})
    return {"status": "USED" if plan.referenceUses else "NOT_USED",
            "retrievalStatus": trace.get("status", "LEGACY_PLAN_NO_RETRIEVAL"),
            "corpusVersion": trace.get("corpusVersion"), "corpusHash": trace.get("corpusHash"),
            "retrieverVersion": trace.get("retrieverVersion"),
            "retrievedBeforeGeneration": [{"caseId": row["id"], "score": row["score"],
                "sourceUrl": row["source"]["url"], "recordKind": row["recordKind"]} for row in rows.values()],
            "usedForGeneration": [{**use.model_dump(), "source": rows[use.caseId]["source"],
                "recordKind": rows[use.caseId]["recordKind"], "factualPattern": rows[use.caseId]["description"]}
                for use in plan.referenceUses],
            "sourceAttributions": list(dict.fromkeys(rows[use.caseId]["source"]["attributionText"] for use in plan.referenceUses)),
            "attributionRequired": bool(plan.referenceUses),
            "note": "Retrieved factual techniques are distinct from post-generation similarity matches; fictional dialogue is newly generated."}
