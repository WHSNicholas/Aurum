# Report assets

These bundled variable fonts keep PDF rendering offline and consistent across machines:

- Geist: `https://github.com/google/fonts/tree/main/ofl/geist`
- Geist Mono: `https://github.com/google/fonts/tree/main/ofl/geistmono`
- Cormorant: `https://github.com/google/fonts/tree/main/ofl/cormorant`

Downloaded from the Google Fonts repository on 23 October 2026. Each font is distributed under the SIL Open
Font License 1.1; its full copyright notice and license are included alongside it. The font files are unmodified
apart from their simplified filenames.

The six PNG summary icons come from the user's supplied `data/icons/` reference assets. Their original
512-by-512 dimensions and alpha channels are preserved. The copies here are tinted dark teal (`#0d4f4f`),
except `net_worth.png` and `net_income.png`, which are white for the highlighted cards. Original files in
`data/icons/` are unchanged. Reports use these bundled copies, so no private folder or runtime image-editing
dependency is needed. The expense chart is drawn as local SVG by `pdf.py`.

The investment report's `market_value.png` and `unrealised_gain.png` were extracted from the supplied Eigen One
investment PDF. Their original embedded colour images and transparency masks are preserved without recolouring.
The allocation donut is generated locally as SVG.
