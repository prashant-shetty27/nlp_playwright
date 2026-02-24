// content.js

// ==========================================
// 1. STATE MANAGEMENT & CACHING
// ==========================================
let activePageName = ""; 
let dbSchema = null; 
let isFetchingSchema = false;
let lastClickTime = 0; 

chrome.storage.session.get(["savedPageName"]).then((result) => {
    if (result.savedPageName) {
        activePageName = result.savedPageName;
    }
});

// ==========================================
// 2. NETWORK LAYER (Memoized Schema Fetcher)
// ==========================================
function ensureSchemaAndShowModal(smartLocator, elementDNA, forceRefresh = false) {
    if (forceRefresh) {
        console.log("🔄 ML Spy: Forced schema cache refresh.");
        dbSchema = null;
    }

    if (dbSchema !== null) {
        return showShadowModal(smartLocator, elementDNA);
    }

    if (isFetchingSchema) return; 
    isFetchingSchema = true;

    chrome.runtime.sendMessage({ action: "GET_LOCATORS" }, (response) => {
        isFetchingSchema = false;
        if (response && response.status === "success") {
            dbSchema = response.data; 
        } else {
            console.warn("⚠️ ML Spy: Backend unreachable. Defaulting to manual entry.");
            dbSchema = {}; 
        }
        showShadowModal(smartLocator, elementDNA);
    });
}

// ==========================================
// 3. THE TRIGGER (Debounced Option + Click)
// ==========================================
document.addEventListener('click', function(event) {
    if (!event.altKey) return; 
    
    // ARCHITECTURAL UPGRADE: Snappier 250ms debounce
    const now = Date.now();
    if (now - lastClickTime < 250) return;
    lastClickTime = now;

    event.preventDefault();
    event.stopPropagation();

    let target = event.target;
    let rawGuess = target.innerText ? target.innerText.substring(0, 15) : (target.id || target.name || target.tagName);
    let smartLocatorName = rawGuess.trim().toLowerCase().replace(/[^a-z0-9]/g, '_') || `element_${Math.floor(Math.random() * 1000)}`;

    const elementDNA = {
        tagName: target.tagName.toLowerCase(),
        className: target.className || null,
        innerText: target.innerText ? target.innerText.trim() : null,
        rect: getSpatialCoordinates(target),
        attributes: extractAllAttributes(target)
    };

    // ARCHITECTURAL UPGRADE: If user holds Shift, force cache purge
    const forceRefresh = event.shiftKey;
    ensureSchemaAndShowModal(smartLocatorName, elementDNA, forceRefresh);

}, true); 


