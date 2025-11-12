import json
from pathlib import Path

path = Path("/home/django/appertivo/Outscraper-20251110231245xs5f_restaurant.json")
with open(path, "r", encoding="utf-8") as f:
    data = json.load(f)

# Flatten structure if nested
records = []
if isinstance(data, list):
    records = data
elif isinstance(data, dict) and "data" in data:
    for batch in data["data"]:
        if isinstance(batch, list):
            records.extend(batch)
        elif isinstance(batch, dict):
            records.append(batch)

lengths = {}
for rec in records:
    for k, v in rec.items():
        if isinstance(v, str):
            l = len(v)
            if k not in lengths or l > lengths[k]:
                lengths[k] = l

# Sort by longest fields
sorted_fields = sorted(lengths.items(), key=lambda x: x[1], reverse=True)

print("Top 20 longest string fields:")
for k, v in sorted_fields[:20]:
    print(f"{k:25} {v}")
