from cmcoder.providers.profiles import model_size_b, resolve_profile


def test_model_size_parsing() -> None:
    assert model_size_b("qwen3-27b") == 27
    assert model_size_b("qwen3:8b") == 8
    assert model_size_b("Qwen3-7B-Instruct") == 7
    assert model_size_b("qwen3-30b-a3b") == 30
    assert model_size_b("gpt-4o") is None


def test_qwen3_large_and_small_profiles() -> None:
    big = resolve_profile("qwen3-27b")
    small = resolve_profile("qwen3-7b")
    assert big.prompt_tier == "full"
    assert small.prompt_tier == "compact"
    assert big.thinking_switch == "chat_template_kwargs"


def test_overrides_and_server_info() -> None:
    p = resolve_profile(
        "qwen3-27b",
        overrides=[{"match": "qwen3-27*", "thinkingSwitch": "prompt", "contextWindow": 65536}],
        server_info={"max_input_tokens": 131072, "supports_function_calling": False},
    )
    assert p.thinking_switch == "prompt"
    assert p.context_window == 131072  # server value wins
    assert p.tool_calling == "prompted"


def test_unknown_model_gets_defaults() -> None:
    p = resolve_profile("some-model")
    assert p.tool_calling == "native"
    assert p.context_window == 32768
