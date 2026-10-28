from __future__ import annotations

from types import SimpleNamespace

from engineering_team.settings import load_settings


class _LLMRecorder:
    calls: list[dict]

    def __init__(self) -> None:
        self.calls = []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(**kwargs)


def test_build_llm_sends_only_parameters_the_provider_accepts(monkeypatch) -> None:
    from engineering_team import llm as llm_module
    from engineering_team.llm import build_llm

    recorder = _LLMRecorder()
    monkeypatch.setattr(llm_module, "LLM", recorder)

    openai_settings = load_settings()
    build_llm(openai_settings.resolve_model("engineering_lead"), openai_settings)
    anthropic_settings = load_settings(overrides={"provider": "anthropic"})
    build_llm(anthropic_settings.resolve_model("engineering_lead"), anthropic_settings)
    ollama_settings = load_settings(overrides={"provider": "ollama"})
    build_llm(ollama_settings.resolve_model("backend_engineer"), ollama_settings)

    openai_call, anthropic_call, ollama_call = recorder.calls
    # GPT-6 needs the Responses API for tools. The context window is not a constructor option:
    # CrewAI's native providers forward unknown options to the API (see the next test).
    assert openai_call == {
        "model": "openai/gpt-6.1-sol",
        "reasoning_effort": "high",
        "api": "responses",
    }
    # Anthropic ignores reasoning effort, so it must not be sent.
    assert anthropic_call == {"model": "anthropic/claude-sonnet-5-5"}
    assert ollama_call["base_url"] == "http://localhost:11434"
    assert "context_window_size" not in ollama_call


def test_build_llm_applies_the_context_window_without_sending_it_to_the_api() -> None:
    """Regression: ``context_window_size=`` reached ``Responses.create()`` and failed every call."""
    from engineering_team.llm import build_llm

    openai_settings = load_settings()
    openai_llm = build_llm(openai_settings.resolve_model("engineering_lead"), openai_settings)
    assert openai_llm.get_context_window_size() == int(1_050_000 * 0.75)
    assert "context_window_size" not in getattr(openai_llm, "additional_params", {})

    ollama_settings = load_settings(overrides={"provider": "ollama"})
    ollama_llm = build_llm(ollama_settings.resolve_model("backend_engineer"), ollama_settings)
    assert ollama_llm.get_context_window_size() == int(32_768 * 0.75)
    assert "context_window_size" not in getattr(ollama_llm, "additional_params", {})
