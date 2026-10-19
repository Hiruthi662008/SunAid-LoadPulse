"""
SunAid-LoadPulse Simulator
Simulates realistic solar irradiance profiles and evaluates both Baseline (single-threshold)
and LoadPulse (fluctuation-aware, hysteresis + confirmation delays + dwell time) controllers.
"""

import math
import random
import csv
import io
from typing import Dict, List, Any, Tuple


class SolarProfileGenerator:
    """Generates realistic solar power (mW) profiles over time."""

    @staticmethod
    def generate_profile(
        profile_type: str = "flickering_shade",
        duration_sec: float = 60.0,
        dt_sec: float = 0.2,
        seed: int = 42
    ) -> List[Dict[str, float]]:
        """
        Generates a sequence of solar telemetry samples at dt_sec intervals.
        Each sample contains:
          - time_sec: elapsed time in seconds
          - power_mw: available solar power in mW (scaled for 6V 1-3W panel + 100 ohm load)
          - voltage_v: estimated solar bus voltage (3.0V - 6.5V)
          - current_ma: estimated current through load (mA)
          - ldr_raw: simulated 12-bit ADC reading (0-4095) proportional to ambient lux
        """
        rng = random.Random(seed)
        num_steps = int(duration_sec / dt_sec)
        samples = []

        for step in range(num_steps):
            t = step * dt_sec

            if profile_type == "clear_sky":
                # Smooth diurnal bell-curve with slight natural thermal/air jitter (+/- 4 mW)
                # Max power ~340 mW, nominal ~280 mW
                base_power = 270.0 + 60.0 * math.sin(math.pi * (t / duration_sec))
                noise = rng.uniform(-4.0, 4.0)
                power = max(10.0, base_power + noise)

            elif profile_type == "passing_clouds":
                # Clear sun interrupted by two passing cloud intervals
                # Cloud 1: t in [15, 28]s -> power dips to ~150 mW (below optional threshold)
                # Cloud 2: t in [36, 46]s -> deep overcast dip to ~60 mW (below essential threshold)
                if 15.0 <= t <= 28.0:
                    cloud_depth = 130.0 * math.sin(math.pi * (t - 15.0) / 13.0)
                    base_power = 280.0 - cloud_depth
                elif 36.0 <= t <= 46.0:
                    cloud_depth = 220.0 * math.sin(math.pi * (t - 36.0) / 10.0)
                    base_power = 280.0 - cloud_depth
                else:
                    base_power = 280.0
                noise = rng.uniform(-5.0, 5.0)
                power = max(15.0, base_power + noise)

            elif profile_type == "flickering_shade":
                # Wind blowing tree leaves/branches, causing high-frequency power oscillation
                # precisely hovering around the 180-210 mW optional threshold window.
                # Fast fluctuations occurring every 0.4s to 1.5s
                fast_wave = 45.0 * math.sin(2.0 * math.pi * t / 2.2)
                medium_wave = 25.0 * math.cos(2.0 * math.pi * t / 4.7)
                noise = rng.uniform(-18.0, 18.0)
                # Center around 195 mW (straddling the optional OFF/ON thresholds)
                power = max(30.0, 195.0 + fast_wave + medium_wave + noise)

            elif profile_type == "sunset_ramp_down":
                # Gradual decrease from 320 mW down to 20 mW over the duration
                decay = 1.0 - (t / duration_sec)
                base_power = 20.0 + 300.0 * max(0.0, decay)
                noise = rng.uniform(-4.0, 4.0)
                power = max(5.0, base_power + noise)

            else:
                power = 200.0 + rng.uniform(-10.0, 10.0)

            # Derive electrical values based on 6V panel & 100 ohm resistor test load
            # P = V * I; with test load R=100 ohm: V = sqrt(P * R), I = V / R
            # clamped within realistic open-circuit / loaded operating range
            calc_v = math.sqrt(max(0.1, power * 0.1))  # power in mW * 100 ohm / 1000 = power * 0.1
            calc_v = min(6.5, max(1.5, calc_v))
            calc_i = (calc_v / 100.0) * 1000.0  # mA

            # Simulated LDR reading (GPIO34 12-bit ADC: 0-4095)
            ldr = min(4095, int((power / 350.0) * 3600 + rng.uniform(-50, 50)))
            ldr = max(100, ldr)

            samples.append({
                "time_sec": round(t, 2),
                "power_mw": round(power, 1),
                "voltage_v": round(calc_v, 2),
                "current_ma": round(calc_i, 1),
                "ldr_raw": ldr
            })

        return samples


