//! Typed client for the local FastAPI backend.
//!
//! Every request carries the session bearer token as an `X-Tiktuv-Token` header.
//! Requests run on a Tokio runtime (reqwest requires one); the results are
//! bridged back to GPUI's executor through an executor-agnostic channel, so the
//! UI never blocks and never touches a Tokio reactor itself.

use std::future::Future;

use reqwest::{RequestBuilder, Response};
use serde::de::DeserializeOwned;
use serde::Serialize;

use crate::models::*;

/// A backend error, carrying the Hebrew message and remedy for display.
#[derive(Debug, Clone)]
pub struct ApiError {
    pub code: String,
    pub message: String,
    pub remedy: String,
    pub detail: String,
    pub status: u16,
}

impl std::fmt::Display for ApiError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "{}: {}", self.code, self.detail)
    }
}

impl ApiError {
    fn unreachable() -> Self {
        Self {
            code: "BACKEND_UNREACHABLE".to_string(),
            message: "לא ניתן להתחבר לשירות המקומי.".to_string(),
            remedy: "ודאו שהיישום פועל, ונסו להפעיל אותו מחדש.".to_string(),
            detail: String::new(),
            status: 0,
        }
    }

    fn invalid(detail: String, status: u16) -> Self {
        Self {
            code: "INVALID_RESPONSE".to_string(),
            message: "התקבלה תשובה בלתי צפויה מהשירות המקומי.".to_string(),
            remedy: "נסו שוב, או בדקו את יומן היישום.".to_string(),
            detail,
            status,
        }
    }

    fn from_body(status: u16, body: &str) -> Self {
        #[derive(serde::Deserialize)]
        struct Payload {
            #[serde(default)]
            code: String,
            #[serde(default)]
            message: String,
            #[serde(default)]
            remedy: String,
            #[serde(default)]
            detail: serde_json::Value,
        }
        if let Ok(p) = serde_json::from_str::<Payload>(body) {
            let detail = if p.detail.is_string() {
                p.detail.as_str().unwrap_or("").to_string()
            } else {
                p.detail.to_string()
            };
            return Self {
                code: if p.code.is_empty() { "INTERNAL".into() } else { p.code },
                message: if p.message.is_empty() {
                    "אירעה שגיאה בלתי צפויה.".into()
                } else {
                    p.message
                },
                remedy: if p.remedy.is_empty() {
                    "נסו שוב, או בדקו את יומן היישום.".into()
                } else {
                    p.remedy
                },
                detail,
                status,
            };
        }
        Self::invalid(body.to_string(), status)
    }
}

#[derive(Clone)]
pub struct Api {
    http: reqwest::Client,
    base_url: String,
    token: String,
    rt: tokio::runtime::Handle,
}

impl Api {
    pub fn new(base_url: String, token: String, rt: tokio::runtime::Handle) -> Self {
        let http = reqwest::Client::builder().build().expect("build http client");
        Self {
            http,
            base_url,
            token,
            rt,
        }
    }

    pub fn base_url(&self) -> &str {
        &self.base_url
    }

    pub fn rt(&self) -> &tokio::runtime::Handle {
        &self.rt
    }

    fn url(&self, path: &str) -> String {
        format!("{}{}", self.base_url, path)
    }

    fn get(&self, path: &str) -> RequestBuilder {
        self.http
            .get(self.url(path))
            .header("X-Tiktuv-Token", self.token.as_str())
    }

    fn post_json(&self, path: &str, body: impl Serialize) -> RequestBuilder {
        self.http
            .post(self.url(path))
            .header("X-Tiktuv-Token", self.token.as_str())
            .json(&body)
    }

    fn put_json(&self, path: &str, body: impl Serialize) -> RequestBuilder {
        self.http
            .put(self.url(path))
            .header("X-Tiktuv-Token", self.token.as_str())
            .json(&body)
    }

    fn delete(&self, path: &str) -> RequestBuilder {
        self.http
            .delete(self.url(path))
            .header("X-Tiktuv-Token", self.token.as_str())
    }

    /// Drive a request future on the Tokio runtime and await it on this executor.
    async fn run<T, F>(&self, f: F) -> Result<T, ApiError>
    where
        T: Send + 'static,
        F: Future<Output = Result<T, ApiError>> + Send + 'static,
    {
        let (tx, rx) = async_channel::bounded(1);
        let handle = self.rt.clone();
        handle.spawn(async move {
            let _ = tx.send(f.await).await;
        });
        match rx.recv().await {
            Ok(inner) => inner,
            Err(_) => Err(ApiError::unreachable()),
        }
    }

