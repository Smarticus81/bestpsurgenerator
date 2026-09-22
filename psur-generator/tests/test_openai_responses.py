"""Responses payload translation and normalized output without network access."""
from types import SimpleNamespace

import openai
import pytest

import llm_client


@pytest.fixture
def response_stub(monkeypatch):
    captured = {}
    response = SimpleNamespace(status="completed", output_text="generated text",
                               model="gpt-6-astra", usage=SimpleNamespace(input_tokens=12, output_tokens=34))
    def create(**kwargs):
        captured.update(kwargs)
        return response
    monkeypatch.setattr(llm_client, "OPENAI_API_KEY", "synthetic-test-key")
    monkeypatch.setattr(llm_client, "_ollama_override", None)
    monkeypatch.setattr(openai, "OpenAI", lambda **kwargs: SimpleNamespace(responses=SimpleNamespace(create=create)))
    return captured, response


def test_astra_response_translation_and_usage(response_stub):
    captured, _ = response_stub
    result = llm_client.create_message(
        model="gpt-6-astra", max_tokens=4096, system="Use evidence only",
        messages=[{"role": "user", "content": [
            {"type": "text", "text": "Read this chart"},
            {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "AA=="}},
        ]}],
    )
    assert captured["model"] == "gpt-6-astra"
    assert captured["instructions"] == "Use evidence only"
    assert captured["max_output_tokens"] == 4096
    assert captured["store"] is False
    assert "temperature" not in captured
    assert "messages" not in captured
    assert captured["input"][0]["content"] == [
        {"type": "input_text", "text": "Read this chart"},
        {"type": "input_image", "image_url": "data:image/png;base64,AA=="},
    ]
    assert result.content[0].text == "generated text"
    assert (result.usage.input_tokens, result.usage.output_tokens) == (12, 34)
    assert result.provider == "openai"


def test_non_reasoning_model_retains_temperature(response_stub):
    llm_client.create_message(model="gpt-4.1", max_tokens=256, temperature=0.2,
                              messages=[{"role": "user", "content": "hello"}])
    assert response_stub[0]["temperature"] == 0.2


@pytest.mark.parametrize("status,text", [("incomplete", "partial"), ("completed", "")])
def test_incomplete_or_empty_response_fails(response_stub, status, text):
    response_stub[1].status = status
    response_stub[1].output_text = text
    with pytest.raises(RuntimeError):
        llm_client.create_message(model="gpt-6-astra", max_tokens=10,
                                  messages=[{"role": "user", "content": "hello"}])


def test_assistant_history_and_url_images():
    result = llm_client._translate_messages_for_openai(None, [
        {"role": "assistant", "content": [{"type": "text", "text": "Prior response"}]},
        {"role": "user", "content": [{"type": "image", "source": {"type": "url", "url": "https://example.com/chart.png"}}]},
    ])
    assert result[0]["content"] == [{"type": "output_text", "text": "Prior response"}]
    assert result[1]["content"][0]["image_url"] == "https://example.com/chart.png"


def test_configured_deployment_alias_routes_to_openai(response_stub, monkeypatch):
    monkeypatch.setattr(llm_client, "MODEL", "project-astra")
    monkeypatch.setattr(llm_client, "LLM_PROVIDER", "openai")
    llm_client.create_message(model="project-astra", max_tokens=100,
                              messages=[{"role": "user", "content": "hello"}])
    assert response_stub[0]["model"] == "project-astra"
    assert "temperature" not in response_stub[0]


def test_missing_key_fails_without_network(monkeypatch):
    monkeypatch.setattr(llm_client, "OPENAI_API_KEY", None)
    with pytest.raises(RuntimeError, match="No OPENAI_API_KEY"):
        llm_client._call_openai(model="gpt-6-astra", max_tokens=10, temperature=0.1,
                                system=None, messages=[])
