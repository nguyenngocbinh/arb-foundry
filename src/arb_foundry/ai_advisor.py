"""Optional OpenAI Agents SDK commentary; never part of execution or risk approval."""
from __future__ import annotations

import os

from arb_foundry.agent_runtime import AgentCycleReport


def summarize_cycle(report: AgentCycleReport, *, model: str | None = None) -> str | None:
    """Return qualitative commentary when explicitly enabled; otherwise return None."""
    if os.getenv("AI_ADVISOR_ENABLED", "false").strip().lower() != "true":
        return None
    if not os.getenv("OPENAI_API_KEY"):
        return "AI advisor skipped: OPENAI_API_KEY is not configured."
    try:
        from agents import Agent, Runner
    except ImportError as exc:
        raise RuntimeError("Install arb-foundry[ai] to enable the optional AI advisor.") from exc

    agent = Agent(
        name="Arb Foundry Paper Trade Analyst",
        model=model or os.getenv("OPENAI_MODEL", "gpt-6"),
        instructions=(
            "You are a cautious quantitative trading research analyst. Analyze only supplied "
            "completed paper-cycle facts. Never recommend live execution, invent market facts, "
            "or override deterministic risk decisions. Highlight small samples, assumed fees "
            "and slippage, inventory constraints, stale data and legging risk. Reply in Vietnamese "
            "with observations, risks and next validation tests. State this is paper research, "
            "not investment advice."
        ),
    )
    result = Runner.run_sync(agent, f"Analyze this completed paper cycle: {report!r}")
    return str(result.final_output)