// ==========================================
// 4. THE SHADOW DOM MODAL (Leak-Free & Animated)
// ==========================================
function showShadowModal(smartLocator, elementDNA) {
    let existingHost = document.getElementById('ml-spy-host');
    if (existingHost) existingHost.remove();

    const host = document.createElement('div');
    host.id = 'ml-spy-host';
    host.style.position = 'fixed';
    host.style.top = '20px';
    host.style.right = '20px';
    host.style.zIndex = '2147483647';
    document.body.appendChild(host);

    // ARCHITECTURAL UPGRADE: mode 'open' allows Chrome DevTools inspection
    const shadow = host.attachShadow({ mode: 'open' });

    shadow.innerHTML = `
        <style>
            :host { all: initial; } 
            
            /* ARCHITECTURAL UPGRADE: Professional Polish Animation */
            @keyframes slideDown {
                0% { opacity: 0; transform: translateY(-10px); }
                100% { opacity: 1; transform: translateY(0); }
            }

            .spy-container {
                width: 340px; background: #ffffff; border: 2px solid #2563eb; 
                border-radius: 8px; box-shadow: 0 25px 50px -12px rgba(0,0,0,0.5); 
                font-family: system-ui, -apple-system, sans-serif; color: #000;
                display: flex; flex-direction: column;
                animation: slideDown 0.2s ease-out forwards;
            }
            .spy-header {
                font-size: 16px; font-weight: bold; color: #1e3a8a; 
                padding: 12px 16px; border-bottom: 1px solid #e5e7eb;
                cursor: grab; background: #f8fafc; border-radius: 6px 6px 0 0;
                display: flex; justify-content: space-between; align-items: center;
                user-select: none;
            }
            .spy-header:active { cursor: grabbing; }
            .spy-body { padding: 16px; }
            label { display: block; font-size: 12px; font-weight: bold; color: #374151; margin-bottom: 4px; }
            input { 
                width: 100%; box-sizing: border-box; padding: 8px; 
                border: 1px solid #d1d5db; border-radius: 4px; 
                margin-bottom: 15px; font-size: 14px;
            }
            input:focus { outline: 2px solid #3b82f6; border-color: transparent; }
            .btn-row { display: flex; justify-content: space-between; margin-top: 5px; }
            button { padding: 8px 16px; border: none; border-radius: 4px; cursor: pointer; font-weight: bold; transition: opacity 0.2s;}
            button:hover { opacity: 0.9; }
            .btn-cancel { background: #ef4444; color: #fff; }
            .btn-save { background: #22c55e; color: #fff; }
            .close-icon { cursor: pointer; color: #9ca3af; font-size: 18px; }
            .close-icon:hover { color: #ef4444; }
        </style>

        <div class="spy-container" id="spy-window">
            <div class="spy-header" id="spy-drag-handle">
                <span>🕵️ ML Spy</span>
                <span class="close-icon" id="spy-close-icon">✖</span>
            </div>
            
            <div class="spy-body">
                <label>1. Page Name (Context)</label>
                <input id="spy-page-input" list="spy-page-list" placeholder="Select or type a new page..." value="${activePageName}">
                <datalist id="spy-page-list"></datalist>

                <label>2. Locator Name</label>
                <input id="spy-locator-input" list="spy-locator-list" value="${smartLocator}">
                <datalist id="spy-locator-list"></datalist>

                <div class="btn-row">
                    <button class="btn-cancel" id="spy-cancel-btn">Cancel</button>
                    <button class="btn-save" id="spy-save-btn">Save & Update</button>
                </div>
            </div>
        </div>
    `;

    const pageInput = shadow.getElementById('spy-page-input');
    const pageList = shadow.getElementById('spy-page-list');
    const locatorInput = shadow.getElementById('spy-locator-input');
    const locatorList = shadow.getElementById('spy-locator-list');

    Object.keys(dbSchema).forEach(page => {
        const opt = document.createElement('option');
        opt.value = page;
        pageList.appendChild(opt);
    });

    const updateLocatorsDropdown = (pageVal) => {
        locatorList.innerHTML = '';
        const normalizedPage = pageVal.trim().toLowerCase().replace(/[^a-z0-9]/g, '_');
        
        const targetSchema = dbSchema[normalizedPage] || dbSchema[pageVal];
        if (targetSchema) {
            targetSchema.forEach(loc => {
                const opt = document.createElement('option');
                opt.value = loc;
                locatorList.appendChild(opt);
            });
        }
    };

    updateLocatorsDropdown(pageInput.value);
    pageInput.addEventListener('input', (e) => updateLocatorsDropdown(e.target.value));

    // --- ARCHITECTURAL UPGRADE: Leak-Free Drag Logic ---
    const dragHandle = shadow.getElementById('spy-drag-handle');
    let isDragging = false, offsetX, offsetY;

    const onMouseMove = (e) => {
        if (!isDragging) return;
        host.style.right = 'auto'; 
        host.style.left = (e.clientX - offsetX) + 'px';
        host.style.top = (e.clientY - offsetY) + 'px';
    };

    const onMouseUp = () => isDragging = false;

    dragHandle.addEventListener('mousedown', (e) => {
        isDragging = true;
        const rect = host.getBoundingClientRect();
        offsetX = e.clientX - rect.left;
        offsetY = e.clientY - rect.top;
    });

    // Explicitly binding variables so we can destroy them later
    document.addEventListener('mousemove', onMouseMove);
    document.addEventListener('mouseup', onMouseUp);

    // --- DESTRUCTOR LOGIC ---
    const escListener = (e) => {
        if (e.key === 'Escape') closeModal();
    };
    document.addEventListener('keydown', escListener);

    const closeModal = () => {
        host.remove();
        // The most critical lines in this script: Stopping memory leaks
        document.removeEventListener('keydown', escListener);
        document.removeEventListener('mousemove', onMouseMove);
        document.removeEventListener('mouseup', onMouseUp);
    };

    shadow.getElementById('spy-close-icon').addEventListener('click', closeModal);
    shadow.getElementById('spy-cancel-btn').addEventListener('click', closeModal);

    shadow.getElementById('spy-save-btn').addEventListener('click', () => {
        let finalPage = pageInput.value.trim();
        let finalLocator = locatorInput.value.trim() || smartLocator;

        if (!finalPage) {
            alert("Page Name cannot be empty.");
            return pageInput.focus();
        }

        activePageName = finalPage;
        chrome.storage.session.set({ savedPageName: activePageName });

        elementDNA.userPageName = finalPage;
        elementDNA.userLocatorName = finalLocator;

        chrome.runtime.sendMessage({ action: "SAVE_ELEMENT", data: elementDNA }).catch(() => {});
        closeModal(); // This now cleanly destroys the UI and all global listeners
    });

    setTimeout(() => locatorInput.focus(), 50);
}

// ==========================================
// 5. EXTRACTION HELPERS
// ==========================================
function getSpatialCoordinates(element) {
    const rect = element.getBoundingClientRect();
    return { x: Math.round(rect.x), y: Math.round(rect.y), width: Math.round(rect.width), height: Math.round(rect.height) };
}

function extractAllAttributes(element) {
    let attributesData = {};
    for (let i = 0; i < element.attributes.length; i++) {
        let attr = element.attributes[i];
        if (attr.value && attr.value.length < 300) attributesData[attr.name] = attr.value;
    }
    return attributesData;
}