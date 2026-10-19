"""
SunAid-LoadPulse Dashboard Web Server
Provides REST API proxying to ESP32 hardware or built-in realistic solar simulator,
serving live Chart.js visualization, interactive controls, and comparison benchmarks.
"""

from flask import Flask, render_template, request, jsonify, Response
import requests
import time
import os
import sys

# Ensure local simulator module can be imported
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))
from simulator import (
    SolarProfileGenerator,
    BaselineController,
    LoadPulseController,
    SimulationRunner
)

app = Flask(__name__, static_folder="static", template_folder="templates")

# Runtime Global State
state = {
    "sim_mode": True,               # Start in Simulation mode by default (demo ready!)
    "esp32_ip": "192.168.1.100",    # Default ESP32 IP address
    "active_mode": "LOADPULSE",     # "BASELINE" or "LOADPULSE"
    "active_profile": "flickering_shade",
    "sim_step_idx": 0,
    "sim_samples": [],
    "last_step_time": time.time(),
    "config": {
        "baseline_ess": 100.0,
        "baseline_opt": 200.0,
        "lp_ess_off": 90.0,
        "lp_ess_on": 120.0,
        "lp_opt_off": 180.0,
        "lp_opt_on": 210.0,
        "shortage_delay_ms": 3000,
        "recovery_delay_ms": 5000,
        "min_dwell_ms": 2000
    }
}

# Controllers for Simulation Mode
sim_controllers = {
    "BASELINE": BaselineController(
        p_ess_thresh=state["config"]["baseline_ess"],
        p_opt_thresh=state["config"]["baseline_opt"]
    ),
    "LOADPULSE": LoadPulseController(
        p_ess_off=state["config"]["lp_ess_off"],
        p_ess_on=state["config"]["lp_ess_on"],
        p_opt_off=state["config"]["lp_opt_off"],
        p_opt_on=state["config"]["lp_opt_on"],
        shortage_delay_sec=state["config"]["shortage_delay_ms"] / 1000.0,
        recovery_delay_sec=state["config"]["recovery_delay_ms"] / 1000.0,
        min_dwell_sec=state["config"]["min_dwell_ms"] / 1000.0
    )
}


def refresh_sim_samples():
    """Refreshes the simulated solar samples for the active profile."""
    state["sim_samples"] = SolarProfileGenerator.generate_profile(
        profile_type=state["active_profile"],
        duration_sec=120.0,
        dt_sec=0.2
    )
    state["sim_step_idx"] = 0


# Initialize simulator profile
refresh_sim_samples()


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/status", methods=["GET"])
def get_status():
    """
    Returns real-time telemetry from either physical ESP32 or local simulation engine.
    """
    if not state["sim_mode"]:
        # Query physical ESP32 over local network
        try:
            esp_url = f"http://{state['esp32_ip']}/api/status"
            resp = requests.get(esp_url, timeout=1.5)
            if resp.status_code == 200:
                data = resp.json()
                data["source"] = "ESP32_HARDWARE"
                data["sim_mode"] = False
                return jsonify(data)
        except Exception as e:
            # Fallback warning if hardware is unreachable
            return jsonify({
                "source": "ESP32_ERROR",
                "error": f"Cannot reach ESP32 at {state['esp32_ip']}: {str(e)}",
                "sim_mode": False,
                "voltage_v": 0.0,
                "current_ma": 0.0,
                "power_mw": 0.0,
                "ldr_raw": 0,
                "relay_essential": False,
                "relay_optional": False,
                "mode": state["active_mode"],
                "switch_count_total": 0,
                "switch_count_essential": 0,
                "switch_count_optional": 0,
                "uptime_essential_pct": 0.0,
                "uptime_optional_pct": 0.0,
                "uptime_seconds": 0.0
            }), 200

    # Simulation Mode step
    samples = state["sim_samples"]
    if not samples:
        refresh_sim_samples()
        samples = state["sim_samples"]

    idx = state["sim_step_idx"] % len(samples)
    current_sample = samples[idx]
    state["sim_step_idx"] += 1

    ctrl = sim_controllers[state["active_mode"]]
    dt = 0.2
    ctrl.step(current_sample["power_mw"], dt)
    metrics = ctrl.get_metrics()

    response_data = {
        "source": "SIMULATION_ENGINE",
        "sim_mode": True,
        "profile": state["active_profile"],
        "voltage_v": current_sample["voltage_v"],
        "current_ma": current_sample["current_ma"],
        "power_mw": current_sample["power_mw"],
        "ldr_raw": current_sample["ldr_raw"],
        "relay_essential": metrics["relay_essential"],
        "relay_optional": metrics["relay_optional"],
        "mode": state["active_mode"],
        "switch_count_total": metrics["total_switches"],
        "switch_count_essential": metrics["switch_count_essential"],
        "switch_count_optional": metrics["switch_count_optional"],
        "uptime_essential_pct": metrics["uptime_essential_pct"],
        "uptime_optional_pct": metrics["uptime_optional_pct"],
        "uptime_seconds": metrics["total_runtime_sec"]
    }

    return jsonify(response_data)


