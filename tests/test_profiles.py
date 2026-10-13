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
    # An explicit contextWindow in settings wins, so a wrong server value can be corrected.
    assert p.context_window == 65536
    assert p.context_window_source == "settings (modelProfiles)"
    assert p.tool_calling == "prompted"
    p = resolve_profile("qwen3-27b", server_info={"max_input_tokens": 131072})
    assert (p.context_window, p.context_window_source) == (131072, "server (/model/info)")


def test_learned_window_beats_model_info_but_not_settings() -> None:
    learned = (40960, "server limit (probe)")
    p = resolve_profile(
        "qwen3-27b", server_info={"max_input_tokens": 131072}, learned_window=learned
    )
    assert (p.context_window, p.context_window_source) == learned
    p = resolve_profile("qwen3-27b", overrides=[{"contextWindow": 65536}], learned_window=learned)
    assert p.context_window == 65536
    assert resolve_profile("qwen3-27b").context_window_source == "built-in default"


def test_unknown_model_gets_defaults() -> None:
    p = resolve_profile("some-model")
    assert p.tool_calling == "native"
    assert p.context_window == 32768
