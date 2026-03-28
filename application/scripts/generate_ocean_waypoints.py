#!/usr/bin/env python3
"""
Offline script: generate ocean waypoints for all ocean edges that
cross land masses, and write them back into network_edges.json.

Usage:
    cd prototype_v4/
    python scripts/generate_ocean_waypoints.py
"""

import json, sys, os, time

# ensure project root is on path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.ocean_router import crosses_land, generate_ocean_waypoints

DATA = os.path.join(os.path.dirname(__file__), "..", "data")
NODES_FILE = os.path.join(DATA, "network_nodes.json")
EDGES_FILE = os.path.join(DATA, "network_edges.json")

def main():
    with open(NODES_FILE) as f:
        ndata = json.load(f)
    with open(EDGES_FILE) as f:
        edata = json.load(f)

    nodes = {n["id"]: n for n in ndata["nodes"]}
    edges = edata["edges"]

    ocean_count = 0
    crossing_count = 0
    wp_count = 0
    skipped_missing = 0
    t0 = time.time()

    for i, e in enumerate(edges):
        if "from" not in e:
            continue
        if e.get("mode") != "ocean":
            continue
        ocean_count += 1

        fid, tid = e["from"], e["to"]
        if fid not in nodes or tid not in nodes:
            skipped_missing += 1
            continue

        lat1, lon1 = nodes[fid]["lat"], nodes[fid]["lon"]
        lat2, lon2 = nodes[tid]["lat"], nodes[tid]["lon"]

        wps = generate_ocean_waypoints(lat1, lon1, lat2, lon2, total_pts=35)
        if wps is not None:
            e["waypoints"] = wps
            crossing_count += 1
            wp_count += len(wps)
        else:
            # Remove any stale waypoints from a prior run
            e.pop("waypoints", None)

        if (ocean_count % 50) == 0:
            print(f"  processed {ocean_count} ocean edges …")

    elapsed = time.time() - t0
    print(f"\n=== Ocean Waypoint Generation ===")
    print(f"Ocean edges:        {ocean_count}")
    print(f"Crossing land:      {crossing_count}")
    print(f"Total waypoints:    {wp_count}")
    print(f"Avg wps/crossing:   {wp_count / max(crossing_count, 1):.1f}")
    print(f"Skipped (no node):  {skipped_missing}")
    print(f"Time:               {elapsed:.1f}s")

    # Write back
    with open(EDGES_FILE, "w") as f:
        json.dump(edata, f, indent=2)
    print(f"\nWrote {EDGES_FILE}")

    # Validation: check no waypoints are on land
    from src.ocean_router import _land_contains
    bad = 0
    for e in edges:
        for wp in e.get("waypoints", []):
            if _land_contains(wp[1], wp[0]):
                bad += 1
    print(f"Waypoints on land:  {bad}")
    if bad:
        print("WARNING: some waypoints fall inside land polygons (110m resolution tolerance)")

if __name__ == "__main__":
    main()
