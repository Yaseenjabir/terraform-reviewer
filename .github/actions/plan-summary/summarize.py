import json
import sys

plan_file = sys.argv[1]

with open(plan_file) as f:
    plan = json.load(f)

print("Planned changes:")
for change in plan.get("resource_changes", []):
    actions = "+".join(change["change"]["actions"])
    print(f"  {actions}  {change['address']}")