class BaselineController:
    """
    Controller A: Baseline Single-Threshold Controller
    Instantly switches loads without delays, hysteresis, or dwell time protection.
    - If power >= p_opt_thresh: Essential ON, Optional ON
    - Else if power >= p_ess_thresh: Essential ON, Optional OFF
    - Else: Essential OFF, Optional OFF
    """

    def __init__(self, p_ess_thresh: float = 100.0, p_opt_thresh: float = 200.0):
        self.p_ess_thresh = p_ess_thresh
        self.p_opt_thresh = p_opt_thresh

        self.is_first_step = True
        self.relay_essential = False
        self.relay_optional = False

        self.switch_count_essential = 0
        self.switch_count_optional = 0
        self.total_switches = 0

        self.essential_uptime_sec = 0.0
        self.optional_uptime_sec = 0.0
        self.total_runtime_sec = 0.0

    def reset_metrics(self):
        """Resets all switch counters and uptime tracking."""
        self.switch_count_essential = 0
        self.switch_count_optional = 0
        self.total_switches = 0
        self.essential_uptime_sec = 0.0
        self.optional_uptime_sec = 0.0
        self.total_runtime_sec = 0.0
        self.is_first_step = True

    def step(self, power_mw: float, dt_sec: float) -> Tuple[bool, bool]:
        target_ess = power_mw >= self.p_ess_thresh
        target_opt = power_mw >= self.p_opt_thresh

        # On the initial step, establish the base state corresponding to available power
        # without treating power-on as a dynamic relay switch event.
        if self.is_first_step:
            self.is_first_step = False
            self.relay_essential = target_ess
            self.relay_optional = target_opt
            self.total_runtime_sec += dt_sec
            if self.relay_essential:
                self.essential_uptime_sec += dt_sec
            if self.relay_optional:
                self.optional_uptime_sec += dt_sec
            return self.relay_essential, self.relay_optional

        # Count switch transitions
        if target_ess != self.relay_essential:
            self.switch_count_essential += 1
            self.total_switches += 1
            self.relay_essential = target_ess

        if target_opt != self.relay_optional:
            self.switch_count_optional += 1
            self.total_switches += 1
            self.relay_optional = target_opt

        # Accumulate uptimes
        self.total_runtime_sec += dt_sec
        if self.relay_essential:
            self.essential_uptime_sec += dt_sec
        if self.relay_optional:
            self.optional_uptime_sec += dt_sec

        return self.relay_essential, self.relay_optional

    def get_metrics(self) -> Dict[str, Any]:
        uptime_ess_pct = (
            (self.essential_uptime_sec / self.total_runtime_sec * 100.0)
            if self.total_runtime_sec > 0 else 0.0
        )
        uptime_opt_pct = (
            (self.optional_uptime_sec / self.total_runtime_sec * 100.0)
            if self.total_runtime_sec > 0 else 0.0
        )
        return {
            "mode": "BASELINE",
            "total_switches": self.total_switches,
            "switch_count_essential": self.switch_count_essential,
            "switch_count_optional": self.switch_count_optional,
            "essential_uptime_sec": round(self.essential_uptime_sec, 2),
            "optional_uptime_sec": round(self.optional_uptime_sec, 2),
            "total_runtime_sec": round(self.total_runtime_sec, 2),
            "uptime_essential_pct": round(uptime_ess_pct, 1),
            "uptime_optional_pct": round(uptime_opt_pct, 1),
            "relay_essential": self.relay_essential,
            "relay_optional": self.relay_optional
        }


