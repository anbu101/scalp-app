///desktop/src-tauri/src/runtime.rs
use std::path::PathBuf;
use std::sync::{Mutex, OnceLock};
use std::time::{Duration, Instant};
use std::thread;
use std::net::TcpStream;

use tauri::{AppHandle, Manager};
use tauri::path::BaseDirectory;
use std::process::{Command, Child, Stdio};

#[cfg(target_os = "windows")]
use std::os::windows::process::CommandExt;

#[cfg(target_os = "windows")]
const CREATE_NO_WINDOW: u32 = 0x0800_0000;

/// Build a Command that never flashes a console window on Windows.
/// On other platforms this is just Command::new.
fn quiet_command<S: AsRef<std::ffi::OsStr>>(program: S) -> Command {
    let mut cmd = Command::new(program);
    #[cfg(target_os = "windows")]
    cmd.creation_flags(CREATE_NO_WINDOW);
    cmd
}

static LAST_MANUAL_STOP: OnceLock<Mutex<Option<Instant>>> = OnceLock::new();
static RESTART_ATTEMPTS: OnceLock<Mutex<u8>> = OnceLock::new();
static APP_HANDLE: OnceLock<AppHandle> = OnceLock::new();

use std::sync::atomic::{AtomicBool, Ordering};

static BACKEND_STARTING: AtomicBool = AtomicBool::new(false);
static BACKEND_PROCESS: Mutex<Option<Child>> = Mutex::new(None);

/* =========================================================
   RUNTIME INIT
   ========================================================= */

pub fn init(app: AppHandle) {
    let _ = APP_HANDLE.set(app);
}

fn app_handle() -> &'static AppHandle {
    APP_HANDLE.get().expect("runtime::init not called")
}

/* =========================================================
   LEGACY UTILITIES (REQUIRED BY commands.rs)
   ========================================================= */

pub fn cap_output(output: &str, max_lines: usize) -> String {
    let lines: Vec<&str> = output.lines().collect();
    if lines.len() <= max_lines {
        output.to_string()
    } else {
        lines[lines.len() - max_lines..].join("\n")
    }
}

pub fn manual_stop_flag() -> &'static Mutex<Option<Instant>> {
    LAST_MANUAL_STOP.get_or_init(|| Mutex::new(None))
}

/* =========================================================
   EMBEDDED BACKEND (DESKTOP MODE)
   ========================================================= */


fn resolve_backend_paths() -> Result<(PathBuf, PathBuf), String> {
    let app = app_handle();

    let resource_dir = app
        .path()
        .resource_dir()
        .map_err(|e| format!("resource_dir not found: {e}"))?;

    // Backend is at resources/backend/ (bundled by Tauri)
    let backend_dir = resource_dir.join("backend");

    eprintln!("[RUNTIME] Resolved backend dir = {}", backend_dir.display());

    if !backend_dir.exists() {
        return Err(format!("Backend directory not found: {}", backend_dir.display()));
    }

    // Platform-specific binary name
    #[cfg(target_os = "windows")]
    let backend_binary = backend_dir.join("scalp-backend.exe");

    #[cfg(not(target_os = "windows"))]
    let backend_binary = backend_dir.join("scalp-backend");

    if !backend_binary.exists() {
        return Err(format!("Backend binary not found: {}", backend_binary.display()));
    }

    eprintln!("[RUNTIME] Backend binary = {}", backend_binary.display());

    Ok((backend_dir, backend_binary))
}

// >>> WATCHDOG_RESTART_20261126 BEGIN (start guard + reaping) <<<
// DEF-029: the watchdog could never restart the backend. Two faults:
//  1. BACKEND_STARTING was set by every start and cleared only on the early
//     returns, never after a successful spawn (nor when resolve_backend_paths
//     failed), so every later start_backend() said "already starting".
//  2. BACKEND_PROCESS kept the Child of a backend that had crashed, so the
//     "process already exists" check refused the restart as well.
// Now a StartingGuard clears the flag on every return path, and a Child that
// has exited is reaped (taken out) before the check. A Child that is still
// running is left alone even when the port is silent (a slow boot is not a
// crash), so the watchdog never kills or doubles a live backend.
struct StartingGuard;

impl StartingGuard {
    fn acquire() -> Option<StartingGuard> {
        BACKEND_STARTING.compare_exchange(false, true, Ordering::SeqCst, Ordering::SeqCst)
            .ok()
            .map(|_| StartingGuard)
    }
}

