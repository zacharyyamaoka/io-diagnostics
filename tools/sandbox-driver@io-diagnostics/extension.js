// Test-only: lets tools/sandbox.sh drivers call org.gnome.Shell.Eval in the THROWAWAY shell,
// so menus can be opened for screenshots without synthetic clicks. Copied into a scratch HOME
// only; never installed or enabled in Zach's session.
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';

export default class SandboxDriver extends Extension {
    enable() {
        global.context.unsafe_mode = true;
    }

    disable() {
        global.context.unsafe_mode = false;
    }
}
