set jsCode to (read POSIX file "/Users/anbu/dev/scalp-app/frontend/send_claude.js")

tell application "Google Chrome"
    repeat with w in windows
        repeat with t in tabs of w
            if (URL of t contains "claude.ai") then
                tell t to execute javascript jsCode
                return "DONE"
            end if
        end repeat
    end repeat
    return "NOT_FOUND"
end tell