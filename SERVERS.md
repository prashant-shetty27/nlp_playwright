# Server & Recorder Quick Reference

## 1. Appium Server (iOS / Android device control)

| Item | Value |
|------|-------|
| Port | **4723** |
| URL  | http://localhost:4723 |
| WDA port (iOS) | **8100** |

### Start
```bash
appium --log data/logs/appium.log --log-timestamp &
```

### Verify
```bash
curl http://localhost:4723/status
```

### Kill stuck WDA port
```bash
lsof -ti:8100 | xargs kill -9
```

---

## 2. Recorder UI (NiceGUI web app)

| Item | Value |
|------|-------|
| Port | **8080** |
| URL  | http://localhost:8080 |
| Script | `ui_builder.py` |

### Start
```bash
cd /Users/prashantshetty/nlp_playwright
python ui_builder.py
```

> The UI auto-clears any stale process on port 8080 at startup.

---

## 3. Spy Server (Chrome extension backend)

| Item | Value |
|------|-------|
| Port | **5050** |
| Script | `spy/server.py` |

### Start
```bash
cd /Users/prashantshetty/nlp_playwright
python spy/server.py
```

---

## 4. Chrome Extension (ML Codeless Spy)

**Load path:** `spy/chrome_extension/`

1. Open Chrome → `chrome://extensions`
2. Enable **Developer mode** (top-right toggle)
3. Click **Load unpacked** → select `spy/chrome_extension/`
4. Click the extension icon → opens side panel recorder
5. **Alt+Click** any element on a page to record it

> The side panel embeds `http://localhost:8080` (ui_builder must be running).

---

## 5. Run a Flow

```bash
# NLP flow
python runner.py flows/ios_demo.flow

# Codeless JSON plan (iOS)
python3 plan_runner.py plans/ios_plan.json

# Appium runner directly
python runner_appium.py
```

---

## Device: iPhone 16 Plus

| Item | Value |
|------|-------|
| UDID | `00008140-0006295A0AEB001C` |
| iOS | 26.2 |
| WDA team | `53NR34M72B` |
| WDA bundle | `com.prashantshetty.integrationapp` |
| `useNewWDA` | `false` (reuse existing WDA) |

---

## Typical startup order

```
1. appium &                    # port 4723
2. python ui_builder.py        # port 8080  (recorder UI)
3. python spy/server.py        # port 5050  (spy backend, if using Chrome ext)
4. Load chrome_extension       # in Chrome (once)
5. python3 plan_runner.py ...  # run your flow
```