    async fn parse<T: DeserializeOwned>(resp: Response) -> Result<T, ApiError> {
        let status = resp.status().as_u16();
        if !resp.status().is_success() {
            let body = resp.text().await.unwrap_or_default();
            return Err(ApiError::from_body(status, &body));
        }
        resp.json::<T>()
            .await
            .map_err(|e| ApiError::invalid(e.to_string(), status))
    }

    async fn parse_ok(resp: Response) -> Result<(), ApiError> {
        let status = resp.status().as_u16();
        if !resp.status().is_success() {
            let body = resp.text().await.unwrap_or_default();
            return Err(ApiError::from_body(status, &body));
        }
        Ok(())
    }

    // -- health -----------------------------------------------------------
    pub async fn health(&self) -> Result<(), ApiError> {
        let req = self.get("/api/health");
        self.run(async move { Self::parse_ok(req.send().await.map_err(|_| ApiError::unreachable())?).await })
            .await
    }

    // -- jobs -------------------------------------------------------------
    pub async fn list_jobs(&self) -> Result<Vec<JobSummary>, ApiError> {
        let req = self.get("/api/jobs");
        self.run(async move {
            let resp = req.send().await.map_err(|_| ApiError::unreachable())?;
            Self::parse(resp).await
        })
        .await
    }

    pub async fn create_job(
        &self,
        media_token: &str,
        settings: &TranscriptionSettings,
        autostart: bool,
    ) -> Result<JobDetail, ApiError> {
        let req = self.post_json(
            "/api/jobs",
            CreateJobRequest {
                media_token: media_token.to_string(),
                settings: settings.clone(),
                autostart,
            },
        );
        self.run(async move {
            let resp = req.send().await.map_err(|_| ApiError::unreachable())?;
            Self::parse(resp).await
        })
        .await
    }

    pub async fn cancel_job(&self, id: &str) -> Result<(), ApiError> {
        let req = self.post_json(&format!("/api/jobs/{id}/cancel"), serde_json::json!({}));
        self.run(async move {
            let resp = req.send().await.map_err(|_| ApiError::unreachable())?;
            Self::parse_ok(resp).await
        })
        .await
    }

    pub async fn retry_job(&self, id: &str) -> Result<JobDetail, ApiError> {
        let req = self.post_json(
            &format!("/api/jobs/{id}/retry?resume=true"),
            serde_json::json!({}),
        );
        self.run(async move {
            let resp = req.send().await.map_err(|_| ApiError::unreachable())?;
            Self::parse(resp).await
        })
        .await
    }

    pub async fn delete_job(&self, id: &str, delete_files: bool) -> Result<(), ApiError> {
        let req = self.delete(&format!("/api/jobs/{id}?delete_files={delete_files}"));
        self.run(async move {
            let resp = req.send().await.map_err(|_| ApiError::unreachable())?;
            Self::parse_ok(resp).await
        })
        .await
    }

    pub async fn clear_completed(&self) -> Result<u64, ApiError> {
        let req = self.post_json("/api/jobs/clear/completed", serde_json::json!({}));
        self.run(async move {
            let resp = req.send().await.map_err(|_| ApiError::unreachable())?;
            #[derive(serde::Deserialize)]
            struct R {
                removed: u64,
            }
            let r: R = Self::parse(resp).await?;
            Ok(r.removed)
        })
        .await
    }

    // -- media ------------------------------------------------------------
    pub async fn import_media(&self, path: &str) -> Result<ImportedMedia, ApiError> {
        let req = self.post_json("/api/media/import", serde_json::json!({ "path": path }));
        self.run(async move {
            let resp = req.send().await.map_err(|_| ApiError::unreachable())?;
            Self::parse(resp).await
        })
        .await
    }

    /// Stream a URL (a local media file) to disk without holding it in memory.
    pub async fn download(&self, url: &str, dest: &std::path::Path) -> Result<(), ApiError> {
        let req = self.http.get(url.to_string());
        let dest = dest.to_path_buf();
        self.run(async move {
            let resp = req.send().await.map_err(|_| ApiError::unreachable())?;
            let status = resp.status().as_u16();
            if !resp.status().is_success() {
                let body = resp.text().await.unwrap_or_default();
                return Err(ApiError::from_body(status, &body));
            }
            use futures_util::StreamExt;
            use tokio::io::AsyncWriteExt;
            let mut file = tokio::fs::File::create(&dest)
                .await
                .map_err(|e| ApiError::invalid(e.to_string(), status))?;
            let mut stream = resp.bytes_stream();
            while let Some(chunk) = stream.next().await {
                let chunk = chunk.map_err(|e| ApiError::invalid(e.to_string(), status))?;
                file.write_all(&chunk)
                    .await
                    .map_err(|e| ApiError::invalid(e.to_string(), status))?;
            }
            Ok(())
        })
        .await
    }

