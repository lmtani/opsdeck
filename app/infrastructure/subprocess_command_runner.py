"""CommandRunner backed by a detached subprocess.

Launches local programs (e.g. opening IGV) on the user's machine, reusing
their shell so `~`, environment variables and pipes behave as typed. The
child is fully detached so GUI apps keep running after the request returns;
we only wait a brief moment to surface commands that fail immediately
(wrong path, unknown program).
"""

import subprocess

from app.domain.errors import CommandError
from app.domain.links import CommandRunner

# How long to watch a freshly launched command before assuming it is a
# long-running program (GUI app) rather than an instant failure.
_STARTUP_GRACE_SECONDS = 0.6


class SubprocessCommandRunner(CommandRunner):
    def run(self, command: str) -> str:
        command = command.strip()
        if not command:
            raise CommandError("Empty command")
        try:
            process = subprocess.Popen(
                command,
                shell=True,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,  # survive after the request returns
            )
        except OSError as error:
            raise CommandError(f"Could not start the command: {error}") from None

        try:
            code = process.wait(timeout=_STARTUP_GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            return "Program started"  # still running — treated as success
        if code != 0:
            raise CommandError(f"Command exited with code {code}")
        return "Command finished"
