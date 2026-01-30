use std::process::{Command, Child};
use std::path::PathBuf;
use std::sync::{Mutex, OnceLock};
use std::time::{Duration, Instant};
use std::thread;
use std::net::TcpStream;

use tauri::{AppHandle, Manager};
use tauri::path::BaseDirectory;

static LAST_MANUAL_STOP: OnceLock<Mutex<Option<Instant>>> = OnceLock::new();
static RESTART_ATTEMPTS: OnceLock<Mutex<u8>> = OnceLock::new();
static APP_HANDLE: OnceLock<AppHandle> = OnceLock::new();

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
   LEGACY DOCKER RUNTIME (UNCHANGED)
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

/* =========================================================
   EMBEDDED BACKEND (DESKTOP MODE)
   ========================================================= */

static BACKEND_PROCESS: Mutex<Option<Child>> = Mutex::new(None);

fn resolve_backend_paths() -> Result<(PathBuf, PathBuf), String> {
    let app = app_handle();

    let resource_dir = app
        .path()
        .resource_dir()
        .map_err(|e| format!("resource_dir not found: {e}"))?;

    let backend_dir = resource_dir.join("resources").join("backend");

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

pub fn start_backend() -> Result<(), String> {  // ← Change return type
    let (_backend_dir, backend_binary) = resolve_backend_paths()?;

    eprintln!("[RUNTIME] Starting backend: {}", backend_binary.display());

    // On macOS Intel, run arm64 binaries via Rosetta
    #[cfg(all(target_os = "macos", target_arch = "x86_64"))]
    let child = {
        eprintln!("[RUNTIME] Detected Intel Mac - launching arm64 backend via Rosetta");
        Command::new("arch")
            .arg("-arm64")
            .arg(&backend_binary)
            .spawn()
            .map_err(|e| format!("Failed to start backend via Rosetta: {e}"))?
    };

    // On macOS Apple Silicon or Windows, run directly
    #[cfg(not(all(target_os = "macos", target_arch = "x86_64")))]
    let child = {
        Command::new(&backend_binary)
            .spawn()
            .map_err(|e| format!("Failed to start backend: {e}"))?
    };

    // Store the process handle
    *BACKEND_PROCESS.lock().unwrap() = Some(child);
    eprintln!("[RUNTIME] Backend started successfully");
    
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
            let _ = start_backend();  // This now returns Result<(), String>
        }
    });
}

/// Legacy compatibility
pub fn start_watchdog() {
    start_backend_watchdog();
}

fn restart_attempts() -> &'static Mutex<u8> {
    RESTART_ATTEMPTS.get_or_init(|| Mutex::new(0))
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
