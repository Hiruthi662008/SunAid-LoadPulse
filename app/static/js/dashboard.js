/**
 * SunAid-LoadPulse Dashboard Logic
 * Real-time Chart.js telemetry, interactive controls, and side-by-side comparison benchmarks.
 */

// Global Chart Instances
let livePowerChart = null;
let relayTimelineChart = null;
let compSwitchChart = null;
let compUptimeChart = null;

// Telemetry Rolling Buffers (max 40 points)
const MAX_POINTS = 40;
const chartData = {
  labels: [],
  power: [],
  voltage: [],
  essentialRelay: [],
  optionalRelay: []
};

// Current local configuration copy
let currentConfig = {
  baseline_ess: 100,
  baseline_opt: 200,
  lp_ess_off: 90,
  lp_ess_on: 120,
  lp_opt_off: 180,
  lp_opt_on: 210,
  shortage_delay_ms: 3000,
  recovery_delay_ms: 5000,
  min_dwell_ms: 2000
};

// Polling interval handle
let pollTimer = null;

document.addEventListener("DOMContentLoaded", () => {
  initTabs();
  initCharts();
  initSliders();
  initEventListeners();
  startTelemetryPolling();

  // Run initial benchmark in background for Comparison Tab
  runBenchmark("flickering_shade");
});

// =========================================================================
// 1. TAB NAVIGATION
// =========================================================================
function initTabs() {
  const tabBtns = document.querySelectorAll(".tab-btn");
  tabBtns.forEach(btn => {
    btn.addEventListener("click", () => {
      tabBtns.forEach(b => b.classList.remove("active"));
      document.querySelectorAll(".tab-content").forEach(c => c.classList.remove("active"));

      btn.classList.add("active");
      const targetId = btn.getAttribute("data-tab");
      const targetContent = document.getElementById(targetId);
      if (targetContent) {
        targetContent.classList.add("active");
      }

      // Trigger resize for Chart.js in newly revealed tab
      if (targetId === "tab-comparison" && compSwitchChart) {
        compSwitchChart.resize();
        compUptimeChart.resize();
      }
    });
  });
}

// =========================================================================
// 2. CHART INITIALIZATION
// =========================================================================
function initCharts() {
  // Chart.js global dark theme defaults
  Chart.defaults.color = "#94a3b8";
  Chart.defaults.font.family = "'Inter', sans-serif";
  Chart.defaults.scale.grid.color = "rgba(51, 65, 85, 0.4)";

  // 1. Live Power & Voltage Chart
  const ctxPower = document.getElementById("livePowerChart").getContext("2d");
  livePowerChart = new Chart(ctxPower, {
    type: "line",
    data: {
      labels: chartData.labels,
      datasets: [
        {
          label: "Solar Power (mW)",
          data: chartData.power,
          borderColor: "#f59e0b",
          backgroundColor: "rgba(245, 158, 11, 0.12)",
          borderWidth: 2.5,
          tension: 0.25,
          fill: true,
          yAxisID: "yPower",
          pointRadius: 0
        },
        {
          label: "Bus Voltage (V)",
          data: chartData.voltage,
          borderColor: "#38bdf8",
          backgroundColor: "transparent",
          borderWidth: 1.8,
          borderDash: [4, 4],
          tension: 0.25,
          yAxisID: "yVoltage",
          pointRadius: 0
        }
      ]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: false,
      interaction: {
        mode: "index",
        intersect: false
      },
      scales: {
        x: {
          display: true,
          title: { display: true, text: "Time (seconds)" },
          ticks: { maxTicksLimit: 8 }
        },
        yPower: {
          type: "linear",
          position: "left",
          title: { display: true, text: "Power (mW)", color: "#f59e0b" },
          min: 0,
          suggestedMax: 350
        },
        yVoltage: {
          type: "linear",
          position: "right",
          title: { display: true, text: "Voltage (V)", color: "#38bdf8" },
          min: 0,
          suggestedMax: 7,
          grid: { drawOnChartArea: false }
        }
      },
      plugins: {
        legend: { display: false }
      }
    }
  });

  // 2. Relay Switching Timeline Chart (Stepped ON/OFF states)
  const ctxRelay = document.getElementById("relayTimelineChart").getContext("2d");
  relayTimelineChart = new Chart(ctxRelay, {
    type: "line",
    data: {
      labels: chartData.labels,
      datasets: [
        {
          label: "Essential Relay (P1)",
          data: chartData.essentialRelay,
          borderColor: "#10b981",
          backgroundColor: "rgba(16, 185, 129, 0.15)",
          borderWidth: 2,
          stepped: true,
          fill: true,
          pointRadius: 0
        },
        {
          label: "Optional Relay (P2)",
          data: chartData.optionalRelay,
          borderColor: "#38bdf8",
          backgroundColor: "rgba(56, 189, 248, 0.1)",
          borderWidth: 2,
          stepped: true,
          fill: true,
          pointRadius: 0
        }
      ]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: false,
      scales: {
        x: {
          display: true,
          ticks: { maxTicksLimit: 8 }
        },
        y: {
          min: -0.1,
          max: 1.1,
          ticks: {
            stepSize: 1,
            callback: value => (value === 1 ? "ON" : value === 0 ? "OFF" : "")
          }
        }
      },
      plugins: {
        legend: { display: false }
      }
    }
  });
}

