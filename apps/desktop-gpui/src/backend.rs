//! Supervises the Python backend.
//!
//! The shell spawns the backend as a child process, waits for it to print its
//! `TIKTUV_READY <path>` handshake line, reads the port and session token from
//! that file, and hands them to the UI. On exit the child is killed so a stray
//! backend can never outlive the window.

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

/// The subset of `instance.lock` we read when attaching to a live backend.
#[derive(Debug, Deserialize)]
struct LockPayload {
    pid: u32,
    port: u16,
    token: String,
}

/// Where the backend keeps its data, mirroring `Settings._default_data_dir`.
pub(crate) fn default_data_dir() -> PathBuf {
    if let Ok(dir) = std::env::var("TIKTUV_DATA_DIR") {
        return PathBuf::from(dir);
    }
    if let Some(dir) = portable_data_dir() {
        return dir;
    }
    #[cfg(windows)]
    {
        let base = std::env::var("LOCALAPPDATA").unwrap_or_else(|_| {
            std::env::var("USERPROFILE")
                .map(|p| format!("{p}\\AppData\\Local"))
                .unwrap_or_default()
        });
        return PathBuf::from(base).join("Tiktuv");
    }
    #[cfg(not(windows))]
    {
        let base = std::env::var("XDG_DATA_HOME").unwrap_or_else(|_| {
            std::env::var("HOME")
                .map(|h| format!("{h}/.local/share"))
                .unwrap_or_default()
        });
        return PathBuf::from(base).join("Tiktuv");
    }
}

/// Directory containing the running executable (the packaged build root when
/// a frozen backend sidecar sits next to it).
fn exe_dir() -> Option<PathBuf> {
    std::env::current_exe()
        .ok()?
        .parent()
        .map(|path| path.to_path_buf())
}

/// The frozen backend of a packaged build: `engine\tiktuv-backend.exe` (a
/// folder build, which starts in a second or two), or beside the exe.
fn sidecar_path() -> Option<PathBuf> {
    let dir = exe_dir()?;
    let name = if cfg!(windows) { "tiktuv-backend.exe" } else { "tiktuv-backend" };
    [dir.join("engine").join(name), dir.join(name)]
        .into_iter()
        .find(|p| p.is_file())
}

/// True when this is a packaged build: a frozen backend sidecar sits next to
/// the executable, so everything (backend, data, models, FFmpeg) lives beside it.
fn is_portable() -> bool {
    sidecar_path().is_some()
}

/// Data directory for a packaged build — inside the folder, so the app is
/// relocatable like the Python portable build.
fn portable_data_dir() -> Option<PathBuf> {
    if is_portable() {
        exe_dir().map(|dir| dir.join("data"))
    } else {
        None
    }
}

