"""
Severity Scale
--------------
  not_applicable - criterion does not apply; excluded from scoring
  pass           - criterion evaluated, no violation; contributes positively
  needs_review   - cannot confirm statically; excluded from scoring
  low            - best-practice gap, technically compliant; minor penalty
  medium         - confirmed violation, moderate AT impact; moderate penalty
  high           - confirmed violation, severe AT impact; heavy penalty

Score Calculation
-----------------
Score is 0-100, representing the percentage of evaluable criteria that pass weighted by severity:
      pass   → full weight  (1.0)
      low    → 75% weight   (0.75) — minor gap, mostly compliant
      medium → 25% weight   (0.25) — confirmed violation, moderate impact
      high   → 0% weight    (0.0)  — confirmed violation, severe impact
  - Score = sum(weights) / count(evaluable) * 100
  - If no evaluable criteria exist, score is None (cannot be determined)
"""

from __future__ import annotations
from typing import Any, Dict, List, Optional


# Weight assigned to each severity level (contribution to score)
_WEIGHTS: Dict[str, float] = {
    "pass":   1.0,
    "low":    0.75,
    "medium": 0.25,
    "high":   0.0,
}

# Severities excluded from scoring entirely
_EXCLUDED = {"not_applicable", "needs_review"}


def compute_score(issues: List[Dict[str, Any]]) -> Dict[str, Any]:
    breakdown = {"pass": 0, "low": 0, "medium": 0, "high": 0}
    not_applicable_count = 0
    needs_review_count   = 0
    total_weight         = 0.0
    evaluable_count      = 0

    for issue in issues:
        severity = issue.get("severity", "")

        if severity == "not_applicable":
            not_applicable_count += 1
        elif severity == "needs_review":
            needs_review_count += 1
        elif severity in _WEIGHTS:
            breakdown[severity] += 1
            total_weight        += _WEIGHTS[severity]
            evaluable_count     += 1

    if evaluable_count == 0:
        score = None
        grade = "N/A"
    else:
        score = round((total_weight / evaluable_count) * 100, 1)
        grade = _grade(score)

    return {
        "score":          score,
        "grade":          grade,
        "evaluable":      evaluable_count,
        "not_applicable": not_applicable_count,
        "needs_review":   needs_review_count,
        "breakdown":      breakdown,
    }


def _grade(score: float) -> str:
    if score >= 90:
        return "A"
    if score >= 75:
        return "B"
    if score >= 50:
        return "C"
    if score >= 25:
        return "D"
    return "F"