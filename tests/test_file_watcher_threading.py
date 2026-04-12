"""Verify the file watcher callback is marshaled to the Qt main thread."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from fgc_sync.services.file_watcher import _ChangeHandler


@pytest.fixture
def _skip_no_qt():
    """Skip if PySide6 is not available."""
    pytest.importorskip("PySide6")


@pytest.mark.usefixtures("_skip_no_qt")
class TestFileWatcherThreadSafety:
    """The file watcher fires from a background thread, so the app controller
    must marshal the callback to the Qt main thread via QMetaObject.invokeMethod
    instead of calling request_sync directly."""

    def test_app_controller_uses_invoke_method(self):
        """_start_watcher must use QMetaObject.invokeMethod, not a direct call."""
        import inspect

        from fgc_sync.controllers.app_controller import AppController

        source = inspect.getsource(AppController._start_watcher)
        assert "QMetaObject.invokeMethod" in source, (
            "_start_watcher must use QMetaObject.invokeMethod to marshal "
            "the file watcher callback to the Qt main thread"
        )

    def test_request_sync_is_a_slot(self):
        """request_sync must be decorated with @Slot() so it can be invoked
        cross-thread via QMetaObject.invokeMethod."""

        from fgc_sync.controllers.sync_controller import SyncController

        meta = SyncController.staticMetaObject
        found = False
        for i in range(meta.methodCount()):
            method = meta.method(i)
            if method.name() == b"request_sync":
                found = True
                break
        assert found, (
            "SyncController.request_sync must be registered as a Qt Slot "
            "so QMetaObject.invokeMethod can call it cross-thread"
        )


class TestChangeHandlerFiresCallback:
    """The raw _ChangeHandler must call its callback — it's the app
    controller's responsibility to wrap that callback for thread safety."""

    def test_fire_calls_callback(self):
        callback = MagicMock()
        handler = _ChangeHandler("test.lua", callback)
        handler._fire()
        callback.assert_called_once()

    def test_fire_swallows_callback_exception(self):
        callback = MagicMock(side_effect=RuntimeError("boom"))
        handler = _ChangeHandler("test.lua", callback)
        # Must not raise
        handler._fire()
        callback.assert_called_once()
