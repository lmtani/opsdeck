"""CommandRunner used in containers (DASHBOARD_COMMANDS=off), where command
cards would run inside the container instead of on the user's machine —
useless and unsafe."""

from app.domain.errors import CommandError
from app.domain.links import CommandRunner


class DisabledCommandRunner(CommandRunner):
    def run(self, command: str) -> str:
        raise CommandError(
            "Command cards launch local programs and are disabled"
            " in this deployment (DASHBOARD_COMMANDS=off)."
        )
