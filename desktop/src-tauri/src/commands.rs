use std::time::Instant;

use crate::runtime::{
    run_runtime_command,
    cap_output,
    RuntimeCommand,
    RuntimeResult,
    manual_stop_flag,
};

#[tauri::command]
pub fn scalp_start() -> RuntimeResult {
    run_runtime_command(RuntimeCommand::Start)
}

#[tauri::command]
pub fn scalp_stop() -> RuntimeResult {
    // Mark manual stop to suppress watchdog restarts
    *manual_stop_flag().lock().unwrap() = Some(Instant::now());
    run_runtime_command(RuntimeCommand::Stop)
}

#[tauri::command]
pub fn scalp_restart() -> RuntimeResult {
    run_runtime_command(RuntimeCommand::Restart)
}

#[tauri::command]
pub fn scalp_status() -> RuntimeResult {
    run_runtime_command(RuntimeCommand::Status)
}

#[tauri::command]
pub fn scalp_logs(lines: Option<usize>) -> RuntimeResult {
    let mut res = run_runtime_command(RuntimeCommand::Logs);

    let max = lines.unwrap_or(200);

    if res.success {
        res.stdout = cap_output(&res.stdout, max);
    }

    res
}
