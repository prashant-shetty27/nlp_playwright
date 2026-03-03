// background.js

// ==========================================
// 1. GLOBAL CONFIGURATION
// ==========================================
const CONFIG = {
    // Python UI server address
    SERVER_URL: "http://127.0.0.1:8080",

    // NETWORK RELIABILITY
    MAX_RETRIES: 3,
    RETRY_DELAY_MS: 500
};

// ==========================================
// 2. RESILIENT NETWORK ENGINE (Retry Logic)
// ==========================================
async function fetchWithRetry(url, options, retriesLeft = CONFIG.MAX_RETRIES) {
    try {
        const response = await fetch(url, options);
        if (!response.ok) {
            throw new Error(`Server rejected payload. Status: ${response.status}`);
        }
        return response;
    } catch (error) {
        if (retriesLeft > 0) {
            console.warn(`[API] ⚠️ Connection failed. Retrying... (${retriesLeft} attempts left)`);
            
            // Wait for the specified delay before trying again
            await new Promise(resolve => setTimeout(resolve, CONFIG.RETRY_DELAY_MS));
            return fetchWithRetry(url, options, retriesLeft - 1);
        } else {
            console.error(`[API] ❌ Fatal Error: Exhausted all ${CONFIG.MAX_RETRIES} retries.`, error);
            throw error;
        }
    }
}

async function transmitToLocalServer(endpoint, payload) {
    try {
        await fetchWithRetry(`${CONFIG.SERVER_URL}${endpoint}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        return true;
    } catch (error) {
        return false;
    }
}

// ==========================================
// 3. SIDE PANEL — Open recorder on icon click
// ==========================================
chrome.action.onClicked.addListener(async (tab) => {
    try {
        await chrome.sidePanel.open({ tabId: tab.id });
        console.log(`🔬 Side panel opened for tab ${tab.id}`);
    } catch (error) {
        console.error("Failed to open side panel:", error);
    }
});

// Make sure the side panel is enabled for all pages after install / update
chrome.runtime.onInstalled.addListener(() => {
    chrome.sidePanel.setOptions({
        enabled: true,
        path: "sidepanel.html"
    });
    console.log("🔬 ML Codeless Spy installed. Click the toolbar icon to open the recorder.");
});

// ==========================================
// 4. THE MESSAGE ROUTER
// ==========================================
chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
    console.log("MESSAGE RECEIVED:", message);

    // --- ROUTE 1: GET_LOCATORS (Schema Fetcher) ---
    // Must be checked before the sender.tab guard — popup may call this during init.
    if (message.action === "GET_LOCATORS") {
        (async () => {
            try {
                console.log("[API] 🔄 Fetching Database Schema...");
                const res = await fetchWithRetry(
                    `${CONFIG.SERVER_URL}/api/get-database-schema`, { method: 'GET' }
                );
                const data = await res.json();
                sendResponse({ status: "success", data: data });
            } catch (e) {
                console.error("[API] ❌ Failed to fetch schema:", e);
                sendResponse({ status: "error", data: {} });
            }
        })();
        return true; // Keep channel open for async response
    }

    // Guardrail: Ensure the sender is actually a webpage tab
    if (!sender.tab) {
        console.warn("Message without tab context ignored");
        return;
    }

    // --- ROUTE 2: SAVE_ELEMENT (Data Ingestion) ---
    // No window isolation — Alt+Click on ANY tab is forwarded to the recorder.
    if (message.action === "SAVE_ELEMENT") {
        (async () => {
            try {
                console.log(`[API] 📤 Transmitting element from tab ${sender.tab.id} (${sender.tab.url})...`);
                const success = await transmitToLocalServer('/api/record-element', message.data);
                sendResponse({ status: success ? "success" : "error" });
            } catch (error) {
                console.error("Service Worker Error:", error);
                sendResponse({ status: "error" });
            }
        })();
        return true; // Keep channel open for async response
    }
});