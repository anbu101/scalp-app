import time
import pyautogui
import pyperclip
from datetime import datetime
import os

# =========================
# USER INPUT
# =========================
TARGET_TIME = input("Enter time (HH:MM, 24hr): ")

use_saved_prompt = input("Paste prompt automatically? (y/n): ").lower()

PROMPT = ""
if use_saved_prompt == "y":
    print("\nPaste your prompt below and press ENTER twice:\n")
    lines = []
    while True:
        line = input()
        if line == "":
            break
        lines.append(line)
    PROMPT = "\n".join(lines)

# =========================
# CONFIG
# =========================
URL_KEYWORD = "claude"
RETRY_INTERVAL = 15   # seconds
MAX_RETRIES = 40      # ~10 minutes

# =========================

from datetime import datetime, timedelta
import time

def wait_until_target():
    print(f"\n⏳ Waiting until {TARGET_TIME}...\n")

    now = datetime.now()

    # Parse input time
    target = datetime.strptime(TARGET_TIME, "%H:%M").replace(
        year=now.year, month=now.month, day=now.day
    )

    # If target already passed today → schedule for tomorrow
    if target <= now:
        target += timedelta(days=1)

    print("⏰ Scheduled for:", target.strftime("%Y-%m-%d %H:%M:%S"))

    while True:
        now = datetime.now()

        if now >= target:
            break

        remaining = (target - now).seconds
        print(f"⏳ {remaining//60}m {remaining%60}s remaining...", end="\r")

        time.sleep(5)

def focus_claude_tab():
    script = '''
    tell application "Google Chrome"
        activate
        set foundTab to false

        repeat with w in windows
            set i to 1
            repeat with t in tabs of w
                if (URL of t contains "claude.ai") then
                    set active tab index of w to i
                    set index of w to 1
                    set foundTab to true
                    exit repeat
                end if
                set i to i + 1
            end repeat

            if foundTab then exit repeat
        end repeat

        return foundTab
    end tell
    '''

    result = os.popen(f"osascript -e '{script}'").read()
    return "true" in result.lower()

def focus_input_area():
    print("Focusing Claude input...")

    # Step 1: exit address bar
    pyautogui.hotkey('command', 'l')
    time.sleep(0.3)
    pyautogui.press('esc')
    time.sleep(0.5)

    # Step 2: click LOWER center (where Claude input is usually located)
    w, h = pyautogui.size()
    pyautogui.click(w/2, h*0.85)   # <-- changed from 0.8 to 0.85
    time.sleep(1)

    # Step 3: force focus using "/"
    pyautogui.press('/')
    time.sleep(0.5)

    # Step 4: clear the slash
    pyautogui.press('backspace')
    time.sleep(0.3)

def paste_prompt():
    pyperclip.copy(PROMPT)
    pyautogui.hotkey("command", "v")
    time.sleep(0.5)

def send_enter():
    pyautogui.press("enter")

def try_send():
    print("Triggering send via AppleScript file...")

    script_path = "/Users/anbu/dev/scalp-app/frontend/send_claude.scpt"
    result = os.popen(f"osascript {script_path}").read()

    print("JS Result:", result.strip())

def main():
    wait_until_target()

    print("🚀 Trigger time reached\n")

    for attempt in range(MAX_RETRIES):
        print(f"🔁 Attempt {attempt+1}")

        found = focus_claude_tab()

        if not found:
            print("❌ Claude tab not found. Retrying...")
            time.sleep(RETRY_INTERVAL)
            continue

        time.sleep(2)

        try_send()

        print("✅ Enter sent. Waiting to confirm...\n")

        # wait to see if Claude accepts it
        time.sleep(5)

        # try again anyway to ensure reliability
        time.sleep(RETRY_INTERVAL)

    print("\n⚠️ Max retries reached. Stopping.")

# =========================

main()