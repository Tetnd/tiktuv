//! Audio playback for the transcript editor.
//!
//! gpui has no media element, so playback is native: the media file is decoded
//! by `rodio`'s bundled symphonia decoders and played through the default audio
//! output. Video files play their audio track (picture is not rendered).
//!
//! The position comes from the sink itself ([`rodio::Sink::get_pos`]), never
//! from a wall clock. A wall clock drifts from the audio on every pause, seek
//! and speed change, and the transcript highlight follows this position — so
//! the two have to be the same number, not two estimates of it.

use std::path::{Path, PathBuf};
use std::time::Duration;

use rodio::Source as _;

pub const SPEEDS: [f32; 7] = [0.5, 0.75, 1.0, 1.25, 1.5, 1.75, 2.0];
pub const SKIP_SECONDS: f64 = 5.0;

pub struct Player {
    // Keep the output stream alive for the lifetime of the player.
    _stream: rodio::OutputStream,
    handle: rodio::OutputStreamHandle,
    sink: Option<rodio::Sink>,
    /// The loaded file, kept so a seek can rebuild the sink if the decoder
    /// refuses to seek in place.
    path: Option<PathBuf>,
    pub playing: bool,
    pub speed: f32,
    pub duration: Option<Duration>,
    pub error: Option<String>,
    /// Where this sink started. A sink that was rebuilt at a position counts
    /// from zero again, so its own clock is an offset from this point.
    offset: Duration,
}

impl Player {
    pub fn new() -> Self {
        let (stream, handle) =
            rodio::OutputStream::try_default().expect("open the default audio output device");
        Self {
            _stream: stream,
            handle,
            sink: None,
            path: None,
            playing: false,
            speed: 1.0,
            duration: None,
            error: None,
            offset: Duration::ZERO,
        }
    }

    /// Load a media file and prepare it for playback (does not start playing).
    pub fn load(&mut self, path: &Path) {
        self.stop();
        self.path = Some(path.to_path_buf());
        self.duration = None;
        match self.build_sink(path, Duration::ZERO) {
            Ok(duration) => {
                self.duration = duration;
                self.offset = Duration::ZERO;
                self.error = None;
            }
            Err(message) => {
                self.error = Some(message);
                self.duration = None;
            }
        }
    }

    /// Decode `path`, drop everything before `start`, and hand it to a fresh
    /// sink. The sink is left paused: a rodio sink plays as soon as a source
    /// is appended, which would start the audio the moment a transcript is
    /// opened while the interface still shows a play button.
    fn build_sink(&mut self, path: &Path, start: Duration) -> Result<Option<Duration>, String> {
        let file = std::fs::File::open(path)
            .map_err(|e| format!("לא ניתן לפתוח את המדיה: {e}"))?;
        let decoder = rodio::Decoder::new(std::io::BufReader::new(file))
            .map_err(|e| format!("לא ניתן לפענח את המדיה: {e}"))?;
        let duration = decoder.total_duration();

        let sink = rodio::Sink::try_new(&self.handle)
            .map_err(|_| "לא ניתן לפתוח את התקן השמע.".to_string())?;
        sink.pause();
        sink.set_speed(self.speed);
        if start.is_zero() {
            sink.append(decoder);
        } else {
            sink.append(decoder.skip_duration(start));
        }
        self.sink = Some(sink);
        Ok(duration)
    }

    pub fn loaded(&self) -> bool {
        self.sink.is_some()
    }

    fn stop(&mut self) {
        if let Some(sink) = self.sink.take() {
            sink.stop();
        }
        self.playing = false;
        self.offset = Duration::ZERO;
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
        }
    }

    pub fn pause(&mut self) {
        if let Some(sink) = &self.sink {
            sink.pause();
            self.playing = false;
        }
    }

    /// Jump to `seconds`.
    ///
    /// Seeking in place is what the symphonia decoders offer, and it is
    /// instant. Not every container supports it, though, and a failed seek
    /// used to leave the audio where it was while the interface moved on — so
    /// when the decoder says no, the sink is rebuilt at the target instead.
    pub fn seek(&mut self, seconds: f64) {
        let target = Duration::from_secs_f64(seconds.max(0.0));
        let target = match self.duration {
            // Seeking past the end stops the sound on some decoders; stay a
            // moment short of it so the player still has something to play.
            Some(total) if target >= total => total.saturating_sub(Duration::from_millis(200)),
            _ => target,
        };

        if let Some(sink) = &self.sink
            && sink.try_seek(target).is_ok()
        {
            // The sink now counts from the target itself.
            self.offset = Duration::ZERO;
            return;
        }

        let Some(path) = self.path.clone() else {
            return;
        };
        let was_playing = self.playing;
        self.stop();
        match self.build_sink(&path, target) {
            Ok(duration) => {
                if duration.is_some() {
                    self.duration = duration;
                }
                self.offset = target;
                self.error = None;
                if was_playing {
                    self.play();
                }
            }
            Err(message) => self.error = Some(message),
        }
    }

    pub fn skip(&mut self, delta: f64) {
        self.seek(self.position_seconds() + delta);
    }

    pub fn set_speed(&mut self, speed: f32) {
        self.speed = speed;
        if let Some(sink) = &self.sink {
            sink.set_speed(speed);
        }
    }

    /// Current playback position in seconds, as the sink itself reports it
    /// (which already accounts for pauses, seeks and the playback speed).
    pub fn position_seconds(&self) -> f64 {
        match &self.sink {
            Some(sink) => (self.offset + sink.get_pos()).as_secs_f64(),
            None => 0.0,
        }
    }

    /// True when playback reached the end.
    pub fn ended(&self) -> bool {
        let Some(sink) = &self.sink else {
            return false;
        };
        if sink.empty() {
            return true;
        }
        match self.duration {
            Some(total) => self.offset + sink.get_pos() >= total,
            None => false,
        }
    }
}
