# Vendored terminal emulator

The page is served offline, so the terminal emulator ships in the checkout (docs/runs-v1.md section 7).

- `xterm.js`, `xterm.css`: xterm.js 6.0.0 (`@xterm/xterm`, MIT, see LICENSE), the emulator VS Code and Jupyter use.
- `addon-fit.js`: `@xterm/addon-fit` 0.11.0 (MIT, same licence), sizes the terminal to its box.

Both are the npm packages' `lib/*.js` (UMD builds) and `css/xterm.css`, unmodified except that the trailing `//# sourceMappingURL` comment is
dropped (the maps are not shipped, so the browser would ask for a file that is not there). To update: `npm pack @xterm/xterm @xterm/addon-fit` in an empty directory and copy the same four files.
