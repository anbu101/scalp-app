#!/usr/bin/env python3
"""
apply_update_confirm_fix.py  —  fence UPDATE_CONFIRM_FIX_20261116

Bug: clicking "Confirm update" on the in-app update banner sometimes does
nothing. The banner stays on "Restart Scalp now to install vX?" for good,
and clicking again does not help.

Root cause (frontend + Rust):
  1. onConfirm() first awaits invoke("stop_backend_for_update") with no
     timeout and no visible state change. It also clears the 6 s auto-disarm
     timer, so if that call never returns, the banner stays "armed" forever.
     Each extra click just queues another await.
  2. stop_backend_for_update runs `lsof -ti tcp:47321` and kill -9's EVERY
     pid it gets back. That includes CLIENTS of the backend, not only the
     listener. App.jsx polls health every 5 s, so the Scalp webview's own
     WebKit networking process always has a socket open to :47321 and gets
     SIGKILLed. A Chrome tab on the app is killed too. Verified: on a
     listener + client pair, `lsof -ti tcp:PORT` returns both pids;
     `lsof -nP -t -iTCP:PORT -sTCP:LISTEN` returns only the listener.
  3. It is a sync #[tauri::command], so Tauri v2 runs it on the MAIN thread
     (lsof + kills + 2 s sleep, with the window frozen).
  4. The backend was killed BEFORE the download. If the download then failed
     or hung, the backend stayed dead and the strategies stopped. The watchdog
     cannot bring it back (BACKEND_STARTING is never reset after a successful
     start; logged separately).

Fix:
  UpdateBanner.jsx
    - Shows "Downloading…" as soon as Confirm is clicked; ignores repeat clicks.
    - Order is now download -> stop backend -> install -> relaunch. The
      backend and strategies keep running through the download. They stop
      only once the installer is fully on disk.
    - stop_backend_for_update is limited to 10 s, so the update goes ahead
      even if the call hangs.
    - Falls back to downloadAndInstall only if this updater build has no
      separate download()/install().
    - If the update fails after the backend was stopped, the error says so.
  main.rs
    - stop_backend_for_update is async and does its work in spawn_blocking
      (no longer on the main thread).
    - kill_process_on_port kills LISTENERS only (macOS/Linux:
      -sTCP:LISTEN), never this process's own pid, and each pid once
      (Windows netstat prints IPv4 and IPv6 lines).

Run from the repo root:  python3 apply_update_confirm_fix.py
Then a full build (Rust changed):  ./desktop/build-scalp.sh both
"""
import os
import shutil
import subprocess
import sys

FENCE = "UPDATE_CONFIRM_FIX_20261116"
ROOT = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else os.getcwd())
JSX = os.path.join(ROOT, "frontend/src/components/UpdateBanner.jsx")
RS = os.path.join(ROOT, "desktop/src-tauri/src/main.rs")


def die(msg):
    print("ABORT: " + msg)
    sys.exit(1)


def rep_once(text, old, new, label):
    n = text.count(old)
    if n != 1:
        die("anchor '%s' found %d times (expected 1)" % (label, n))
    return text.replace(old, new)


# ---------------------------------------------------------------- preflight
for p in (JSX, RS):
    if not os.path.isfile(p):
        die("missing " + p + " (run from the scalp-app repo root)")

jsx = open(JSX, encoding="utf-8").read()
rs = open(RS, encoding="utf-8").read()

if FENCE in jsx or FENCE in rs:
    die("already applied (%s present)" % FENCE)
for parent, txt, name in (
    ("AUTO_UPDATER_20260821", jsx, "UpdateBanner.jsx"),
    ("NATIVE_GAP_RETRY_20260827", jsx, "UpdateBanner.jsx"),
    ("AUTO_UPDATER_20260821", rs, "main.rs"),
):
    if parent not in txt:
        die("parent fence %s missing in %s" % (parent, name))

try:
    st = subprocess.run(
        ["git", "status", "--porcelain", "--", JSX, RS],
        cwd=ROOT, capture_output=True, text=True, check=True,
    ).stdout.strip()
    if st and "--allow-dirty" not in sys.argv:
        die("uncommitted changes in target files:\n" + st +
            "\n(commit/stash first, or pass --allow-dirty)")
