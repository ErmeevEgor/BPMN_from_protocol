#!/usr/bin/env python3
"""Explicitly install the portable skill's Python and Node dependencies."""
from __future__ import annotations

import argparse
from pathlib import Path
import shutil
import subprocess
import sys

from runtime_support import bpmn_node_dir


SCRIPTS = Path(__file__).resolve().parent


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--install", action="store_true",
                        help="Perform installation; without this flag only print the commands")
    parser.add_argument("--skip-python", action="store_true")
    parser.add_argument("--skip-node", action="store_true")
    args = parser.parse_args()
    commands: list[list[str]] = []
    if not args.skip_python:
        commands.append([sys.executable, "-m", "pip", "install", "-r", str(SCRIPTS / "requirements.txt")])
    if not args.skip_node:
        npm = shutil.which("npm") or shutil.which("npm.cmd")
        if not npm:
            print("Node setup unavailable: install Node.js 18+ first", file=sys.stderr)
            return 1
        npm_action = "ci" if (bpmn_node_dir() / "package-lock.json").is_file() else "install"
        commands.append([npm, npm_action, "--prefix", str(bpmn_node_dir())])
    if not args.install:
        print("No changes made. Re-run with --install to execute:")
        for command in commands:
            print("  " + subprocess.list2cmdline(command))
        return 0
    for command in commands:
        print("Running: " + subprocess.list2cmdline(command))
        code = subprocess.run(command).returncode
        if code:
            return code
    return subprocess.run([sys.executable, str(SCRIPTS / "check_runtime.py")]).returncode


if __name__ == "__main__":
    raise SystemExit(main())
