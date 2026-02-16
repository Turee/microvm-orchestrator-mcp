"""Rich TUI for live VM monitoring."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..tools import Orchestrator
    from .log_capture import LogCapture


def start_tui(
    orchestrator: Orchestrator | None = None,
    log_capture: LogCapture | None = None,
) -> None:
    """Launch the TUI application.

    Args:
        orchestrator: Orchestrator instance for read-only task access.
        log_capture: Optional LogCapture handler for server log tab.
    """
    from .app import TUIApp

    app = TUIApp(orchestrator, log_capture=log_capture)
    app.run()
