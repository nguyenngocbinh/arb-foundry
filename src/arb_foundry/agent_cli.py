"""CLI controls for the autonomous paper-only spot agent."""
from __future__ import annotations

import argparse
import json
import time

from arb_foundry.agent_runtime import AutonomousSpotAgent, SpotAgentConfig
from arb_foundry.ai_advisor import summarize_cycle
from arb_foundry.kill_switch import KillSwitchStore


def main() -> None:
    parser = argparse.ArgumentParser(description="Arb Foundry autonomous spot paper agent")
    parser.add_argument(
        "command",
        choices=("status", "arm-paper", "halt-paper", "run-once", "run-loop"),
    )
    parser.add_argument("--reason", default="operator requested paper simulation")
    parser.add_argument("--interval-seconds", type=int, default=15)
    args = parser.parse_args()
    if args.interval_seconds < 1:
        parser.error("--interval-seconds must be at least 1")

    config = SpotAgentConfig.from_env()
    config.database_dir.mkdir(parents=True, exist_ok=True)
    switch_path = config.database_dir / "paper-kill-switch.sqlite3"

    if args.command in {"status", "arm-paper", "halt-paper"}:
        with KillSwitchStore(switch_path) as switch:
            if args.command == "arm-paper":
                status = switch.arm(args.reason)
            elif args.command == "halt-paper":
                status = switch.halt(args.reason)
            else:
                status = switch.status()
            print(json.dumps({
                "state": status.state.value,
                "reason": status.reason,
                "changed_at": status.changed_at.isoformat(),
                "event_id": status.event_id,
            }, indent=2))
        return

    agent = AutonomousSpotAgent(config)
    while True:
        try:
            report = agent.run_once()
            print(json.dumps({
                "scanned_books": report.scanned_books,
                "candidate_count": report.candidate_count,
                "simulated_count": report.simulated_count,
                "approved_count": report.approved_count,
                "rejected_count": report.rejected_count,
                "opportunities": report.opportunities,
                "decisions": report.decisions,
            }, indent=2))
            commentary = summarize_cycle(report)
            if commentary:
                print("\\nAI ANALYST\\n" + commentary)
        except KeyboardInterrupt:
            print("Agent stopped by operator.")
            return
        except Exception as exc:
            print(json.dumps({"error": f"{type(exc).__name__}: {exc}"}, indent=2))
            if args.command == "run-once":
                raise
        if args.command == "run-once":
            return
        time.sleep(args.interval_seconds)


if __name__ == "__main__":
    main()
