"""Shango Slack Simulator (slacksim).

Terminal-based Slack console emulator for interactive testing, debugging,
and remote agent supervision.
"""

from slacksim._version import __version__
from slacksim.line_reader import ConsoleLineReader
from slacksim.slack_formatter import format_slack_to_ansi
from slacksim.slacksim import SimConfig, SlackSimulator, run_main

__all__ = [
    "ConsoleLineReader",
    "SimConfig",
    "SlackSimulator",
    "__version__",
    "format_slack_to_ansi",
    "run_main",
]
