// Tauri v2 entrypoint for Scalp desktop app

mod runtime;
mod commands;

use tauri::{Manager, WindowEvent};
use tauri::tray::TrayIconBuilder;
use tauri::menu::{Menu, MenuItem};

pub fn run() {
    // -------------------------------------------------
    // 🔁 Start backend health watchdog (PROD ONLY)
    // -------------------------------------------------

    tauri::Builder::default()
        // -------------------------------
        // Setup
        // -------------------------------
        .setup(|app| {
            let window = app
                .get_webview_window("main")
                .expect("main window not found");

            // -------------------------------------------------
            // 🚀 STEP 2: Auto-start backend on app launch
            // -------------------------------------------------
            println!("[APP] Auto-starting backend...");
            //runtime::start_backend();

            // -------------------------------
            // Tray Menu (Tauri v2)
            // -------------------------------
            let show = MenuItem::with_id(
                app,
                "show",
                "Show Scalp",
                true,
                None::<&str>,
            )?;

            let hide = MenuItem::with_id(
                app,
                "hide",
                "Hide Scalp",
                true,
                None::<&str>,
            )?;

            let quit = MenuItem::with_id(
                app,
                "quit",
                "Quit Scalp",
                true,
                None::<&str>,
            )?;

            let menu = Menu::with_items(app, &[&show, &hide, &quit])?;

            TrayIconBuilder::new()
                .menu(&menu)
                .on_menu_event(|app, event| {
                    let window = app.get_webview_window("main");

                    match event.id().as_ref() {
                        "show" => {
                            if let Some(w) = window {
                                let _ = w.show();
                                let _ = w.set_focus();
                            }
                        }
                        "hide" => {
                            if let Some(w) = window {
                                let _ = w.hide();
                            }
                        }
                        "quit" => {
                            // TODO (next step):
                            // runtime::stop_backend_gracefully();
                            std::process::exit(0);
                        }
                        _ => {}
                    }
                })
                .build(app)?;

            // macOS UX: show window initially
            let _ = window.show();

            Ok(())
        })

        // -------------------------------
        // Window Close → Hide to Tray
        // -------------------------------
        .on_window_event(|window, event| {
            if let WindowEvent::CloseRequested { api, .. } = event {
                let _ = window.hide();
                api.prevent_close();
            }
        })

        // -------------------------------
        // IPC Commands
        // -------------------------------
        .invoke_handler(tauri::generate_handler![
            commands::scalp_start,
            commands::scalp_stop,
            commands::scalp_restart,
            commands::scalp_status,
            commands::scalp_logs
        ])

        // -------------------------------
        // Run
        // -------------------------------
        .run(tauri::generate_context!())
        .expect("error while running Scalp");
}
