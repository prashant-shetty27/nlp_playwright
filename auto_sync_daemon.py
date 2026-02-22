import time
import subprocess
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

class AutoSyncHandler(FileSystemEventHandler):
    def on_modified(self, event):
        # We only care if you modify your actions or your locators.
        # We ignore changes to logs, videos, or unrelated files to save CPU.
        if event.src_path.endswith("actions.py") or event.src_path.endswith(".json"):
            print(f"\n[*] OS Event Detected: Modified {event.src_path}")
            self.trigger_sync()

    def trigger_sync(self):
        print("[*] Rebuilding snippets architecture automatically...")
        # subprocess safely executes your existing script without crashing this daemon
        result = subprocess.run(["python3", "sync_snippets.py"], capture_output=True, text=True)
        
        if result.returncode == 0:
            print("[+] Sync successful. Step files updated.")
        else:
            print(f"[-] Sync failed. Check sync_snippets.py logic:\n{result.stderr}")

def start_daemon(path_to_watch: str):
    event_handler = AutoSyncHandler()
    observer = Observer()
    # recursive=False means we only watch the root project directory
    observer.schedule(event_handler, path_to_watch, recursive=False)
    observer.start()
    
    print(f"[*] Auto-Sync Daemon active watching: {path_to_watch}")
    print("[*] Waiting for file changes... (Press Ctrl+C to stop)")
    
    try:
        while True:
            time.sleep(1) # Keep the main thread alive without burning CPU
    except KeyboardInterrupt:
        observer.stop()
        print("\n[*] Auto-Sync Daemon terminated.")
    
    observer.join()

if __name__ == "__main__":
    # '.' means watch the current directory where this script is running
    start_daemon('.')