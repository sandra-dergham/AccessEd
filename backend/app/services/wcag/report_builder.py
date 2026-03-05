from .scoring import compute_score

def build_report(document_meta: dict, issues: list[dict]) -> dict:
    return {
        "meta": document_meta,
        "score": compute_score(issues),
        "issues": issues
    }