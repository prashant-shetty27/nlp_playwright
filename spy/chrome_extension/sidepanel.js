const navInput = document.getElementById('nav-input');
const navGo    = document.getElementById('nav-go');
const frame    = document.getElementById('recorder-frame');
const dot      = document.getElementById('status-dot');
const label    = document.getElementById('status-label');
const retryBtn = document.getElementById('retry-btn');

// ── Get the active tab in THIS window (reliable for side panels) ─────────────
function getActiveTab(cb) {
  chrome.windows.getCurrent({ populate: true }, (win) => {
    const tab = win && win.tabs ? win.tabs.find(t => t.active) : null;
    cb(tab || null);
  });
}

// ── Seed URL bar with the current tab URL ─────────────────────────────────────
getActiveTab((tab) => {
  if (tab && tab.url && !tab.url.startsWith('chrome')) {
    navInput.value = tab.url;
  }
});

// ── Navigate the main tab ─────────────────────────────────────────────────────
function notifyServerUrl(url) {
  fetch('http://127.0.0.1:8080/api/set-start-url', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ url })
  }).catch(() => {});  // fire-and-forget
}

function navigateTo() {
  let url = navInput.value.trim();
  if (!url) return;
  if (!/^https?:\/\//i.test(url)) url = 'https://' + url;
  navInput.value = url;
  notifyServerUrl(url);
  getActiveTab((tab) => {
    if (tab) {
      chrome.tabs.update(tab.id, { url }, () => {
        if (chrome.runtime.lastError) {
          console.error('Navigate error:', chrome.runtime.lastError.message);
        }
      });
    } else {
      chrome.tabs.create({ url });  // fallback: open new tab
    }
  });
}

navGo.addEventListener('click', navigateTo);
navInput.addEventListener('keydown', (e) => { if (e.key === 'Enter') navigateTo(); });

// Keep URL bar in sync as the user browses
chrome.tabs.onUpdated.addListener((tabId, changeInfo, tab) => {
  if (changeInfo.url && tab.active && !changeInfo.url.startsWith('chrome')) {
    navInput.value = changeInfo.url;
  }
});
chrome.tabs.onActivated.addListener(() => {
  getActiveTab((tab) => {
    if (tab && tab.url && !tab.url.startsWith('chrome')) navInput.value = tab.url;
  });
});

// ── Server status check (3s timeout — never gets stuck on "checking…") ────────
function setStatus(online) {
  dot.className = online ? 'connected' : '';
  label.textContent = online ? 'localhost:8080' : 'offline — run: python ui_builder.py';
}

async function checkServer() {
  const ctrl = new AbortController();
  const t = setTimeout(() => ctrl.abort(), 3000);
  try {
    await fetch('http://127.0.0.1:8080', { method: 'HEAD', cache: 'no-store', signal: ctrl.signal });
    clearTimeout(t);
    setStatus(true);
  } catch {
    clearTimeout(t);
    setStatus(false);
  }
}

retryBtn.addEventListener('click', () => {
  frame.src = 'http://localhost:8080';
  checkServer();
});

checkServer();
setInterval(checkServer, 5000);
