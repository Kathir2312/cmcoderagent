from cmcoder.providers.thinking import ThinkSplitter


def run(chunks: list[str]) -> tuple[str, str]:
    s = ThinkSplitter()
    parts = [p for c in chunks for p in s.feed(c)] + s.flush()
    text = "".join(p for k, p in parts if k == "text")
    reasoning = "".join(p for k, p in parts if k == "reasoning")
    return text, reasoning


def test_plain_text_passes_through() -> None:
    assert run(["Hello ", "world"]) == ("Hello world", "")


def test_think_block_split_across_chunks() -> None:
    text, reasoning = run(["<th", "ink>let me ", "think</thi", "nk>\n\nAnswer: 42"])
    assert reasoning == "let me think"
    assert text == "Answer: 42"


def test_text_that_looks_like_a_partial_tag_is_released() -> None:
    assert run(["a < b and <t", "able>"]) == ("a < b and <table>", "")


def test_unclosed_think_is_reasoning() -> None:
    assert run(["<think>still going"]) == ("", "still going")