except (OSError, subprocess.CalledProcessError):
    print("note: git status unavailable, skipping dirty check")

# ---------------------------------------------------------------- JSX edits
jsx = rep_once(
    jsx,
    "const ARM_TIMEOUT_MS = 6000;\n",
    "const ARM_TIMEOUT_MS = 6000;\n"
    "// >>> " + FENCE + " BEGIN (constants) <<<\n"
    "// Upper bound on the pre-install backend stop. The update goes ahead\n"
    "// even if the native call never answers.\n"
    "const STOP_BACKEND_TIMEOUT_MS = 10000;\n"
    "// >>> " + FENCE + " END <<<\n",
    "ARM_TIMEOUT_MS",
)

jsx = rep_once(
    jsx,
    "// Mon–Fri 09:00–15:30 IST (UTC+5:30 fixed, no DST).\n",
    "// >>> " + FENCE + " BEGIN (stopBackendForUpdate) <<<\n"
    "// Stops the backend so the installer can replace locked files (Windows\n"
    "// DLLs). Never throws and never waits longer than\n"
    "// STOP_BACKEND_TIMEOUT_MS. Returns true if a stop was attempted.\n"
    "async function stopBackendForUpdate() {\n"
    "  let invoke = null;\n"
    "  try {\n"
    "    invoke = window.__TAURI__ && window.__TAURI__.core ? window.__TAURI__.core.invoke : null;\n"
    "  } catch (e) {\n"
    "    invoke = null;\n"
    "  }\n"
    "  if (!invoke) return false;\n"
    "  let timer = null;\n"
    "  try {\n"
    "    await Promise.race([\n"
    "      invoke(\"stop_backend_for_update\"),\n"
    "      new Promise((_, reject) => {\n"
    "        timer = setTimeout(\n"
    "          () => reject(new Error(\"timed out after \" + STOP_BACKEND_TIMEOUT_MS + \" ms\")),\n"
    "          STOP_BACKEND_TIMEOUT_MS\n"
    "        );\n"
    "      }),\n"
    "    ]);\n"
    "  } catch (e) {\n"
    "    console.warn(\"[UpdateBanner] stop_backend_for_update:\", e);\n"
    "  } finally {\n"
    "    clearTimeout(timer);\n"
    "  }\n"
    "  return true;\n"
    "}\n"
    "// >>> " + FENCE + " END <<<\n"
    "\n"
    "// Mon–Fri 09:00–15:30 IST (UTC+5:30 fixed, no DST).\n",
    "isMarketHoursIST comment",
)

jsx = rep_once(
    jsx,
    "  const gapRetryRef = useRef(0); // NATIVE_GAP_RETRY_20260827\n",
    "  const gapRetryRef = useRef(0); // NATIVE_GAP_RETRY_20260827\n"
    "  const confirmBusyRef = useRef(false); // " + FENCE + "\n",
    "gapRetryRef",
)

# Replace the whole onConfirm body (from its declaration to the line before
# `if (dismissed) return null;`).
start = jsx.find("  const onConfirm = async () => {\n")
end = jsx.find("  if (dismissed) return null;\n")
if start < 0 or end < 0 or end < start or jsx.count("  const onConfirm = async () => {\n") != 1:
    die("onConfirm block anchors not found")
old_block = jsx[start:end]
for must in ('invoke("stop_backend_for_update")', "updateObj.downloadAndInstall", "nativeRelaunch()"):
    if must not in old_block:
        die("onConfirm block does not look like the expected original (missing %s)" % must)

