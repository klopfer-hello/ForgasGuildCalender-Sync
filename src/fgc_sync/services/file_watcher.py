"""File watcher for WoW SavedVariables using watchdog."""

from __future__ import annotations

import logging
from pathlib import Path
from threading import Timer

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from fgc_sync.services.config import SAVED_VARIABLES_FILENAME

log = logging.getLogger(__name__)

DEBOUNCE_SECONDS = 2.0


class _ChangeHandler(FileSystemEventHandler):
    def __init__(self, target_filename: str, callback):
        super().__init__()
        self._target = target_filename.lower()
        self._callback = callback
        self._timer: Timer | None = None

    def on_modified(self, event):
        if event.is_directory:
            return
        if Path(event.src_path).name.lower() == self._target:
            if self._timer is not None:
                self._timer.cancel()
            self._timer = Timer(DEBOUNCE_SECONDS, self._fire)
            self._timer.daemon = True
            self._timer.start()

    def _fire(self):
        log.info("SavedVariables change detected, triggering sync")
        try:
            self._callback()
        except Exception:
            log.exception("Error in file watcher callback")


class FileWatcher:
    """Watches the SavedVariables directory for changes."""

    def __init__(self, directory: Path, callback, filename: str = SAVED_VARIABLES_FILENAME):
        self._directory = directory
        self._callback = callback
        self._filename = filename
        self._observer: Observer | None = None

    @property
    def is_running(self) -> bool:
        return self._observer is not None and self._observer.is_alive()

    def start(self):
        if self.is_running:
            return
        handler = _ChangeHandler(self._filename, self._callback)
        self._observer = Observer()
        self._observer.schedule(handler, str(self._directory), recursive=False)
        self._observer.daemon = True
        self._observer.start()
        log.info("File watcher started on %s", self._directory)

    def stop(self):
        if self._observer is not None:
            self._observer.stop()
            self._observer.join(timeout=5)
            self._observer = None
            log.info("File watcher stopped")

    def restart(self, directory: Path):
        self.stop()
        self._directory = directory
        self.start()
