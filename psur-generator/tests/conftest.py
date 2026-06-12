"""Shared pytest fixtures for the PSUR generator test suite."""
import sys
from pathlib import Path

# Make psur-generator/ importable regardless of where pytest is invoked from.
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import json  # noqa: E402
import re  # noqa: E402

import pytest  # noqa: E402


@pytest.fixture
def stub_llm(monkeypatch):
    """Stub every LLM call site with a deterministic offline fake.

    - IMDRF coding prompts get a valid coding array back.
    - Everything else gets "{}" (parses as an empty JSON object).
    """
    import llm_client
    from llm_client import _NormalisedResponse, _ContentBlock, _Usage

    calls = []

    def _fake_create_message(*, model, max_tokens, messages, system=None,
                             temperature=0.1):
        user_text = ""
        for msg in messages:
            content = msg.get("content", "")
            if isinstance(content, str):
                user_text += content
        calls.append(user_text[:120])

        if "Code these complaints" in user_text:
            n = len(re.findall(r"COMPLAINT \d+:", user_text))
            codings = [
                {
                    "complaint_number": i + 1,
                    "annex_a_code": "A030101",
                    "annex_a_term": "Cosmetic / aesthetic issue only",
                    "annex_f_code": "F0101",
                    "annex_f_term": "No Harm",
                }
                for i in range(n)
            ]
            text = json.dumps(codings)
        else:
            text = "{}"

        return _NormalisedResponse(
            content=[_ContentBlock(text=text)],
            usage=_Usage(input_tokens=10, output_tokens=10),
            model=model,
            provider="stub",
        )

    # Patch the canonical entry point and every module that imported the
    # function directly into its own namespace.
    monkeypatch.setattr(llm_client, "create_message", _fake_create_message)
    import parsers.pms_plan
    monkeypatch.setattr(parsers.pms_plan, "create_message", _fake_create_message)
    import pipeline.discovery
    monkeypatch.setattr(pipeline.discovery, "create_message", _fake_create_message)

    return calls


@pytest.fixture
def stub_section_agents(monkeypatch):
    """Replace the 13 LLM section agents with an instant offline fake."""
    import agents.orchestrator as orchestrator_mod

    class FakeSectionAgent:
        def __init__(self, section_key, global_context="",
                     uk_market_detected=False, class_i_no_nb=False):
            self.section_key = section_key

        def generate(self, statistics=None, device_context=None,
                     parsed_data=None):
            return {"summary": f"Stub content for {self.section_key}."}

        def remediate(self, section_content=None, remediation_prompt=None,
                      statistics=None, device_context=None, parsed_data=None):
            return section_content or {}

    monkeypatch.setattr(orchestrator_mod, "SectionAgent", FakeSectionAgent)
    return FakeSectionAgent


@pytest.fixture
def stub_audit(monkeypatch):
    """Make the audit-remediation loop pass instantly without LLM calls."""
    import psur_auditor

    class _FakeReport:
        compliance_score = 96
        gap = 0

    def _fake_run_json_audit(psur, uk_market_detected=False, use_llm=True,
                             verbose=False):
        return [], _FakeReport()

    monkeypatch.setattr(psur_auditor, "run_json_audit", _fake_run_json_audit)
    return _fake_run_json_audit
