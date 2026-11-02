"""CLI controls for the autonomous paper-only spot agent."""
from __future__ import annotations

import argparse
import json

from arb_foundry.agent_runtime import AutonomousSpotAgent, SpotAgentConfig
from arb_foundry.ai_advisor import summarize_cycle
from arb_foundry.kill_switch import KillSwitchStore


def main() -> None:
    parser = argparse.ArgumentParser(description="Arb Foundry autonomous spot paper agent")
    parser.add_argument("command", choices=("status", "arm-paper", "halt-paper", "run-once"))
    parser.add_argument("--reason", default="operator requested paper simulation")
    args = parser.parse_args()
    config = SpotAgentConfig.from_env()
    config.database_dir.mkdir(parents=True, exist_ok=True)
    switch_path = config.database_dir / "paper-kill-switch.sqlite3"

    if args.command in {"status", "arm-paper", "halt-paper"}:
        with KillSwitchStore(switch_path) as switch:
            status = switch.arm(args.reason) if args.command == "arm-paper" else (
                switch.halt(args.reason) if args.command == "halt-paper" else switch.status()
            )
            print(json.dumps({
                "state": status.state.value, "reason": status.reason,
                "changed_at": status.changed_at.isoformat(), "event_id": status.event_id,
            }, indent=2))
        return

    report = AutonomousSpotAgent(config).run_once()
    print(json.dumps({
        "scanned_books": report.scanned_books, "candidate_count": report.candidate_count,
        "simulated_count": report.simulated_count, "approved_count": report.approved_count,
        "rejected_count": report.rejected_count, "opportunities": report.opportunities,
        "decisions": report.decisions,
    }, indent=2))
    commentary = summarize_cycle(report)
    if commentary:
        print("\nAI ANALYST\n" + commentary)


if __name__ == "__main__":
    main()
