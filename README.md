# bestpsurgenerator

PSUR generation with source-pack intake, deterministic preparation, and
GPT-6 Astra inference through the OpenAI Responses API.

## Setup

From `psur-generator/`, install `pip install -r requirements.txt`, copy
`.env.example` to `.env`, and supply `OPENAI_API_KEY` through your deployment's
secret configuration. `OPENAI_MODEL` and `OPENAI_REASONING_MODEL` default to
`gpt-6-astra` and can be changed to model IDs available to your API project.
`LLM_PROVIDER=anthropic` opts into the existing Anthropic routing; the CLI's
`--ollama-model` option remains available for local inference.

The implementation uses this repository's Python PSUR harness. It does not
launch the Codex CLI or use a Codex subscription for API authentication.
See the [OpenAI model guide](https://developers.openai.com/api/docs/guides/latest-model)
for Responses API model guidance.

## Upload and generate

Start the service from `psur-generator/`:

```sh
uvicorn server.app:app --host 127.0.0.1 --port 8000
```

Create a ZIP with source files at its root (no enclosing folder). Name files
using discovery keywords such as `sales`, `complaints`, `external_db`,
`previous_psur`, `ract`, `capa`, and `literature`. Device metadata must be named
`device_context.json`. The strict harness requires device context, sales,
complaints, external database evidence, and a previous PSUR unless this is
explicitly the first report. Required fields/columns are defined in
`pipeline/harness.py::REQUIRED_INPUTS`; source templates are under `data/templates/`.
The existing demo pack is for `POST /runs` and may not meet the strict harness's
source-column requirements.

```sh
curl -X POST 'http://localhost:8000/runs/upload?start=2025-01-01&end=2025-12-31' \
  -H 'Content-Type: application/zip' \
  --data-binary @source-pack.zip
```

Use `&first_psur=true` only for a first PSUR. The response contains `run_id`.
Track `GET /runs/{run_id}`, follow `GET /runs/{run_id}/events`, and download files
from `GET /runs/{run_id}/artifacts/{name}` after listing
`GET /runs/{run_id}/artifacts`. Uploaded harness runs emit start, completion, and
error events; detailed stage output is currently in the server logs.

Accepted files: CSV, XLSX, PDF, DOCX, JSON, TXT, MD/MARKDOWN, PNG, JPG/JPEG,
TIFF, and BMP. Legacy `.xls`/`.doc` must be converted first. Limits: 100 MiB
compressed upload, 25 MiB per expanded file, 200 MiB total expanded content,
and 200 files. Filenames must start with an ASCII letter or digit and contain
only ASCII letters, digits, spaces, underscores, hyphens, parentheses, or dots
(at most 200 characters). Nested paths, duplicates ignoring case, symlinks,
encrypted entries, empty files, unsupported types, and invalid archives are
rejected. Failed intake removes its workspace and run reservation.

The upload is streamed into `data/runs/<id>/`, then the harness performs source
discovery, mandatory-input validation, parsing and normalization, deterministic
statistics, section generation, DOCX rendering, and final validation. Files that
cannot be classified produce a failed run instead of waiting for terminal input.
An accepted ZIP returns HTTP 201; later parsing or evidence failures appear in
run status. A completed run can still have `validation.passed=false` and is not
automatically a release-ready report.

The persisted `*_harness_context.json` and the status response's `inference`
field record the configured provider, generation model, reasoning model, and
fallback model. These are configuration provenance, not per-request usage logs.
Existing `POST /runs` keeps the editable demo-pack pipeline.

For local source files, the equivalent CLI is:

```sh
python main.py harness --start 2025-01-01 --end 2025-12-31 -i data/input/ -o data/output/
```

## Verification

Run `python -m pytest -q` from `psur-generator/`. Tests stub inference and do
not require credentials or make billable OpenAI requests.
