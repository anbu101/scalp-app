use tauri::AppHandle;
use std::path::PathBuf;

pub struct ScalpPaths {
    pub resource_dir: PathBuf,
    pub backend_dir: PathBuf,
    pub python_bin: PathBuf,
    pub backend_entry: PathBuf,
    pub user_data_dir: PathBuf,
}

pub fn resolve_paths(app: &AppHandle) -> ScalpPaths {
    let resource_dir = app
        .path_resolver()
        .resource_dir()
        .expect("resource_dir not found");

    let backend_dir = resource_dir.join("backend");

    let python_bin = backend_dir.join("venv/bin/python");
    let backend_entry = backend_dir.join("app/api_server.py");

    let user_data_dir = app
        .path_resolver()
        .app_data_dir()
        .expect("app_data_dir not found");

    ScalpPaths {
        resource_dir,
        backend_dir,
        python_bin,
        backend_entry,
        user_data_dir,
    }
}
