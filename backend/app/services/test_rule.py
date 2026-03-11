import json
from pathlib import Path

# import your rule
from .wcag.batch1_rules import rule_1_4_3

# load your JSON
json_path = r"C:\Users\Lenovo\repos\AccessEd\backend\tmp_uploads\7af046e4-24a8-4b90-bc3b-559e19329e6a.json"

with open(json_path, "r", encoding="utf-8") as f:
    document = json.load(f)

issues = rule_1_4_3(document)

print(json.dumps(issues, indent=2))
print("\nTotal issues:", len(issues))