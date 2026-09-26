"""Unit tests for development/check_release.py commit message parsing."""

from development.check_release import evaluate_release


def test_release_agent_trigger():
    decision = evaluate_release(message="feat: improve agent service /release-agent")
    assert decision.release_agent is True
    assert decision.release_bot is False
    assert decision.is_release is True
    assert decision.tag == "v1.0.0"
    assert decision.version == "1.0.0"


def test_release_bot_trigger():
    decision = evaluate_release(message="feat: improve bot command /release-bot")
    assert decision.release_agent is False
    assert decision.release_bot is True
    assert decision.is_release is True
    assert decision.tag == "v1.0.0"
    assert decision.version == "1.0.0"


def test_release_all_trigger():
    decision = evaluate_release(message="chore: full release /release")
    assert decision.release_agent is True
    assert decision.release_bot is True
    assert decision.is_release is True
    assert decision.tag == "v1.0.0"
    assert decision.version == "1.0.0"


def test_release_with_explicit_version():
    decision = evaluate_release(message="chore: release version /release v1.5.0")
    assert decision.release_agent is True
    assert decision.release_bot is True
    assert decision.version == "1.5.0"
    assert decision.short_version == "1.5"
    assert decision.tag == "v1.5.0"


def test_release_agent_with_explicit_version():
    decision = evaluate_release(message="chore: release agent /release-agent 1.2.3")
    assert decision.release_agent is True
    assert decision.release_bot is False
    assert decision.version == "1.2.3"
    assert decision.tag == "v1.2.3"


def test_release_bot_with_explicit_version():
    decision = evaluate_release(message="chore: release bot /release-bot 2.0.0")
    assert decision.release_agent is False
    assert decision.release_bot is True
    assert decision.version == "2.0.0"
    assert decision.tag == "v2.0.0"


def test_standard_commit_no_triggers():
    decision = evaluate_release(message="docs: update installation instructions")
    assert decision.release_agent is False
    assert decision.release_bot is False
    assert decision.is_release is False


def test_multiline_commit_message():
    msg = """feat: major update to services

Includes updates to agent and bot.
Triggering release:
/release
"""
    decision = evaluate_release(message=msg)
    assert decision.release_agent is True
    assert decision.release_bot is True
    assert decision.is_release is True
