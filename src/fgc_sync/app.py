"""Application bootstrap — creates QApplication and starts the controller."""

from __future__ import annotations

import logging
import sys

from PySide6.QtWidgets import QApplication

from fgc_sync.controllers.app_controller import AppController
from fgc_sync.services.config import Config
from fgc_sync.views.styles import get_stylesheet


def main():
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
