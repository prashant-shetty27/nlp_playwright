// ==========================================
// 1. THE SPY LISTENER (Main Execution)
// ==========================================
document.addEventListener('click', function(event) {
    // Stop the website from hijacking our click
    event.preventDefault();
    event.stopPropagation();

    let target = event.target;

    // --- INTERACTIVE NAMING MODULE ---
    let pageName = sessionStorage.getItem('spy_page_name');
    if (!pageName) {
        pageName = prompt("Enter a Page Name for this screen (e.g., 'home_page', 'login_page'):");
        if (!pageName) return; // User cancelled
        pageName = pageName.trim().toLowerCase().replace(/[^a-z0-9]/g, '_');
        sessionStorage.setItem('spy_page_name', pageName);
    }

    let smartGuess = target.innerText ? target.innerText.substring(0, 15) : (target.id || target.name || target.tagName);
    smartGuess = smartGuess.trim().toLowerCase().replace(/[^a-z0-9]/g, '_');

    let locatorName = prompt(`Enter a name for this ${target.tagName} element:`, smartGuess);
    if (!locatorName) return; // User cancelled
    locatorName = locatorName.trim().toLowerCase().replace(/[^a-z0-9]/g, '_');
    // ---------------------------------

    // --- DOM EXTRACTION MODULE ---
    // Utilizing our helper functions for clean architecture
    const elementDNA = {
        userPageName: pageName,
        userLocatorName: locatorName,
        tagName: target.tagName.toLowerCase(),
        className: target.className || null,
        innerText: target.innerText ? target.innerText.trim() : null,
        rect: getSpatialCoordinates(target),
        attributes: extractAllAttributes(target)
    };

    // Transmit to Python Backend
    console.log(`🕵️ Spy captured: ${pageName} -> ${locatorName}`);
    chrome.runtime.sendMessage({ action: "SAVE_ELEMENT", data: elementDNA });

}, true); 


// ==========================================
// 2. EXTRACTION HELPERS
// ==========================================

function getSpatialCoordinates(element) {
    const rect = element.getBoundingClientRect();
    return {
        x: Math.round(rect.x),
        y: Math.round(rect.y),
        width: Math.round(rect.width),
        height: Math.round(rect.height)
    };
}

function extractAllAttributes(element) {
    let attributesData = {};
    for (let i = 0; i < element.attributes.length; i++) {
        let attr = element.attributes[i];
        
        // Architectural Guardrail: Ignore massive base64 image strings or inline styles
        if (attr.value && attr.value.length < 300) {
            attributesData[attr.name] = attr.value;
        }
    }
    return attributesData;
}