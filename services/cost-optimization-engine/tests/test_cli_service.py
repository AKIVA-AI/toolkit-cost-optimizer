"""Tests for the service CLI (build_parser, argument parsing)."""

from __future__ import annotations

import os

os.environ["ENVIRONMENT"] = "test"
os.environ["SECRET_KEY"] = "test-secret-key-not-for-production"
os.environ["JWT_SECRET_KEY"] = "test-jwt-secret-key-not-for-production"
os.environ["DATABASE_URL"] = "sqlite+aiosqlite://"
os.environ["POSTGRES_PASSWORD"] = "testpass"

import pytest

from toolkit_cost_optimization_engine.cli import build_parser


class TestBuildParser:
    def test_serve_command_exists(self):
        parser = build_parser()
        args = parser.parse_args(["serve"])
        assert args.command == "serve"

    def test_serve_defaults(self):
        parser = build_parser()
        args = parser.parse_args(["serve"])
        assert args.host is None
        assert args.port is None
        assert args.reload is False
        assert args.log_level is None

    def test_serve_with_flags(self):
        parser = build_parser()
        args = parser.parse_args(["serve", "--host", "127.0.0.1", "--port", "9000", "--reload"])
        assert args.host == "127.0.0.1"
        assert args.port == 9000
        assert args.reload is True

    def test_serve_log_level(self):
        parser = build_parser()
        args = parser.parse_args(["serve", "--log-level", "debug"])
        assert args.log_level == "debug"

    def test_no_command_fails(self):
        parser = build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args([])
