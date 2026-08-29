import json

with open("region.json", "r", encoding="utf-8") as f:
    region_data = json.load(f)

with open("typing.json", "r", encoding="utf-8") as f:
    typing_data = json.load(f)


def extract_names(data):
    names = set()

    if isinstance(data, dict):
        for value in data.values():
            if isinstance(value, (dict, list)):
                names.update(extract_names(value))

    elif isinstance(data, list):
        for item in data:
            if isinstance(item, str):
                names.add(item)
            elif isinstance(item, (dict, list)):
                names.update(extract_names(item))

    return names


region_names = extract_names(region_data)
typing_names = extract_names(typing_data)


# Exact checks
names_to_check = [
    "10% zygarde",
    "zygarde-10"
]


print("=" * 60)
print("CHECKING ZYGARDE 10%")
print("=" * 60)

for name in names_to_check:

    print()
    print(f"Name: {name}")

    print(
        f"  Region: {'YES' if name in region_names else 'NO'}"
    )

    print(
        f"  Typing: {'YES' if name in typing_names else 'NO'}"
    )