@app.route("/api/config", methods=["POST"])
def post_config():
    """
    Updates controller thresholds and parameters.
    Propagates to both the local simulator and physical ESP32 (if connected).
    """
    data = request.get_json(silent=True) or request.form.to_dict()

    if not data:
        return jsonify({"status": "error", "message": "No data provided"}), 400

    # Update mode and reset counters if mode changed
    if "mode" in data:
        mode_val = str(data["mode"]).upper()
        if mode_val in ["BASELINE", "LOADPULSE"]:
            if mode_val != state["active_mode"]:
                state["active_mode"] = mode_val
                # Reset simulation counters when switching modes
                sim_controllers[mode_val].reset_metrics()
                state["sim_step_idx"] = 0

    # Update threshold config
    for key in state["config"].keys():
        if key in data:
            try:
                state["config"][key] = float(data[key])
            except ValueError:
                pass

    # Update local simulation controller instances
    sim_controllers["BASELINE"] = BaselineController(
        p_ess_thresh=state["config"]["baseline_ess"],
        p_opt_thresh=state["config"]["baseline_opt"]
    )
    sim_controllers["LOADPULSE"] = LoadPulseController(
        p_ess_off=state["config"]["lp_ess_off"],
        p_ess_on=state["config"]["lp_ess_on"],
        p_opt_off=state["config"]["lp_opt_off"],
        p_opt_on=state["config"]["lp_opt_on"],
        shortage_delay_sec=state["config"]["shortage_delay_ms"] / 1000.0,
        recovery_delay_sec=state["config"]["recovery_delay_ms"] / 1000.0,
        min_dwell_sec=state["config"]["min_dwell_ms"] / 1000.0
    )

    # Forward to ESP32 if not in simulation mode
    esp_response = None
    if not state["sim_mode"]:
        try:
            esp_url = f"http://{state['esp32_ip']}/api/config"
            resp = requests.post(esp_url, data=data, timeout=2.0)
            esp_response = resp.json() if resp.status_code == 200 else resp.text
        except Exception as e:
            esp_response = f"Could not reach ESP32: {str(e)}"

    return jsonify({
        "status": "ok",
        "active_mode": state["active_mode"],
        "config": state["config"],
        "esp_response": esp_response
    })


@app.route("/api/mode_toggle", methods=["POST"])
def toggle_mode():
    """Quick toggle endpoint between BASELINE and LOADPULSE, resetting counters."""
    new_mode = "BASELINE" if state["active_mode"] == "LOADPULSE" else "LOADPULSE"
    state["active_mode"] = new_mode

    # Reset counters on mode switch
    sim_controllers[new_mode].reset_metrics()
    state["sim_step_idx"] = 0

    if not state["sim_mode"]:
        try:
            requests.post(f"http://{state['esp32_ip']}/api/config", data={"mode": new_mode}, timeout=1.5)
        except Exception:
            pass

    return jsonify({"status": "ok", "mode": new_mode})


@app.route("/api/set_sim_mode", methods=["POST"])
def set_sim_mode():
    """Toggle between physical hardware communication and built-in simulator."""
    data = request.get_json(silent=True) or request.form.to_dict()
    if "sim_mode" in data:
        state["sim_mode"] = str(data["sim_mode"]).lower() in ["true", "1", "yes"]
    if "profile" in data:
        state["active_profile"] = str(data["profile"])
        refresh_sim_samples()
    if "esp32_ip" in data:
        state["esp32_ip"] = str(data["esp32_ip"]).strip()

    return jsonify({
        "status": "ok",
        "sim_mode": state["sim_mode"],
        "active_profile": state["active_profile"],
        "esp32_ip": state["esp32_ip"]
    })


