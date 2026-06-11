"""Shared error type and the argparse subclass used across the CLI."""
import argparse
import sys

from plan_keeper import __version__


class HelpfulArgumentParser(argparse.ArgumentParser):
    """Print full help (not just usage) before erroring on bad args, and stamp
    the plan-keeper version at the top of every help screen."""

    def format_help(self) -> str:
        # Prepend a version banner so any help output — top-level, every
        # subcommand, and the help printed on an argument error — always names
        # the running version. `self.prog` is the full command path
        # ("plan_keeper_cli crew queue" for a deep subparser); its first token
        # is the root program name, keeping the banner identical everywhere.
        root = self.prog.split()[0] if self.prog else "plan_keeper_cli"
        return f"{root} {__version__}\n\n{super().format_help()}"

    def error(self, message: str):
        self.print_help(sys.stderr)
        self.exit(2, f"\n{self.prog}: error: {message}\n")


class PlanKeeperCliError(Exception):
    """Expected, user-facing errors. Carries an exit code."""

    def __init__(self, msg: str, code: int):
        super().__init__(msg)
        self.code = code