// =========================================================================
// 3. REAL-TIME TELEMETRY POLLING
// =========================================================================
function startTelemetryPolling() {
  if (pollTimer) clearInterval(pollTimer);

  pollTimer = setInterval(async () => {
    try {
      const resp = await fetch("/api/status");
      if (!resp.ok) return;
      const data = await resp.json();
      updateDashboardUI(data);
    } catch (err) {
      console.warn("Telemetry fetch error:", err);
    }
  }, 400); // 400ms poll interval
}

function updateDashboardUI(data) {
  // Alert Banner for ESP32 connection error
  const alertEl = document.getElementById("statusAlert");
  if (data.error) {
    alertEl.textContent = data.error;
    alertEl.classList.remove("hidden");
  } else {
    alertEl.classList.add("hidden");
  }

  // Electrical metrics
  document.getElementById("valPower").textContent = data.power_mw ? data.power_mw.toFixed(1) : "0.0";
  document.getElementById("valVoltage").textContent = `${data.voltage_v ? data.voltage_v.toFixed(2) : "0.00"} V`;
  document.getElementById("valCurrent").textContent = `${data.current_ma ? data.current_ma.toFixed(1) : "0.0"} mA`;

  // Uptime & Switches
  document.getElementById("valUptimePct").textContent = data.uptime_essential_pct ? data.uptime_essential_pct.toFixed(1) : "0.0";
  document.getElementById("valSwitchTotal").textContent = data.switch_count_total || 0;
  document.getElementById("valSwitchEss").textContent = data.switch_count_essential || 0;

  // Active Mode Badge
  const isLP = data.mode === "LOADPULSE";
  const modeBtn = document.getElementById("btnModeToggle");
  const navModeText = document.getElementById("navModeText");
  const valModeTag = document.getElementById("valModeTag");

  valModeTag.textContent = data.mode || "LOADPULSE";

  if (isLP) {
    modeBtn.className = "mode-btn mode-loadpulse";
    navModeText.textContent = "LOADPULSE ACTIVE";
  } else {
    modeBtn.className = "mode-btn mode-baseline";
    navModeText.textContent = "BASELINE ACTIVE";
  }

  // IP / Source Indicator
  const displayIp = document.getElementById("displayIp");
  if (data.sim_mode) {
    displayIp.textContent = `Simulator (${data.profile || "active"})`;
  } else {
    displayIp.textContent = `Hardware (${data.source || "ESP32"})`;
  }

  // Relay LED Lights and Status Text
  const lightEss = document.getElementById("lightEssential");
  const textEss = document.getElementById("statusEssential");
  if (data.relay_essential) {
    lightEss.className = "relay-light light-on-essential";
    textEss.textContent = "ACTIVE (ON)";
    textEss.className = "metric-value status-text text-emerald";
  } else {
    lightEss.className = "relay-light light-off";
    textEss.textContent = "SHED (OFF)";
    textEss.className = "metric-value status-text text-red";
  }

  const lightOpt = document.getElementById("lightOptional");
  const textOpt = document.getElementById("statusOptional");
  if (data.relay_optional) {
    lightOpt.className = "relay-light light-on-optional";
    textOpt.textContent = "ACTIVE (ON)";
    textOpt.className = "metric-value status-text text-sky";
  } else {
    lightOpt.className = "relay-light light-off";
    textOpt.textContent = "SHED (OFF)";
    textOpt.className = "metric-value status-text text-muted";
  }

  // Update Chart Rolling Buffers
  const nowLabel = (data.uptime_seconds !== undefined)
    ? `${data.uptime_seconds.toFixed(1)}s`
    : new Date().toLocaleTimeString().split(" ")[0];

  chartData.labels.push(nowLabel);
  chartData.power.push(data.power_mw);
  chartData.voltage.push(data.voltage_v);
  chartData.essentialRelay.push(data.relay_essential ? 1 : 0);
  chartData.optionalRelay.push(data.relay_optional ? 1 : 0);

  if (chartData.labels.length > MAX_POINTS) {
    chartData.labels.shift();
    chartData.power.shift();
    chartData.voltage.shift();
    chartData.essentialRelay.shift();
    chartData.optionalRelay.shift();
  }

  if (livePowerChart) livePowerChart.update("none");
  if (relayTimelineChart) relayTimelineChart.update("none");
}

