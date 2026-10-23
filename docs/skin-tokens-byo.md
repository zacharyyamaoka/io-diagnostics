# Bring your own tokens — writing an IO diagnostics skin

*2026-10-22, skin lane 7. Contract: `docs/round2-seams.md` §1–2. Starting point: `io_diagnostics/web/skins/unstyled.css`
(system colours only). Reference look: `skins/claude.css`.*

A skin is one file, `io_diagnostics/web/skins/<id>.css`, holding **only token declarations** inside exactly two
selectors, `[data-skin="<id>"][data-mode="light"]` and `[data-skin="<id>"][data-mode="dark"]`. Every renderer, the
tree and the Table read colours, fonts and sizes through these tokens, so a skin never needs a selector of its own.
Copy `unstyled.css`, rename the two selectors, replace the values a brand owns, then flip your line in
`skins/index.json` to `"ready": true`.

## The 39 BASE tokens, by role

Every one of them **must be declared in both mode rules** — the test reads the skin's own CSSOM rule, so a token
inherited from the `tokens.css` fallback counts as missing. "Brand" = set it to your brand's value; "keep" = the
unstyled value is a fine default.

| Role | Tokens | Brand or keep |
|---|---|---|
| Surfaces | `--io-bg` (desk), `--io-surface` (tree, table, cards), `--io-surface-2` (toolbar, status bar, table head), `--io-surface-alt` (detail pane behind the cards) | **brand** |
| | `--io-field` (inputs, segmented track), `--io-popover` (pop-over, dialog) | keep |
| Window bar | `--io-chrome-bg`, `--io-chrome-text` (the desktop top-bar mock) | keep |
| Text | `--io-text` (row names, body), `--io-text-strong` (headings, selected name), `--io-muted` (labels, messages, column heads), `--io-faint` (ids, captions, counts) | **brand** |
| | `--io-off` (not connected: the whole row greyed), `--io-icon` (type-icon stroke) | keep |
| Lines | `--io-border` (pane / card edges), `--io-border-soft` (row separators) | keep |
| Interaction | `--io-accent` (active control, open chip, check boxes), `--io-accent-text` (links), `--io-focus` (focus ring) | **brand** |
| | `--io-hover`, `--io-selected` (keep it **neutral**), `--io-selected-line` | keep |
| Status | `--io-warn` (triangle), `--io-error` (octagon ×), `--io-stale` (hollow clock), `--io-unknown` (disc ?), `--io-on-status` (glyph knocked out of a fill) | **brand** — unstyled leaves them neutral and lets the shapes carry the level |
| | `--io-ok` (a passing precondition, a "plugged" event — never a row), `--io-info` (neutral facts), `--io-shadow` (mixed down, never raw) | keep |
| Type | `--io-font-ui`, `--io-font-mono` | **brand** |
| | `--io-font-display` (detail title, modal heads), `--io-font-size` (body, 13px) | keep |
| Shape | `--io-radius` (cards, inputs, buttons), `--io-radius-sm` (rows, tags), `--io-radius-lg` (pop-over, modal), `--io-row-h` (one tree **and** Table row), `--io-indent` (one nesting step) | keep |

The 28 DERIVED tokens (`--io-*-text`, `--io-*-soft`, `--io-*-line`, `--io-knock`, `--io-guide*`, `--io-control*`,
`--io-pressed`, `--io-mark`, `--io-overlay`, `--io-shadow-pop`, `--io-radius-pill`) are computed in `tokens.css`
from the base ones with `color-mix()`. Override one only when the mix fails a gate below — for example level words
(`--io-warn-text`) that fall under your contrast target, as `skins/contrast.css` does.

## The gates your skin must pass

`tests/test_tree_variants.js` picks up every ready skin automatically and runs, in light **and** dark, on the v6 tree
and the Table:

| Gate | What fails it |
|---|---|
| `tokens` | a token resolves empty on `<html>`, or your rule for that mode omits a BASE token |
| `quiet-ok` | an OK row paints any colour: sRGB chroma (max − min channel) ≥ 0.12 in its background, text, icon or border. Keep `--io-text`, `--io-muted`, `--io-faint`, `--io-icon`, `--io-border*`, `--io-hover`, `--io-selected` and `--io-selected-line` neutral — a tinted grey is fine, a brand-blue selection is not |
| `shapes`, `right` | a problem row has no visible shape, or it sits left of the name (structural; a skin breaks it only by hiding things) |
| `labels`, `mode` | structural and the Light / Dark control; a skin cannot break them |
| `literals` | a colour literal outside `tokens.css` and `skins/` (so: never put colours anywhere but your skin) |

