"""Rich TUI for live VM monitoring."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..tools import Orchestrator


def start_tui(orchestrator: Orchestrator | None = None) -> None:
    """Launch the TUI application.

    Args:
        orchestrator: Orchestrator instance for read-only task access.
    """
    from .app import TUIApp

    app = TUIApp(orchestrator)
    app.run()
