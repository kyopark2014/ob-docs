"""Minimal LangGraph agent ↔ tool loop (adapted from agentic-work)."""

from __future__ import annotations

import logging
from typing import Annotated, Any, Literal, Optional

from langchain_core.messages import AIMessage, BaseMessage, SystemMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode
from typing_extensions import TypedDict

from application.open_agent.llm import get_chat_model
from application.open_agent.prompt import SYSTEM_PROMPT
from application.open_agent.tools_code import get_code_tools
from application.open_agent.tools_vault import get_vault_tools

logger = logging.getLogger("open_agent.graph")


class State(TypedDict):
    messages: Annotated[list, add_messages]


def get_all_tools() -> list:
    return [*get_vault_tools(), *get_code_tools()]


def _assistant_text(msg: AIMessage) -> str:
    content = msg.content
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text", "")))
            elif isinstance(block, str):
                parts.append(block)
        return "".join(parts)
    return str(content) if content else ""


def build_agent(
    *,
    model_name: Optional[str] = None,
    tools: Optional[list] = None,
    system_prompt: str = SYSTEM_PROMPT,
):
    """Compile a sync StateGraph: agent ↔ tools."""
    tool_list = tools if tools is not None else get_all_tools()
    tool_node = ToolNode(tool_list, handle_tool_errors=True)
    chat = get_chat_model(model_name)
    bound = chat.bind_tools(tool_list) if tool_list else chat

    def call_model(state: State) -> dict[str, Any]:
        raw: list[BaseMessage] = list(state.get("messages") or [])
        # Drop orphan ToolMessages / incomplete tool rounds for Bedrock safety.
        messages = _sanitize_for_bedrock(raw)
        model_messages = [SystemMessage(content=system_prompt), *messages]
        try:
            response = bound.invoke(model_messages)
        except Exception:
            logger.exception("call_model failed")
            response = AIMessage(content="답변을 생성하지 못했습니다. 잠시 후 다시 시도해 주세요.")
        if not isinstance(response, AIMessage):
            response = AIMessage(content=str(getattr(response, "content", response)))
        return {"messages": [response]}

    def should_continue(state: State) -> Literal["continue", "end"]:
        messages = state.get("messages") or []
        last = messages[-1] if messages else None
        if isinstance(last, AIMessage) and last.tool_calls:
            return "continue"
        return "end"

    workflow = StateGraph(State)
    workflow.add_node("agent", call_model)
    workflow.add_node("action", tool_node)
    workflow.add_edge(START, "agent")
    workflow.add_conditional_edges(
        "agent",
        should_continue,
        {"continue": "action", "end": END},
    )
    workflow.add_edge("action", "agent")
    return workflow.compile()


def _sanitize_for_bedrock(messages: list) -> list:
    from langchain_core.messages import ToolMessage

    out: list = []
    i = 0
    n = len(messages)
    while i < n:
        msg = messages[i]
        if isinstance(msg, ToolMessage):
            i += 1
            continue
        if isinstance(msg, AIMessage) and getattr(msg, "tool_calls", None):
            needed = {tc["id"] for tc in msg.tool_calls if tc.get("id")}
            tool_msgs: list = []
            j = i + 1
            while j < n and isinstance(messages[j], ToolMessage):
                tool_msgs.append(messages[j])
                j += 1
            have = {m.tool_call_id for m in tool_msgs}
            if needed and needed <= have:
                out.append(msg)
                out.extend(tool_msgs)
                i = j
            else:
                # Incomplete tool round — keep text only.
                text = _assistant_text(msg)
                if text:
                    out.append(AIMessage(content=text))
                i = j if tool_msgs else i + 1
            continue
        out.append(msg)
        i += 1
    return out
