// 1. Turn on the walkie-talkie receiver
chrome.runtime.onMessage.addListener(function(request, sender, sendResponse) {
    
    // 2. Check the label on the message
    if (request.action === "SAVE_ELEMENT") {
        
        console.log("HQ Preparing to transmit to Python:", request.data);
        
        // 3. The Bridge: Throw the data over the wall to Python
        // Change this single line in background.js:
        fetch("http://localhost:5050/record", {
            method: "POST", // We are 'posting' data, not getting it
            headers: {
                "Content-Type": "application/json" // Tell Python to expect JSON
            },
            body: JSON.stringify(request.data) // Convert the JavaScript object to a raw string
        })
        .then(response => response.json()) // Wait for Python to reply
        .then(reply => console.log("Python Engine replied:", reply))
        .catch(error => console.error("Bridge is down! Is Python running?", error));
        
        // 4. Let the spy know we handled it
        sendResponse({ status: "Success", message: "DNA transmitting to Python..." });
    }
    
    return true; 
});