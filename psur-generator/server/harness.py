"""Adapt the strict source-pack harness to service status/artifact responses."""
import json
from dataclasses import asdict

from pipeline.harness import run_harness


def run_uploaded_harness(*, start_date, end_date, input_dir, output_dir,
                         emitter, first_psur=False):
    emitter.phase_started("harness", detail="Validate and prepare the uploaded source pack")
    try:
        result = run_harness(
            start_date=start_date, end_date=end_date, input_dir=input_dir,
            output_dir=output_dir, is_first_psur=first_psur,
            confirm_first_psur_explicit=first_psur,
            interactive=False,
        )
        issues = [asdict(issue) for issue in result.issues]
        errors = [issue for issue in issues if issue["severity"] == "ERROR"]
        validation_path = output_dir / "validation.json"
        validation_path.write_text(json.dumps({"passed": not errors, "issues": issues},
                                              indent=2), encoding="utf-8")
        artifacts = {path.name: path for path in output_dir.iterdir() if path.is_file()}
        types = {".json": "application/json", ".png": "image/png",
                 ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"}
        metadata = [{"name": name, "size_bytes": path.stat().st_size,
                     "content_type": types.get(path.suffix, "application/octet-stream")}
                    for name, path in artifacts.items()]
        emitter.phase_completed("harness")
        emitter.complete(artifacts=metadata, validation={"passed": not errors,
                                                        "error_count": len(errors)})
        return {"artifacts": artifacts, "artifacts_meta": metadata,
                "is_valid": not errors, "errors": errors,
                "inference": result.context.get("harness_meta").get("inference"),
                "json_path": result.json_path, "docx_path": result.docx_path,
                "stats_path": result.stats_path, "validation_path": validation_path}
    except Exception as exc:
        emitter.error(str(exc))
        raise