impl Drop for StartingGuard {
    fn drop(&mut self) {
        BACKEND_STARTING.store(false, Ordering::SeqCst);
    }
}

/// Drop the handle of a backend process that has exited. Returns true while a
/// process that is still running is held.
fn reap_exited(slot: &mut Option<Child>) -> bool {
    let exited = match slot.as_mut() {
        None => return false,
        Some(child) => match child.try_wait() {
            Ok(Some(status)) => {
                eprintln!("[RUNTIME] Previous backend process exited ({status}); handle released");
                true
            }
            Ok(None) => false,
            Err(e) => {
                eprintln!("[RUNTIME] Could not query the backend process ({e}); treating it as running");
                false
            }
        },
    };
    if exited {
        *slot = None;
    }
    slot.is_some()
}

pub fn start_backend() -> Result<(), String> {
    let thread_id = std::thread::current().id();
    eprintln!("[RUNTIME] start_backend() called from thread {:?}", thread_id);

    // Atomic check-and-set to prevent race conditions; the guard clears the
    // flag on every return below.
    let _starting = match StartingGuard::acquire() {
        Some(g) => g,
        None => {
            eprintln!("[RUNTIME] Backend already starting, skipping (thread {:?})", thread_id);
            return Ok(());
        }
    };

    eprintln!("[RUNTIME] Thread {:?} won the race, proceeding with start", thread_id);

    // Check if backend is already running by testing the port
    if backend_http_alive() {
        eprintln!("[RUNTIME] Backend already running on port 47321");
        return Ok(());
    }

    // Also check process handle (a crashed backend's handle is reaped first)
    {
        let mut process_guard = BACKEND_PROCESS.lock().unwrap();
        if reap_exited(&mut process_guard) {
            eprintln!("[RUNTIME] Backend process already exists (still running)");
            return Ok(());
        }
    }
// >>> WATCHDOG_RESTART_20261126 END (start guard + reaping) <<<

    // Resolve paths AFTER all checks pass
    let (backend_dir, backend_binary) = resolve_backend_paths()?;

    eprintln!("[RUNTIME] Starting backend: {}", backend_binary.display());

    let child = quiet_command(&backend_binary)
        .current_dir(&backend_dir)
        .env("SCALP_ENV", "desktop")
        .env("SCALP_HOST", "0.0.0.0")
        .env("SCALP_PORT", "47321")
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn()
        .map_err(|e| format!("Failed to start backend: {e}"))?;

    eprintln!("[RUNTIME] Backend process spawned with PID: {:?}", child.id());

    // Store the process handle
    *BACKEND_PROCESS.lock().unwrap() = Some(child);
    eprintln!("[RUNTIME] Backend started successfully");

    // Ensure Tailscale Funnel is active so Zerodha's OAuth callback can
    // reach this machine over the public internet via the Tailscale HTTPS hostname.
    // Fire-and-forget: silent no-op if Tailscale is not installed or not logged in.
    start_tailscale_funnel();

    Ok(())
}


pub fn stop_backend() {
    eprintln!("[RUNTIME] Stopping backend...");
    if let Some(mut child) = BACKEND_PROCESS.lock().unwrap().take() {
        eprintln!("[RUNTIME] Killing backend process");
        let _ = child.kill();
        eprintln!("[RUNTIME] Backend stopped");
    } else {
        eprintln!("[RUNTIME] No backend process to stop");
    }
}

fn backend_http_alive() -> bool {
    TcpStream::connect_timeout(
        &"127.0.0.1:47321".parse().unwrap(),
        Duration::from_secs(1),
    )
    .is_ok()
}

/* =========================================================
   TAILSCALE FUNNEL — ZERODHA CALLBACK ONLY
   >>> TS_FUNNEL_SCOPE_20261017 BEGIN <<<

   Before this fence the app ran `tailscale funnel --bg 47321`, which put
   the WHOLE backend (every API route + the UI) on the public internet at
   https://<machine>.ts.net, with no API auth behind it.

   Funnel now publishes exactly ONE path:
       https://<machine>.ts.net/zerodha/callback
           -> http://127.0.0.1:47321/zerodha/callback
   The Kite redirect URL users already registered keeps working unchanged.

   On every backend start:
     1. Read `tailscale serve status --json`.
     2. If the legacy root handler ("/" -> :47321) is present on :443,
        remove ONLY that mount (`tailscale serve --https=443 --set-path=/ off`).
        Any other serve/funnel config on the machine is left alone.
     3. If the callback-only Funnel handler is missing, add it.
   If the status output can't be parsed (very old CLI), steps 2-3 run
   blind; their errors are harmless and only logged.

   Phone access over the tailnet (http://<tailscale-ip>:47321) does not go
   through Funnel and is unaffected. Defence in depth: the backend also
   rejects every Tailscale-proxied request except GET /zerodha/callback
   (backend/app/utils/funnel_guard.py, same fence).
   Completely silent if Tailscale is not installed or not logged in.
   ========================================================= */

