//desktop/src-tauri/src/main.rs
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
mod commands;

/// Tauri command: stop the backend and kill any process on port 47321.
/// Called from UpdateBanner.jsx right before downloadAndInstall() so the
/// NSIS installer can overwrite locked DLLs (e.g. VCRUNTIME140.dll).
// >>> UPDATE_CONFIRM_FIX_20261116 BEGIN (async command) <<<
// Was a sync command, so Tauri v2 ran it on the MAIN thread (window frozen
// for lsof + kills + 2 s settle). Now async, with the blocking work in
// spawn_blocking.
#[tauri::command]
async fn stop_backend_for_update() -> String {
    eprintln!("[MAIN] stop_backend_for_update invoked (pre-update cleanup)");
    let res = tauri::async_runtime::spawn_blocking(|| {
        runtime::stop_backend();
        kill_process_on_port(47321);
        // Extra settle time so the OS releases file locks
        std::thread::sleep(Duration::from_millis(2000));
    })
    .await;
    match res {
        Ok(_) => {
            eprintln!("[MAIN] stop_backend_for_update done");
            "ok".to_string()
        }
        Err(e) => {
            eprintln!("[MAIN] stop_backend_for_update failed: {}", e);
            "error".to_string()
        }
    }
}
// >>> UPDATE_CONFIRM_FIX_20261116 END <<<

fn main() {
    eprintln!("[MAIN] ========== MAIN FUNCTION STARTED ==========");
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        // >>> AUTO_UPDATER_20260821 BEGIN <<<
        // In-app updater: check/download/install driven from UpdateBanner.jsx
        // via window.__TAURI__.updater (withGlobalTauri). Endpoint + pubkey
        // live in tauri.conf.json. process plugin provides relaunch().
        .plugin(tauri_plugin_updater::Builder::new().build())
        .plugin(tauri_plugin_process::init())
        // >>> AUTO_UPDATER_20260821 END <<<
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

            // >>> SCALP_UI_SERVE BEGIN <<<
            // The frontend dev server on :3000 (start_frontend_dev_server) was
            // removed here. The backend now serves the built React UI directly
            // on :47321 (see SCALP_UI_SERVE block in backend/api_server.py), so
            // mobile uses one always-on origin / the Tailscale Funnel hostname.
            // To restore the old :3000 dev-server behavior, re-add the
            // runtime::start_frontend_dev_server() match block here (and the two
            // runtime::stop_frontend_dev_server() cleanup calls below).
            // >>> SCALP_UI_SERVE END <<<

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
                    // >>> SCALP_UI_SERVE: removed runtime::stop_frontend_dev_server(); <<<
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
                // >>> SCALP_UI_SERVE: removed runtime::stop_frontend_dev_server(); <<<
            }
        })
        .invoke_handler(tauri::generate_handler![
            stop_backend_for_update,
            commands::scalp_start,
            commands::scalp_stop,
            commands::scalp_restart,
            commands::scalp_status,
            commands::scalp_logs
        ])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}

/// Kill any process listening on the given port (macOS/Linux/Windows)
fn kill_process_on_port(port: u16) {
    #[cfg(any(target_os = "macos", target_os = "linux"))]
    {
        eprintln!("[MAIN] Checking for processes on port {}", port);
        // >>> UPDATE_CONFIRM_FIX_20261116 BEGIN (listeners only) <<<
        // `lsof -ti tcp:PORT` also returns CLIENTS of the port (the
        // webview's own network process, browser tabs on the UI), which
        // then got kill -9. Only the LISTENING process is the backend.
        let output = quiet_command("lsof")
            .args(&["-nP", "-t", &format!("-iTCP:{}", port), "-sTCP:LISTEN"])
            .output();
        // >>> UPDATE_CONFIRM_FIX_20261116 END <<<

        if let Ok(output) = output {
            let pids = String::from_utf8_lossy(&output.stdout);
            // UPDATE_CONFIRM_FIX_20261116: never this process, each pid once
            let self_pid = std::process::id().to_string();
            let mut pid_list: Vec<&str> = pids
                .lines()
                .map(|s| s.trim())
                .filter(|s| !s.is_empty() && *s != self_pid)
                .collect();
            pid_list.sort();
            pid_list.dedup();

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
            // UPDATE_CONFIRM_FIX_20261116: never this process, each pid once (IPv4 + IPv6 rows)
            let self_pid = std::process::id().to_string();
            let mut killed: Vec<String> = Vec::new();

            for line in output_str.lines() {
                if line.contains(&format!(":{}", port)) && line.contains("LISTENING") {
                    if let Some(pid) = line.split_whitespace().last() {
                        if pid == self_pid || killed.iter().any(|k| k == pid) {
                            continue;
                        }
                        killed.push(pid.to_string());
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