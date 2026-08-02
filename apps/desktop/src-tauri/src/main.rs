// Hide the console window on Windows release builds.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod backend;

use backend::BackendProcess;
use tauri::{Manager, RunEvent, State, WindowEvent};

/// Connection details handed to the web view so it can reach the local API.
#[derive(serde::Serialize, Clone)]
struct Connection {
    base_url: String,
    token: String,
    version: String,
    data_dir: String,
}

struct AppState {
    backend: BackendProcess,
}

/// The frontend calls this once on load. The token never touches disk on the
/// UI side and is never written into the bundle.
#[tauri::command]
fn connection(state: State<'_, AppState>) -> Connection {
    let handshake = &state.backend.handshake;
    Connection {
        base_url: handshake.base_url(),
        token: handshake.token.clone(),
        version: handshake.version.clone(),
        data_dir: handshake.data_dir.clone(),
    }
}

/// Ask the backend to shut down cleanly (also handled on window close).
#[tauri::command]
fn shutdown_backend(state: State<'_, AppState>) {
    state.backend.shutdown();
}

fn main() {
    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_shell::init())
        .setup(|app| {
            let resource_dir = app.path().resource_dir().ok();
            let backend = BackendProcess::start(resource_dir).map_err(|error| {
                eprintln!("Tiktuv backend failed to start: {error}");
                error
            })?;
            app.manage(AppState { backend });
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![connection, shutdown_backend])
        .on_window_event(|window, event| {
            if let WindowEvent::Destroyed = event {
                if let Some(state) = window.app_handle().try_state::<AppState>() {
                    state.backend.shutdown();
                }
            }
        })
        .build(tauri::generate_context!())
        .expect("failed to build the Tiktuv application")
        .run(|app_handle, event| {
            if let RunEvent::ExitRequested { .. } = event {
                if let Some(state) = app_handle.try_state::<AppState>() {
                    state.backend.shutdown();
                }
            }
        });
}
