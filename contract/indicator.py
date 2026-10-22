"""Render a top-bar menu model as a real AppIndicator (Ayatana, system python). Shared by V2, V4, V5.

Model: {"icon": "ok"|"attention", "label": str, "menu": [item...], "notify": {"title", "body"}?}
item:  {"text": str, "enabled": bool, "children": [item...], "action": str} or {"sep": true}
Actions are local and harmless ("copy:<text>", "open:<path>", "quit"); nothing here touches hardware.
DBusMenu renders text rows, submenus and separators only: no colour, no layout. That limit is
part of what V2/V4/V5 are judged on.
"""

from __future__ import annotations

import math
import os
import subprocess
import tempfile
from pathlib import Path

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("AyatanaAppIndicator3", "0.1")
gi.require_version("Notify", "0.7")
import cairo  # noqa: E402
from gi.repository import AyatanaAppIndicator3 as AppIndicator  # noqa: E402
from gi.repository import GLib, Gtk, Notify  # noqa: E402

OPEN = "/home/bam/.local/bin/open"  # Zach's focus-safe opener (never xdg-open)


def draw_icons(directory: Path) -> None:
    """22 px symbolic glyphs: a bus (three taps on a line), plain and with an amber attention dot."""

    for name, attention in (("io-diagnostics-ok", False), ("io-diagnostics-attention", True)):
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 22, 22)
        context = cairo.Context(surface)
        context.set_source_rgba(1, 1, 1, 0.95)
        context.set_line_width(1.6)
        context.move_to(3, 15)
        context.line_to(19, 15)
        context.stroke()
        for x in (5, 11, 17):
            context.move_to(x, 15)
            context.line_to(x, 9)
            context.stroke()
            context.arc(x, 7.5, 2.2, 0, 2 * math.pi)
            context.fill()
        if attention:
            context.set_source_rgb(0.98, 0.65, 0.15)
            context.arc(18, 5, 3.6, 0, 2 * math.pi)
            context.fill()
        surface.write_to_png(str(directory / f"{name}.png"))


class Indicator:
    def __init__(self, app_id: str, title: str):
        self.icon_dir = Path(tempfile.mkdtemp(prefix=f"{app_id}-icons-", dir=os.environ.get("XDG_RUNTIME_DIR")))
        draw_icons(self.icon_dir)
        self.indicator = AppIndicator.Indicator.new(app_id, "io-diagnostics-ok", AppIndicator.IndicatorCategory.HARDWARE)
        self.indicator.set_icon_theme_path(str(self.icon_dir))
        self.indicator.set_status(AppIndicator.IndicatorStatus.ACTIVE)
        self.indicator.set_title(title)
        Notify.init(title)

    def show(self, model: dict) -> None:
        self.indicator.set_icon_full(f"io-diagnostics-{model.get('icon', 'ok')}", model.get("title", "IO diagnostics"))
        label, guide = model.get("label", ""), model.get("label_guide", "")
        # WHY set the label only after registration: ubuntu-appindicators (GNOME 46) builds its label
        # actor on a label CHANGE; a label set before the item registered never showed (measured).
        self.indicator.set_label("", "")
        GLib.timeout_add(2000, lambda: (self.indicator.set_label(label, guide), False)[1])
        menu = Gtk.Menu()
        for item in model.get("menu", []):
            menu.append(self._item(item))
        menu.show_all()
        self.indicator.set_menu(menu)

    def notify(self, title: str, body: str) -> None:
        # WHY a plain banner: no actions, default urgency, so GNOME shows it without taking focus.
        Notify.Notification.new(title, body, str(self.icon_dir / "io-diagnostics-attention.png")).show()

    def _item(self, spec: dict) -> Gtk.MenuItem:
        if spec.get("sep"):
            return Gtk.SeparatorMenuItem()
        item = Gtk.MenuItem(label=spec["text"])
        item.set_sensitive(spec.get("enabled", bool(spec.get("action") or spec.get("children"))))
        if spec.get("children"):
            submenu = Gtk.Menu()
            for child in spec["children"]:
                submenu.append(self._item(child))
            item.set_submenu(submenu)
        action = spec.get("action", "")
        if action.startswith("copy:"):
            item.connect("activate", lambda _i, text=action[5:]: Gtk.Clipboard.get_default(Gtk.Window().get_display()).set_text(text, -1))
        elif action.startswith("open:"):
            item.connect("activate", lambda _i, path=action[5:]: subprocess.Popen([OPEN, path]))
        elif action == "quit":
            item.connect("activate", lambda _i: Gtk.main_quit())
        return item