// =========================================================================
// 4. SLIDERS & CONTROLS
// =========================================================================
function initSliders() {
  const sliderIds = [
    "lp_opt_off",
    "lp_opt_on",
    "lp_ess_off",
    "lp_ess_on",
    "shortage_delay_ms",
    "recovery_delay_ms",
    "min_dwell_ms"
  ];

  sliderIds.forEach(id => {
    const slider = document.getElementById(id);
    const label = document.getElementById(`val_${id}`);
    if (slider && label) {
      slider.addEventListener("input", () => {
        label.textContent = slider.value;
      });
    }
  });
}

function initEventListeners() {
  // Mode Button in Header
  document.getElementById("btnModeToggle").addEventListener("click", async () => {
    try {
      const resp = await fetch("/api/mode_toggle", { method: "POST" });
      const data = await resp.json();
      // Clear rolling chart buffer on mode switch so metrics start fresh for the new mode
      chartData.labels = [];
      chartData.power = [];
      chartData.voltage = [];
      chartData.essentialRelay = [];
      chartData.optionalRelay = [];
      console.log("Mode toggled to:", data.mode);
    } catch (e) {
      console.error(e);
    }
  });

  // Simulation Mode Toggle Checkbox
  const simToggle = document.getElementById("simModeToggle");
  simToggle.addEventListener("change", async () => {
    try {
      await fetch("/api/set_sim_mode", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ sim_mode: simToggle.checked })
      });
    } catch (e) {
      console.error(e);
    }
  });

  // Profile Selector Buttons
  const profileBtns = document.querySelectorAll(".profile-btn");
  profileBtns.forEach(btn => {
    btn.addEventListener("click", async () => {
      profileBtns.forEach(b => b.classList.remove("active"));
      btn.classList.add("active");
      const profile = btn.getAttribute("data-profile");

      await fetch("/api/set_sim_mode", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ sim_mode: true, profile: profile })
      });

      // Synchronize comparison dropdown too
      const compSelect = document.getElementById("selectCompareProfile");
      if (compSelect) {
        compSelect.value = profile;
        runBenchmark(profile);
      }
    });
  });

  // Save Config Button
  document.getElementById("btnSaveConfig").addEventListener("click", async () => {
    const selectedMode = document.querySelector('input[name="controller_mode"]:checked').value;
    const payload = {
      mode: selectedMode,
      lp_opt_off: parseFloat(document.getElementById("lp_opt_off").value),
      lp_opt_on: parseFloat(document.getElementById("lp_opt_on").value),
      lp_ess_off: parseFloat(document.getElementById("lp_ess_off").value),
      lp_ess_on: parseFloat(document.getElementById("lp_ess_on").value),
      shortage_delay_ms: parseInt(document.getElementById("shortage_delay_ms").value),
      recovery_delay_ms: parseInt(document.getElementById("recovery_delay_ms").value),
      min_dwell_ms: parseInt(document.getElementById("min_dwell_ms").value)
    };

    try {
      const resp = await fetch("/api/config", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload)
      });
      const res = await resp.json();
      alert("Configuration successfully applied!");
    } catch (e) {
      alert("Error applying configuration: " + e.message);
    }
  });

  // Reset Metrics Button
  document.getElementById("btnResetMetrics").addEventListener("click", async () => {
    try {
      await fetch("/api/reset_metrics", { method: "POST" });
      chartData.labels = [];
      chartData.power = [];
      chartData.voltage = [];
      chartData.essentialRelay = [];
      chartData.optionalRelay = [];
      alert("Metrics counters and charts reset.");
    } catch (e) {
      console.error(e);
    }
  });

  // Set IP Button
  document.getElementById("btnSaveIp").addEventListener("click", async () => {
    const ip = document.getElementById("inputEspIp").value.trim();
    if (!ip) return;
    try {
      await fetch("/api/set_sim_mode", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ esp32_ip: ip, sim_mode: false })
      });
      document.getElementById("simModeToggle").checked = false;
      alert(`Hardware IP set to: ${ip}. Simulation Mode turned OFF.`);
    } catch (e) {
      alert("Error saving IP: " + e.message);
    }
  });

  // Comparison Tab: Run Benchmark Button
  document.getElementById("btnRunBenchmark").addEventListener("click", () => {
    const profile = document.getElementById("selectCompareProfile").value;
    runBenchmark(profile);
  });

  // Comparison Profile Select Change
  document.getElementById("selectCompareProfile").addEventListener("change", (e) => {
    const profile = e.target.value;
    document.getElementById("btnExportCsv").href = `/api/export_csv?profile=${profile}`;
    runBenchmark(profile);
  });
}

