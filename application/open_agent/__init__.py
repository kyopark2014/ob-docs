"""In-process LangGraph Open Agent for ob-note.

Replaces AgentCore InvokeHarness + code-interpreter. Vault note edits go through
first-class ``vault_*`` tools in the same ECS process (no CI sandbox ACL issues).
"""

from application.open_agent.runner import (
    agent_ready,
    iter_agent_events,
    normalize_session_id,
)

__all__ = [
    "agent_ready",
    "iter_agent_events",
    "normalize_session_id",
]