/// A cheap liveness probe: the backend owns loopback `:port` while it runs.
fn port_is_open(port: u16) -> bool {
    std::net::TcpStream::connect_timeout(
        &std::net::SocketAddr::from(([127, 0, 0, 1], port)),
        Duration::from_secs(2),
    )
    .is_ok()
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
    /// Locate the backend: an explicit `TIKTUV_BACKEND_PATH` override, else the
    /// development virtualenv (`<repo>/.venv/Scripts/python.exe -m tiktuv`).
    fn executable() -> Option<(PathBuf, Vec<String>)> {
        // Packaged build: a frozen backend sidecar next to this executable.
        if let Some(sidecar) = sidecar_path() {
            return Some((sidecar, vec![]));
        }

        // Development: an explicit override.
        if let Ok(path) = std::env::var("TIKTUV_BACKEND_PATH") {
            let path = PathBuf::from(path);
            if path.is_file() {
                return Some((path, vec!["-m".into(), "tiktuv".into()]));
            }
        }

        // Development: walk up from the current directory looking for `.venv`.
        let mut root = std::env::current_dir().ok()?;
        for _ in 0..6 {
            let venv = root
                .join(".venv")
                .join(if cfg!(windows) { "Scripts" } else { "bin" })
                .join(if cfg!(windows) { "python.exe" } else { "python" });
            if venv.is_file() {
                return Some((venv, vec!["-m".into(), "tiktuv".into()]));
            }
            if !root.pop() {
                break;
            }
        }
        None
    }

    pub fn start() -> Result<Self, BackendError> {
        // If a backend is already serving this data directory, attach to it
        // instead of spawning a second instance (which the backend refuses).
        if let Some(handshake) = Self::try_attach() {
            return Ok(Self {
                child: Mutex::new(None),
                handshake,
            });
        }

        Self::spawn()
    }

    /// Attach to a live backend by reading its `instance.lock`, if any.
    fn try_attach() -> Option<Handshake> {
        let data_dir = default_data_dir();
        let raw = std::fs::read_to_string(data_dir.join("instance.lock")).ok()?;
        let lock: LockPayload = serde_json::from_str(&raw).ok()?;
        if !port_is_open(lock.port) {
            return None;
        }
        Some(Handshake {
            version: "1.0.0".to_string(),
            host: "127.0.0.1".to_string(),
            port: lock.port,
            token: lock.token,
            pid: lock.pid,
            data_dir: data_dir.to_string_lossy().into_owned(),
        })
    }

    fn spawn() -> Result<Self, BackendError> {
        let (program, args) = Self::executable().ok_or(BackendError::NotFound)?;

        let mut command = Command::new(&program);
        command
            .args(&args)
            .stdout(Stdio::piped())
            .stderr(if cfg!(debug_assertions) { Stdio::inherit() } else { Stdio::null() })
            .stdin(Stdio::null());

        // Packaged build: keep data, models and FFmpeg inside the folder so the
        // app runs from any path, mirroring the Python portable build.
        if is_portable() {
            if let Some(dir) = exe_dir() {
                command
                    .env("TIKTUV_DATA_DIR", dir.join("data"))
                    .env("TIKTUV_MODELS_DIR", dir.join("models"))
                    .env(
                        "TIKTUV_FFMPEG_PATH",
                        dir.join("tools").join(if cfg!(windows) { "ffmpeg.exe" } else { "ffmpeg" }),
                    )
                    .env(
                        "TIKTUV_FFPROBE_PATH",
                        dir.join("tools").join(if cfg!(windows) { "ffprobe.exe" } else { "ffprobe" }),
                    );
            }
        }

        #[cfg(windows)]
        command.creation_flags(CREATE_NO_WINDOW);

        let mut child = command
            .spawn()
            .map_err(|error| BackendError::Spawn(error.to_string()))?;

        let stdout = child
            .stdout
            .take()
            .ok_or_else(|| BackendError::Spawn("no stdout pipe".into()))?;

        // Block on the single handshake line. Everything else the backend emits
        // goes to stderr, so this stays unambiguous.
        let deadline = Instant::now() + STARTUP_TIMEOUT;
        let mut reader = BufReader::new(stdout);
        let mut line = String::new();
        let handshake_path = loop {
            if Instant::now() > deadline {
                let _ = child.kill();
                let _ = child.wait();
                return Err(BackendError::Timeout(STARTUP_TIMEOUT.as_secs()));
            }
            line.clear();
            match reader.read_line(&mut line) {
                Ok(0) => {
                    let _ = child.kill();
                    let _ = child.wait();
                    return Err(BackendError::Spawn("backend exited during startup".into()));
                }
                Ok(_) => {
                    if let Some(rest) = line.trim().strip_prefix("TIKTUV_READY ") {
                        break PathBuf::from(rest);
                    }
                }
                Err(error) => {
                    let _ = child.kill();
                    let _ = child.wait();
                    return Err(BackendError::Spawn(error.to_string()));
                }
            }
        };

        let raw = std::fs::read_to_string(&handshake_path)
            .map_err(|error| BackendError::Handshake(error.to_string()))?;
        let handshake: Handshake = serde_json::from_str(&raw)
            .map_err(|error| BackendError::Handshake(error.to_string()))?;

        // The backend prints READY before uvicorn binds (app construction and
        // database setup still run), so wait until the port actually accepts.
        while Instant::now() < deadline && !port_is_open(handshake.port) {
            std::thread::sleep(Duration::from_millis(50));
        }

        Ok(Self {
            child: Mutex::new(Some(child)),
            handshake,
        })
    }

    /// Terminate the backend. Idempotent, safe to call from `Drop`.
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

// -- interface preferences ---------------------------------------------------
fn preferences_path() -> PathBuf {
    default_data_dir().join("ui.json")
}

/// Whether the dark theme was chosen last time.
pub fn load_theme_preference() -> bool {
    std::fs::read_to_string(preferences_path())
        .ok()
        .and_then(|raw| serde_json::from_str::<serde_json::Value>(&raw).ok())
        .and_then(|value| value.get("dark").and_then(|v| v.as_bool()))
        .unwrap_or(false)
}

pub fn save_theme_preference(dark: bool) {
    let path = preferences_path();
    if let Some(parent) = path.parent() {
        let _ = std::fs::create_dir_all(parent);
    }
    let _ = std::fs::write(path, serde_json::json!({ "dark": dark }).to_string());
}
