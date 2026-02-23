"""Data models for agent sessions and window title parsing."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from enum import Enum

BRAILLE_SPINNER_CHARS = frozenset({"\u2802", "\u2810"})  # ⠂ ⠐
IDLE_CHAR = "\u2733"  # ✳
ATTENTION_EMOJI = "\U0001f514"  # 🔔
STATUS_CHARS = BRAILLE_SPINNER_CHARS | {IDLE_CHAR}

TERMINAL_CLASSES = frozenset(
    {"Alacritty", "com.mitchellh.ghostty", "kitty", "foot", "org.wezfurlong.wezterm"}
)

MONITOR_DIR = os.path.join(
    os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}"),
    "claude-monitor",
)


class AgentState(Enum):
    ACTIVE = "active"
    IDLE = "idle"
    ATTENTION = "attention"


@dataclass
class AgentSession:
    address: str
    session_name: str
    task_description: str
    state: AgentState
    workspace_id: int
    window_class: str
    workspace_group: int = field(init=False)
    pid: int | None = None
    cost_usd: float | None = None
    duration_ms: int | None = None
    context_used_pct: float | None = None
    model_name: str | None = None
    lines_added: int | None = None
    lines_removed: int | None = None

    def __post_init__(self):
        self.workspace_group = self.workspace_id % 10


def parse_window_title(title: str, window_class: str) -> dict | None:
    """Parse a Hyprland window title into session info.

    Returns a dict with session_name, state, task_description, has_attention
    or None if the title doesn't match a Claude Code session.
    """
    if window_class not in TERMINAL_CLASSES:
        return None

    if " | " not in title:
        return None

    session_part, rest = title.split(" | ", 1)

    has_attention = session_part.startswith(ATTENTION_EMOJI)
    if has_attention:
        session_part = session_part[len(ATTENTION_EMOJI) :].lstrip()

    if not session_part:
        return None

    rest = rest.lstrip()
    if not rest:
        return None

    status_char = rest[0]
    if status_char not in STATUS_CHARS:
        return None

    task_description = rest[1:].lstrip()

    if status_char in BRAILLE_SPINNER_CHARS:
        state = AgentState.ATTENTION if has_attention else AgentState.ACTIVE
    else:
        state = AgentState.ATTENTION if has_attention else AgentState.IDLE

    return {
        "session_name": session_part,
        "state": state,
        "task_description": task_description,
        "has_attention": has_attention,
    }
