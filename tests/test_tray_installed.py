"""The installed tray opens RobotDx's own window; without the desktop entry it keeps the browser URL."""

from io_diagnostics.tray import as_installed, chip_model, dot_status, top_bar


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
    assert model["title"] == "RobotDx"
    assert not any(str(item.get("action", "")).startswith("open:") for item in model["menu"])


def _attention(*levels):
    return [{"level": level, "name": f"thing {at}", "path": f"host/thing/{at}", "message": level} for at, level in enumerate(levels)]


def test_the_top_bar_shows_no_text_only_the_status_dot():
    """Zach (2026-10-23): "get rid of the IO and the count... just a simple icon that shows a color for the status"."""

    quiet = top_bar(chip_model({"devices": 3, "attention": []}, [], ""))
    assert quiet["label"] == "" and quiet["label_guide"] == "" and quiet["icon"] == "ok"
    loud = top_bar(chip_model({"devices": 3, "attention": _attention("warn", "error")}, [], ""))
    assert loud["label"] == "" and loud["icon"] == "error"
    assert any("need" in item.get("text", "") for item in loud["menu"])  # the menu still says what needs you


def test_the_dot_colour_is_error_then_warn_then_grey():
    assert dot_status([]) == "ok"
    assert dot_status(_attention("stale", "warn")) == "warn"  # an actionable warning is not hidden behind grey
    assert dot_status(_attention("warn", "error", "stale")) == "error"
    assert dot_status(_attention("unknown")) == "stale"


def test_every_status_has_an_icon():
    """Each status the dot can take has a Big Sticker icon (contract/topbar), its heart recoloured, its face white."""

    import pytest

    pytest.importorskip("gi")
    from contract.indicator import FACE, HEARTS, TOPBAR, recolour

    statuses = {dot_status(_attention(level)) for level in ("ok", "warn", "error", "stale", "unknown")} | {"ok"}
    assert statuses <= set(HEARTS)
    for status in statuses:
        drawn = recolour((TOPBAR / f"{status}.svg").read_text(), HEARTS[status])
        assert f'fill="{FACE}"' in drawn and f'fill="{HEARTS[status]}"' in drawn, status
