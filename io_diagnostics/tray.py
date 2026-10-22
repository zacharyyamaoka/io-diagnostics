"""The top-bar chip: "IO n ⚠" + Recent + what needs you + "Open dashboard", through V2's real AppIndicator.

``chip_model`` is pure (no GTK) and is what ``/api/v1/state`` returns as ``chip``, so the page and the tray show
one model. ``run_tray`` renders it with ``contract/indicator.py`` (Ayatana AppIndicator, system python's ``gi``);
the dashboard opens only when the user clicks "Open dashboard" (Zach's focus-safe opener, never at start).
Tests build the model; they never show the indicator.
"""

from __future__ import annotations

MARK = {"error": "✖", "warn": "▲", "unknown": "?", "stale": "◷", "ok": "·"}  # shapes, not colour dots (decision 3)
LABEL_GUIDE = "IO 00 ⚠"
MENU_TEXT = 110  # DBusMenu rows never wrap; past this a row ends in an explicit "…" (the dashboard has the full text)


def _fit(text: str) -> str:
    return text if len(text) <= MENU_TEXT else text[:MENU_TEXT - 1].rstrip() + "…"


def recent_text(event: dict) -> str:
    """One Recent row. A fault transition reads by entity name ("D405 ·6036: fault confirmed (CameraMoved)"),
    since the FaultManager's own sentence names the path."""

    args = event.get("args") or {}
    if str(event.get("code", "")).startswith("Fault") and event.get("name"):
        return f"{event['name']}: {event.get('kind', '')} ({args.get('fault', '')})"
    return str(event.get("message", ""))


def chip_model(summary: dict, recent: list[dict], url: str = "") -> dict:
    """The contract/indicator.py model. The count is the root rollup's independent causes that are not OK, so one
    dead bus is one, not N+1 (knock-on collapse)."""

    attention = summary.get("attention", [])
    devices = summary.get("devices", 0)
    count = len(attention)
    menu: list[dict] = [{"text": f"{count} need{'s' if count == 1 else ''} you · {devices} devices" if count
                         else f"All {devices} devices OK"}, {"sep": True}, {"text": "Recent"}]
    menu += [{"text": _fit(f"   {event.get('time', '')[:5]}   {recent_text(event)}")} for event in recent[:5]] or [{"text": "   nothing yet"}]
    if attention:
        menu.append({"sep": True})
        for item in attention[:12]:
            device = item.get("device") or {}
            where = item["name"] if not device or device.get("path") == item["path"] else f"{device['name']} › {item['name']}"
            menu.append({"text": _fit(f"{MARK.get(item['level'], '?')}  {where}  —  {item['message']}")})
    menu.append({"sep": True})
    if url:
        menu.append({"text": "Open dashboard", "action": f"open:{url}"})
    menu.append({"text": "Quit", "action": "quit"})
    return {"icon": "attention" if count else "ok", "label": f"IO {count} ⚠" if count else "IO ✓", "label_guide": LABEL_GUIDE,
            "title": "IO diagnostics", "menu": menu}


def gi_available() -> bool:
    try:
        import gi  # noqa: F401
    except ImportError:
        return False
    return True


def run_tray(model_source, *, interval_s: float = 2.0, on_quit=None) -> None:
    """Show the chip and refresh it every ``interval_s`` from ``model_source()``. Blocks in the GTK main loop."""

    from contract.indicator import Indicator  # pins Gtk 3.0 before Gtk is imported
    from gi.repository import GLib, Gtk

    tray = Indicator("io-diagnostics", "IO diagnostics")
    state = {"model": None}

    def update() -> bool:
        try:
            model = model_source()
        except Exception:  # noqa: BLE001 - keep the last menu rather than kill the tray
            return True
        if state["model"] is None:
            tray.show(model)  # first time: V2's label-after-registration dance
        elif model != state["model"]:
            # WHY not tray.show() again: it blanks the label for 2 s on every change (it waits for registration)
            tray.indicator.set_icon_full(f"io-diagnostics-{model.get('icon', 'ok')}", model.get("title", "IO diagnostics"))
            tray.indicator.set_label(model.get("label", ""), model.get("label_guide", ""))
            menu = Gtk.Menu()
            for item in model.get("menu", []):
                menu.append(tray._item(item))  # noqa: SLF001 - the shared renderer's item builder
            menu.show_all()
            tray.indicator.set_menu(menu)
        state["model"] = model
        return True

    update()
    GLib.timeout_add(int(interval_s * 1000), update)
    try:
        Gtk.main()
    finally:
        if on_quit:
            on_quit()