    // -- media (Zoom import) ---------------------------------------------
    pub async fn import_from_url(
        &self,
        url: &str,
        passcode: Option<&str>,
    ) -> Result<UrlFetchStatus, ApiError> {
        let req = self.post_json(
            "/api/media/from-url",
            serde_json::json!({ "url": url, "passcode": passcode }),
        );
        self.run(async move {
            let resp = req.send().await.map_err(|_| ApiError::unreachable())?;
            Self::parse(resp).await
        })
        .await
    }

    pub async fn url_import_status(&self) -> Result<UrlFetchStatus, ApiError> {
        let req = self.get("/api/media/from-url");
        self.run(async move {
            let resp = req.send().await.map_err(|_| ApiError::unreachable())?;
            Self::parse(resp).await
        })
        .await
    }

    pub async fn cancel_url_import(&self) -> Result<UrlFetchStatus, ApiError> {
        let req = self.delete("/api/media/from-url");
        self.run(async move {
            let resp = req.send().await.map_err(|_| ApiError::unreachable())?;
            Self::parse(resp).await
        })
        .await
    }

    // -- models -----------------------------------------------------------
    pub async fn models(&self) -> Result<Vec<ModelInfo>, ApiError> {
        let req = self.get("/api/models");
        self.run(async move {
            let resp = req.send().await.map_err(|_| ApiError::unreachable())?;
            Self::parse(resp).await
        })
        .await
    }

    // -- system -----------------------------------------------------------
    pub async fn system(&self) -> Result<SystemInfo, ApiError> {
        let req = self.get("/api/system");
        self.run(async move {
            let resp = req.send().await.map_err(|_| ApiError::unreachable())?;
            Self::parse(resp).await
        })
        .await
    }

    // -- models -----------------------------------------------------------
    pub async fn download_model(&self, model_id: &str) -> Result<ModelDownloadStatus, ApiError> {
        let req = self.post_json(
            "/api/models/download",
            serde_json::json!({ "model_id": model_id }),
        );
        self.run(async move {
            let resp = req.send().await.map_err(|_| ApiError::unreachable())?;
            Self::parse(resp).await
        })
        .await
    }

    pub async fn download_status(&self) -> Result<ModelDownloadStatus, ApiError> {
        let req = self.get("/api/models/download");
        self.run(async move {
            let resp = req.send().await.map_err(|_| ApiError::unreachable())?;
            Self::parse(resp).await
        })
        .await
    }

    pub async fn cancel_download(&self) -> Result<ModelDownloadStatus, ApiError> {
        let req = self.delete("/api/models/download");
        self.run(async move {
            let resp = req.send().await.map_err(|_| ApiError::unreachable())?;
            Self::parse(resp).await
        })
        .await
    }

    pub async fn delete_model(&self, model_id: &str) -> Result<u64, ApiError> {
        // `:path` route — the id may legitimately contain `/`.
        let req = self.delete(&format!("/api/models/{model_id}"));
        self.run(async move {
            let resp = req.send().await.map_err(|_| ApiError::unreachable())?;
            #[derive(serde::Deserialize)]
            struct R {
                reclaimed_bytes: u64,
            }
            let r: R = Self::parse(resp).await?;
            Ok(r.reclaimed_bytes)
        })
        .await
    }

    // -- system / storage -------------------------------------------------
    pub async fn cleanup_temp(&self) -> Result<u64, ApiError> {
        let req = self.post_json("/api/system/cleanup/temp", serde_json::json!({}));
        self.run(async move {
            let resp = req.send().await.map_err(|_| ApiError::unreachable())?;
            #[derive(serde::Deserialize)]
            struct R {
                reclaimed_bytes: u64,
            }
            let r: R = Self::parse(resp).await?;
            Ok(r.reclaimed_bytes)
        })
        .await
    }

    // -- Soniox / DeepSeek keys -------------------------------------------
    pub async fn soniox_status(&self) -> Result<SonioxStatus, ApiError> {
        let req = self.get("/api/soniox/status");
        self.run(async move {
            let resp = req.send().await.map_err(|_| ApiError::unreachable())?;
            Self::parse(resp).await
        })
        .await
    }

