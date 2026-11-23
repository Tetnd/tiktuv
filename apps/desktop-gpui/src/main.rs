// No console window in release builds; the backend logs to a file.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

//! Tiktuv (תכתוב) — native desktop shell, built on gpui-kit.
//!
//! Supervises the Python FastAPI backend and renders the Hebrew-first UI.

mod api;
mod app;
mod backend;
mod dialogs;
mod diff;
mod editor;
mod format;
mod history;
mod models;
mod player;
mod rtl_field;
mod rtl_text;
mod queue;
mod seek_bar;
mod shell;
mod speaker_dialog;
mod settings;
mod system;
mod ui;

use gpui_kit::AppContext as _;
use gpui_kit::base::LayoutDirection;
use gpui_kit::{Bounds, WindowBounds, WindowOptions, px, size};

fn main() {
    // A GUI build has no console, so a crash would vanish without a trace.
    // Keep the message where the backend's own logs live.
    std::panic::set_hook(Box::new(|info| {
        let dir = backend::default_data_dir().join("logs");
        let _ = std::fs::create_dir_all(&dir);
        let line = format!("{info}
{}
", std::backtrace::Backtrace::force_capture());
        let _ = std::fs::write(dir.join("desktop-panic.log"), line);
    }));
    // reqwest needs a Tokio reactor; run one on a background thread and keep a
    // handle so API calls can hop onto it from GPUI's own executor.
    let rt = tokio::runtime::Builder::new_multi_thread()
        .enable_all()
        .build()
        .expect("build tokio runtime");
    let handle = rt.handle().clone();
    std::thread::spawn(move || {
        rt.block_on(std::future::pending::<()>());
    });

    // Start the Python backend on a background thread so the window opens at
    // once; the UI shows "connecting" until the handshake arrives.
    let api = api::Api::new(handle);
    let (backend_tx, backend_rx) = async_channel::bounded(1);
    std::thread::spawn(move || {
        let _ = backend_tx.send_blocking(backend::BackendProcess::start());
    });

    gpui_kit::application()
        .with_assets(gpui_kit::assets::Assets)
        .run(move |cx| {
            gpui_kit::init(cx);
            rtl_field::init(cx);
            // Restore the light/dark choice from last time.
            ui::apply_theme(backend::load_theme_preference(), cx);
            // The whole interface is Hebrew and reads right-to-left.
            LayoutDirection::Rtl.set_global(cx);

            // Size the window ourselves. GPUI's default is a fixed 1536×1095
            // clipped only to the display's *full* size, so on a 1080p screen
            // (or any display scaled above 100%) it opens taller than the
            // desktop and the bottom of the app sits under the taskbar.
            //
            // `Bounds::centered` clamps to the display's visible bounds — the
            // work area, with the taskbar already excluded — so this asks for a
            // comfortable size and silently gets less when there is less.
            let bounds = Bounds::centered(None, size(px(1180.), px(820.)), cx);
            // The minimum is clamped the same way: a minimum larger than the
            // screen is a window that cannot be resized to fit.
            let min_size = size(
                bounds.size.width.min(px(860.)),
                bounds.size.height.min(px(600.)),
            );
            let options = WindowOptions {
                window_bounds: Some(WindowBounds::Windowed(bounds)),
                window_min_size: Some(min_size),
                ..Default::default()
            };

            gpui_kit::open_window(options, cx, |window, cx| {
                window.set_window_title("תכתוב");
                cx.new(|cx| app::App::new(api.clone(), backend_rx, window, cx))
            })
            .expect("open window");
        });
}