new_block = (
    "  // >>> " + FENCE + " BEGIN (onConfirm) <<<\n"
    "  // Order: download -> stop backend -> install -> relaunch. The backend\n"
    "  // and strategies keep running through the download. The UI leaves\n"
    "  // 'armed' at once, and repeat clicks are ignored.\n"
    "  const onConfirm = async () => {\n"
    "    clearTimeout(armTimerRef.current);\n"
    "    if (confirmBusyRef.current) return;\n"
    "    const updateObj = updateObjRef.current;\n"
    "    if (!updateObj) {\n"
    "      setPhase(\"idle\");\n"
    "      return;\n"
    "    }\n"
    "    confirmBusyRef.current = true;\n"
    "    setErrorMsg(\"\");\n"
    "    setProgressPct(null);\n"
    "    setPhase(\"downloading\");\n"
    "\n"
    "    let backendStopped = false;\n"
    "    let total = 0;\n"
    "    let received = 0;\n"
    "    const onEvent = (ev) => {\n"
    "      if (!ev) return;\n"
    "      if (ev.event === \"Started\") {\n"
    "        total = (ev.data && ev.data.contentLength) || 0;\n"
    "        received = 0;\n"
    "        setProgressPct(0);\n"
    "      } else if (ev.event === \"Progress\") {\n"
    "        received += (ev.data && ev.data.chunkLength) || 0;\n"
    "        if (total > 0) {\n"
    "          setProgressPct(Math.min(99, Math.round((received / total) * 100)));\n"
    "        }\n"
    "      } else if (ev.event === \"Finished\") {\n"
    "        setProgressPct(100);\n"
    "      }\n"
    "    };\n"
    "\n"
    "    try {\n"
    "      const canSplit =\n"
    "        typeof updateObj.download === \"function\" && typeof updateObj.install === \"function\";\n"
    "      if (canSplit) {\n"
    "        await updateObj.download(onEvent);\n"
    "        setPhase(\"installing\");\n"
    "        backendStopped = await stopBackendForUpdate();\n"
    "        await updateObj.install();\n"
    "      } else {\n"
    "        // Older updater JS without split download/install.\n"
    "        backendStopped = await stopBackendForUpdate();\n"
    "        await updateObj.downloadAndInstall(onEvent);\n"
    "        setPhase(\"installing\");\n"
    "      }\n"
    "      const relaunch = nativeRelaunch();\n"
    "      if (relaunch) {\n"
    "        await relaunch();\n"
    "      } else {\n"
    "        setErrorMsg(\"Installed. Please quit and reopen Scalp to finish the update.\");\n"
    "        setPhase(\"error\");\n"
    "      }\n"
    "    } catch (e) {\n"
    "      confirmBusyRef.current = false;\n"
    "      setPhase(\"error\");\n"
    "      setErrorMsg(\n"
    "        \"Auto-update failed (\" +\n"
    "          String((e && e.message) || e) +\n"
    "          \").\" +\n"
    "          (backendStopped\n"
    "            ? \" The trading backend was stopped for the install. Quit and reopen Scalp before trading.\"\n"
    "            : \"\") +\n"
    "          \" You can install manually from the releases page.\"\n"
    "      );\n"
    "    }\n"
    "  };\n"
    "  // >>> " + FENCE + " END <<<\n"
    "\n"
)
jsx = jsx[:start] + new_block + jsx[end:]

# ---------------------------------------------------------------- Rust edits
old_cmd = (
    "#[tauri::command]\n"
    "fn stop_backend_for_update() -> String {\n"
    "    eprintln!(\"[MAIN] stop_backend_for_update invoked (pre-update cleanup)\");\n"
    "    runtime::stop_backend();\n"
    "    kill_process_on_port(47321);\n"
    "    // Extra settle time so the OS releases file locks\n"
    "    std::thread::sleep(Duration::from_millis(2000));\n"
    "    eprintln!(\"[MAIN] stop_backend_for_update done\");\n"
    "    \"ok\".to_string()\n"
    "}\n"
)
new_cmd = (
    "// >>> " + FENCE + " BEGIN (async command) <<<\n"
    "// Was a sync command, so Tauri v2 ran it on the MAIN thread (window frozen\n"
    "// for lsof + kills + 2 s settle). Now async, with the blocking work in\n"
    "// spawn_blocking.\n"
    "#[tauri::command]\n"
    "async fn stop_backend_for_update() -> String {\n"
    "    eprintln!(\"[MAIN] stop_backend_for_update invoked (pre-update cleanup)\");\n"
    "    let res = tauri::async_runtime::spawn_blocking(|| {\n"
    "        runtime::stop_backend();\n"
    "        kill_process_on_port(47321);\n"
    "        // Extra settle time so the OS releases file locks\n"
    "        std::thread::sleep(Duration::from_millis(2000));\n"
    "    })\n"
    "    .await;\n"
    "    match res {\n"
    "        Ok(_) => {\n"
    "            eprintln!(\"[MAIN] stop_backend_for_update done\");\n"
    "            \"ok\".to_string()\n"
    "        }\n"
    "        Err(e) => {\n"
    "            eprintln!(\"[MAIN] stop_backend_for_update failed: {}\", e);\n"
    "            \"error\".to_string()\n"
    "        }\n"
    "    }\n"
    "}\n"
    "// >>> " + FENCE + " END <<<\n"
)
rs = rep_once(rs, old_cmd, new_cmd, "stop_backend_for_update")

