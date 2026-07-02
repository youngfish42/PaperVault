"""Tests for the topic-drift guard inside the suggest prompt.

The harness v2 prompt is the single lever that decides what an LLM returns
for keyword expansion. Without a topic-anchor rule, providers (especially
DeepSeek-v3-class) drift to loosely adjacent research communities — e.g.
``time series agent`` → ``Decision Transformer`` / ``offline reinforcement
learning`` / ``Trajectory Transformer`` — because "agent" gets read as
"RL agent". These tests assert that the prompt hard-codes the anchor rule
and surfaces bad/good examples so the LLM is less likely to wander.
"""

from __future__ import annotations

from papervault.services import suggest


def test_build_prompt_includes_topic_anchor_rule():
    system, user = suggest._build_prompt("time series agent", max_keywords=5)

    assert "TOPIC ANCHOR" in system
    assert "core noun" in system.lower()
    # The anchor rule must apply to adjacent subareas specifically, since
    # that is the clause that triggered the original drift.
    assert "adjacent research subareas" in system
    assert "share a core noun" in system


def test_build_prompt_surfaces_bad_and_good_examples():
    system, _ = suggest._build_prompt("time series agent", max_keywords=5)

    # Bad examples that previously slipped through (RL / sequence-model drift).
    assert "Decision Transformer" in system
    assert "offline reinforcement learning" in system
    assert "Trajectory Transformer" in system
    assert "BAD" in system

    # Good examples: the prompt deliberately no longer enumerates literal
    # TS-LLM names (Lag-Llama, TimesFM, ETTh1) as GOOD. Listing them
    # turned the prompt into a few-shot copycat template — every TS query
    # ended up suggesting the same three names regardless of the actual
    # topic. The GOOD slots are now abstract placeholders that tell the
    # model the *shape* of a good answer; the model has to generate
    # concrete names from its own knowledge of the topic.
    assert "Lag-Llama" not in system
    assert "TimesFM" not in system
    assert "ETTh1" not in system
    assert "MOMENT" not in system
    assert "GOOD" in system
    # An on-topic abstract phrase (no specific model name) is kept so the
    # model can see one concrete rephrasing of the topic itself.
    assert "time series imputation" in system
    # The placeholders must signal "fresh, not copied" so the LLM doesn't
    # fall back to a fixed shortlist.
    assert "fresh from your own knowledge" in system
    assert "do NOT copy names" in system


def test_build_prompt_user_message_mentions_topic_and_count():
    _, user = suggest._build_prompt("graph neural networks", max_keywords=8)

    assert "graph neural networks" in user
    assert "8" in user
    assert "core nouns" in user


def test_build_prompt_bans_generic_filler():
    system, _ = suggest._build_prompt("federated learning", max_keywords=5)

    # Original v1 prompt banned "machine learning" / "deep learning" as filler;
    # v2 must keep that.
    assert "machine learning" in system.lower()
    assert "deep learning" in system.lower()
    assert "BANNED" in system


def test_build_prompt_requires_double_quoting_for_multi_word():
    system, _ = suggest._build_prompt("knowledge graph", max_keywords=5)

    # The OR-merge splitter relies on this for downstream phrase integrity.
    assert "double quotes" in system
    assert "quoted phrases" in system