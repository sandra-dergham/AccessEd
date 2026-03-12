import json

from .wcag.batch1_rules import (
    rule_2_5_1,
    rule_2_5_2,
    rule_2_5_3,
    rule_2_5_4,
)

JSON_PATH = r"backend/tmp_uploads/e48b461f-b956-459c-a51f-f0d8a4d27b39.json"


def main():
    with open(JSON_PATH, "r", encoding="utf-8") as f:
        document = json.load(f)

    issues = []
    issues.extend(rule_2_5_1(document))
    issues.extend(rule_2_5_2(document))
    issues.extend(rule_2_5_3(document))
    issues.extend(rule_2_5_4(document))

    print(json.dumps(issues, indent=2, ensure_ascii=False))
    print("\nTotal issues:", len(issues))

    by_rule = {}
    for issue in issues:
        criterion = issue.get("criterion", "unknown")
        by_rule[criterion] = by_rule.get(criterion, 0) + 1

    print("\nCounts by rule:")
    for criterion, count in sorted(by_rule.items()):
        print(f"{criterion}: {count}")


if __name__ == "__main__":
    main()