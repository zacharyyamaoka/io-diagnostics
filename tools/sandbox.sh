#!/usr/bin/env bash
# Run one driver script against a throwaway GNOME Shell 46, never Zach's session.
#
# Copied from /home/bam/claude-hub-tray/test/tray_sandbox.sh (its proven pattern), trimmed:
#   - its own invisible X display (Xvfb), never Zach's screen or top bar;
#   - its own D-Bus session (dbus-run-session, the hub's no-activation config);
#   - a scratch HOME / XDG dirs and GSETTINGS_BACKEND=keyfile, so extensions are "enabled" only in
#     a scratch keyfile and his dconf database is never opened (checked before/after below).
# Extensions under prototypes/*/extension/ named in IO_SANDBOX_EXTENSIONS are copied into the
# scratch HOME only. sandbox-driver@io-diagnostics turns on Shell's Eval in THIS shell only, so a
# driver can open menus without synthetic clicks.
#
#   tools/sandbox.sh <driver.py> <scratch-parent-dir> ["extension-uuid ..."]
set -euo pipefail

DRIVER=$(realpath -e "$1")
PARENT=$(realpath -e "$2")
EXTENSIONS=${3:-${IO_SANDBOX_EXTENSIONS:-}}
HERE=$(cd "$(dirname "$0")" && pwd)
REPO=$(dirname "$HERE")
SCR=$(mktemp -d "$PARENT/io-sandbox.XXXXXX")
SCREEN=${IO_SANDBOX_SCREEN:-1600x900}
echo "run dir: $SCR"

EXT_DIR="$SCR/home/.local/share/gnome-shell/extensions"
mkdir -p "$SCR"/{home/.config/glib-2.0/settings,run,out} "$EXT_DIR"
chmod 700 "$SCR/run"
cp -r "$HERE/sandbox-driver@io-diagnostics" "$EXT_DIR/"
ENABLED="'ubuntu-appindicators@ubuntu.com', 'sandbox-driver@io-diagnostics'"
for uuid in $EXTENSIONS; do
  src=$(ls -d "$REPO"/prototypes/*/extension/"$uuid")
  cp -r "$src" "$EXT_DIR/"
  ENABLED="$ENABLED, '$uuid'"
done
printf "[org/gnome/shell]\nenabled-extensions=[%s]\ndisable-user-extensions=false\nwelcome-dialog-last-shown-version='999'\n\n[org/gnome/desktop/interface]\ncolor-scheme='prefer-dark'\ngtk-theme='Yaru-dark'\n" "$ENABLED" > "$SCR/home/.config/glib-2.0/settings/keyfile"

DCONF="$HOME/.config/dconf/user"
dconf_before=$(stat -c %Y "$DCONF" 2>/dev/null || echo none)

D=""
for n in $(seq 150 199); do [ -e "/tmp/.X11-unix/X$n" ] || [ -e "/tmp/.X$n-lock" ] || { D=$n; break; }; done
Xvfb ":$D" -screen 0 "${SCREEN}x24" -nolisten tcp +extension GLX >"$SCR/out/xvfb.log" 2>&1 &
XVFB=$!
trap 'kill $XVFB 2>/dev/null || true' EXIT
for _ in $(seq 1 50); do [ -e "/tmp/.X11-unix/X$D" ] && break; sleep 0.1; done

INNER='
set -u
gnome-shell --x11 --replace --mode="${IO_SANDBOX_MODE:-ubuntu}" >"$IO_SANDBOX_DIR/out/gnome-shell.log" 2>&1 &
SHELL_PID=$!
for _ in $(seq 1 150); do gdbus call --session --dest org.gnome.Shell --object-path /org/gnome/Shell --method org.gnome.Shell.Eval "1" >/dev/null 2>&1 && break; sleep 0.2; done
/usr/bin/python3 "$@"
rc=$?
kill "$SHELL_PID" 2>/dev/null
exit $rc
'
set +e
env -i \
  HOME="$SCR/home" \
  PATH="/usr/local/bin:/usr/bin:/bin" \
  LANG="${LANG:-C.UTF-8}" \
  DISPLAY=":$D" \
  XDG_RUNTIME_DIR="$SCR/run" \
  XDG_CONFIG_HOME="$SCR/home/.config" \
  XDG_DATA_HOME="$SCR/home/.local/share" \
  XDG_CACHE_HOME="$SCR/home/.cache" \
  GSETTINGS_BACKEND=keyfile \
  IO_SANDBOX_DIR="$SCR" \
  IO_SANDBOX_SCREEN="$SCREEN" \
  IO_REPO="$REPO" \
  IO_SANDBOX_MODE="${IO_SANDBOX_MODE:-ubuntu}" \
  dbus-run-session --config-file=/home/bam/claude-hub-tray/test/private-session-bus.conf -- bash -c "$INNER" inner "$DRIVER" 2>&1 | tee "$SCR/out/driver.log"
rc=${PIPESTATUS[0]}
set -e

dconf_after=$(stat -c %Y "$DCONF" 2>/dev/null || echo none)
if [ "$dconf_before" = "$dconf_after" ]; then echo "PASS  Zach's dconf database untouched (mtime $dconf_after)"; else echo "FAIL  dconf mtime changed: $dconf_before -> $dconf_after"; rc=1; fi
echo "run dir: $SCR"
exit "$rc"
