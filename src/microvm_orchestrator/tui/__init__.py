"""Rich TUI for live VM monitoring."""


def start_tui():
    """Launch the TUI application."""
    from .app import TUIApp

    app = TUIApp()
    app.run()
