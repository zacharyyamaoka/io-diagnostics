// V1 "Quick Settings": Rack IO as a native GNOME Shell 46 Quick Settings menu toggle, modelled on
// GNOME's own Bluetooth device list. A panel icon appears only when something needs you.
//
// WHY nothing but data inside the shell: the extension only renders the JSON that helper.py prints
// (helper.py runs the drivers' diagnostics out of process), so a hung or crashing driver can never
// freeze or crash the top bar. Prototype: copied into a scratch HOME by tools/sandbox.sh only.
import GObject from 'gi://GObject';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import St from 'gi://St';
import Clutter from 'gi://Clutter';

import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as PopupMenu from 'resource:///org/gnome/shell/ui/popupMenu.js';
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';
import {QuickMenuToggle, SystemIndicator} from 'resource:///org/gnome/shell/ui/quickSettings.js';

const REPO = GLib.getenv('IO_REPO') || '/home/bam/io-diagnostics';
const HELPER = `${REPO}/prototypes/v1-quick-settings/helper.py`;
const MARK = {ok: '', warn: 'dialog-warning-symbolic', error: 'dialog-error-symbolic', blocked: 'changes-prevent-symbolic'};

function runHelper(frame, callback) {
    const proc = Gio.Subprocess.new(['/usr/bin/python3', HELPER, '--frame', frame], Gio.SubprocessFlags.STDOUT_PIPE);
    proc.communicate_utf8_async(null, null, (process, result) => {
        try {
            const [, stdout] = process.communicate_utf8_finish(result);
            callback(JSON.parse(stdout));
        } catch (error) {
            console.error(`io-diagnostics helper failed: ${error}`);
        }
    });
}

function infoItem(text, iconName = null) {
    const item = iconName ? new PopupMenu.PopupImageMenuItem(text, iconName) : new PopupMenu.PopupMenuItem(text);
    item.reactive = false;
    // WHY wrap, not ellipsize: a fault line is the reason to open the row; it must read in full.
    item.label.clutter_text.ellipsize = 0;
    item.label.clutter_text.line_wrap = true;
    item.label.x_expand = true;
    return item;
}

const IoToggle = GObject.registerClass(
class IoToggle extends QuickMenuToggle {
    constructor() {
        super({title: 'Rack IO', subtitle: '…', iconName: 'network-wired-symbolic', toggleMode: false});
        this.menu.setHeader('network-wired-symbolic', 'Rack IO', '');
        this._devices = new PopupMenu.PopupMenuSection();
        this.menu.addMenuItem(this._devices);
        this.menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());
        this._footer = infoItem('Drivers: moteus_servo · intel_rgbd · obsbot_ptz_camera');
        this.menu.addMenuItem(this._footer);
    }

    render(model) {
        this.subtitle = model.subtitle;
        this.checked = model.attention;
        this.menu.setHeader('network-wired-symbolic', 'Rack IO', `${model.devices.length} devices · ${model.subtitle}`);
        this._devices.removeAll();
        this._rows = {};
        for (const device of model.devices) {
            const row = new PopupMenu.PopupSubMenuMenuItem(device.kind === 'bus' ? device.key : device.title, true);
            row.icon.icon_name = device.kind === 'bus' ? 'network-wired-symbolic' : 'camera-web-symbolic';
            const status = new St.Label({text: device.short, y_align: Clutter.ActorAlign.CENTER, style: 'font-size: 0.85em; opacity: 0.75; padding-left: 8px;'});
            row.insert_child_below(status, row._triangleBin);
            if (MARK[device.level])
                row.insert_child_below(new St.Icon({icon_name: MARK[device.level], style_class: 'popup-menu-icon'}), row._triangleBin);
            row.menu.addMenuItem(infoItem(device.headline));
            for (const [, text] of device.ids)
                row.menu.addMenuItem(infoItem(text));
            const pick = {identity: ['ids', 'usb serial', 'sdk serial', '/dev/fdcanusb', 'position'], versions: ['CAN 15', 'firmware', 'latest'], comms: ['bus', 'meaning', 'link']};
            for (const [section, labels] of Object.entries(pick)) {
                for (const entry of device.sections[section] || []) {
                    if (labels.includes(entry.label))
                        row.menu.addMenuItem(infoItem(`${entry.label}: ${entry.value}`));
                }
            }
            if (device.gate === 'blocked')
                row.menu.addMenuItem(infoItem(`Checks wait: ${device.gate_reason}`, 'changes-prevent-symbolic'));
            for (const action of device.actions) {
                const item = new PopupMenu.PopupMenuItem(`${action}`);
                item.setSensitive(false); // WHY: this round runs passive-only; a confirmed + gated path exists in contract/
                row.menu.addMenuItem(item);
            }
            this._devices.addMenuItem(row);
            this._rows[device.key] = row;
        }
    }
});

const IoIndicator = GObject.registerClass(
class IoIndicator extends SystemIndicator {
    constructor() {
        super();
        this._indicator = this._addIndicator();
        this._indicator.icon_name = 'dialog-warning-symbolic';
        this._indicator.visible = false;
        this.toggle = new IoToggle();
        this.quickSettingsItems.push(this.toggle);
    }

    refresh(frame, notify) {
        runHelper(frame, model => {
            this.toggle.render(model);
            this._indicator.visible = model.attention;
            if (notify)
                Main.notify(model.notify.title, model.notify.body);
        });
    }
});

export default class IoDiagnosticsExtension extends Extension {
    enable() {
        this._indicator = new IoIndicator();
        Main.panel.statusArea.quickSettings.addExternalIndicator(this._indicator);
        this._indicator.refresh('F3', false);
        // Test hook for tools/sandbox.sh drivers (Eval is only on inside that throwaway shell).
        globalThis.__ioDiagnostics = this._indicator;
    }

    disable() {
        delete globalThis.__ioDiagnostics;
        this._indicator.quickSettingsItems.forEach(item => item.destroy());
        this._indicator.destroy();
        this._indicator = null;
    }
}
