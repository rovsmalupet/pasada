"""The behavioural knowledge base and its retrieval, as evaluated (notebook 2.4)."""

import pandas as pd

from . import config as C

_KB = _VECTORS = _EMBEDDER = None


def _load():
    global _KB, _VECTORS, _EMBEDDER
    if _KB is None:
        from sentence_transformers import SentenceTransformer
        if not C.KB_FILE.is_file():
            raise SystemExit(f"{C.KB_FILE} not found (set KB_FILE to the knowledge base CSV)")
        _KB = pd.read_csv(C.KB_FILE, dtype=str).fillna("")
        _EMBEDDER = SentenceTransformer(C.EMBEDDING_MODEL, device="cpu")
        _VECTORS = _EMBEDDER.encode([kb_text(r) for _, r in _KB.iterrows()], normalize_embeddings=True)


def kb_text(row):
    return f"{row['statement']} (about: {row['attribute']}; factor: {row['factor']})"


def agent_query(agent):
    words = agent["commuter_type"].replace("_", " ")
    text = (f"Transport mode choice of a {agent['age_group'].replace(' - ', ' to ')} year old "
            f"{agent['sex'].lower()} {words} commuter")
    if agent.get("occupation_category"):
        text += f", occupation: {agent['occupation_category']}"
    text += (f", per capita income decile {agent['per_capita_income_decile']} of 10 "
             "(1 is the lowest income)")
    text += ", household has a car" if agent["household_car_access"] else ", household has no car"
    text += ", household has a bicycle" if agent["household_bicycle_access"] else ", household has no bicycle"
    return text


def kb_allowed(row, agent):
    attribute = row["attribute"].strip().lower()
    if attribute.startswith("commuter type:"):
        return attribute.split(":", 1)[1].strip() == agent["commuter_type"].replace("_", " ")
    return True


def retrieve(agent, k=None):
    _load()
    k = k or C.KB_TOP_K
    query = _EMBEDDER.encode([agent_query(agent)], normalize_embeddings=True)[0]
    scores = _VECTORS @ query
    allowed = [i for i in range(len(_KB)) if kb_allowed(_KB.iloc[i], agent)]
    best = sorted(allowed, key=lambda i: -scores[i])[:k]
    return [{**_KB.iloc[i].to_dict(), "score": round(float(scores[i]), 3)} for i in best]


def findings_text(rows):
    return "\n".join(f"- [{r['id']}] {r['statement']} ({r['source']}; {r['population']})" for r in rows)