class LoadPulseController:
    """
    Controller B: SunAid-LoadPulse Smart Controller
    Features:
      1. Priority Load Logic: Optional load (priority 2) shed first, Essential (priority 1) preserved.
      2. Hysteresis Thresholds: Separate OFF threshold (shedding) and ON threshold (restoration).
      3. Shortage Confirmation Delay: Sustained dip required before shedding (ignores micro-dips).
      4. Recovery Stability Delay: Sustained recovery required before reconnecting load.
      5. Minimum Relay Dwell Time: Enforces minimum duration before a relay can switch state again.
    """

    def __init__(
        self,
        p_ess_off: float = 90.0,
        p_ess_on: float = 120.0,
        p_opt_off: float = 180.0,
        p_opt_on: float = 210.0,
        shortage_delay_sec: float = 3.0,
        recovery_delay_sec: float = 5.0,
        min_dwell_sec: float = 2.0
    ):
        self.p_ess_off = p_ess_off
        self.p_ess_on = p_ess_on
        self.p_opt_off = p_opt_off
        self.p_opt_on = p_opt_on

        self.shortage_delay_sec = shortage_delay_sec
        self.recovery_delay_sec = recovery_delay_sec
        self.min_dwell_sec = min_dwell_sec

        self.is_first_step = True
        self.relay_essential = False
        self.relay_optional = False

        # Timers (in seconds)
        self.time_below_ess_off = 0.0
        self.time_above_ess_on = 0.0
        self.time_below_opt_off = 0.0
        self.time_above_opt_on = 0.0

        # Time elapsed since last relay switch
        self.time_since_ess_switch = min_dwell_sec
        self.time_since_opt_switch = min_dwell_sec

        self.switch_count_essential = 0
        self.switch_count_optional = 0
        self.total_switches = 0

        self.essential_uptime_sec = 0.0
        self.optional_uptime_sec = 0.0
        self.total_runtime_sec = 0.0

    def reset_metrics(self):
        """Resets all switch counters, timing accumulators, and uptime tracking."""
        self.switch_count_essential = 0
        self.switch_count_optional = 0
        self.total_switches = 0
        self.essential_uptime_sec = 0.0
        self.optional_uptime_sec = 0.0
        self.total_runtime_sec = 0.0
        self.time_below_ess_off = 0.0
        self.time_above_ess_on = 0.0
        self.time_below_opt_off = 0.0
        self.time_above_opt_on = 0.0
        self.is_first_step = True

    def step(self, power_mw: float, dt_sec: float) -> Tuple[bool, bool]:
        # On the initial step (power-on), establish appropriate load states based on
        # available solar power without imposing recovery stability delay.
        # Recovery stability delays apply when recovering from a shortage, not on cold boot with good power.
        if self.is_first_step:
            self.is_first_step = False
            if power_mw >= self.p_ess_on:
                self.relay_essential = True
                self.time_since_ess_switch = 0.0
            if self.relay_essential and power_mw >= self.p_opt_on:
                self.relay_optional = True
                self.time_since_opt_switch = 0.0

            self.total_runtime_sec += dt_sec
            if self.relay_essential:
                self.essential_uptime_sec += dt_sec
            if self.relay_optional:
                self.optional_uptime_sec += dt_sec
            return self.relay_essential, self.relay_optional

        self.time_since_ess_switch += dt_sec
        self.time_since_opt_switch += dt_sec
        self.total_runtime_sec += dt_sec

        # ----------------- 1. ESSENTIAL LOAD LOGIC -----------------
        if self.relay_essential:
            # Check for sustained shortage below p_ess_off
            if power_mw < self.p_ess_off:
                self.time_below_ess_off += dt_sec
                if (
                    self.time_below_ess_off >= self.shortage_delay_sec
                    and self.time_since_ess_switch >= self.min_dwell_sec
                ):
                    self.relay_essential = False
                    self.switch_count_essential += 1
                    self.total_switches += 1
                    self.time_since_ess_switch = 0.0
                    self.time_below_ess_off = 0.0
            else:
                self.time_below_ess_off = 0.0
        else:
            # Check for sustained recovery above p_ess_on
            if power_mw >= self.p_ess_on:
                self.time_above_ess_on += dt_sec
                if (
                    self.time_above_ess_on >= self.recovery_delay_sec
                    and self.time_since_ess_switch >= self.min_dwell_sec
                ):
                    self.relay_essential = True
                    self.switch_count_essential += 1
                    self.total_switches += 1
                    self.time_since_ess_switch = 0.0
                    self.time_above_ess_on = 0.0
            else:
                self.time_above_ess_on = 0.0

        # ----------------- 2. OPTIONAL LOAD LOGIC -----------------
        # Priority rule: Optional load can ONLY turn ON if Essential is already ON
        if self.relay_optional:
            # Shed immediately if essential was shed, or after sustained shortage below p_opt_off
            if not self.relay_essential:
                if self.time_since_opt_switch >= self.min_dwell_sec:
                    self.relay_optional = False
                    self.switch_count_optional += 1
                    self.total_switches += 1
                    self.time_since_opt_switch = 0.0
                    self.time_below_opt_off = 0.0
            elif power_mw < self.p_opt_off:
                self.time_below_opt_off += dt_sec
                if (
                    self.time_below_opt_off >= self.shortage_delay_sec
                    and self.time_since_opt_switch >= self.min_dwell_sec
                ):
                    self.relay_optional = False
                    self.switch_count_optional += 1
                    self.total_switches += 1
                    self.time_since_opt_switch = 0.0
                    self.time_below_opt_off = 0.0
            else:
                self.time_below_opt_off = 0.0
        else:
            # Restore optional only if essential is active and power has remained above p_opt_on
            if self.relay_essential and power_mw >= self.p_opt_on:
                self.time_above_opt_on += dt_sec
                if (
                    self.time_above_opt_on >= self.recovery_delay_sec
                    and self.time_since_opt_switch >= self.min_dwell_sec
                ):
                    self.relay_optional = True
                    self.switch_count_optional += 1
                    self.total_switches += 1
                    self.time_since_opt_switch = 0.0
                    self.time_above_opt_on = 0.0
            else:
                self.time_above_opt_on = 0.0

        # Accumulate uptimes
        if self.relay_essential:
            self.essential_uptime_sec += dt_sec
        if self.relay_optional:
            self.optional_uptime_sec += dt_sec

        return self.relay_essential, self.relay_optional

    def get_metrics(self) -> Dict[str, Any]:
        uptime_ess_pct = (
            (self.essential_uptime_sec / self.total_runtime_sec * 100.0)
            if self.total_runtime_sec > 0 else 0.0
        )
        uptime_opt_pct = (
            (self.optional_uptime_sec / self.total_runtime_sec * 100.0)
            if self.total_runtime_sec > 0 else 0.0
        )
        return {
            "mode": "LOADPULSE",
            "total_switches": self.total_switches,
            "switch_count_essential": self.switch_count_essential,
            "switch_count_optional": self.switch_count_optional,
            "essential_uptime_sec": round(self.essential_uptime_sec, 2),
            "optional_uptime_sec": round(self.optional_uptime_sec, 2),
            "total_runtime_sec": round(self.total_runtime_sec, 2),
            "uptime_essential_pct": round(uptime_ess_pct, 1),
            "uptime_optional_pct": round(uptime_opt_pct, 1),
            "relay_essential": self.relay_essential,
            "relay_optional": self.relay_optional
        }


