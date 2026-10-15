"""Command-Line Interface (CLI) entrypoint for the Employee Information Multi-Agent System."""
from __future__ import annotations

import argparse
import sys
import uuid
from pathlib import Path
from typing import List, Optional

from app.config import AppConfig, ConfigurationError, load_config
from app.database.connection import initialize_database, seed_demo_data
from app.graph import EmployeeAgentWorkflow
from app.logging.setup import configure_logging


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Employee Information Multi-Agent System CLI",
    )
    parser.add_argument(
        "--db-path",
        type=str,
        default=None,
        help="Override SQLite database file path.",
    )
    parser.add_argument(
        "--seed",
        action="store_true",
        help="Seed demonstration departments and employees if the database is empty.",
    )
    parser.add_argument(
        "--conversation-id",
        type=str,
        default="cli-session-1",
        help="Conversation ID used to persist context and confirmations across turns.",
    )
    parser.add_argument(
        "--message",
        "-m",
        type=str,
        default=None,
        help="Execute a single message non-interactively and exit.",
    )
    parser.add_argument(
        "--provider",
        type=str,
        default=None,
        help="Override LLM_PROVIDER (e.g. deterministic, openai_compatible, groq, gemini_openai, ollama).",
    )
    parser.add_argument(
        "--console-logs",
        action="store_true",
        help="Print structured JSON logs to stderr in addition to the log file.",
    )
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    try:
        base_cfg = load_config(override_db_path=Path(args.db_path) if args.db_path else None)
        provider = (args.provider or base_cfg.llm_provider).strip().lower()
        cfg = AppConfig(
            llm_provider=provider,
            llm_model=base_cfg.llm_model,
            llm_api_key=base_cfg.llm_api_key,
            llm_base_url=base_cfg.llm_base_url,
            llm_temperature=base_cfg.llm_temperature,
            database_path=base_cfg.database_path,
            log_level=base_cfg.log_level,
            log_file_path=base_cfg.log_file_path,
        )
        configure_logging(
            log_level=cfg.log_level,
            log_file_path=cfg.log_file_path,
            console_output=args.console_logs,
        )

        initialize_database(cfg.database_path)
        if args.seed:
            seeded = seed_demo_data(cfg.database_path)
            if seeded:
                print(f"[Database initialized and seeded with demo dataset at {cfg.database_path}]")
            else:
                print(f"[Database at {cfg.database_path} already contains data; skipping seed]")

        if cfg.llm_provider != "deterministic":
            cfg.validate_for_live_llm()

        workflow = EmployeeAgentWorkflow(config=cfg)
    except ConfigurationError as exc:
        print(f"Configuration Error: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"Startup Error: {exc}", file=sys.stderr)
        return 1

    conv_id = args.conversation_id
    if args.message is not None:
        state = workflow.handle_message(args.message, conversation_id=conv_id)
        print(state.get("final_response", ""))
        return 0

    print("====================================================================")
    print(" Employee Information Multi-Agent System (CLI)")
    print(f" Conversation ID : {conv_id}")
    print(f" Database        : {cfg.database_path}")
    print(f" Mode / Provider : {cfg.llm_provider}")
    print(" Commands        : 'exit' or 'quit' to leave, '/new' for new session")
    print("====================================================================")

    while True:
        try:
            user_input = input(f"\n[{conv_id}] You > ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nExiting.")
            break

        if not user_input:
            continue
        if user_input.lower() in {"exit", "quit", ":q"}:
            print("Goodbye.")
            break
        if user_input.lower() == "/new":
            conv_id = f"cli-{uuid.uuid4().hex[:8]}"
            print(f"[Started new conversation session: {conv_id}]")
            continue

        result_state = workflow.handle_message(user_input, conversation_id=conv_id)
        print(f"\nAssistant > {result_state.get('final_response', '')}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
