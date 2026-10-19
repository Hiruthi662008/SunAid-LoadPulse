"""
Test suite for SunAid-LoadPulse controller logic against the simulator.
Validates:
1. LoadPulse drastically reduces relay switching events on flickering shade profiles.
2. LoadPulse preserves essential load uptime during intermittent dips.
3. Priority ordering: Optional load is shed first, Essential load is preserved.
4. Hysteresis prevents rapid cycling near threshold boundaries.
5. Minimum dwell time is strictly observed.
"""

import unittest
import sys
import os

# Add parent directory to path so app.simulator can be imported
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.simulator import (
    BaselineController,
    LoadPulseController,
    SolarProfileGenerator,
    SimulationRunner
)


class TestSunAidControllers(unittest.TestCase):

    def test_flickering_shade_switch_reduction(self):
        """
        Primary Hackathon Claim Verification:
        Under high-frequency flickering shade (e.g. tree branches / cloud edges),
        Baseline controller chatters excessively (>20 switches),
        while LoadPulse controller suppresses micro-dips and achieves
        a massive (>80%) reduction in relay switches.
        """
        res = SimulationRunner.run_comparison(
            profile_type="flickering_shade",
            duration_sec=60.0,
            dt_sec=0.2
        )

        baseline_switches = res["baseline"]["total_switches"]
        loadpulse_switches = res["loadpulse"]["total_switches"]
        reduction_pct = res["reduction_pct"]

        print(f"\n[Test] Flickering Profile - Baseline Switches: {baseline_switches}, "
              f"LoadPulse Switches: {loadpulse_switches}, Reduction: {reduction_pct}%")

        self.assertGreater(baseline_switches, 20, "Baseline should experience relay chattering")
        self.assertLessEqual(loadpulse_switches, 5, "LoadPulse should suppress spurious switching")
        self.assertGreaterEqual(reduction_pct, 80.0, "Chatter reduction must exceed 80%")
        self.assertEqual(
            res["loadpulse"]["uptime_essential_pct"], 100.0,
            "LoadPulse essential uptime must be 100% since power never dropped below critical 90 mW"
        )

    def test_priority_load_shedding_order(self):
        """
        Verify Priority Logic:
        When power drops from abundant to intermediate to low,
        Optional load must be shed first, while Essential load remains ON.
        """
        controller = LoadPulseController(
            p_ess_off=90.0,
            p_ess_on=120.0,
            p_opt_off=180.0,
            p_opt_on=210.0,
            shortage_delay_sec=1.0,
            recovery_delay_sec=1.0,
            min_dwell_sec=0.5
        )

        dt = 0.2
        # 1. Warm up at high power (300 mW) for 2 seconds to engage both loads
        for _ in range(10):
            controller.step(300.0, dt)
        self.assertTrue(controller.relay_essential, "Essential should be ON at 300 mW")
        self.assertTrue(controller.relay_optional, "Optional should be ON at 300 mW")

        # 2. Power drops to 140 mW (below opt_off 180 mW, but well above ess_off 90 mW)
        # For 1.6 seconds (exceeding 1.0s shortage delay)
        for _ in range(8):
            controller.step(140.0, dt)

        self.assertTrue(controller.relay_essential, "Essential MUST remain ON during moderate power dip")
        self.assertFalse(controller.relay_optional, "Optional MUST be shed during moderate power dip")

        # 3. Power drops to 40 mW (below ess_off 90 mW) for 1.6 seconds
        for _ in range(8):
            controller.step(40.0, dt)

        self.assertFalse(controller.relay_essential, "Essential must be shed when power is critically low")
        self.assertFalse(controller.relay_optional, "Optional remains OFF")

    def test_restoration_order(self):
        """
        Verify Restoration Order:
        When power recovers, Essential load must be restored before Optional load.
        """
        controller = LoadPulseController(
            p_ess_off=90.0,
            p_ess_on=120.0,
            p_opt_off=180.0,
            p_opt_on=210.0,
            shortage_delay_sec=1.0,
            recovery_delay_sec=1.0,
            min_dwell_sec=0.5
        )
        dt = 0.2

        # Start cold (0 mW)
        for _ in range(5):
            controller.step(0.0, dt)
        self.assertFalse(controller.relay_essential)
        self.assertFalse(controller.relay_optional)

        # Power rises to 150 mW (above ess_on 120 mW, but below opt_on 210 mW)
        for _ in range(8):
            controller.step(150.0, dt)

        self.assertTrue(controller.relay_essential, "Essential should restore when power exceeds ess_on")
        self.assertFalse(controller.relay_optional, "Optional must NOT restore until power exceeds opt_on")

        # Power rises to 250 mW (above opt_on 210 mW)
        for _ in range(8):
            controller.step(250.0, dt)

        self.assertTrue(controller.relay_essential, "Essential remains ON")
        self.assertTrue(controller.relay_optional, "Optional should now restore")

    def test_shortage_delay_ignores_brief_spurious_dips(self):
        """
        Verify Shortage Confirmation Delay:
        A dip lasting less than shortage_delay_sec (e.g. 1.0s) should NOT shed the load.
        """
        controller = LoadPulseController(
            p_ess_off=90.0,
            p_ess_on=120.0,
            p_opt_off=180.0,
            p_opt_on=210.0,
            shortage_delay_sec=2.0,
            recovery_delay_sec=1.0,
            min_dwell_sec=0.5
        )
        dt = 0.2

        # Turn both on
        for _ in range(10):
            controller.step(300.0, dt)
        self.assertTrue(controller.relay_optional)

        # Brief dip for 0.8s (4 steps of 0.2s < 2.0s shortage delay)
        for _ in range(4):
            controller.step(50.0, dt)

        self.assertTrue(controller.relay_optional, "Brief 0.8s dip must not shed load before confirmation")
        self.assertTrue(controller.relay_essential, "Essential must remain unaffected by brief dip")

        # Power bounces back to 300 mW
        for _ in range(5):
            controller.step(300.0, dt)

        self.assertTrue(controller.relay_optional, "Optional remained steadily ON through brief transient")

    def test_dwell_time_enforcement(self):
        """
        Verify Minimum Dwell Time:
        Relay cannot change state faster than min_dwell_sec even if thresholds are triggered.
        """
        min_dwell = 1.0  # 1.0 second dwell time
        controller = LoadPulseController(
            p_ess_off=90.0,
            p_ess_on=120.0,
            p_opt_off=180.0,
            p_opt_on=210.0,
            shortage_delay_sec=0.1,
            recovery_delay_sec=0.1,
            min_dwell_sec=min_dwell
        )
        dt = 0.1

        # Turn on
        for _ in range(3):
            controller.step(300.0, dt)
        self.assertTrue(controller.relay_essential)

        # Immediate crash to 0 mW for 0.4s (< min_dwell of 1.0s)
        for _ in range(4):
            controller.step(0.0, dt)

        # Even though shortage delay was 0.1s, dwell time protects relay from immediate state flip
        self.assertTrue(controller.relay_essential, "Relay dwell time must prevent premature state change")

    def test_counter_reset_on_mode_switch(self):
        """
        Verify Counter Reset on Mode Switch:
        When switching modes or resetting metrics, switch counters and uptime tracking
        must return to clean 0 values.
        """
        controller = LoadPulseController()
        dt = 0.2

        # Run several steps to accumulate switches and uptime
        for _ in range(10):
            controller.step(300.0, dt)
        for _ in range(25):
            controller.step(50.0, dt)

        metrics_before = controller.get_metrics()
        self.assertGreater(metrics_before["total_runtime_sec"], 0)
        self.assertGreater(metrics_before["total_switches"], 0)

        # Trigger reset
        controller.reset_metrics()
        metrics_after = controller.get_metrics()

        self.assertEqual(metrics_after["total_switches"], 0, "Switches must be reset to 0")
        self.assertEqual(metrics_after["switch_count_essential"], 0)
        self.assertEqual(metrics_after["switch_count_optional"], 0)
        self.assertEqual(metrics_after["total_runtime_sec"], 0.0, "Runtime must reset to 0.0s")
        self.assertEqual(metrics_after["essential_uptime_sec"], 0.0)

    def test_sustained_shortage_causes_real_downtime(self):
        """
        Verify Genuine Shortage Detection (No Threshold Cheating):
        Under Passing Clouds with a deep 10s overcast dip below 90 mW,
        LoadPulse MUST shed the essential load and record real downtime (< 100% uptime).
        """
        res = SimulationRunner.run_comparison(
            profile_type="passing_clouds",
            duration_sec=60.0,
            dt_sec=0.2
        )

        lp_uptime = res["loadpulse"]["uptime_essential_pct"]
        print(f"[Test] Passing Clouds - LoadPulse Essential Uptime: {lp_uptime}% (Real Shortage Present)")

        # Uptime should be below 95% due to the genuine cloud shortage and 5s stability recovery
        self.assertLess(lp_uptime, 95.0, "Essential load must record genuine downtime during severe cloud cover")
        self.assertGreater(lp_uptime, 75.0, "Essential load must recover once cloud clears")


if __name__ == "__main__":
    unittest.main()
