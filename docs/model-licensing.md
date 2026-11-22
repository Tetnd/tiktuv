# Model licensing and attribution

Tiktuv's own code is MIT (see [LICENSE](../LICENSE)). The models are not
covered by that licence and are **not** bundled with the application — they are
downloaded on your explicit request, from their original publishers.

## ivrit-ai/whisper-large-v3 — the default

* Repository: <https://huggingface.co/ivrit-ai/whisper-large-v3>
* Project: [ivrit.ai](https://github.com/ivrit-ai/ivrit.ai)
* Base model: `openai/whisper-large-v3`
* Approximate download: 3.1 GB

A Hebrew fine-tune of Whisper large-v3 by the ivrit.ai project, whose purpose is
to build open Hebrew speech resources. Trained on datasets including
`ivrit-ai/crowd-transcribe-v5`, `ivrit-ai/crowd-recital-whisper-training` and
`ivrit-ai/knesset-plenums-whisper-training`.

**Check the licence on the model page before any commercial use.** Licence terms
can change, and the authoritative statement is on Hugging Face, not here.
Tiktuv displays the licence string in the app's model list.

If you use it in research, cite the ivrit.ai project.

## Fallback models — never selected automatically

These are in the catalogue so you can pick one deliberately. Tiktuv will never
substitute one for the Hebrew model on its own; if the Hebrew model fails to
load, the job fails with an explicit error and you choose what to do.

| Model | Licence | Notes |
|---|---|---|
| `openai/whisper-large-v3` | Apache-2.0 | General multilingual. Noticeably weaker on Hebrew. |
| `openai/whisper-small` | Apache-2.0 | Fast, low accuracy. For smoke-testing only. |

The UI marks both as not Hebrew-specialised.

## Speaker diarization — not bundled

Tiktuv ships no diarization model. The reliable open-source option,
`pyannote/speaker-diarization-3.1`, is a gated repository: it requires a Hugging
Face account, acceptance of two separate model licences, and an access token.
That conflicts with an application that promises to work locally with no
sign-in, and its terms do not permit us to redistribute the weights.

The feature is therefore an interface (`tiktuv.diarization.DiarizationBackend`).
If you accept those terms yourself, you can install `pyannote.audio` and
register a backend. See [architecture.md](architecture.md#diarization).

## Third-party software

| Component | Licence | Role |
|---|---|---|
| FFmpeg | LGPL-2.1+ / GPL-2+ by build | Media decoding. Invoked as a separate process, never linked. |
| PyTorch | BSD-3-Clause | Inference runtime |
| Transformers | Apache-2.0 | Model loading and the ASR pipeline |
| FastAPI / Starlette | MIT / BSD-3-Clause | Local HTTP API |
| ReportLab | BSD-3-Clause | PDF generation |
| python-docx | MIT | DOCX generation |
| python-bidi | LGPL-3.0 | Unicode bidi for PDF painting |
| gpui-kit | MIT / Apache-2.0 | The application's GUI toolkit |

**FFmpeg** is a separate executable invoked over a process boundary, so its
licence does not propagate to Tiktuv. If you redistribute Tiktuv with an FFmpeg
build, comply with that build's terms — the Gyan "full" builds referenced in the
README are GPL.

**python-bidi** is LGPL-3.0 and is imported as a library. Dynamic linking is
compatible with MIT distribution, but if you statically bundle it you must
provide the means to relink. See the PDF font notes in
[export-formats.md](export-formats.md#pdf-fonts).

## Fonts

Tiktuv bundles no font. The PDF exporter looks for a bundled TTF in
`apps/backend/tiktuv/assets/fonts/`, then falls back to a system Hebrew font
(Arial, David or Tahoma on Windows; DejaVu Sans on Linux). If you ship Tiktuv
with a font, confirm that font's licence permits embedding in PDFs.