const TS_BACKEND_PORT_SUFFIX: &str = ":47321";
const TS_CALLBACK_PATH: &str = "/zerodha/callback";
const TS_CALLBACK_TARGET: &str = "http://127.0.0.1:47321/zerodha/callback";

#[derive(Debug, PartialEq, Eq)]
struct FunnelPlan {
    remove_legacy_root: bool,
    add_callback: bool,
}

fn proxies_to_backend(proxy: &str) -> bool {
    let p = proxy.trim_end_matches('/');
    p.ends_with(TS_BACKEND_PORT_SUFFIX)
}

fn proxies_to_callback(proxy: &str) -> bool {
    let p = proxy.trim_end_matches('/');
    p.ends_with(":47321/zerodha/callback")
}

/// Decide what to change from `tailscale serve status --json` output.
/// None / unparsable -> act blind (remove + add; errors only logged).
/// "null" or "{}"    -> nothing configured -> just add the callback.
fn plan_funnel_changes(status_json: Option<&str>) -> FunnelPlan {
    let blind = FunnelPlan { remove_legacy_root: true, add_callback: true };

    let text = match status_json.map(|s| s.trim()) {
        Some(s) if !s.is_empty() => s,
        _ => return blind,
    };
    let cfg: serde_json::Value = match serde_json::from_str(text) {
        Ok(v) => v,
        Err(_) => return blind,
    };
    if cfg.is_null() {
        return FunnelPlan { remove_legacy_root: false, add_callback: true };
    }
    if !cfg.is_object() {
        return blind;
    }

    let mut remove_legacy_root = false;
    let mut callback_ok = false;

    if let Some(web) = cfg.get("Web").and_then(|w| w.as_object()) {
        for (host_port, site) in web {
            if !host_port.ends_with(":443") {
                continue;
            }
            let handlers = match site.get("Handlers").and_then(|h| h.as_object()) {
                Some(h) => h,
                None => continue,
            };
            let proxy_of = |mount: &str| -> String {
                handlers
                    .get(mount)
                    .and_then(|h| h.get("Proxy"))
                    .and_then(|p| p.as_str())
                    .unwrap_or("")
                    .to_string()
            };
            if proxies_to_backend(&proxy_of("/")) {
                remove_legacy_root = true;
            }
            let funnel_on = cfg
                .get("AllowFunnel")
                .and_then(|a| a.get(host_port.as_str()))
                .and_then(|b| b.as_bool())
                .unwrap_or(false);
            if funnel_on && proxies_to_callback(&proxy_of(TS_CALLBACK_PATH)) {
                callback_ok = true;
            }
        }
    }

    FunnelPlan { remove_legacy_root, add_callback: !callback_ok }
}

fn run_tailscale(bin: &str, args: &[&str], what: &str) -> bool {
    let result = quiet_command(bin)
        .args(args)
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::piped())
        .output();

    match result {
        Ok(out) if out.status.success() => {
            eprintln!("[TAILSCALE] {what}: ok");
            true
        }
        Ok(out) => {
            let stderr = String::from_utf8_lossy(&out.stderr);
            eprintln!("[TAILSCALE] {what} skipped ({}): {}", out.status, stderr.trim());
            false
        }
        Err(e) => {
            eprintln!("[TAILSCALE] {what} could not run: {e}");
            false
        }
    }
}

