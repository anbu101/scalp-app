use tauri::Manager;
use std::process::Command;
use std::thread;
use std::time::Duration;
use tauri_plugin_shell; // 🔧 CHANGED LINE (replaced a blank line)
use tauri_plugin_updater::UpdaterExt;

mod runtime;

#[tauri::command]
async fn check_for_updates(app: tauri::AppHandle) -> Result<(), String> {
    eprintln!("[UPDATER] Manual check triggered");
    match app.updater() {
        Ok(updater) => {
            updater
                .check()
                .await
                .map_err(|e| format!("Update check failed: {}", e))?;
            Ok(())
        }
        Err(e) => Err(format!("Updater unavailable: {}", e)),
    }
}

fn main() {
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_updater::Builder::new().build())
        .invoke_handler(tauri::generate_handler![check_for_updates])
        .setup(|app| {
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
            runtime::start_backend();
            eprintln!("[MAIN] Backend start requested");

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

            // ==================================================
            // 🔥 OTA UPDATE CHECK (ONLY ADDITION)
            // ==================================================
            {
                let app_handle = app.handle().clone();
                tauri::async_runtime::spawn(async move {
                    eprintln!("[UPDATER] Checking for updates...");
                    match app_handle.updater() {
                        Ok(updater) => {
                            if let Err(e) = updater.check().await {
                                eprintln!("[UPDATER] Update check failed: {}", e);
                            }
                        }
                        Err(e) => {
                            eprintln!("[UPDATER] Updater unavailable: {}", e);
                        }
                    }
                });
            }
            // ==================================================

            eprintln!("[MAIN] Scalp setup completed");

            // ----------------------------------
            // 🔁 EXPLICIT OTA UPDATE CHECK (TAURI v2 – FIXED)
            // ----------------------------------
            let app_handle = app.handle().clone();

            tauri::async_runtime::spawn(async move {
                eprintln!("[UPDATER] Explicit check_for_updates() called");

                match app_handle.updater() {
                    Ok(updater) => {
                        match updater.check().await {
                            Ok(Some(update)) => {
                                eprintln!(
                                    "[UPDATER] Update available: {} -> {}",
                                    update.current_version,
                                    update.version
                                );

                                // This will show the dialog automatically (because dialog=true)
                                
                                // ==========================================
                                // 🔴 CRITICAL: STOP BACKEND BEFORE INSTALL
                                // ==========================================
                                eprintln!("[UPDATER] Stopping backend before update install...");
                                runtime::stop_backend();
                                kill_process_on_port(47321);
                                thread::sleep(Duration::from_secs(2));
                                // ==========================================

                                if let Err(e) = update
                                    .download_and_install(
                                        |chunk, total| {
                                            eprintln!(
                                                "[UPDATER] Downloaded {} bytes{}",
                                                chunk,
                                                total.map(|t| format!(" / {}", t)).unwrap_or_default()
                                            );
                                        },
                                        || {
                                            eprintln!("[UPDATER] Download finished, installing update...");
                                        },
                                    )
                                    .await
                                {
                                    eprintln!("[UPDATER] Failed to install update: {:?}", e);
                                }

                            }
                            Ok(None) => {
                                eprintln!("[UPDATER] No updates available");
                            }
                            Err(e) => {
                                eprintln!("[UPDATER] Update check failed: {:?}", e);
                            }
                        }
                    }
                    Err(e) => {
                        eprintln!("[UPDATER] Failed to init updater: {:?}", e);
                    }
                }
            });

            Ok(())
        })
        .on_window_event(|_window, event| {
            // Global cleanup handler (backup)
            if let tauri::WindowEvent::Destroyed = event {
                eprintln!("[MAIN] Window destroyed, final cleanup...");
                kill_process_on_port(47321);
                runtime::stop_backend();
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
        let output = Command::new("lsof")
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
                    let kill_result = Command::new("kill")
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
        let output = Command::new("netstat")
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
                        let kill_result = Command::new("taskkill")
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
