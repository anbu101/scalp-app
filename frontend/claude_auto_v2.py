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

def wait_until_target():
    print(f"\n⏳ Waiting until {TARGET_TIME}...\n")
    while True:
        now = datetime.now().strftime("%H:%M")
        if now >= TARGET_TIME:
            break
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
    # exit address bar
    pyautogui.hotkey('command', 'l')
    time.sleep(0.3)
    pyautogui.press('esc')
    time.sleep(0.5)

    # click center (Claude input usually there)
    w, h = pyautogui.size()
    pyautogui.click(w/2, h*0.8)
    time.sleep(0.5)

def paste_prompt():
    pyperclip.copy(PROMPT)
    pyautogui.hotkey("command", "v")
    time.sleep(0.5)

def send_enter():
    pyautogui.press("enter")

def try_send():
    focus_input_area()

    if PROMPT:
        paste_prompt()

    send_enter()

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