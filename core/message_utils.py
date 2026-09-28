from __future__ import annotations

from typing import Any

from langchain_core.messages import HumanMessage, ToolMessage


def prepare_tool_call_history(history: list[Any], first_turn_instruction: str) -> list[Any]:
    """Make a trimmed tool-call history valid across OpenAI-compatible providers.

    Round-local trimming intentionally removes upstream user content. Gemini requires
    the first assistant function-call turn to follow a user turn, so a focused user
    instruction is restored without reintroducing the upstream reports.
    """
    prepared = list(history)
    while prepared and isinstance(prepared[0], ToolMessage):
        prepared.pop(0)

    if not prepared or not isinstance(prepared[0], HumanMessage):
        prepared.insert(0, HumanMessage(content=first_turn_instruction))
    return prepared
