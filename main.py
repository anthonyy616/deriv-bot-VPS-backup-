import uvicorn
import os
import sys
import time
import logging
import traceback

# 1. Setup Crash Logging
# This saves errors to a file on your C drive so you can debug "invisible" crashes.
LOG_PATH = r"C:\bot_crash_log.txt"
logging.basicConfig(
    filename=LOG_PATH, 
    level=logging.ERROR,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

# 2. Import the app
# Ensure 'api' folder is in the same directory as this script or Python path is set correctly.
from api.server import app

if __name__ == "__main__":
    # Ensure the root directory is in the python path
    sys.path.append(os.path.dirname(os.path.abspath(__file__)))

    print("🚀 Starting Bot Server...")
    print("-----------------------------------")
    print("ENTRY POINT: api/server.py")
    print("ENGINE:      Polling Mode (Active)")
    print("URL:         http://45.144.242.97:800") 
    print("LOGGING:     " + LOG_PATH)
    print("-----------------------------------")

    # 3. The "Invincible" Loop
    while True:
        try:
            # UPDATED: host="0.0.0.0" opens it to the web
            # UPDATED: port=800 is the port we opened in the firewall
            uvicorn.run(app, host="0.0.0.0", port=800)
            
            # If uvicorn exits cleanly (rare), we break the loop to avoid spamming restarts
            print("Server stopped cleanly.")
            break

        except KeyboardInterrupt:
            # Allows you to stop it manually with Ctrl+C if testing in terminal
            print("\n🛑 Shutting down (User Interrupt)...")
            break
            
        except Exception as e:
            # 4. Crash Handling
            # If the code breaks, it lands here instead of killing the process.
            error_msg = traceback.format_exc()
            print(f"\n⚠️ CRASH DETECTED. Restarting in 5s... \nError: {e}")
            
            # Write full error to file
            logging.error(f"Server Crashed:\n{error_msg}")
            
            # Wait 5 seconds before restarting to prevent CPU 100% loop
            time.sleep(5)
            print("♻️ Restarting now...")