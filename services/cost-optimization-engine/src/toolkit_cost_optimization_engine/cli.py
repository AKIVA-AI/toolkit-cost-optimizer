"""
CLI entrypoint for Toolkit Cost Optimization Engine.
"""

from __future__ import annotations

import argparse

import uvicorn

from .core.config import get_settings


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="toolkit-cost-optimizer",
        description="Toolkit Cost Optimization Engine CLI",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    serve = subparsers.add_parser("serve", help="Run the API server")
    serve.add_argument("--host", default=None, help="Override host binding")
    serve.add_argument("--port", type=int, default=None, help="Override port")
    serve.add_argument("--reload", action="store_true", help="Enable auto-reload")
    serve.add_argument("--log-level", default=None, help="Override log level")

    return parser


def main() -> None:
    args = build_parser().parse_args()
    settings = get_settings()

    if args.command == "serve":
        uvicorn.run(
            "toolkit_cost_optimization_engine.main:app",
            host=args.host or settings.HOST,
            port=args.port or settings.PORT,
            reload=args.reload or settings.DEBUG,
            log_level=(args.log_level or settings.LOG_LEVEL).lower(),
        )


if __name__ == "__main__":
    main()