rs = rep_once(
    rs,
    "        let output = quiet_command(\"lsof\")\n"
    "            .args(&[\"-ti\", &format!(\"tcp:{}\", port)])\n"
    "            .output();\n",
    "        // >>> " + FENCE + " BEGIN (listeners only) <<<\n"
    "        // `lsof -ti tcp:PORT` also returns CLIENTS of the port (the\n"
    "        // webview's own network process, browser tabs on the UI), which\n"
    "        // then got kill -9. Only the LISTENING process is the backend.\n"
    "        let output = quiet_command(\"lsof\")\n"
    "            .args(&[\"-nP\", \"-t\", &format!(\"-iTCP:{}\", port), \"-sTCP:LISTEN\"])\n"
    "            .output();\n"
    "        // >>> " + FENCE + " END <<<\n",
    "lsof args",
)

rs = rep_once(
    rs,
    "            let pid_list: Vec<&str> =\n"
    "                pids.lines().map(|s| s.trim()).filter(|s| !s.is_empty()).collect();\n",
    "            // " + FENCE + ": never this process, each pid once\n"
    "            let self_pid = std::process::id().to_string();\n"
    "            let mut pid_list: Vec<&str> = pids\n"
    "                .lines()\n"
    "                .map(|s| s.trim())\n"
    "                .filter(|s| !s.is_empty() && *s != self_pid)\n"
    "                .collect();\n"
    "            pid_list.sort();\n"
    "            pid_list.dedup();\n",
    "unix pid_list",
)

rs = rep_once(
    rs,
    "            let output_str = String::from_utf8_lossy(&output.stdout);\n"
    "            let mut found = false;\n",
    "            let output_str = String::from_utf8_lossy(&output.stdout);\n"
    "            let mut found = false;\n"
    "            // " + FENCE + ": never this process, each pid once (IPv4 + IPv6 rows)\n"
    "            let self_pid = std::process::id().to_string();\n"
    "            let mut killed: Vec<String> = Vec::new();\n",
    "windows found",
)

rs = rep_once(
    rs,
    "                    if let Some(pid) = line.split_whitespace().last() {\n"
    "                        found = true;\n",
    "                    if let Some(pid) = line.split_whitespace().last() {\n"
    "                        if pid == self_pid || killed.iter().any(|k| k == pid) {\n"
    "                            continue;\n"
    "                        }\n"
    "                        killed.push(pid.to_string());\n"
    "                        found = true;\n",
    "windows pid",
)

# ---------------------------------------------------------------- write (staged)
targets = [(JSX, jsx), (RS, rs)]
tmp_paths = []
try:
    for path, content in targets:
        tmp = path + ".tmp-" + FENCE
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(content)
        tmp_paths.append((tmp, path))
    for path, _ in targets:
        shutil.copy2(path, path + ".bak-" + FENCE)
    for tmp, path in tmp_paths:
        os.replace(tmp, path)
except Exception as e:
    for tmp, _ in tmp_paths:
        if os.path.exists(tmp):
            os.remove(tmp)
    die("write failed: %s" % e)

print("applied %s" % FENCE)
for path, _ in targets:
    print("  patched " + os.path.relpath(path, ROOT) + "  (backup .bak-" + FENCE + ")")

# ---------------------------------------------------------------- optional parse check
esb = os.path.join(ROOT, "frontend/node_modules/.bin/esbuild")
if os.path.exists(esb):
    r = subprocess.run([esb, JSX, "--loader:.jsx=jsx", "--log-level=error"],
                       capture_output=True, text=True)
    if r.returncode != 0:
        print("WARNING: esbuild parse failed:\n" + r.stderr)
        print("restore with: cp <file>.bak-%s <file>" % FENCE)
        sys.exit(2)
    print("  esbuild parse OK")
print("next: full rebuild (Rust changed):  ./desktop/build-scalp.sh both")
