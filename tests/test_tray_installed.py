"""The installed tray opens RobotDiagnostics' own window; without the desktop entry it keeps the browser URL."""

from io_diagnostics.tray import as_installed, chip_model


def _chip():
    return chip_model({"level": "ok", "counts": {}, "attention": []}, [], url="http://127.0.0.1:8820/")


def test_without_the_desktop_entry_the_dashboard_opens_the_url(tmp_path):
    model = as_installed(_chip(), entry=tmp_path / "robotdiagnostics.desktop")
    assert {"text": "Open dashboard", "action": "open:http://127.0.0.1:8820/"} in model["menu"]


def test_installed_the_dashboard_opens_the_app_window(tmp_path):
    entry = tmp_path / "robotdiagnostics.desktop"
    entry.write_text("[Desktop Entry]\n")
    model = as_installed(_chip(), entry=entry)
    assert {"text": "Open dashboard", "action": "launch:robotdiagnostics"} in model["menu"]
    assert model["title"] == "RobotDiagnostics"
    assert not any(str(item.get("action", "")).startswith("open:") for item in model["menu"])
