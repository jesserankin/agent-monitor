"""Agent Monitor TUI application."""

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import DataTable, Footer, Header


class AgentMonitorApp(App):
    TITLE = "Agent Monitor"
    CSS_PATH = "monitor.tcss"
    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("r", "refresh", "Refresh"),
        Binding("enter", "switch_group", "Switch"),
    ]

    def compose(self) -> ComposeResult:
        yield Header()
        yield DataTable(id="sessions")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one(DataTable)
        table.cursor_type = "row"
        table.add_columns("Group", "Session", "Status", "Task")

    def action_refresh(self) -> None:
        pass

    def action_switch_group(self) -> None:
        pass


def main():
    app = AgentMonitorApp()
    app.run()


if __name__ == "__main__":
    main()
