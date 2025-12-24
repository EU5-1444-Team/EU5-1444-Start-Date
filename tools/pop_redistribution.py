import pyradox
import shutil
import re

SAM_AREA = [
    "medininkai", "palanga", "rietavas", "silale", "skuodas", "taurage",
    "raseiniai", "jurbarkas", "kedainiai", "panemune", "tendziogala",
    "siauliai", "kraziai", "mazeikiai", "papile", "zagare",
]

POPS_FILE = "main_menu/setup/start/06_pops.txt"

# Find a block for a given location
def find_named_block(text, name):
    pattern = re.compile(rf"{re.escape(name)}\s*=\s*\{{", re.MULTILINE)
    match = pattern.search(text)
    if not match:
        return None  # allow missing blocks

    open_brace_index = match.end() - 1
    depth = 1
    for i in range(open_brace_index + 1, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
        if depth == 0:
            return text[match.start():i+1]
    return None

def main():
    # Backup
    shutil.copy(POPS_FILE, POPS_FILE + ".bak")

    with open(POPS_FILE, encoding="utf-8") as f:
        text = f.read()

    tree = pyradox.parse(text)
    locations = tree["locations"]

    all_location_groups = {}

    # Step 1: Consolidate pops for each location
    for location_name, location_data in locations.items():
        if "define_pop" not in location_data:
            continue

        pops = list(location_data.find_all("define_pop"))
        if not any(pop["religion"] == "romuva" for pop in pops):
            continue

        religions = set(pop["religion"] for pop in pops)
        majority_religion = max(
            [(r, sum(pop["size"] for pop in pops if pop["religion"] == r)) for r in religions],
            key=lambda x: x[1],
        )[0]

        pop_groups = {(pop["type"], pop["religion"]): pop["size"] for pop in pops}
        new_groups = {}

        for (ptype, religion), size in pop_groups.items():
            match majority_religion:
                case "catholic":
                    new_religion = "catholic" if religion == "romuva" else religion
                    key = (ptype, new_religion)
                    new_groups[key] = new_groups.get(key, 0) + size
                case "orthodox":
                    new_religion = "orthodox" if religion == "romuva" else religion
                    key = (ptype, new_religion)
                    new_groups[key] = new_groups.get(key, 0) + size
                case "romuva":
                    if ptype == "nobles":
                        key = ("nobles", "catholic")
                        new_groups[key] = new_groups.get(key, 0) + size
                        continue
                    if religion != "romuva":
                        key = (ptype, religion)
                        new_groups[key] = new_groups.get(key, 0) + size
                        continue
                    if location_name in SAM_AREA:
                        romuva_share = 0.8
                        catholic_share = 0.2
                    else:
                        romuva_share = 0.5
                        catholic_share = 0.5
                    new_groups[(ptype, "romuva")] = new_groups.get((ptype, "romuva"), 0) + size * romuva_share
                    new_groups[(ptype, "catholic")] = new_groups.get((ptype, "catholic"), 0) + size * catholic_share

        # Clean and round
        new_groups = {k: round(v, 3) for k, v in new_groups.items() if abs(v) > 1e-3}

        # Store consolidated block per location
        if location_name not in all_location_groups:
            all_location_groups[location_name] = {}
        for k, v in new_groups.items():
            all_location_groups[location_name][k] = all_location_groups[location_name].get(k, 0) + v

    # Step 2: Rebuild and replace all blocks in one pass
    for location_name, groups in all_location_groups.items():
        # Get original pops to extract cultures
        pops = list(locations[location_name].find_all("define_pop"))
        type_to_culture = {pop["type"]: pop["culture"] for pop in pops}

        lines = [f"{location_name} = {{"]
        for (ptype, religion), size in sorted(groups.items()):
            culture = type_to_culture.get(ptype, "unknown")
            lines.append(f"\tdefine_pop = {{\ttype = {ptype}\tsize = {size:.3f}\tculture = {culture}\treligion = {religion} }}")
        lines.append("}")
        block_text = "\n".join(lines)

        # Replace in file
        old_block = find_named_block(text, location_name)
        if old_block:
            text = text.replace(old_block, block_text)
            print(f"Rebuilt block for {location_name}")
        else:
            print(f"Warning: {location_name} block not found, skipping")

    with open(POPS_FILE, "w", encoding="utf-8") as f:
        f.write(text)

if __name__ == "__main__":
    main()
