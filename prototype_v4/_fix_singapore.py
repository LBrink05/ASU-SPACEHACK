"""Fix isolated singapore_city node by adding drayage edges."""
import json
import pathlib

p = pathlib.Path(__file__).parent / "data" / "network_edges.json"
edges = json.loads(p.read_text())

# Check if singapore_city edges already exist
sing_edges = [e for e in edges["edges"] if "singapore_city" in (e["from"], e["to"])]
print(f"Existing singapore_city edges: {len(sing_edges)}")

if len(sing_edges) == 0:
    new_edges = [
        {
            "from": "singapore_city",
            "to": "port_singapore",
            "mode": "truck",
            "distance_km": 25,
            "transit_days": 0.1,
            "cost_usd_per_tonne": 15,
            "co2_g_per_tonne_km": 62,
            "compliance_score": 3,
            "ets_scope": False,
        },
        {
            "from": "port_singapore",
            "to": "singapore_city",
            "mode": "truck",
            "distance_km": 25,
            "transit_days": 0.1,
            "cost_usd_per_tonne": 15,
            "co2_g_per_tonne_km": 62,
            "compliance_score": 3,
            "ets_scope": False,
        },
    ]
    edges["edges"].extend(new_edges)
    p.write_text(json.dumps(edges, indent=2))
    print(f"Total edges now: {len(edges['edges'])}")
    print("Added singapore_city <-> port_singapore drayage edges")
else:
    print("Edges already exist, skipping.")
