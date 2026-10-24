"""Render a top-bar menu model as a real AppIndicator (Ayatana, system python). Shared by V2, V4, V5.

Model: {"icon": "ok"|"warn"|"error"|"stale"|"attention", "label": str, "menu": [item...], "notify": {"title", "body"}?}
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


# The status dot's colours: Discord's own status palette (red, idle amber, offline grey). "ok" has no dot.
# "attention" is the older two-icon model's name (the V2/V4/V5 prototypes and the notification), drawn as warn.
DOTS = {"ok": None, "warn": (0.941, 0.698, 0.196), "error": (0.949, 0.247, 0.263), "stale": (0.502, 0.518, 0.557),
        "attention": (0.941, 0.698, 0.196)}


def draw_icons(directory: Path) -> None:
    """22 px glyphs: a calm robot face (a rounded head, two eyes joined by a line), plain or with a status dot.
    WHY a face: Zach's RobotDiagnostics placeholder (2026-10-23) until a logo is picked from brand/logos; an original
    two-dot face in the spirit of a health robot, not a copy of any character. WHY a dot in a cut-out ring at the
    bottom right: Zach (2026-10-23) "a simple icon that shows a color for the status similar to discord", whose
    badge sits there with a transparent gap, so the colour reads on any top bar without touching the face's lines."""

    for status, dot in DOTS.items():
        name = f"io-diagnostics-{status}"
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 22, 22)
        context = cairo.Context(surface)
        context.set_source_rgba(1, 1, 1, 0.95)
        context.set_line_width(1.6)
        context.save()
        context.translate(11, 12)
        context.scale(9.0, 7.0)
        context.arc(0, 0, 1, 0, 2 * math.pi)
        context.restore()
        context.stroke()
        for x in (7.2, 14.8):
            context.arc(x, 12, 1.7, 0, 2 * math.pi)
            context.fill()
        context.set_line_width(1.1)
        context.move_to(7.2, 12)
        context.line_to(14.8, 12)
        context.stroke()
        if dot:
            context.set_operator(cairo.OPERATOR_CLEAR)
            # WHY at (18.2, 18.2): the ring clears the right eye and the line's end (5.5 px away), cutting only the
            # head's outline, so the face itself never changes (measured on the rendered strip, 2026-10-23)
            context.arc(18.2, 18.2, 4.9, 0, 2 * math.pi)
            context.fill()
            context.set_operator(cairo.OPERATOR_OVER)
            context.set_source_rgb(*dot)
            context.arc(18.2, 18.2, 3.5, 0, 2 * math.pi)
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
        self.indicator.set_icon_full(f"io-diagnostics-{model.get('icon', 'ok')}", model.get("title", "RobotDx"))
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
        # WHY a leading "_" mnemonic: ubuntu-appindicators unescapes labels with a NON-global
        # label.replace(/_([^_])/, '$1') (dbusMenu.js:723, GNOME 46), so a plain label showed
        # "rotor_to__output__ratio" (measured). A leading mnemonic absorbs that one replace and the
        # authored text, underscores included, renders exactly as written.
        item = Gtk.MenuItem.new_with_mnemonic("_" + spec["text"])
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
        elif action.startswith("launch:"):
            # WHY gtk-launch: it starts an installed desktop entry (RobotDiagnostics' own app window), the stock way
            item.connect("activate", lambda _i, desktop_id=action[7:]: subprocess.Popen(["gtk-launch", desktop_id]))
        elif action == "quit":
            item.connect("activate", lambda _i: Gtk.main_quit())
        return item
