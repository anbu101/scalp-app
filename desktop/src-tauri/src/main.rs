#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use tauri::Manager;
use std::process::Command;
use std::thread;
use std::time::Duration;

#[cfg(target_os = "windows")]
use std::os::windows::process::CommandExt;

#[cfg(target_os = "windows")]
const CREATE_NO_WINDOW: u32 = 0x0800_0000;

/// Build a Command that never flashes a console window on Windows.
/// On other platforms this is just Command::new.
fn quiet_command(program: &str) -> Command {
    let mut cmd = Command::new(program);
    #[cfg(target_os = "windows")]
    cmd.creation_flags(CREATE_NO_WINDOW);
    cmd
}

mod runtime;

fn main() {
    eprintln!("[MAIN] ========== MAIN FUNCTION STARTED ==========");
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .setup(|app| {
            eprintln!("[MAIN] ========== SETUP CLOSURE STARTED ==========");
            eprintln!("[MAIN] Scalp app setup started");

            // ----------------------------------
            // 🔒 KILL ANY EXISTING BACKEND ON PORT 47321
            // ----------------------------------
            kill_process_on_port(47321);
            eprintln!("[MAIN] Cleared port 47321");

            // ----------------------------------
            // 🔒 INITIALIZE RUNTIME (PATHS, STATE)
            // ----------------------------------
            let handle = app.handle().clone();
            runtime::init(handle);
            eprintln!("[MAIN] Runtime initialized");

            // ----------------------------------
            // 🔒 START NATIVE BACKEND (ONCE)
            // ----------------------------------
            match runtime::start_backend() {
                Ok(_) => eprintln!("[MAIN] Backend started successfully"),
                Err(e) => {
                    eprintln!("[MAIN] ⚠ Failed to start backend: {}", e);
                }
            }

            // Start frontend dev server for mobile access (works in both debug and release)
            match runtime::start_frontend_dev_server() {
                Ok(_) => eprintln!("[MAIN] Frontend dev server started for mobile access"),
                Err(e) => {
                    eprintln!("[MAIN] ⚠ Failed to start frontend dev server: {}", e);
                    eprintln!("[MAIN] Mobile access will not be available");
                }
            }

            // Give backend time to start
            thread::sleep(Duration::from_millis(2000));
            eprintln!("[MAIN] Waited for backend startup");

            runtime::start_backend_watchdog();
            eprintln!("[MAIN] Backend watchdog started");

            // ----------------------------------
            // 🔒 GET MAIN WINDOW & SETUP CLEANUP
            // ----------------------------------
            let window = app
                .get_webview_window("main")
                .expect("main window not found");

            // Setup cleanup on window close
            window.on_window_event(|event| {
                if let tauri::WindowEvent::CloseRequested { .. } = event {
                    eprintln!("[MAIN] Window closing, cleaning up backend...");
                    kill_process_on_port(47321);
                    runtime::stop_backend();
                    runtime::stop_frontend_dev_server();
                }
            });

            // ----------------------------------
            // 🔒 FORCE DEV URL LOAD (DEV MODE ONLY)
            // ----------------------------------
            #[cfg(debug_assertions)]
            {
                eprintln!("[MAIN] DEV MODE: forcing frontend URL load");
                window
                    .eval("window.location.href = 'http://127.0.0.1:3000';")
                    .expect("failed to load dev URL");
            }

            // ----------------------------------
            // 🔒 API BASE INJECTION (DESKTOP)
            // ----------------------------------
            let api_base = "http://127.0.0.1:47321";

            eprintln!("[MAIN] Preparing to inject API base: {}", api_base);

            // Wait for page to be ready
            thread::sleep(Duration::from_millis(1000));

            // Inject with comprehensive logging
            let injection_script = format!(
                r#"
                console.log('[TAURI] ======= API BASE INJECTION START =======');
                console.log('[TAURI] Injecting API base: {}');
                window.__SCALP_API_BASE__ = '{}';
                console.log('[TAURI] window.__SCALP_API_BASE__ = ', window.__SCALP_API_BASE__);
                console.log('[TAURI] window.__TAURI__ exists:', !!window.__TAURI__);
                console.log('[TAURI] ======= API BASE INJECTION COMPLETE =======');
                "#,
                api_base, api_base
            );

            window
                .eval(&injection_script)
                .expect("failed to inject API base");

            eprintln!("[MAIN] API base injected: {}", api_base);
            eprintln!("[MAIN] Scalp setup completed");

            Ok(())
        })
        .on_window_event(|_window, event| {
            if let tauri::WindowEvent::Destroyed = event {
                eprintln!("[MAIN] Window destroyed, final cleanup...");
                kill_process_on_port(47321);
                runtime::stop_backend();
                runtime::stop_frontend_dev_server();
            }
        })
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}

/// Kill any process listening on the given port (macOS/Linux/Windows)
fn kill_process_on_port(port: u16) {
    #[cfg(any(target_os = "macos", target_os = "linux"))]
    {
        eprintln!("[MAIN] Checking for processes on port {}", port);
        let output = quiet_command("lsof")
            .args(&["-ti", &format!("tcp:{}", port)])
            .output();

        if let Ok(output) = output {
            let pids = String::from_utf8_lossy(&output.stdout);
            let pid_list: Vec<&str> =
                pids.lines().map(|s| s.trim()).filter(|s| !s.is_empty()).collect();

            if pid_list.is_empty() {
                eprintln!("[MAIN] No processes found on port {}", port);
            } else {
                for pid in pid_list {
                    eprintln!("[MAIN] Killing process {} on port {}", pid, port);
                    let kill_result = quiet_command("kill")
                        .args(&["-9", pid])
                        .output();

                    match kill_result {
                        Ok(_) => eprintln!("[MAIN] Successfully killed process {}", pid),
                        Err(e) => eprintln!("[MAIN] Failed to kill process {}: {}", pid, e),
                    }
                }

                thread::sleep(Duration::from_millis(1000));
                eprintln!("[MAIN] Port {} cleaned", port);
            }
        } else {
            eprintln!("[MAIN] Failed to check processes on port {}", port);
        }
    }

    #[cfg(target_os = "windows")]
    {
        eprintln!("[MAIN] Checking for processes on port {}", port);
        let output = quiet_command("netstat")
            .args(&["-ano"])
            .output();

        if let Ok(output) = output {
            let output_str = String::from_utf8_lossy(&output.stdout);
            let mut found = false;

            for line in output_str.lines() {
                if line.contains(&format!(":{}", port)) && line.contains("LISTENING") {
                    if let Some(pid) = line.split_whitespace().last() {
                        found = true;
                        eprintln!("[MAIN] Killing process {} on port {}", pid, port);
                        let kill_result = quiet_command("taskkill")
                            .args(&["/F", "/PID", pid])
                            .output();

                        match kill_result {
                            Ok(_) => eprintln!("[MAIN] Successfully killed process {}", pid),
                            Err(e) => eprintln!("[MAIN] Failed to kill process {}: {}", pid, e),
                        }
                    }
                }
            }

            if !found {
                eprintln!("[MAIN] No processes found on port {}", port);
            }

            thread::sleep(Duration::from_millis(1000));
            eprintln!("[MAIN] Port {} cleaned", port);
        } else {
            eprintln!("[MAIN] Failed to check processes on port {}", port);
        }
    }
}