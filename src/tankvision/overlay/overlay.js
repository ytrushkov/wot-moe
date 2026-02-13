/**
 * WoT Console Overlay - WebSocket client.
 *
 * Connects to the WoT Console Overlay app's WebSocket server and updates the DOM
 * with live MoE data.
 */

const WS_PORT = new URLSearchParams(window.location.search).get("ws_port") || "5174";
const WS_URL = `ws://localhost:${WS_PORT}`;

const RECONNECT_INTERVAL_MS = 3000;

// DOM elements
const overlay = document.getElementById("overlay");
const tankNameEl = document.getElementById("tank-name");
const moePercentEl = document.getElementById("moe-percent");
const progressFill = document.getElementById("progress-fill");
const progressTarget = document.getElementById("progress-target");
const deltaEl = document.getElementById("delta");
const damageEl = document.getElementById("damage");

let ws = null;

function connect() {
    ws = new WebSocket(WS_URL);

    ws.onopen = () => {
        console.log("Connected to WoT Console Overlay");
    };

    ws.onmessage = (event) => {
        try {
            const data = JSON.parse(event.data);
            updateOverlay(data);
        } catch (e) {
            console.error("Failed to parse message:", e);
        }
    };

    ws.onclose = () => {
        console.log("Disconnected, reconnecting...");
        setTimeout(connect, RECONNECT_INTERVAL_MS);
    };

    ws.onerror = (err) => {
        console.error("WebSocket error:", err);
        ws.close();
    };
}

function updateOverlay(data) {
    // Tank name
    tankNameEl.textContent = data.tank_name || "Waiting for Battle...";

    // MoE percentage display
    if (data.in_battle) {
        moePercentEl.textContent = data.projected_moe_percent.toFixed(2) + "%";
    } else if (data.moe_percent > 0) {
        moePercentEl.textContent = data.moe_percent.toFixed(2) + "%";
    } else {
        moePercentEl.textContent = "--.--%";
    }

    // Progress bar
    const pct = data.in_battle ? data.projected_moe_percent : data.moe_percent;
    progressFill.style.width = Math.min(100, Math.max(0, pct)) + "%";

    // Progress bar color
    progressFill.classList.remove("positive", "negative");
    if (data.delta > 0) {
        progressFill.classList.add("positive");
    } else if (data.delta < 0) {
        progressFill.classList.add("negative");
    }

    // Delta indicator
    const sign = data.delta >= 0 ? "+" : "";
    deltaEl.textContent = sign + data.delta.toFixed(2) + "%";
    deltaEl.classList.remove("positive", "negative", "neutral");
    if (data.delta > 0) {
        deltaEl.classList.add("positive");
    } else if (data.delta < 0) {
        deltaEl.classList.add("negative");
    } else {
        deltaEl.classList.add("neutral");
    }

    // Damage counter
    damageEl.textContent = data.combined_damage.toLocaleString();

    // Status class
    overlay.classList.remove("idle");
    if (data.status === "idle") {
        overlay.classList.add("idle");
    }
}

// Apply configuration from URL params
function applyConfig() {
    const params = new URLSearchParams(window.location.search);

    const layout = params.get("layout");
    if (layout === "compact") {
        overlay.classList.remove("standard");
        overlay.classList.add("compact");
    }

    if (params.get("colorblind") === "true") {
        overlay.classList.add("colorblind");
    }

    const opacity = params.get("opacity");
    if (opacity) {
        overlay.style.opacity = parseFloat(opacity);
    }

    const scale = params.get("scale");
    if (scale) {
        overlay.style.transform = `scale(${parseFloat(scale)})`;
        overlay.style.transformOrigin = "top left";
    }
}

applyConfig();
connect();
