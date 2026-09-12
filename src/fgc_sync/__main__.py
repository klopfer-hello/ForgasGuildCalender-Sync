"""Allow running as `python -m fgc_sync`."""

import sys

# Flags only the headless CLI understands. Any of them has to select the CLI
# entry point: app.main() parses with parse_known_args and would silently
# ignore them, so `python -m fgc_sync --dry-run` would start a live GUI sync.
_CLI_FLAGS = frozenset(
    {
        "--headless",
        "--dry-run",
        "--discord-only",
        "--weekly-only",
        "--force",
        "--setup",
        "--export-code",
        "--check-update",
        "--update",
        "--about",
        "--version",
    }
)

if _CLI_FLAGS.intersection(sys.argv[1:]):
    # --headless only picks the entry point; the CLI parser doesn't define it.
    sys.argv = [arg for arg in sys.argv if arg != "--headless"]
    from fgc_sync.cli import main
else:
    from fgc_sync.app import main

main()