// =========================================================================
// 5. COMPARISON BENCHMARK ENGINE & CHARTS
// =========================================================================
async function runBenchmark(profile = "flickering_shade") {
  try {
    const btn = document.getElementById("btnRunBenchmark");
    if (btn) btn.disabled = true;

    const resp = await fetch(`/api/compare?profile=${profile}&duration=60`);
    if (!resp.ok) return;
    const data = await resp.json();

    // Update Summary KPI Cards
    document.getElementById("compBaselineSwitches").textContent = data.baseline.total_switches;
    document.getElementById("compLoadPulseSwitches").textContent = data.loadpulse.total_switches;
    document.getElementById("compReductionPct").textContent = `${data.reduction_pct}%`;
    document.getElementById("compUptimePct").textContent = `${data.loadpulse.uptime_essential_pct}%`;

    // Render Side-by-Side Charts
    renderComparisonCharts(data);

    if (btn) btn.disabled = false;
  } catch (err) {
    console.error("Benchmark error:", err);
  }
}

function renderComparisonCharts(data) {
  // 1. Switch Count Comparison Bar Chart
  const ctxSwitch = document.getElementById("compSwitchChart").getContext("2d");
  if (compSwitchChart) compSwitchChart.destroy();

  compSwitchChart = new Chart(ctxSwitch, {
    type: "bar",
    data: {
      labels: ["Total Switches", "Essential Load Switches", "Optional Load Switches"],
      datasets: [
        {
          label: "Baseline (Single Threshold)",
          data: [
            data.baseline.total_switches,
            data.baseline.switch_count_essential,
            data.baseline.switch_count_optional
          ],
          backgroundColor: "rgba(239, 68, 68, 0.75)",
          borderColor: "#ef4444",
          borderWidth: 1.5,
          borderRadius: 4
        },
        {
          label: "SunAid-LoadPulse",
          data: [
            data.loadpulse.total_switches,
            data.loadpulse.switch_count_essential,
            data.loadpulse.switch_count_optional
          ],
          backgroundColor: "rgba(16, 185, 129, 0.75)",
          borderColor: "#10b981",
          borderWidth: 1.5,
          borderRadius: 4
        }
      ]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      scales: {
        y: {
          beginAtZero: true,
          title: { display: true, text: "Switch Events (Lower is Better)" }
        }
      },
      plugins: {
        legend: { position: "top" }
      }
    }
  });

  // 2. Uptime % Comparison Bar Chart
  const ctxUptime = document.getElementById("compUptimeChart").getContext("2d");
  if (compUptimeChart) compUptimeChart.destroy();

  compUptimeChart = new Chart(ctxUptime, {
    type: "bar",
    data: {
      labels: ["Essential Load Uptime %", "Optional Load Uptime %"],
      datasets: [
        {
          label: "Baseline (Single Threshold)",
          data: [
            data.baseline.uptime_essential_pct,
            data.baseline.uptime_optional_pct
          ],
          backgroundColor: "rgba(148, 163, 184, 0.5)",
          borderColor: "#94a3b8",
          borderWidth: 1.5,
          borderRadius: 4
        },
        {
          label: "SunAid-LoadPulse",
          data: [
            data.loadpulse.uptime_essential_pct,
            data.loadpulse.uptime_optional_pct
          ],
          backgroundColor: "rgba(56, 189, 248, 0.75)",
          borderColor: "#38bdf8",
          borderWidth: 1.5,
          borderRadius: 4
        }
      ]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      scales: {
        y: {
          beginAtZero: true,
          max: 100,
          title: { display: true, text: "Uptime % (Higher is Better)" }
        }
      },
      plugins: {
        legend: { position: "top" }
      }
    }
  });
}
