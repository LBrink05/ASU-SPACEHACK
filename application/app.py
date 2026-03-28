"""
app.py - NeoNomad Flask application.

Multimodal green corridor route planner with real satellite data.
"""

import os
import json
from flask import Flask, render_template, request, jsonify

from src import routing, gee_fetchers, waypoint_generator, ets_advisor, synthetic_sat

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
    g = routing.get_graph()
    edge_count = g.number_of_edges()
    gee_ok = gee_fetchers.init_gee()
    return render_template("index.html", nodes=nodes, edge_count=edge_count, gee_available=gee_ok)


@app.route("/api/nodes")
def api_nodes():
    return jsonify(routing.node_options())


@app.route("/api/corridors")
def api_corridors():
    path = os.path.join(os.path.dirname(__file__), "data", "corridors.geojson")
    with open(path) as f:
        return jsonify(json.load(f))


@app.route("/api/geojson-ports")
def api_geojson_ports():
    path = os.path.join(os.path.dirname(__file__), "data", "ports.geojson")
    with open(path) as f:
        return jsonify(json.load(f))


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

    # Collect optional waypoints (up to 6 intermediate stops)
    raw_waypoints = data.get("waypoints", [])
    if not isinstance(raw_waypoints, list):
        raw_waypoints = []
    waypoints = [w.strip() for w in raw_waypoints if isinstance(w, str) and w.strip()]
    if len(waypoints) > 6:
        return jsonify({"error": "Maximum 6 intermediate stops allowed.", "routes": []})

    # Build ordered stop list and check for consecutive duplicates
    stops = [origin] + waypoints + [destination]
    for i in range(len(stops) - 1):
        if stops[i] == stops[i + 1]:
            return jsonify({"error": f"Consecutive duplicate stop: {stops[i]}.", "routes": []})

    w_emissions = float(data.get("w_emissions", 0.35))
    w_cost = float(data.get("w_cost", 0.25))
    w_compliance = float(data.get("w_compliance", 0.25))
    w_time = float(data.get("w_time", 0.15))
    w_satellite = float(data.get("w_satellite", 0.0))
    w_disruption = float(data.get("w_disruption", 0.0))
    sat_mode = data.get("sat_mode", "offline")  # offline | online | synthetic

    # Single-leg or multi-leg routing
    if len(stops) == 2:
        routes = routing.find_routes(
            origin, destination, cargo_t,
            w_emissions, w_cost, w_compliance, w_time,
            k=3,
        )
    else:
        routes = routing.find_multi_routes(
            stops, cargo_t,
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

        # Satellite data enrichment (mode: offline | online | synthetic)
        if sat_mode == "online" and gee_fetchers.is_available():
            enriched = gee_fetchers.enrich_waypoints(all_waypoints, sample_every=4)
            route["satellite"] = gee_fetchers.satellite_summary(enriched, route=route)
            route["satellite"]["sat_mode"] = "online"
        elif sat_mode == "synthetic":
            enriched = synthetic_sat.generate_synthetic_waypoints(
                all_waypoints, route, sample_every=4
            )
            route["satellite"] = gee_fetchers.satellite_summary(enriched, route=route)
            route["satellite"]["gee_available"] = True  # treat as available for UI
            route["satellite"]["sat_mode"] = "synthetic"
        else:
            route["satellite"] = {
                "gee_available": False,
                "sat_mode": "offline",
                "waypoints_sampled": 0,
                "waypoints_total": len(all_waypoints),
                "no2_mean": None, "no2_count": 0,
                "sar_mean": None, "sar_count": 0,
                "sst_mean": None, "sst_count": 0,
                "co_mean": None, "co_count": 0,
                "emissions_verification": None,
                "sea_state": None,
                "port_congestion": [],
                "air_quality": None,
                "satellite_risk_score": None,
                "warning_zones": [],
            }

        # ETS compliance assessment
        ets = ets_advisor.assess_route(route, graph_nodes)
        route["ets"] = ets

        # ── Supply-chain disruption risk ──
        sat = route["satellite"]
        signals = []   # (value 0-1, weight)
        n_available = 0

        # Sea-state risk
        if sat.get("sea_state") and sat["sea_state"].get("sea_risk_score") is not None:
            signals.append((sat["sea_state"]["sea_risk_score"], 0.25))
            n_available += 1
        else:
            signals.append((0.15, 0.25))  # mild prior

        # Port congestion
        pc = sat.get("port_congestion") or []
        if pc:
            lvl_map = {"high": 1.0, "moderate": 0.5, "low": 0.1, "unknown": 0.3}
            pc_score = sum(lvl_map.get(p.get("congestion_level", "unknown"), 0.3) for p in pc) / len(pc)
            signals.append((pc_score, 0.20))
            n_available += 1
        else:
            signals.append((0.2, 0.20))

        # Corridor pollution (congestion proxy)
        aq = sat.get("air_quality")
        if aq and aq.get("polluted_pct") is not None:
            signals.append((aq["polluted_pct"] / 100.0, 0.15))
            n_available += 1
        else:
            signals.append((0.15, 0.15))

        # Tropical storm proxy
        if sat.get("sea_state") and sat["sea_state"].get("sst_storm_risk") is not None:
            signals.append((1.0 if sat["sea_state"]["sst_storm_risk"] else 0.0, 0.15))
            n_available += 1
        else:
            signals.append((0.1, 0.15))

        # Transit time exposure
        td = route.get("total_transit_days", 0)
        signals.append((min(td / 30.0, 1.0), 0.15))
        n_available += 1

        # Regulatory compliance (inverted: low compliance = risk)
        comp = route.get("avg_compliance_score", 0.9)
        signals.append((1.0 - comp, 0.10))
        n_available += 1

        total_w = sum(w for _, w in signals)
        risk_score = sum(v * w for v, w in signals) / total_w if total_w else 0.0
        risk_pct = round(risk_score * 100, 1)

        # Margin of error: narrows as more real signals are available
        max_signals = 6
        margin = round(3.0 + 12.0 * (1.0 - n_available / max_signals), 1)

        route["disruption_risk"] = {
            "pct": risk_pct,
            "margin": margin,
            "signals_available": n_available,
            "signals_total": max_signals,
        }

        # Waypoint coordinates for map (just lat/lon, not full enriched data)
        route["waypoint_coords"] = [
            {"lat": wp["lat"], "lon": wp["lon"]}
            for wp in all_waypoints[::2]  # every other point for rendering
        ]

    # Post-hoc blended re-ranking (satellite + disruption risk)
    if (w_satellite > 0 or w_disruption > 0) and len(routes) > 1:
        # Compute a normalised base score for each route from cost/time/CO2/compliance
        co2_vals  = [r["total_co2_kg"]          for r in routes]
        cost_vals = [r["total_cost_usd"]        for r in routes]
        time_vals = [r["total_transit_days"]     for r in routes]
        comp_vals = [r["avg_compliance_score"]   for r in routes]

        def _minmax(vals):
            lo, hi = min(vals), max(vals)
            return [(v - lo) / (hi - lo) if hi > lo else 0.0 for v in vals]

        norm_co2  = _minmax(co2_vals)
        norm_cost = _minmax(cost_vals)
        norm_time = _minmax(time_vals)
        # Compliance: higher = better, invert so lower = better score
        norm_comp = [1.0 - c for c in _minmax(comp_vals)]

        tw = w_emissions + w_cost + w_compliance + w_time
        if tw == 0:
            tw = 1.0

        w_post = min(w_satellite + w_disruption, 1.0)

        for i, route in enumerate(routes):
            base = (
                w_emissions  * norm_co2[i]
                + w_cost     * norm_cost[i]
                + w_compliance * norm_comp[i]
                + w_time     * norm_time[i]
            ) / tw

            # Satellite environmental risk
            risk = route["satellite"].get("satellite_risk_score")
            sat_score = risk if risk is not None else 0.5

            # Disruption risk
            dr = route.get("disruption_risk", {}).get("pct", 15.0) / 100.0

            # Weighted post-hoc blend
            post_score = 0.0
            if w_post > 0:
                post_score = (w_satellite * sat_score + w_disruption * dr) / w_post

            route["_blend"] = (1.0 - w_post) * base + w_post * post_score

        routes.sort(key=lambda r: r["_blend"])
        for rank, route in enumerate(routes, start=1):
            route["rank"] = rank
            route.pop("_blend", None)

    return jsonify({"routes": routes})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5052))
    print(f"\n  NeoNomad starting on http://127.0.0.1:{port}\n")
    app.run(debug=True, port=port)
