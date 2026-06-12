"""End-to-end pipeline run against the bundled mock pack, LLM stubbed.

Asserts the run completes and the full regulatory decision-event set is
emitted: denominator, cadence, UK MDR activation, IMDRF coding, RACT
occurrence codes, the trend verdict, and the final validation outcome.
"""
import json
from pathlib import Path

import pytest

from config import INPUT_DIR
from events import QueueEmitter
from pipeline.run import run_generation


@pytest.fixture
def run_result(tmp_path, stub_llm, stub_section_agents, stub_audit):
    emitter = QueueEmitter()
    result = run_generation(
        start_date="2023-01-01",
        end_date="2023-12-31",
        input_dir=Path(INPUT_DIR),
        output_dir=tmp_path / "output",
        emitter=emitter,
    )
    emitter.close()
    return result, emitter.events_since(0)


def test_run_completes_with_artifacts(run_result):
    result, events = run_result
    assert result["device_name"].startswith("Laparoscopic Stapler X100")
    assert result["report_type"] == "PSUR"  # Class IIb device
    assert result["docx_path"].exists()
    assert result["json_path"].exists()
    assert result["stats_path"].exists()
    assert result["validation_path"].exists()

    # The PSUR JSON carries the de-branded in-house form id
    psur = json.loads(result["json_path"].read_text())
    assert psur["form"]["form_id"] == "RG-PSUR-001"
    assert all(key in psur["sections"] for key in (
        "A_executive_summary", "D_information_on_serious_incidents",
        "G_information_from_trend_reporting", "J_scientific_literature_review",
        "M_findings_and_conclusions",
    ))
    assert len(psur["sections"]) == 13

    # complete event mirrors the artifact set
    completes = [e for e in events if e["kind"] == "complete"]
    assert len(completes) == 1
    names = {a["name"] for a in completes[0]["artifacts"]}
    assert result["docx_path"].name in names
    assert result["json_path"].name in names
    assert "validation" in completes[0]


def test_expected_decision_set_emitted(run_result):
    _, events = run_result
    decisions = [e for e in events if e["kind"] == "decision"]
    by_label = {}
    for d in decisions:
        by_label.setdefault(d["decision"], []).append(d)

    # 1. Denominator selection (single-use → units distributed)
    denom = by_label["denominator_selection"][0]
    assert denom["output"] == "units_distributed"
    assert denom["regulatory_basis"] == ["MDCG 2022-21"]

    # 2. PSUR-vs-PMSR cadence (Class IIb → annual PSUR, 44ZM)
    cadence = by_label["psur_vs_pmsr_cadence"][0]
    assert cadence["output"]["report_type"] == "PSUR"
    assert "UK MDR 2024 Reg 44ZM" in cadence["regulatory_basis"]
    assert "EU MDR Art. 86" in cadence["regulatory_basis"]

    # 3. UK MDR activation (mock pack has UK sales rows)
    uk = by_label["uk_mdr_activation"][0]
    assert uk["regulatory_basis"] == ["UK MDR 2024 Reg 44ZE"]
    assert uk["inputs_summary"]["uk_units"] > 0

    # 4. IMDRF auto-coding — at least one assignment, Annex A+F basis
    imdrf = by_label["imdrf_auto_coding"]
    assert len(imdrf) >= 1
    assert all(set(d["regulatory_basis"]) == {"IMDRF Annex A", "IMDRF Annex F"}
               for d in imdrf)

    # 5. RACT occurrence-code assignments with rate comparison (ISO 14971)
    ract = by_label["ract_occurrence_classification"]
    assert len(ract) >= 1
    assert all(d["regulatory_basis"] == ["ISO 14971"] for d in ract)
    assert all(d["output"]["occurrence_code"].startswith("O") for d in ract)
    verdicts = {d["output"]["rate_vs_ract"] for d in ract}
    assert "EXCEEDS" in verdicts  # November misfire cluster exceeds RACT max

    # 6. Trend verdict — the mock pack genuinely trips Western Electric Rule 1
    trend = by_label["ucl_trend_verdict"][0]
    assert set(trend["regulatory_basis"]) == {"UK MDR 2024 Reg 44ZN", "MDCG 2022-21"}
    assert trend["output"] == "ALERT"
    assert any("Rule 1" in v
               for v in trend["inputs_summary"]["western_electric_violations"])

    # 7. Final 331-point validation outcome
    final = by_label["final_validation_outcome"][0]
    assert set(final["regulatory_basis"]) == {"EU MDR Art. 86", "MDCG 2022-21"}
    assert isinstance(final["output"]["passed"], bool)
    assert isinstance(final["output"]["error_count"], int)

    # Every decision carries a human-readable reason
    assert all(d["reason"] for d in decisions)


def test_phase_progress_and_per_section_events(run_result):
    _, events = run_result
    progress = [e for e in events if e["kind"] == "progress"]
    phases = {e["phase"] for e in progress}
    for expected in ("discovery", "parsing", "device_context", "imdrf_coding",
                     "statistics", "charts", "generation", "audit",
                     "validation", "rendering", "artifacts"):
        assert expected in phases, f"missing phase '{expected}'"

    section_events = [e for e in progress
                      if e["phase"] == "generation" and e.get("section")]
    sections_seen = {e["section"] for e in section_events}
    assert len(sections_seen) == 13
    assert "D_information_on_serious_incidents" in sections_seen

    # Envelope sanity: contiguous monotonic seq across the whole run
    seqs = [e["seq"] for e in events]
    assert seqs == list(range(len(seqs)))
