//! Audio playback for the transcript editor.
//!
//! gpui has no media element, so playback is native: the media file is decoded
//! by `rodio`'s bundled symphonia decoders and played through the default audio
//! output. Video files play their audio track (picture is not rendered).

use std::path::Path;
use std::time::{Duration, Instant};

use rodio::Source as _;

pub const SPEEDS: [f32; 7] = [0.5, 0.75, 1.0, 1.25, 1.5, 1.75, 2.0];
pub const SKIP_SECONDS: f64 = 5.0;

pub struct Player {
    // Keep the output stream alive for the lifetime of the player.
    _stream: rodio::OutputStream,
    handle: rodio::OutputStreamHandle,
    sink: Option<rodio::Sink>,
    pub playing: bool,
    pub speed: f32,
    pub duration: Option<Duration>,
    pub error: Option<String>,
    /// Playback position at the last pause/seek, in seconds.
    position: Duration,
    /// Set while playing, so wall-clock elapsed can be added to `position`.
    started_at: Option<Instant>,
}

impl Player {
    pub fn new() -> Self {
        let (stream, handle) = rodio::OutputStream::try_default()
            .expect("open the default audio output device");
        Self {
            _stream: stream,
            handle,
            sink: None,
            playing: false,
            speed: 1.0,
            duration: None,
            error: None,
            position: Duration::ZERO,
            started_at: None,
        }
    }

    /// Load a media file and prepare it for playback (does not start playing).
    pub fn load(&mut self, path: &Path) {
        self.stop();
        let file = match std::fs::File::open(path) {
            Ok(file) => file,
            Err(e) => {
                self.error = Some(format!("לא ניתן לפתוח את המדיה: {e}"));
                return;
            }
        };
        match rodio::Decoder::new(std::io::BufReader::new(file)) {
            Ok(decoder) => {
                self.duration = decoder.total_duration();
                let Ok(sink) = rodio::Sink::try_new(&self.handle) else {
                    self.error = Some("לא ניתן לפתוח את התקן השמע.".to_string());
                    return;
                };
                sink.set_speed(self.speed);
                sink.append(decoder);
                self.sink = Some(sink);
                self.position = Duration::ZERO;
                self.started_at = None;
                self.error = None;
            }
            Err(e) => {
                self.error = Some(format!("לא ניתן לפענח את המדיה: {e}"));
                self.duration = None;
            }
        }
    }

    pub fn loaded(&self) -> bool {
        self.sink.is_some()
    }

    fn stop(&mut self) {
        if let Some(sink) = self.sink.take() {
            sink.stop();
        }
        self.playing = false;
        self.position = Duration::ZERO;
        self.started_at = None;
    }

    pub fn toggle(&mut self) {
        if self.sink.is_none() {
            return;
        }
        if self.playing {
            self.pause();
        } else {
            self.play();
        }
    }

    pub fn play(&mut self) {
        if let Some(sink) = &self.sink {
            sink.play();
            self.playing = true;
            self.started_at = Some(Instant::now());
        }
    }

    pub fn pause(&mut self) {
        if let Some(sink) = &self.sink {
            self.position = self.position_seconds_raw();
            sink.pause();
            self.playing = false;
            self.started_at = None;
        }
    }

    pub fn seek(&mut self, seconds: f64) {
        if let Some(sink) = &self.sink {
            let target = Duration::from_secs_f64(seconds.max(0.0));
            let _ = sink.try_seek(target);
            self.position = target;
            self.started_at = Some(Instant::now());
        }
    }

    pub fn skip(&mut self, delta: f64) {
        let current = self.position_seconds_raw().as_secs_f64();
        self.seek(current + delta);
    }

    pub fn set_speed(&mut self, speed: f32) {
        self.speed = speed;
        if let Some(sink) = &self.sink {
            sink.set_speed(speed);
        }
    }

    /// Current playback position, accounting for active playback and speed.
    fn position_seconds_raw(&self) -> Duration {
        match (&self.sink, self.started_at) {
            (Some(_), Some(t)) if self.playing => {
                self.position + t.elapsed().mul_f32(self.speed)
            }
            _ => self.position,
        }
    }

    pub fn position_seconds(&self) -> f64 {
        self.position_seconds_raw().as_secs_f64()
    }

    /// True when playback reached the end (only when duration is known).
    pub fn ended(&self) -> bool {
        if !self.playing {
            return false;
        }
        match self.duration {
            Some(d) if self.position_seconds_raw() >= d => true,
            _ => false,
        }
    }
}
