from typing import Any, Dict, Literal

Severity = Literal["low", "not applicable", "needs review", "medium", "high"]

def make_issue(
    criterion: str,
    issue: str,
    location: Dict[str, Any],
    severity: Severity,
    recommendation: str,
) -> Dict[str, Any]:
    obj: Dict[str, Any] = {
        "criterion": criterion,
        "issue": issue,
        "location": location,
        "severity": severity,
        "recommendation": recommendation,
    }
    return obj