@app.route("/api/compare", methods=["GET"])
def run_comparison_benchmark():
    """
    Runs both Baseline and LoadPulse on a specified profile and returns side-by-side data.
    """
    profile = request.args.get("profile", "flickering_shade")
    duration = float(request.args.get("duration", 60.0))

    b_cfg = {
        "p_ess_thresh": state["config"]["baseline_ess"],
        "p_opt_thresh": state["config"]["baseline_opt"]
    }
    lp_cfg = {
        "p_ess_off": state["config"]["lp_ess_off"],
        "p_ess_on": state["config"]["lp_ess_on"],
        "p_opt_off": state["config"]["lp_opt_off"],
        "p_opt_on": state["config"]["lp_opt_on"],
        "shortage_delay_sec": state["config"]["shortage_delay_ms"] / 1000.0,
        "recovery_delay_sec": state["config"]["recovery_delay_ms"] / 1000.0,
        "min_dwell_sec": state["config"]["min_dwell_ms"] / 1000.0
    }

    comparison = SimulationRunner.run_comparison(
        profile_type=profile,
        duration_sec=duration,
        dt_sec=0.2,
        baseline_cfg=b_cfg,
        loadpulse_cfg=lp_cfg
    )

    return jsonify(comparison)


@app.route("/api/export_csv", methods=["GET"])
def export_csv_benchmark():
    """Exports comparison benchmark data as a downloadable CSV."""
    profile = request.args.get("profile", "flickering_shade")
    duration = float(request.args.get("duration", 60.0))

    b_cfg = {
        "p_ess_thresh": state["config"]["baseline_ess"],
        "p_opt_thresh": state["config"]["baseline_opt"]
    }
    lp_cfg = {
        "p_ess_off": state["config"]["lp_ess_off"],
        "p_ess_on": state["config"]["lp_ess_on"],
        "p_opt_off": state["config"]["lp_opt_off"],
        "p_opt_on": state["config"]["lp_opt_on"],
        "shortage_delay_sec": state["config"]["shortage_delay_ms"] / 1000.0,
        "recovery_delay_sec": state["config"]["recovery_delay_ms"] / 1000.0,
        "min_dwell_sec": state["config"]["min_dwell_ms"] / 1000.0
    }

    comparison = SimulationRunner.run_comparison(
        profile_type=profile,
        duration_sec=duration,
        dt_sec=0.2,
        baseline_cfg=b_cfg,
        loadpulse_cfg=lp_cfg
    )

    csv_data = SimulationRunner.export_csv(comparison)
    filename = f"sunaid_loadpulse_benchmark_{profile}.csv"

    return Response(
        csv_data,
        mimetype="text/csv",
        headers={"Content-disposition": f"attachment; filename={filename}"}
    )


@app.route("/api/reset_metrics", methods=["POST"])
def reset_metrics():
    """Resets counters on simulator or ESP32."""
    sim_controllers["BASELINE"] = BaselineController(
        p_ess_thresh=state["config"]["baseline_ess"],
        p_opt_thresh=state["config"]["baseline_opt"]
    )
    sim_controllers["LOADPULSE"] = LoadPulseController(
        p_ess_off=state["config"]["lp_ess_off"],
        p_ess_on=state["config"]["lp_ess_on"],
        p_opt_off=state["config"]["lp_opt_off"],
        p_opt_on=state["config"]["lp_opt_on"],
        shortage_delay_sec=state["config"]["shortage_delay_ms"] / 1000.0,
        recovery_delay_sec=state["config"]["recovery_delay_ms"] / 1000.0,
        min_dwell_sec=state["config"]["min_dwell_ms"] / 1000.0
    )
    state["sim_step_idx"] = 0

    if not state["sim_mode"]:
        try:
            requests.post(f"http://{state['esp32_ip']}/api/reset_metrics", timeout=1.5)
        except Exception:
            pass

    return jsonify({"status": "ok", "message": "Metrics reset successfully"})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print("\n=======================================================")
    print("   SunAid-LoadPulse Dashboard Server Starting          ")
    print(f"   Access UI at: http://0.0.0.0:{port}                ")
    print("   Simulation Mode: ACTIVE (hardware demo fallback)   ")
    print("=======================================================\n")
    app.run(host="0.0.0.0", port=port, debug=False)
