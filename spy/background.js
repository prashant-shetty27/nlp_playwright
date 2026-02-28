// background.js

// ==========================================
// 1. GLOBAL CONFIGURATION (The Control Panel)
// ==========================================
const CONFIG = {
    // Change this if you ever deploy your Python UI to a remote server
    SERVER_URL: "http://127.0.0.1:8080",    
    // TOGGLE INCOGNITO MODE HERE:
    // true = Clean session (good for public sites)
    // false = Uses normal browser cookies (mandatory for sites requiring Login/2FA)
    USE_INCOGNITO: false, 
    
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
// 3. WINDOW LIFECYCLE MANAGEMENT
// ==========================================
async function startRecordingSession(startUrl) {
    try {
        const newWindow = await chrome.windows.create({
            url: startUrl,
            state: "maximized",
            incognito: CONFIG.USE_INCOGNITO // Now dynamically controlled by your config
        });
        
        // Persist the ID to session storage to survive worker sleep
        await chrome.storage.session.set({ activeRecordingWindowId: newWindow.id });
        console.log(`🔒 Recording locked to Window ID: ${newWindow.id} | Incognito: ${CONFIG.USE_INCOGNITO}`);
        
    } catch (error) { 
        console.error("Architecture Error: Failed to spawn window.", error); 
    }
}

chrome.action.onClicked.addListener(async (tab) => {
    const targetUrl = tab.url && tab.url.startsWith("http") ? tab.url : "https://www.google.com";
    await startRecordingSession(targetUrl);
});

// ==========================================
// 4. THE MESSAGE ROUTER & GATEKEEPER
// ==========================================

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
    console.log("MESSAGE RECEIVED:", message);
    // --- ROUTE 1: GET_LOCATORS (Schema Fetcher) ---
    // This MUST be checked before validating sender.tab, because the popup 
    // might request this data right as the page is initializing.
    if (message.action === "GET_LOCATORS") {
        (async () => {
            try {
                console.log("[API] 🔄 Fetching Database Schema...");
                const res = await fetchWithRetry(`${CONFIG.SERVER_URL}/api/get-database-schema`, { method: 'GET' });
                const data = await res.json();
                sendResponse({ status: "success", data: data });
            } catch(e) {
                console.error("[API] ❌ Failed to fetch schema:", e);
                sendResponse({ status: "error", data: {} });
            }
        })();
        return true; // Keep message channel open for async response
    }

    // Guardrail: Ensure the sender is actually a webpage tab
    if (!sender.tab) {
    console.warn("Message without tab context ignored");
    return;
}

    // --- ROUTE 2: SAVE_ELEMENT (Data Ingestion) ---
    (async () => {
        try {
            // Retrieve resilient state
            const storage = await chrome.storage.session.get("activeRecordingWindowId");
            
            // The Firewall: Ignore clicks happening outside our dedicated recording window
            if (sender.tab.windowId !== storage.activeRecordingWindowId) {
                return sendResponse({ status: "ignored", reason: "outside_isolated_window" });
            }

            if (message.action === "SAVE_ELEMENT") {
                console.log(`[API] 📤 Transmitting element...`);
                const success = await transmitToLocalServer('/api/record-element', message.data);
                sendResponse({ status: success ? "success" : "error" });
            } 
        } catch (error) { 
            console.error("Service Worker Error:", error);
            sendResponse({ status: "error" }); 
        }
    })();
    
    return true; // Keep message channel open for async response
});