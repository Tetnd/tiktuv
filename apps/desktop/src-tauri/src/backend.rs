//! Supervises the bundled Python backend.
//!
//! The shell spawns the backend as a child process, waits for it to print its
//! `TIKTUV_READY <path>` handshake line, reads the port and session token from
//! that file, and hands them to the web view. On exit the child is killed so a
//! stray backend can never outlive the window.

use std::io::{BufRead, BufReader};
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use std::time::{Duration, Instant};

use serde::{Deserialize, Serialize};

#[cfg(windows)]
use std::os::windows::process::CommandExt;

/// Detach the child from the console so no window flashes on Windows.
#[cfg(windows)]
const CREATE_NO_WINDOW: u32 = 0x0800_0000;

/// How long to wait for the backend to become ready before giving up.
const STARTUP_TIMEOUT: Duration = Duration::from_secs(90);

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Handshake {
    pub version: String,
    pub host: String,
    pub port: u16,
    pub token: String,
    pub pid: u32,
    pub data_dir: String,
}

impl Handshake {
    pub fn base_url(&self) -> String {
        format!("http://{}:{}", self.host, self.port)
    }
}

#[derive(Debug, thiserror::Error)]
pub enum BackendError {
    #[error("could not find the Tiktuv backend executable")]
    NotFound,
    #[error("failed to start the backend: {0}")]
    Spawn(String),
    #[error("the backend did not become ready within {0} seconds")]
    Timeout(u64),
    #[error("the backend handshake file was unreadable: {0}")]
    Handshake(String),
}

pub struct BackendProcess {
    child: Mutex<Option<Child>>,
    pub handshake: Handshake,
}

impl BackendProcess {
    /// Locate the backend: the bundled sidecar in a packaged build, or the
    /// development virtualenv when running `tauri dev`.
    fn executable(resource_dir: Option<PathBuf>) -> Option<(PathBuf, Vec<String>)> {
        if let Some(dir) = resource_dir {
            let bundled = dir.join(if cfg!(windows) { "tiktuv-backend.exe" } else { "tiktuv-backend" });
            if bundled.is_file() {
                return Some((bundled, vec![]));
            }
        }

        // Development: repo-root .venv, three levels up from src-tauri.
        let mut root = std::env::current_dir().ok()?;
        for _ in 0..4 {
            let venv = root.join(".venv").join(if cfg!(windows) { "Scripts" } else { "bin" }).join(
                if cfg!(windows) { "python.exe" } else { "python" },
            );
            if venv.is_file() {
                return Some((venv, vec!["-m".into(), "tiktuv".into()]));
            }
            if !root.pop() {
                break;
            }
        }
        None
    }

    pub fn start(resource_dir: Option<PathBuf>) -> Result<Self, BackendError> {
        let (program, args) = Self::executable(resource_dir).ok_or(BackendError::NotFound)?;

        let mut command = Command::new(&program);
        command
            .args(&args)
            .stdout(Stdio::piped())
            .stderr(Stdio::inherit())
            .stdin(Stdio::null());

        #[cfg(windows)]
        command.creation_flags(CREATE_NO_WINDOW);

        let mut child = command
            .spawn()
            .map_err(|error| BackendError::Spawn(error.to_string()))?;

        let stdout = child
            .stdout
            .take()
            .ok_or_else(|| BackendError::Spawn("no stdout pipe".into()))?;

        // Block on the single handshake line. Everything else the backend
        // emits goes to stderr, so this stays unambiguous.
        let deadline = Instant::now() + STARTUP_TIMEOUT;
        let mut reader = BufReader::new(stdout);
        let mut line = String::new();
        let handshake_path = loop {
            if Instant::now() > deadline {
                let _ = child.kill();
                return Err(BackendError::Timeout(STARTUP_TIMEOUT.as_secs()));
            }
            line.clear();
            match reader.read_line(&mut line) {
                Ok(0) => {
                    let _ = child.kill();
                    return Err(BackendError::Spawn("backend exited during startup".into()));
                }
                Ok(_) => {
                    if let Some(rest) = line.trim().strip_prefix("TIKTUV_READY ") {
                        break PathBuf::from(rest);
                    }
                }
                Err(error) => {
                    let _ = child.kill();
                    return Err(BackendError::Spawn(error.to_string()));
                }
            }
        };

        let raw = std::fs::read_to_string(&handshake_path)
            .map_err(|error| BackendError::Handshake(error.to_string()))?;
        let handshake: Handshake = serde_json::from_str(&raw)
            .map_err(|error| BackendError::Handshake(error.to_string()))?;

        Ok(Self {
            child: Mutex::new(Some(child)),
            handshake,
        })
    }

    /// Terminate the backend. Idempotent, and safe to call from a window-close
    /// handler and from `Drop`.
    pub fn shutdown(&self) {
        if let Ok(mut guard) = self.child.lock() {
            if let Some(mut child) = guard.take() {
                let _ = child.kill();
                let _ = child.wait();
            }
        }
    }
}

impl Drop for BackendProcess {
    fn drop(&mut self) {
        self.shutdown();
    }
}
