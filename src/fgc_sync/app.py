"""Application bootstrap — creates QApplication and starts the controller."""

from __future__ import annotations

import logging
import sys
import traceback

from fgc_sync.services.config import Config


def _crash_log(msg: str):
    """Write to the crash log when normal logging is not yet available."""
    try:
        from fgc_sync.services.config import _app_data_dir
        log_path = _app_data_dir() / "crash.log"
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(msg + "\n")
    except Exception:
        pass


def main():
    try:
        _main()
    except Exception:
        _crash_log(traceback.format_exc())
        raise


def _main():
    import argparse
    parser = argparse.ArgumentParser(description="FGC Calendar Sync")
    parser.add_argument(
        "--config-dir", type=str, default=None,
        help="Use a custom config directory (for testing or multi-user setups)",
    )
    args, _ = parser.parse_known_args()

    if args.config_dir:
        from pathlib import Path
        config_dir = Path(args.config_dir)
        config_dir.mkdir(parents=True, exist_ok=True)
        config = Config(config_dir / "config.json")
    else:
        config = Config()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(config.app_data_dir / "sync.log", encoding="utf-8"),
        ],
    )
    log = logging.getLogger(__name__)

    from fgc_sync.services.updater import cleanup_after_update
    cleanup_after_update()

    from PySide6.QtWidgets import QApplication

    from fgc_sync.controllers.app_controller import AppController
    from fgc_sync.views.styles import get_stylesheet

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName("FGC Calendar Sync")
    app.setStyleSheet(get_stylesheet())

    # Store controller on the QApplication to prevent garbage collection
    controller = AppController(config)
    app.controller = controller  # prevent GC
    controller.start()

    log.info("Event loop starting")
    exit_code = app.exec()
    log.info("Event loop exited with code %d", exit_code)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