class SimulationRunner:
    """Executes comparative simulations across both controllers and formats outputs."""

    @staticmethod
    def run_comparison(
        profile_type: str = "flickering_shade",
        duration_sec: float = 60.0,
        dt_sec: float = 0.2,
        baseline_cfg: Dict[str, float] = None,
        loadpulse_cfg: Dict[str, float] = None
    ) -> Dict[str, Any]:
        """Runs both controllers side-by-side on identical solar data."""
        samples = SolarProfileGenerator.generate_profile(
            profile_type=profile_type,
            duration_sec=duration_sec,
            dt_sec=dt_sec
        )

        b_cfg = baseline_cfg or {"p_ess_thresh": 100.0, "p_opt_thresh": 200.0}
        lp_cfg = loadpulse_cfg or {
            "p_ess_off": 90.0,
            "p_ess_on": 120.0,
            "p_opt_off": 180.0,
            "p_opt_on": 210.0,
            "shortage_delay_sec": 3.0,
            "recovery_delay_sec": 5.0,
            "min_dwell_sec": 2.0
        }

        baseline = BaselineController(**b_cfg)
        loadpulse = LoadPulseController(**lp_cfg)

        timeline = []

        for sample in samples:
            t = sample["time_sec"]
            p = sample["power_mw"]
            v = sample["voltage_v"]
            i = sample["current_ma"]
            ldr = sample["ldr_raw"]

            b_ess, b_opt = baseline.step(p, dt_sec)
            lp_ess, lp_opt = loadpulse.step(p, dt_sec)

            timeline.append({
                "time_sec": t,
                "power_mw": p,
                "voltage_v": v,
                "current_ma": i,
                "ldr_raw": ldr,
                "baseline_essential": b_ess,
                "baseline_optional": b_opt,
                "loadpulse_essential": lp_ess,
                "loadpulse_optional": lp_opt
            })

        b_metrics = baseline.get_metrics()
        lp_metrics = loadpulse.get_metrics()

        # Calculate chattering suppression percentage
        reduction_pct = 0.0
        if b_metrics["total_switches"] > 0:
            saved = b_metrics["total_switches"] - lp_metrics["total_switches"]
            reduction_pct = round((saved / b_metrics["total_switches"]) * 100.0, 1)

        return {
            "profile_type": profile_type,
            "duration_sec": duration_sec,
            "samples_count": len(samples),
            "baseline": b_metrics,
            "loadpulse": lp_metrics,
            "reduction_pct": reduction_pct,
            "timeline": timeline
        }

    @staticmethod
    def export_csv(comparison_data: Dict[str, Any]) -> str:
        """Exports simulation timeline and metrics to CSV format string."""
        output = io.StringIO()
        writer = csv.writer(output)

        writer.writerow(["# SunAid-LoadPulse Simulation Export"])
        writer.writerow(["# Profile", comparison_data["profile_type"]])
        writer.writerow(["# Duration (s)", comparison_data["duration_sec"]])
        writer.writerow(["# Baseline Total Switches", comparison_data["baseline"]["total_switches"]])
        writer.writerow(["# LoadPulse Total Switches", comparison_data["loadpulse"]["total_switches"]])
        writer.writerow(["# Chatter Reduction %", f"{comparison_data['reduction_pct']}%"])
        writer.writerow([])

        # Header row
        writer.writerow([
            "time_sec",
            "power_mw",
            "voltage_v",
            "current_ma",
            "ldr_raw",
            "baseline_essential",
            "baseline_optional",
            "loadpulse_essential",
            "loadpulse_optional"
        ])

        for row in comparison_data["timeline"]:
            writer.writerow([
                row["time_sec"],
                row["power_mw"],
                row["voltage_v"],
                row["current_ma"],
                row["ldr_raw"],
                1 if row["baseline_essential"] else 0,
                1 if row["baseline_optional"] else 0,
                1 if row["loadpulse_essential"] else 0,
                1 if row["loadpulse_optional"] else 0
            ])

        return output.getvalue()


if __name__ == "__main__":
    # Quick sanity check run
    for p in ["clear_sky", "passing_clouds", "flickering_shade", "sunset_ramp_down"]:
        res = SimulationRunner.run_comparison(profile_type=p, duration_sec=60.0)
        print(
            f"Profile: {p:<18} | Baseline Switches: {res['baseline']['total_switches']:<3} | "
            f"LoadPulse Switches: {res['loadpulse']['total_switches']:<3} | "
            f"Reduction: {res['reduction_pct']}% | "
            f"LP Ess Uptime: {res['loadpulse']['uptime_essential_pct']}%"
        )