    pub async fn set_soniox_key(&self, api_key: &str) -> Result<SonioxStatus, ApiError> {
        let req = self.put_json("/api/soniox/key", serde_json::json!({ "api_key": api_key }));
        self.run(async move {
            let resp = req.send().await.map_err(|_| ApiError::unreachable())?;
            Self::parse(resp).await
        })
        .await
    }

    pub async fn clear_soniox_key(&self) -> Result<SonioxStatus, ApiError> {
        let req = self.delete("/api/soniox/key");
        self.run(async move {
            let resp = req.send().await.map_err(|_| ApiError::unreachable())?;
            Self::parse(resp).await
        })
        .await
    }

    pub async fn deepseek_status(&self) -> Result<DeepSeekStatus, ApiError> {
        let req = self.get("/api/deepseek/status");
        self.run(async move {
            let resp = req.send().await.map_err(|_| ApiError::unreachable())?;
            Self::parse(resp).await
        })
        .await
    }

    pub async fn set_deepseek_key(&self, api_key: &str) -> Result<DeepSeekStatus, ApiError> {
        let req = self.put_json("/api/deepseek/key", serde_json::json!({ "api_key": api_key }));
        self.run(async move {
            let resp = req.send().await.map_err(|_| ApiError::unreachable())?;
            Self::parse(resp).await
        })
        .await
    }

    pub async fn clear_deepseek_key(&self) -> Result<DeepSeekStatus, ApiError> {
        let req = self.delete("/api/deepseek/key");
        self.run(async move {
            let resp = req.send().await.map_err(|_| ApiError::unreachable())?;
            Self::parse(resp).await
        })
        .await
    }

    // -- jobs -------------------------------------------------------------
    pub async fn delete_job_outputs(&self, id: &str) -> Result<u64, ApiError> {
        let req = self.delete(&format!("/api/jobs/{id}/outputs"));
        self.run(async move {
            let resp = req.send().await.map_err(|_| ApiError::unreachable())?;
            #[derive(serde::Deserialize)]
            struct R {
                removed: u64,
            }
            let r: R = Self::parse(resp).await?;
            Ok(r.removed)
        })
        .await
    }

    // -- transcript -------------------------------------------------------
    pub async fn get_transcript(&self, id: &str) -> Result<Transcript, ApiError> {
        let req = self.get(&format!("/api/jobs/{id}/transcript"));
        self.run(async move {
            let resp = req.send().await.map_err(|_| ApiError::unreachable())?;
            Self::parse(resp).await
        })
        .await
    }

    pub async fn save_transcript(
        &self,
        id: &str,
        segments: &[Segment],
        speakers: &std::collections::HashMap<String, String>,
    ) -> Result<Transcript, ApiError> {
        let req = self.put_json(
            &format!("/api/jobs/{id}/transcript"),
            serde_json::json!({ "segments": segments, "speakers": speakers }),
        );
        self.run(async move {
            let resp = req.send().await.map_err(|_| ApiError::unreachable())?;
            Self::parse(resp).await
        })
        .await
    }

    // -- exports ----------------------------------------------------------
    pub async fn export_job(
        &self,
        id: &str,
        formats: &[ExportFormat],
        sections: &[ExportSection],
        output_dir: Option<&str>,
    ) -> Result<ExportResponse, ApiError> {
        let req = self.post_json(
            &format!("/api/jobs/{id}/export"),
            serde_json::json!({
                "formats": formats,
                "sections": sections,
                "output_dir": output_dir,
            }),
        );
        self.run(async move {
            let resp = req.send().await.map_err(|_| ApiError::unreachable())?;
            Self::parse(resp).await
        })
        .await
    }

    /// Hand the stored transcript to DeepSeek for proofreading and get the
    /// corrected one back. Slow by nature — a reasoning model, one round-trip
    /// per batch of segments — so callers show progress and do not time out.
    pub async fn proofread(&self, id: &str) -> Result<Transcript, ApiError> {
        let req = self.post_json(&format!("/api/jobs/{id}/proofread"), serde_json::json!({}));
        self.run(async move {
            let resp = req.send().await.map_err(|_| ApiError::unreachable())?;
            Self::parse(resp).await
        })
        .await
    }

    // -- AI analysis ------------------------------------------------------
    pub async fn get_analysis(&self, id: &str) -> Result<TranscriptAnalysis, ApiError> {
        let req = self.get(&format!("/api/jobs/{id}/analysis"));
        self.run(async move {
            let resp = req.send().await.map_err(|_| ApiError::unreachable())?;
            Self::parse(resp).await
        })
        .await
    }

