# Privacy

Tiktuv is local-first. This document states exactly what that means, and how to
verify it rather than take it on trust.

## The guarantee

* **No recording ever leaves your machine.** There is no upload endpoint to any
  remote host, no cloud transcription, no "anonymous sample" collection.
* **No analytics, no telemetry, no crash reporting.** Not opt-out — absent.
* **No accounts.** There is no sign-in screen because there is nothing to sign
  in to.
* **One network operation exists**, and only you can trigger it: downloading a
  model from Hugging Face, after the app has shown you the size and the exact
  destination folder.

## Where your data lives

Everything is under one directory: `%LOCALAPPDATA%\Tiktuv`

| Folder | Contents | Delete it? |
|---|---|---|
| `media/` | Copies of imported recordings | Removed when you delete a job |
| `transcripts/` | One JSON file per job, including partial results | Removed with the job |
| `exports/` | Files you exported (unless you chose another folder) | Yours; delete any time |
| `temp/` | Scratch space | **מחיקת קבצים זמניים** button |
| `models/` | Downloaded model weights (~3.1 GB each) | Per-model delete button |
| `logs/` | Application log, rotated at 5 MB × 3 | Safe to delete |
| `tiktuv.sqlite3` | Job history metadata | Delete via the history screen |
| `runtime.json` | Live port and session token; removed on exit | Do not share |

The exact paths are shown in the app on the **מערכת ומודלים** screen.

## Your original files are never modified

Importing a file **copies** it into `media/`. The original is never moved,
rewritten, or re-encoded. Deleting a job removes Tiktuv's copy and leaves yours
alone.

## Verifying the claims

The backend binds to `127.0.0.1` only, and `Settings` refuses any other host —
you can see the check fail:

```bash
.venv\Scripts\python.exe -c "from tiktuv.config import Settings; Settings(host='0.0.0.0')"
```

Watch its network activity while transcribing:

```bash
netstat -ano | findstr <backend-pid>
```

You should see only the loopback listener. Or read the code: the only outbound
call in the codebase is `snapshot_download` in
`apps/backend/tiktuv/asr/registry.py`, reachable solely from
`POST /api/models/download`.

## Log redaction

Absolute paths are reduced to `<path>/<basename>` before being logged or
returned in an error, so `C:\Users\Dana Cohen\Private\meeting.mp3` becomes
`<path>/meeting.mp3`. The filename is kept because it is what makes an error
actionable. This is on by default (`TIKTUV_REDACT_PATHS_IN_ERRORS`).

Redaction is not a licence to share logs blindly — filenames themselves can be
sensitive. Skim before sending.

## Deleting everything

1. **מחיקת קבצים זמניים** — clears `temp/`.
2. Per-model **מחיקה** — removes downloaded weights.
3. **ניקוי עבודות שהסתיימו** — removes finished jobs, their transcripts and
   media copies.
4. To remove all trace, close Tiktuv and delete `%LOCALAPPDATA%\Tiktuv`.

Deleting a *history record* and deleting *exported files* are separate actions
on purpose: removing a job should not silently destroy documents you produced.
