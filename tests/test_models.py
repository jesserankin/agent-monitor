"""Tests for data models and window title parsing."""

from agent_monitor.models import AgentState, parse_window_title


class TestParseWindowTitle:
    """Test parse_window_title() with various title formats."""

    def test_standard_active(self):
        result = parse_window_title(
            "ge-class | \u2810 Browser Game Implementation", "com.mitchellh.ghostty"
        )
        assert result is not None
        assert result["session_name"] == "ge-class"
        assert result["state"] == AgentState.ACTIVE
        assert result["task_description"] == "Browser Game Implementation"
        assert result["has_attention"] is False

    def test_standard_idle(self):
        result = parse_window_title(
            "iam-catalog | \u2733 Claude Code", "com.mitchellh.ghostty"
        )
        assert result is not None
        assert result["session_name"] == "iam-catalog"
        assert result["state"] == AgentState.IDLE
        assert result["task_description"] == "Claude Code"
        assert result["has_attention"] is False

    def test_attention(self):
        result = parse_window_title(
            "\U0001f514 ge-play-narrative | \u2733 Browser Testing",
            "com.mitchellh.ghostty",
        )
        assert result is not None
        assert result["session_name"] == "ge-play-narrative"
        assert result["state"] == AgentState.ATTENTION
        assert result["task_description"] == "Browser Testing"
        assert result["has_attention"] is True

    def test_attention_active(self):
        result = parse_window_title(
            "\U0001f514 ge-play-narrative | \u2810 Browser Testing",
            "com.mitchellh.ghostty",
        )
        assert result is not None
        assert result["session_name"] == "ge-play-narrative"
        assert result["state"] == AgentState.ATTENTION
        assert result["task_description"] == "Browser Testing"
        assert result["has_attention"] is True

    def test_non_claude_session(self):
        result = parse_window_title(
            "BoK | jesse@office:~/projects/bok", "com.mitchellh.ghostty"
        )
        assert result is None

    def test_no_pipe(self):
        result = parse_window_title("Firefox", "firefox")
        assert result is None

    def test_non_terminal_class(self):
        result = parse_window_title(
            "ge-class | \u2810 Browser Game Implementation", "firefox"
        )
        assert result is None

    def test_attention_empty_session_name(self):
        result = parse_window_title(
            "\U0001f514 | \u2733 Browser Testing", "com.mitchellh.ghostty"
        )
        assert result is None
