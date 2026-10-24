"""Render a top-bar menu model as a real AppIndicator (Ayatana, system python). Shared by V2, V4, V5.

Model: {"icon": "ok"|"warn"|"error"|"stale"|"attention", "label": str, "menu": [item...], "notify": {"title", "body"}?}
item:  {"text": str, "enabled": bool, "children": [item...], "action": str} or {"sep": true}
Actions are local and harmless ("copy:<text>", "open:<path>", "quit"); nothing here touches hardware.
DBusMenu renders text rows, submenus and separators only: no colour, no layout. That limit is
part of what V2/V4/V5 are judged on.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("AyatanaAppIndicator3", "0.1")
gi.require_version("Notify", "0.7")
from gi.repository import AyatanaAppIndicator3 as AppIndicator  # noqa: E402
from gi.repository import GLib, Gtk, Notify  # noqa: E402

OPEN = "/home/bam/.local/bin/open"  # Zach's focus-safe opener (never xdg-open)


# The heart's colour per status: the brand rose when all is OK, then Discord's own status palette (idle amber,
# red, offline grey); the error and stale hearts are also broken / dotted (brand/logos states), so the status reads
# without colour too. "attention" is the older two-icon model's name (V2/V4/V5 prototypes, the notification) = warn.
HEARTS = {"ok": "#f483a5", "warn": "#f0b232", "error": "#f23f43", "stale": "#80848e", "attention": "#f0b232"}
FACE = "#ffffff"
TOPBAR = Path(__file__).resolve().parent / "topbar"  # brand/logos r3-01 Big Sticker symbolic + states, copied
SVG = "{http://www.w3.org/2000/svg}"


def recolour(svg_text: str, heart: str) -> str:
    """The symbolic mark with its head and face (the group's first four shapes: outline, bridge, two eyes) in white
    and every later shape (the heart, whole or broken or dotted) in ``heart``."""

    import xml.etree.ElementTree as ElementTree

    ElementTree.register_namespace("", SVG[1:-1])
    root = ElementTree.fromstring(svg_text)
    group = root.find(f"{SVG}g")
    group.set("fill", FACE)
    for shape in list(group)[4:]:
        shape.set("fill", heart)
    return ElementTree.tostring(root, encoding="unicode")


def draw_icons(directory: Path) -> None:
    """22 px glyphs: the RobotDx mark, Big Sticker (Zach, 2026-10-24: "final logo selection is the original Big
    Sticker Heart"), its face always the same and white, its heart coloured and shaped by the status. WHY colour on
    the heart: Zach (2026-10-23) "a simple icon that shows a color for the status similar to discord", and health
    shows only through the heart (the logo rounds' rule). WHY PNGs drawn here, not the -symbolic files: GNOME
    recolours a symbolic icon to the panel's text colour, which would drop the status colour."""

    gi.require_version("GdkPixbuf", "2.0")
    from gi.repository import GdkPixbuf

    for status, heart in HEARTS.items():
        source = TOPBAR / f"{'warn' if status == 'attention' else status}.svg"
        loader = GdkPixbuf.PixbufLoader.new_with_type("svg")
        loader.set_size(22, 22)
        loader.write(recolour(source.read_text(), heart).encode())
        loader.close()
        loader.get_pixbuf().savev(str(directory / f"io-diagnostics-{status}.png"), "png", [], [])


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
