"""Functional tests for NeoNomad."""
import json
import urllib.request

BASE = "http://127.0.0.1:5050"

def api(path, payload=None):
    if payload:
        req = urllib.request.Request(
            BASE + path,
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
        )
    else:
        req = urllib.request.Request(BASE + path)
    resp = urllib.request.urlopen(req)
    return json.loads(resp.read())

# 1. GET /api/nodes
nodes = api("/api/nodes")
print(f"1. /api/nodes: {len(nodes)} nodes")
ids = [n["id"] for n in nodes]
assert len(ids) == len(set(ids)), "DUPLICATE NODE IDS"
for n in nodes:
    assert all(k in n for k in ("id", "label", "type", "lat", "lon", "region", "ets_scope")), f"Missing key in node {n['id']}"
print("   All nodes have required keys. OK")

# 2. Same origin and destination
data = api("/api/routes", {"origin": "frankfurt", "destination": "frankfurt", "cargo_t": 100, "fetch_satellite": False})
print(f"2. Same origin/dest: routes={len(data['routes'])}, error={data.get('error', 'none')}")

# 3. Non-existent node
try:
    data = api("/api/routes", {"origin": "fake_node", "destination": "frankfurt", "cargo_t": 100, "fetch_satellite": False})
    print(f"3. Fake node: routes={len(data['routes'])}, error={data.get('error', 'none')}")
except Exception as e:
    print(f"3. Fake node: EXCEPTION {e}")

# 4. Negative cargo
data = api("/api/routes", {"origin": "port_shanghai", "destination": "frankfurt", "cargo_t": -10, "fetch_satellite": False})
print(f"4. Negative cargo (-10t): routes={len(data['routes'])}")
if data["routes"]:
    r = data["routes"][0]
    print(f"   Route 1 CO2: {r['total_co2_kg']}kg (should be negative = BUG)")

# 5. Zero cargo
data = api("/api/routes", {"origin": "port_shanghai", "destination": "frankfurt", "cargo_t": 0, "fetch_satellite": False})
print(f"5. Zero cargo: routes={len(data['routes'])}")
if data["routes"]:
    r = data["routes"][0]
    print(f"   Route 1 CO2: {r['total_co2_kg']}kg, Cost: ${r['total_cost_usd']}")

# 6. Custom lat/lon (near Shanghai)
data = api("/api/routes", {
    "custom_origin": True, "origin_lat": 31.0, "origin_lon": 121.0,
    "custom_destination": True, "dest_lat": 50.0, "dest_lon": 8.5,
    "cargo_t": 100, "fetch_satellite": False
})
print(f"6. Custom lat/lon: routes={len(data['routes'])}, error={data.get('error', 'none')}")
if data["routes"]:
    print(f"   Snapped to: {data['routes'][0]['node_labels'][0]} -> {data['routes'][0]['node_labels'][-1]}")

# 7. Very large weights
data = api("/api/routes", {
    "origin": "port_shanghai", "destination": "frankfurt",
    "cargo_t": 100, "fetch_satellite": False,
    "w_emissions": 1.0, "w_cost": 0, "w_compliance": 0, "w_time": 0
})
print(f"7. Max emissions weight: Route 1 = {' -> '.join(data['routes'][0]['node_labels'])}")
print(f"   CO2: {data['routes'][0]['total_co2_kg']}kg")

# 8. All weights zero
data = api("/api/routes", {
    "origin": "port_shanghai", "destination": "frankfurt",
    "cargo_t": 100, "fetch_satellite": False,
    "w_emissions": 0, "w_cost": 0, "w_compliance": 0, "w_time": 0
})
print(f"8. All weights zero: routes={len(data['routes'])}")

# 9. Check satellite summary keys when GEE off
data = api("/api/routes", {"origin": "port_shanghai", "destination": "frankfurt", "cargo_t": 100, "fetch_satellite": False})
r = data["routes"][0]
sat = r["satellite"]
required_sat_keys = {"gee_available", "waypoints_sampled", "waypoints_total", "no2_mean", "sar_mean", "sst_mean", "co_mean"}
missing = required_sat_keys - set(sat.keys())
print(f"9. Satellite keys present: {not missing} (missing: {missing})")

# 10. Check ETS keys
ets = r["ets"]
required_ets_keys = {"legs", "total_liable_co2_kg", "total_ets_cost_eur", "has_ets_exposure", "phase_in_factor", "eua_price_eur", "year"}
missing_ets = required_ets_keys - set(ets.keys())
print(f"10. ETS keys present: {not missing_ets} (missing: {missing_ets})")

# 11. Check isolated node: singapore_city
data = api("/api/routes", {"origin": "singapore_city", "destination": "frankfurt", "cargo_t": 100, "fetch_satellite": False})
print(f"11. singapore_city -> frankfurt: routes={len(data['routes'])}, error={data.get('error', 'none')}")

print("\nDone.")
