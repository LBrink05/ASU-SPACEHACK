"""
app.py - NeoNomad Flask application.

Multimodal green corridor route planner with real satellite data.
"""

import os
import json
from flask import Flask, render_template, request, jsonify

from src import routing, gee_fetchers, waypoint_generator, ets_advisor

app = Flask(__name__)


def _graph_nodes_dict():
    """Build a lookup dict of node_id -> node data from the graph."""
    g = routing.get_graph()
    return {
        n: {
            "region": d["region"],
            "ets_scope": d["ets_scope"],
            "lat": d["lat"],
            "lon": d["lon"],
            "label": d["label"],
        }
        for n, d in g.nodes(data=True)
    }


@app.route("/")
def index():
    nodes = routing.node_options()
    gee_ok = gee_fetchers.init_gee()
    print(f"GEE initialized: {gee_ok}", flush=True)
    return render_template("index.html", nodes=nodes, gee_available=gee_ok)


@app.route("/api/nodes")
def api_nodes():
    return jsonify(routing.node_options())


@app.route("/api/routes", methods=["POST"])
def api_routes():
    data = request.get_json(force=True)

    origin = data.get("origin", "")
    destination = data.get("destination", "")
    cargo_t = float(data.get("cargo_t", 1.0))

    # Validate cargo weight
    if cargo_t <= 0:
        return jsonify({"error": "Cargo weight must be a positive number.", "routes": []})

    # Custom lat/lon → snap to nearest node
    if data.get("custom_origin"):
        try:
            origin = routing.snap_to_nearest_node(
                float(data["origin_lat"]), float(data["origin_lon"])
            )
        except (TypeError, ValueError):
            return jsonify({"error": "Invalid origin coordinates.", "routes": []})
    if data.get("custom_destination"):
        try:
            destination = routing.snap_to_nearest_node(
                float(data["dest_lat"]), float(data["dest_lon"])
            )
        except (TypeError, ValueError):
            return jsonify({"error": "Invalid destination coordinates.", "routes": []})

    if origin == destination:
        return jsonify({"error": "Origin and destination cannot be the same.", "routes": []})

    w_emissions = float(data.get("w_emissions", 0.35))
    w_cost = float(data.get("w_cost", 0.25))
    w_compliance = float(data.get("w_compliance", 0.25))
    w_time = float(data.get("w_time", 0.15))
    fetch_satellite = data.get("fetch_satellite", True)

    app.logger.info(f"Finding routes: origin={origin}, dest={destination}, cargo_t={cargo_t}, fetch_satellite={fetch_satellite}")

    routes = routing.find_routes(
        origin, destination, cargo_t,
        w_emissions, w_cost, w_compliance, w_time,
        k=3,
    )

    if not routes:
        return jsonify({"error": "No routes found between those nodes.", "routes": []})

    graph_nodes = _graph_nodes_dict()

    for route in routes:
        # Generate waypoints for satellite enrichment
        legs = waypoint_generator.waypoints_for_route(route)
        route["legs_with_waypoints"] = []

        all_waypoints = []
        for leg in legs:
            all_waypoints.extend(leg["waypoints"])
            route["legs_with_waypoints"].append({
                "from": leg["from"],
                "to": leg["to"],
                "mode": leg["mode"],
                "waypoint_count": len(leg["waypoints"]),
            })

        print(f"Route has {len(all_waypoints)} total waypoints", flush=True)

        # Satellite data enrichment (if enabled)
        if fetch_satellite and gee_fetchers.is_available():
            print(f"Fetching satellite data for {len(all_waypoints)} waypoints...", flush=True)
            enriched = gee_fetchers.enrich_waypoints(all_waypoints, sample_every=4)
            route["satellite"] = gee_fetchers.satellite_summary(enriched)
            print(f"Satellite summary: {route['satellite']}", flush=True)
        else:
            print(f"Skipping satellite fetch (fetch_satellite={fetch_satellite}, gee_available={gee_fetchers.is_available()})", flush=True)
            route["satellite"] = {
                "gee_available": False,
                "waypoints_sampled": 0,
                "waypoints_total": len(all_waypoints),
                "no2_mean": None, "no2_count": 0,
                "sar_mean": None, "sar_count": 0,
                "sst_mean": None, "sst_count": 0,
                "co_mean": None, "co_count": 0,
            }

        # ETS compliance assessment
        ets = ets_advisor.assess_route(route, graph_nodes)
        route["ets"] = ets

        # Waypoint coordinates for map (just lat/lon, not full enriched data)
        route["waypoint_coords"] = [
            {"lat": wp["lat"], "lon": wp["lon"]}
            for wp in all_waypoints[::2]  # every other point for rendering
        ]

    return jsonify({"routes": routes})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5052))
    print(f"\n  NeoNomad starting on http://127.0.0.1:{port}\n", flush=True)
    app.run(debug=True, port=port)