fn start_tailscale_funnel() {
    thread::spawn(|| {
        // Small delay — let the backend finish binding its port first
        thread::sleep(Duration::from_secs(2));

        #[cfg(target_os = "windows")]
        let tailscale_bin = "tailscale.exe";

        #[cfg(not(target_os = "windows"))]
        let tailscale_bin = "tailscale";

        let status = quiet_command(tailscale_bin)
            .args(["serve", "status", "--json"])
            .stdin(Stdio::null())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .output();

        let status_json: String = match status {
            Err(e) => {
                // Binary not found — Tailscale not installed on this machine
                eprintln!("[TAILSCALE] Not available: {e}");
                return;
            }
            Ok(out) if !out.status.success() => {
                // Not logged in / daemon not running — nothing is being served
                let stderr = String::from_utf8_lossy(&out.stderr);
                eprintln!("[TAILSCALE] Funnel skipped ({}): {}", out.status, stderr.trim());
                return;
            }
            Ok(out) => String::from_utf8_lossy(&out.stdout).into_owned(),
        };

        let plan = plan_funnel_changes(Some(status_json.as_str()));
        eprintln!("[TAILSCALE] Funnel scope plan: {:?}", plan);

        if plan.remove_legacy_root {
            run_tailscale(
                tailscale_bin,
                &["serve", "--https=443", "--set-path=/", "off"],
                "Removed legacy public root (/ -> :47321)",
            );
        }

        if plan.add_callback {
            let set_path = format!("--set-path={}", TS_CALLBACK_PATH);
            run_tailscale(
                tailscale_bin,
                &["funnel", "--bg", "--https=443", set_path.as_str(), TS_CALLBACK_TARGET],
                "Funnel active for /zerodha/callback only",
            );
        } else {
            eprintln!("[TAILSCALE] Funnel already scoped to /zerodha/callback");
        }
    });
}
/* >>> TS_FUNNEL_SCOPE_20261017 END <<< */


/* =========================================================
   WATCHDOGS
   ========================================================= */

// >>> WATCHDOG_RESTART_20261126 BEGIN (update pause) <<<
// Once the backend is stopped for an update install (stop_backend_for_update)
// or the window is closing, the watchdog must not bring it back: on Windows a
// restarted backend re-locks the DLLs the installer is replacing, and on every
// platform it would run the old build mid-install. The pause lasts for the
// rest of this process: a good install relaunches the app, and a failed one
// tells the user to quit and reopen Scalp before trading (UpdateBanner.jsx).
static WATCHDOG_PAUSED: AtomicBool = AtomicBool::new(false);

pub fn pause_watchdog(reason: &str) {
    if !WATCHDOG_PAUSED.swap(true, Ordering::SeqCst) {
        eprintln!("[WATCHDOG] Paused: {reason}");
    }
}

#[derive(Debug, PartialEq)]
enum WatchdogStep {
    Paused,
    Healthy,
    GaveUp,
    Restart,
}

/// One watchdog tick: paused beats everything, a live port resets the
/// attempts, and after MAX_RESTARTS failed tries it stops trying.
const MAX_RESTARTS: u8 = 3;

fn watchdog_step(paused: bool, alive: bool, attempts: u8) -> WatchdogStep {
    if paused {
        WatchdogStep::Paused
    } else if alive {
        WatchdogStep::Healthy
    } else if attempts >= MAX_RESTARTS {
        WatchdogStep::GaveUp
    } else {
        WatchdogStep::Restart
    }
}

pub fn start_backend_watchdog() {
    if cfg!(debug_assertions) {
        eprintln!("[WATCHDOG] Disabled in dev mode");
        return;
    }

    thread::spawn(|| {
        thread::sleep(Duration::from_secs(10));

        loop {
            thread::sleep(Duration::from_secs(15));

            let paused = WATCHDOG_PAUSED.load(Ordering::SeqCst);
            let alive = !paused && backend_http_alive();
            let mut attempts = restart_attempts().lock().unwrap();
            match watchdog_step(paused, alive, *attempts) {
                WatchdogStep::Paused => {}
                WatchdogStep::Healthy => *attempts = 0,
                WatchdogStep::GaveUp => {}
                WatchdogStep::Restart => {
                    *attempts += 1;
                    eprintln!("[WATCHDOG] Backend not answering on 47321 — restart attempt {}/{}",
                              *attempts, MAX_RESTARTS);
                    if let Err(e) = start_backend() {
                        eprintln!("[WATCHDOG] Restart failed: {e}");
                    }
                }
            }
        }
    });
}

#[cfg(test)]
mod watchdog_restart_tests {
    use super::*;

    // the start-flag tests share BACKEND_STARTING: one at a time
    static FLAG_LOCK: Mutex<()> = Mutex::new(());

