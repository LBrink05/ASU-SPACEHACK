"""Quick data audit script for NeoNomad."""
import json
import networkx as nx

with open("data/network_nodes.json") as f:
    nodes = json.load(f)
with open("data/network_edges.json") as f:
    edges = json.load(f)

node_ids = {n["id"] for n in nodes["nodes"]}
print(f"Nodes: {len(node_ids)}")
print(f"Edges: {len(edges['edges'])}")

# Broken edge references
bad = []
for e in edges["edges"]:
    if e["from"] not in node_ids:
        bad.append(f"  FROM missing: {e['from']}")
    if e["to"] not in node_ids:
        bad.append(f"  TO missing: {e['to']}")
if bad:
    print("BROKEN EDGES:")
    for b in bad:
        print(b)
else:
    print("All edges reference valid nodes.")

# Duplicate edges
seen = set()
dupes = []
for e in edges["edges"]:
    key = (e["from"], e["to"], e["mode"])
    if key in seen:
        dupes.append(key)
    seen.add(key)
print(f"Duplicate edges: {dupes}" if dupes else "No duplicate edges.")

# Emission factor sanity
ranges = {"ocean": (3, 30), "truck": (40, 120), "rail": (10, 40), "air": (400, 900), "last_mile": (30, 200)}
for e in edges["edges"]:
    lo, hi = ranges.get(e["mode"], (0, 9999))
    if not (lo <= e["co2_g_per_tonne_km"] <= hi):
        print(f"  SUSPECT {e['mode']} CO2: {e['co2_g_per_tonne_km']} for {e['from']}->{e['to']}")

# Coordinate ranges
for n in nodes["nodes"]:
    if not (-90 <= n["lat"] <= 90):
        print(f"  BAD LAT: {n['id']} lat={n['lat']}")
    if not (-180 <= n["lon"] <= 180):
        print(f"  BAD LON: {n['id']} lon={n['lon']}")

# ETS scope check
eu_ets = [n for n in nodes["nodes"] if n.get("ets_scope") and n["region"] != "europe"]
if eu_ets:
    print(f"NON-EU with ets_scope=true: {[n['id'] for n in eu_ets]}")
else:
    print("ETS scope consistent with EU region.")

print(f"Regions: {sorted(set(n['region'] for n in nodes['nodes']))}")
print(f"Types: {sorted(set(n['type'] for n in nodes['nodes']))}")
print(f"Modes: {sorted(set(e['mode'] for e in edges['edges']))}")

# Isolated nodes
g = nx.DiGraph()
for e in edges["edges"]:
    g.add_edge(e["from"], e["to"])
isolated = node_ids - set(g.nodes())
if isolated:
    print(f"ISOLATED NODES: {isolated}")
else:
    print("No isolated nodes.")

# Compliance score range
for e in edges["edges"]:
    if not (1 <= e["compliance_score"] <= 5):
        print(f"  BAD compliance: {e['compliance_score']} for {e['from']}->{e['to']}")

# Distance sanity: check if edge distance matches haversine
import math
def hav(lat1, lon1, lat2, lon2):
    R = 6371.0
    rlat1, rlat2 = math.radians(lat1), math.radians(lat2)
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat/2)**2 + math.cos(rlat1)*math.cos(rlat2)*math.sin(dlon/2)**2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))

node_lookup = {n["id"]: n for n in nodes["nodes"]}
print("\nDistance check (claimed vs haversine):")
for e in edges["edges"]:
    n1 = node_lookup[e["from"]]
    n2 = node_lookup[e["to"]]
    actual = hav(n1["lat"], n1["lon"], n2["lat"], n2["lon"])
    claimed = e["distance_km"]
    ratio = claimed / actual if actual > 0 else 0
    if ratio < 0.7 or ratio > 1.5:
        print(f"  MISMATCH {e['from']}->{e['to']} ({e['mode']}): claimed={claimed}km, haversine={actual:.0f}km, ratio={ratio:.2f}")

# Transit time sanity
print("\nTransit time check:")
for e in edges["edges"]:
    d = e["distance_km"]
    t = e["transit_days"]
    if t <= 0:
        print(f"  ZERO TRANSIT: {e['from']}->{e['to']}")
        continue
    speed_kmh = d / (t * 24)
    mode = e["mode"]
    expected = {"ocean": (15, 45), "truck": (30, 100), "rail": (30, 120), "air": (500, 1000), "last_mile": (15, 80)}
    lo, hi = expected.get(mode, (1, 9999))
    if not (lo <= speed_kmh <= hi):
        print(f"  SUSPECT speed {e['from']}->{e['to']} ({mode}): {speed_kmh:.0f} km/h (distance={d}km, days={t})")
