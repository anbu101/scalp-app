use std::path::PathBuf;
use std::sync::{Mutex, OnceLock};
use std::time::{Duration, Instant};
use std::thread;
use std::net::TcpStream;

use tauri::{AppHandle, Manager};
use tauri::path::BaseDirectory;
use std::process::{Command, Child, Stdio};

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

pub fn start_backend() -> Result<(), String> {
    let thread_id = std::thread::current().id();
    eprintln!("[RUNTIME] start_backend() called from thread {:?}", thread_id);
    
    // Atomic check-and-set to prevent race conditions
    if BACKEND_STARTING.compare_exchange(
        false,
        true,
        Ordering::SeqCst,
        Ordering::SeqCst
    ).is_err() {
        eprintln!("[RUNTIME] Backend already starting, skipping (thread {:?})", thread_id);
        return Ok(());
    }
    
    eprintln!("[RUNTIME] Thread {:?} won the race, proceeding with start", thread_id);

    // Check if backend is already running by testing the port
    if backend_http_alive() {
        eprintln!("[RUNTIME] Backend already running on port 47321");
        BACKEND_STARTING.store(false, Ordering::SeqCst);
        return Ok(());
    }

    // Also check process handle
    {
        let process_guard = BACKEND_PROCESS.lock().unwrap();
        if process_guard.is_some() {
            eprintln!("[RUNTIME] Backend process already exists");
            BACKEND_STARTING.store(false, Ordering::SeqCst);
            return Ok(());
        }
    }

    // Resolve paths AFTER all checks pass
    let (backend_dir, backend_binary) = resolve_backend_paths()?;

    eprintln!("[RUNTIME] Starting backend: {}", backend_binary.display());

    let child = Command::new(&backend_binary)
        .current_dir(&backend_dir)
        .env("SCALP_ENV", "desktop")
        .env("SCALP_HOST", "0.0.0.0")
        .env("SCALP_PORT", "47321")
        .stdin(Stdio::null())
        .stdout(Stdio::inherit())
        .stderr(Stdio::inherit())
        .spawn()
        .map_err(|e| {
            BACKEND_STARTING.store(false, Ordering::SeqCst);
            format!("Failed to start backend: {e}")
        })?;

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
   TAILSCALE FUNNEL
   Runs `tailscale funnel --bg 47321` after the backend starts.

   WHY FUNNEL (not serve):
   - `tailscale serve`  → exposes only within your tailnet (private)
   - `tailscale funnel` → exposes to the public internet via HTTPS
   Zerodha's OAuth callback comes from Zerodha's servers on the
   internet, so `funnel` is required for the redirect to succeed.

   The --bg flag persists the config in the Tailscale daemon,
   surviving terminal closes, app restarts, and Tailscale updates.
   Completely silent if Tailscale is not installed or not logged in.
   Works on macOS and Windows.
   ========================================================= */

fn start_tailscale_funnel() {
    thread::spawn(|| {
        // Small delay — let the backend finish binding its port first
        thread::sleep(Duration::from_secs(2));

        #[cfg(target_os = "windows")]
        let tailscale_bin = "tailscale.exe";

        #[cfg(not(target_os = "windows"))]
        let tailscale_bin = "tailscale";

        // `funnel` takes just the port number — no http:// prefix
        let result = Command::new(tailscale_bin)
            .args(["funnel", "--bg", "47321"])
            .stdout(Stdio::null())
            .stderr(Stdio::piped())
            .output();

        match result {
            Ok(out) if out.status.success() => {
                eprintln!("[TAILSCALE] Funnel active: port 47321 → public Tailscale HTTPS hostname");
            }
            Ok(out) => {
                // Non-zero exit: not logged in, already configured, or unsupported —
                // all acceptable, app continues normally either way
                let stderr = String::from_utf8_lossy(&out.stderr);
                eprintln!("[TAILSCALE] Funnel skipped ({}): {}", out.status, stderr.trim());
            }
            Err(e) => {
                // Binary not found — Tailscale not installed on this machine
                eprintln!("[TAILSCALE] Not available: {e}");
            }
        }
    });
}

/* =========================================================
   WATCHDOGS
   ========================================================= */

pub fn start_backend_watchdog() {
    if cfg!(debug_assertions) {
        eprintln!("[WATCHDOG] Disabled in dev mode");
        return;
    }

    thread::spawn(|| {
        thread::sleep(Duration::from_secs(10));

        loop {
            thread::sleep(Duration::from_secs(15));

            if backend_http_alive() {
                *restart_attempts().lock().unwrap() = 0;
                continue;
            }

            let mut attempts = restart_attempts().lock().unwrap();
            if *attempts >= 3 {
                continue;
            }

            *attempts += 1;
            let _ = start_backend();
        }
    });
}

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

    let output = Command::new(scalp_path).arg(cmd.as_str()).output();

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
    
    let child = Command::new("npm")
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