    // TC-DESK-0001
    #[test]
    fn tc_desk_0001_start_flag_clears_after_a_start() {
        let _l = FLAG_LOCK.lock().unwrap_or_else(|e| e.into_inner());
        BACKEND_STARTING.store(false, Ordering::SeqCst);
        {
            let g = StartingGuard::acquire();
            assert!(g.is_some());
            assert!(StartingGuard::acquire().is_none(), "a second start while one runs is refused");
        }
        assert!(!BACKEND_STARTING.load(Ordering::SeqCst), "the flag is clear once the start returns");
        assert!(StartingGuard::acquire().is_some(), "so the watchdog can start again");
    }

    // TC-DESK-0002
    #[test]
    fn tc_desk_0002_flag_clears_on_an_early_error_return() {
        let _l = FLAG_LOCK.lock().unwrap_or_else(|e| e.into_inner());
        fn failing_start() -> Result<(), String> {
            let _g = StartingGuard::acquire().ok_or("busy")?;
            Err("Backend binary not found".to_string())?;
            Ok(())
        }
        BACKEND_STARTING.store(false, Ordering::SeqCst);
        assert!(failing_start().is_err());
        assert!(!BACKEND_STARTING.load(Ordering::SeqCst));
    }

    // TC-DESK-0003
    #[cfg(unix)]
    #[test]
    fn tc_desk_0003_exited_backend_handle_is_reaped() {
        let mut slot = Some(Command::new("true").spawn().expect("spawn true"));
        let t0 = Instant::now();
        while reap_exited(&mut slot) && t0.elapsed() < Duration::from_secs(5) {
            thread::sleep(Duration::from_millis(20));
        }
        assert!(slot.is_none(), "a crashed backend no longer blocks the restart");
    }

    // TC-DESK-0004
    #[cfg(unix)]
    #[test]
    fn tc_desk_0004_running_backend_is_left_alone() {
        let mut slot = Some(Command::new("sleep").arg("5").spawn().expect("spawn sleep"));
        assert!(reap_exited(&mut slot), "a running backend counts as running");
        assert!(slot.is_some());
        let _ = slot.as_mut().unwrap().kill();
        let _ = slot.as_mut().unwrap().wait();
        assert!(!reap_exited(&mut None));
    }

    // TC-DESK-0005
    #[test]
    fn tc_desk_0005_watchdog_steps() {
        assert_eq!(watchdog_step(false, true, 2), WatchdogStep::Healthy);
        assert_eq!(watchdog_step(false, false, 0), WatchdogStep::Restart);
        assert_eq!(watchdog_step(false, false, MAX_RESTARTS - 1), WatchdogStep::Restart);
        assert_eq!(watchdog_step(false, false, MAX_RESTARTS), WatchdogStep::GaveUp);
    }

    // TC-DESK-0006
    #[test]
    fn tc_desk_0006_paused_for_update_never_restarts() {
        assert_eq!(watchdog_step(true, false, 0), WatchdogStep::Paused);
        assert_eq!(watchdog_step(true, true, 0), WatchdogStep::Paused);
        pause_watchdog("test");
        assert!(WATCHDOG_PAUSED.load(Ordering::SeqCst));
    }
}
// >>> WATCHDOG_RESTART_20261126 END (update pause) <<<

pub fn start_watchdog() {
    start_backend_watchdog();
}

fn restart_attempts() -> &'static Mutex<u8> {
    RESTART_ATTEMPTS.get_or_init(|| Mutex::new(0))
}

/* =========================================================
   LEGACY DOCKER RUNTIME (UNUSED)
   ========================================================= */

#[derive(Debug, serde::Serialize)]
pub struct RuntimeResult {
    pub success: bool,
    pub stdout: String,
    pub stderr: String,
}

#[derive(Debug, Clone, Copy)]
pub enum RuntimeCommand {
    Start,
    Stop,
    Restart,
    Status,
    Logs,
}

impl RuntimeCommand {
    fn as_str(&self) -> &'static str {
        match self {
            RuntimeCommand::Start => "start",
            RuntimeCommand::Stop => "stop",
            RuntimeCommand::Restart => "restart",
            RuntimeCommand::Status => "status",
            RuntimeCommand::Logs => "logs",
        }
    }
}

fn resolve_scalp_path() -> Result<PathBuf, String> {
    let home = dirs::home_dir().ok_or("Unable to resolve HOME directory")?;

    #[cfg(target_os = "windows")]
    let scalp = home.join(".scalp-app").join("bin").join("scalp.bat");

    #[cfg(not(target_os = "windows"))]
    let scalp = home.join(".scalp-app").join("bin").join("scalp");

    if !scalp.exists() {
        return Err(format!("scalp binary not found at {}", scalp.display()));
    }

    Ok(scalp)
}

