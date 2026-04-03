"""Allow running as `python -m fgc_sync`."""

import sys

if "--headless" in sys.argv or "--discord-only" in sys.argv:
    from fgc_sync.cli import main
else:
    from fgc_sync.app import main

main()
