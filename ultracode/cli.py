"""Human/developer command line interface for 42 Ultracode."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import cast

from .controller import Controller, ControllerError
from .dogfood import run_dogfood
from .executor import CodexCliExecutor, ExecutorError, execute_one
from .route_discovery import PILOT_EXIT_CODES, PilotConfig, run_supervised_pilot
from .supervised_delivery import DeliveryError, DeliveryOutcome, deliver_foreground


def _print_json(value: object) -> None:
    if hasattr(value, "to_dict"):
        serializer = cast(Callable[[], object], value.to_dict)
        value = serializer()
    print(json.dumps(value, indent=2, sort_keys=True, default=str))


def _mcp_command(args: argparse.Namespace) -> int:
    from .mcp.server import run_stdio_server

    return run_stdio_server(database=args.database, role=args.role)


def _worker_once_command(args: argparse.Namespace) -> int:
    controller = Controller(args.database)
    executor = CodexCliExecutor(
        workspace=args.workspace,
        model=args.model,
        sandbox=args.sandbox,
        timeout_seconds=args.timeout,
        ignore_user_config=args.ignore_user_config,
    )
    preflight = executor.preflight()
    if not preflight.authenticated:
        raise ExecutorError(f"subscription preflight failed: {preflight.detail}")
    result = execute_one(
        controller,
        run_id=args.run_id,
        worker_id=args.worker_id,
        executor=executor,
        claim_idempotency_key=args.claim_key,
        result_idempotency_key=args.result_key,
    )
    _print_json(result)
    return 0


def _status_command(args: argparse.Namespace) -> int:
    _print_json(Controller(args.database).get_run(args.run_id))
    return 0


def _history_command(args: argparse.Namespace) -> int:
    events = Controller(args.database).history(args.run_id)
    _print_json([event.to_public_dict() for event in events])
    return 0


def _dogfood_command(args: argparse.Namespace) -> int:
    _print_json(
        run_dogfood(
            args.database,
            evidence_path=args.evidence,
            project_root=args.project_root,
        )
    )
    return 0


def _supervised_delivery_command(args: argparse.Namespace) -> int:
    outcome = deliver_foreground(
        message_path=Path(args.message),
        target_alias=args.target_alias,
        route_registry=Path(args.route_registry),
        journal_path=Path(args.journal),
        input_stream=sys.stdin,
        output_stream=sys.stdout,
    )
    _print_json({"outcome": outcome.value})
    return {
        DeliveryOutcome.DELIVERED: 0,
        DeliveryOutcome.FAILED_BEFORE_WRITE: 20,
        DeliveryOutcome.UNCERTAIN: 21,
    }[outcome]


def _supervised_pilot_command(args: argparse.Namespace) -> int:
    outcome = run_supervised_pilot(
        PilotConfig(
            message_path=Path(args.message),
            locator_basename=args.locator_basename,
            report_path=Path(args.report),
            prompt_commit=args.prompt_commit,
            prompt_sha256=args.prompt_sha256,
        )
    )
    return PILOT_EXIT_CODES[outcome]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ultracode", description="42 Ultracode local controller")
    subcommands = parser.add_subparsers(dest="command", required=True)

    mcp = subcommands.add_parser("mcp", help="serve a role-scoped stdio MCP endpoint")
    mcp.add_argument("--role", choices=("planner", "worker", "control"), required=True)
    mcp.add_argument("--database", required=True, help="SQLite database path")
    mcp.set_defaults(handler=_mcp_command)

    status = subcommands.add_parser("status", help="print a reconstructed run snapshot")
    status.add_argument("--database", required=True)
    status.add_argument("run_id")
    status.set_defaults(handler=_status_command)

    history = subcommands.add_parser("history", help="print redacted ordered run events")
    history.add_argument("--database", required=True)
    history.add_argument("run_id")
    history.set_defaults(handler=_history_command)

    dogfood = subcommands.add_parser("dogfood", help="run the bounded local self-dogfood scenario")
    dogfood.add_argument("--database", required=True)
    dogfood.add_argument("--evidence", required=True)
    dogfood.add_argument("--project-root", default=str(Path.cwd()))
    dogfood.set_defaults(handler=_dogfood_command)

    delivery = subcommands.add_parser(
        "supervised-delivery", help="perform one foreground TTY-confirmed Codex task delivery"
    )
    delivery.add_argument("--message", required=True, help="owner-only UTF-8 payload file")
    delivery.add_argument("--target-alias", required=True, help="closed symbolic route alias")
    delivery.add_argument("--route-registry", required=True, help="owner-only route registry")
    delivery.add_argument("--journal", required=True, help="owner-only append journal")
    delivery.set_defaults(handler=_supervised_delivery_command)

    pilot = subcommands.add_parser(
        "supervised-pilot", help="discover and perform one foreground TTY-confirmed Codex task delivery"
    )
    pilot.add_argument("--message", required=True, help="owner-only frozen UTF-8 payload file")
    pilot.add_argument("--locator-basename", required=True, help="fixed private locator basename")
    pilot.add_argument("--report", required=True, help="fresh owner-only sanitized result path")
    pilot.add_argument("--prompt-commit", required=True, help="public prompt commit")
    pilot.add_argument("--prompt-sha256", required=True, help="public prompt SHA-256")
    pilot.set_defaults(handler=_supervised_pilot_command)

    worker = subcommands.add_parser("worker-once", help="claim and execute one Codex CLI turn")
    worker.add_argument("--database", required=True)
    worker.add_argument("--run-id", required=True)
    worker.add_argument("--worker-id", required=True)
    worker.add_argument("--workspace", default=str(Path.cwd()))
    worker.add_argument("--model", default="gpt-5.5")
    worker.add_argument("--sandbox", choices=("read-only", "workspace-write"), default="read-only")
    worker.add_argument("--timeout", type=int, default=900)
    worker.add_argument("--claim-key", default="worker-once:claim")
    worker.add_argument("--result-key", default="worker-once:result")
    worker.add_argument("--ignore-user-config", action=argparse.BooleanOptionalAction, default=True)
    worker.set_defaults(handler=_worker_once_command)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        handler = cast(Callable[[argparse.Namespace], int], args.handler)
        return handler(args)
    except (ControllerError, DeliveryError, ExecutorError, OSError, ValueError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