pub fn run_runtime_command(cmd: RuntimeCommand) -> RuntimeResult {
    let scalp_path = match resolve_scalp_path() {
        Ok(p) => p,
        Err(e) => {
            return RuntimeResult {
                success: false,
                stdout: String::new(),
                stderr: e,
            };
        }
    };

    let output = quiet_command(scalp_path).arg(cmd.as_str()).output();

    match output {
        Ok(out) => RuntimeResult {
            success: out.status.success(),
            stdout: String::from_utf8_lossy(&out.stdout).to_string(),
            stderr: String::from_utf8_lossy(&out.stderr).to_string(),
        },
        Err(e) => RuntimeResult {
            success: false,
            stdout: String::new(),
            stderr: e.to_string(),
        },
    }
}

/* =========================================================
   FRONTEND DEV SERVER (FOR MOBILE ACCESS)
   ========================================================= */

static FRONTEND_PROCESS: Mutex<Option<Child>> = Mutex::new(None);

pub fn start_frontend_dev_server() -> Result<(), String> {
    eprintln!("[RUNTIME] Attempting to start frontend dev server for mobile access...");

    // Check if already running
    {
        let guard = FRONTEND_PROCESS.lock().unwrap();
        if guard.is_some() {
            eprintln!("[RUNTIME] Frontend dev server already running");
            return Ok(());
        }
    }

    let app = app_handle();

    let frontend_dir = if cfg!(debug_assertions) {
        // Debug mode: Navigate from resources back to project root
        let resource_dir = app
            .path()
            .resource_dir()
            .map_err(|e| format!("resource_dir not found: {e}"))?;

        resource_dir
            .parent()
            .and_then(|p| p.parent())
            .ok_or("Cannot resolve project root")?
            .join("frontend")
    } else {
        // Release mode: Try common locations relative to home
        let home = dirs::home_dir().ok_or("Cannot resolve home directory")?;
        let candidate1 = home.join("dev/scalp-app/frontend");
        let candidate2 = home.join("scalp-app/frontend");
        let candidate3 = home.join("Documents/scalp-app/frontend");

        if candidate1.exists() {
            candidate1
        } else if candidate2.exists() {
            candidate2
        } else if candidate3.exists() {
            candidate3
        } else {
            eprintln!("[RUNTIME] Frontend directory not found in:");
            eprintln!("  - {}", candidate1.display());
            eprintln!("  - {}", candidate2.display());
            eprintln!("  - {}", candidate3.display());
            eprintln!("[RUNTIME] Mobile access will not be available");
            eprintln!("[RUNTIME] To fix: Start dev server manually with:");
            eprintln!("  cd ~/dev/scalp-app/frontend && HOST=0.0.0.0 npm run dev");
            return Ok(());
        }
    };

    if !frontend_dir.exists() {
        eprintln!("[RUNTIME] Frontend directory not found at: {}", frontend_dir.display());
        eprintln!("[RUNTIME] Mobile access will not be available");
        return Ok(());
    }

    eprintln!("[RUNTIME] Frontend dir = {}", frontend_dir.display());

    let child = quiet_command("npm")
        .current_dir(&frontend_dir)
        .env("HOST", "0.0.0.0")
        .env("PORT", "3000")
        .arg("run")
        .arg("dev")
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .map_err(|e| {
            eprintln!("[RUNTIME] Failed to start frontend: {e}");
            eprintln!("[RUNTIME] Make sure npm is installed and in PATH");
            eprintln!("[RUNTIME] Manual start: cd {} && HOST=0.0.0.0 npm run dev", frontend_dir.display());
            format!("Failed to start frontend dev server: {e}")
        })?;

    eprintln!("[RUNTIME] Frontend dev server started with PID: {:?}", child.id());
    eprintln!("[RUNTIME] ✅ Mobile access available on port 3000");
    eprintln!("[RUNTIME] Access via your Tailscale hostname on port 3000");

    *FRONTEND_PROCESS.lock().unwrap() = Some(child);

    Ok(())
}

pub fn stop_frontend_dev_server() {
    eprintln!("[RUNTIME] Stopping frontend dev server...");
    if let Some(mut child) = FRONTEND_PROCESS.lock().unwrap().take() {
        eprintln!("[RUNTIME] Killing frontend dev server process");
        let _ = child.kill();
        eprintln!("[RUNTIME] Frontend dev server stopped");
    } else {
        eprintln!("[RUNTIME] No frontend dev server to stop");
    }
}