Not automated yet; each skin lane measures them itself in headless Chrome and reports the numbers:

- **Text contrast (WCAG AA):** every text token ≥ 4.5:1 on every surface it is drawn on — `--io-surface`,
  `-2`, `-alt`, `--io-field`, `--io-popover`, `--io-hover`, `--io-selected` (the selected row is the usual worst
  case) and the control tint. `--io-off` is exempt, like disabled text.
- **Shape fills:** `--io-warn`, `--io-error`, `--io-stale`, `--io-unknown` ≥ 3:1 on `--io-surface` (and on
  `--io-selected`, where the selected problem row draws them).
- **Distinguishable:** the four shapes already differ by form; if you colour them, keep the four fills apart from
  each other too (CIE ΔE ≥ 20 is a comfortable margin), and never make warn and error the same hue.

## Example: a 20-line brand skin

Values here are checked: every text token ≥ 4.5:1 on every surface, every fill ≥ 3:1 on the surface and the
selection, every neutral under the 0.12 chroma bar.

```css
/* Skin · Acme — Acme's navy chrome and blue accent; everything else is the stock grammar. */
[data-skin="acme"][data-mode="light"] {
  --io-bg: #eef1f4; --io-surface: #ffffff; --io-surface-2: #f6f8fa; --io-surface-alt: #f3f5f7; --io-field: #ffffff; --io-popover: #ffffff;
  --io-chrome-bg: #0b2545; --io-chrome-text: #ffffff; --io-text: #1f2933; --io-text-strong: #0b1620; --io-muted: #52606d; --io-faint: #56636f;
  --io-off: #a7b1bb; --io-icon: #7b8794; --io-border: #d9dee3; --io-border-soft: #e8ecef; --io-hover: #f3f5f7; --io-selected: #e6eaee;
  --io-selected-line: #b8c2cc; --io-accent: #0a6cff; --io-accent-text: #0052cc; --io-focus: #0a6cff;
  --io-warn: #b26b00; --io-error: #c8102e; --io-stale: #6b7785; --io-unknown: #6f42c1; --io-on-status: #ffffff; --io-ok: #2f855a; --io-info: #0a6cff; --io-shadow: #0b1620;
  --io-font-ui: Inter, system-ui, sans-serif; --io-font-mono: ui-monospace, monospace; --io-font-display: Inter, system-ui, sans-serif; --io-font-size: 13px;
  --io-radius: 6px; --io-radius-sm: 4px; --io-radius-lg: 10px; --io-row-h: 28px; --io-indent: 14px;
}
[data-skin="acme"][data-mode="dark"] {
  --io-bg: #0b1620; --io-surface: #111d29; --io-surface-2: #16232f; --io-surface-alt: #0e1924; --io-field: #0b1620; --io-popover: #1a2733;
  --io-chrome-bg: #050b12; --io-chrome-text: #e6edf3; --io-text: #d9e2ec; --io-text-strong: #ffffff; --io-muted: #9aa5b1; --io-faint: #8d99a6;
  --io-off: #4b5763; --io-icon: #8d99a6; --io-border: #28333f; --io-border-soft: #1e2a37; --io-hover: #172430; --io-selected: #22303d;
  --io-selected-line: #404c58; --io-accent: #4c9aff; --io-accent-text: #6cb0ff; --io-focus: #4c9aff;
  --io-warn: #f0a020; --io-error: #ff5c6c; --io-stale: #9aa5b1; --io-unknown: #a98bff; --io-on-status: #0b1620; --io-ok: #48bb78; --io-info: #4c9aff; --io-shadow: #000000;
  --io-font-ui: Inter, system-ui, sans-serif; --io-font-mono: ui-monospace, monospace; --io-font-display: Inter, system-ui, sans-serif; --io-font-size: 13px;
  --io-radius: 6px; --io-radius-sm: 4px; --io-radius-lg: 10px; --io-row-h: 28px; --io-indent: 14px;
}
```

## What a skin cannot do today

Only tokens: no selectors, no structural CSS. A focus-ring width, a box-drawing tree (Terminal) or print-only rules
need a new token or a seam change in `tokens.css` / `app.css` — ask for it in your handoff instead of adding
selectors to the skin.