    pub async fn create_analysis(&self, id: &str) -> Result<TranscriptAnalysis, ApiError> {
        let req = self.post_json(&format!("/api/jobs/{id}/analysis"), serde_json::json!({}));
        self.run(async move {
            let resp = req.send().await.map_err(|_| ApiError::unreachable())?;
            Self::parse(resp).await
        })
        .await
    }

    /// Replace the entity list of an existing analysis, leaving the rest of it
    /// untouched. Used by the editor's add/remove/rename controls.
    pub async fn save_named_entities(
        &self,
        id: &str,
        entities: &[NamedEntity],
    ) -> Result<TranscriptAnalysis, ApiError> {
        let req = self.put_json(
            &format!("/api/jobs/{id}/analysis/named-entities"),
            serde_json::json!({ "named_entities": entities }),
        );
        self.run(async move {
            let resp = req.send().await.map_err(|_| ApiError::unreachable())?;
            Self::parse(resp).await
        })
        .await
    }

    /// Replace the speaker-summary list of an existing analysis, leaving the
    /// rest of it untouched. Used by the editor's summary fields.
    pub async fn save_speaker_summaries(
        &self,
        id: &str,
        summaries: &[SpeakerSummary],
    ) -> Result<TranscriptAnalysis, ApiError> {
        let req = self.put_json(
            &format!("/api/jobs/{id}/analysis/speaker-summaries"),
            serde_json::json!({ "speaker_summaries": summaries }),
        );
        self.run(async move {
            let resp = req.send().await.map_err(|_| ApiError::unreachable())?;
            Self::parse(resp).await
        })
        .await
    }

    /// Replace the action-item list of an existing analysis, leaving the rest
    /// of it untouched. Used by the editor's add/edit/remove controls.
    pub async fn save_action_items(
        &self,
        id: &str,
        items: &[ActionItem],
    ) -> Result<TranscriptAnalysis, ApiError> {
        let req = self.put_json(
            &format!("/api/jobs/{id}/analysis/action-items"),
            serde_json::json!({ "action_items": items }),
        );
        self.run(async move {
            let resp = req.send().await.map_err(|_| ApiError::unreachable())?;
            Self::parse(resp).await
        })
        .await
    }

    /// Replace the key-point list of an existing analysis, leaving the rest of
    /// it untouched. Used by the editor's add/edit/remove controls.
    pub async fn save_key_points(
        &self,
        id: &str,
        points: &[String],
    ) -> Result<TranscriptAnalysis, ApiError> {
        let req = self.put_json(
            &format!("/api/jobs/{id}/analysis/key-points"),
            serde_json::json!({ "key_points": points }),
        );
        self.run(async move {
            let resp = req.send().await.map_err(|_| ApiError::unreachable())?;
            Self::parse(resp).await
        })
        .await
    }

    /// Replace the speaker-quote list of an existing analysis, leaving the
    /// rest of it untouched. Used by the editor's add/edit/remove controls.
    pub async fn save_speaker_quotes(
        &self,
        id: &str,
        quotes: &[SpeakerQuote],
    ) -> Result<TranscriptAnalysis, ApiError> {
        let req = self.put_json(
            &format!("/api/jobs/{id}/analysis/speaker-quotes"),
            serde_json::json!({ "speaker_quotes": quotes }),
        );
        self.run(async move {
            let resp = req.send().await.map_err(|_| ApiError::unreachable())?;
            Self::parse(resp).await
        })
        .await
    }

    /// Replace the summary of an existing analysis, leaving the rest of it
    /// untouched. Used by the editor's תקציר field.
    pub async fn save_summary(
        &self,
        id: &str,
        summary: &str,
    ) -> Result<TranscriptAnalysis, ApiError> {
        let req = self.put_json(
            &format!("/api/jobs/{id}/analysis/summary"),
            serde_json::json!({ "summary": summary }),
        );
        self.run(async move {
            let resp = req.send().await.map_err(|_| ApiError::unreachable())?;
            Self::parse(resp).await
        })
        .await
    }

    /// URL for streaming media. The token rides as a query parameter because
    /// media/byte-stream fetches cannot send headers.
    pub fn media_url(&self, id: &str) -> String {
        format!(
            "{}/api/jobs/{id}/media?token={}",
            self.base_url,
            urlencode(&self.token)
        )
    }
}

fn urlencode(s: &str) -> String {
    let mut out = String::with_capacity(s.len());
    for b in s.bytes() {
        match b {
            b'A'..=b'Z' | b'a'..=b'z' | b'0'..=b'9' | b'-' | b'_' | b'.' | b'~' => {
                out.push(b as char)
            }
            _ => out.push_str(&format!("%{b:02X}")),
        }
    }
    out
}
