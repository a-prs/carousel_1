# Self-hosted fonts

Cyrillic + latin woff2 subsets pulled from Google Fonts css2 (Chrome UA so the
right woff2 is served). Regenerate with `tests/_fetch_fonts.py`. `faces.json` is
the manifest templates.py reads to emit `@font-face` rules with absolute file://
URIs (server-side render only).

## Phase 3 font-coverage finding (plan §11 point 4)

The adversarial review flagged a real risk that **Fira Sans Extra Condensed
weight 800 might not carry Cyrillic**. Verified 2026-07-08: Google Fonts css2
**does** serve a distinct Cyrillic-subset woff2 for `Fira Sans Extra Condensed
800` (`...-800-cyrillic-*.woff2`, ~11.6 KB). So it covers Cyrillic and matches
the confirmed `style_test.html` reference exactly — no substitution needed.

vauforma `design_tokens`: `font_head = "Fira Sans Extra Condensed"` (headings),
`font_head_alt = "Fira Sans Condensed"` (fallback + kickers/labels),
`font_body = "PT Sans"`. Each family self-hosts both latin and cyrillic subsets
so latin glyphs (e.g. "GLP-1", digits, "@handle") don't tofu.
