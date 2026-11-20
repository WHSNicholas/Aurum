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

The four additional summary icons use the [Solar Icon Set](https://github.com/480-Design/Solar-Icon-Set)
by 480 Design / Damir Tuarshe, licensed under [Creative Commons Attribution 4.0 International](https://creativecommons.org/licenses/by/4.0/).
Each source is from revision `44017167688b49109d88ae6a98979b23d8950db0`. Aurum rasterises the original
24×24 SVG artwork to transparent 512×512 PNGs and tints the silhouettes for the report theme.

| Report measure | PNG | Original Solar Bold artwork |
| --- | --- | --- |
| Total Savings | `savings.png` | [Safe Square](https://github.com/480-Design/Solar-Icon-Set/blob/44017167688b49109d88ae6a98979b23d8950db0/icons/SVG/Bold/Money/Safe%20Square.svg) |
| Total Invested | `invested_cost.png` | [Wad Of Money](https://github.com/480-Design/Solar-Icon-Set/blob/44017167688b49109d88ae6a98979b23d8950db0/icons/SVG/Bold/Money/Wad%20Of%20Money.svg) |
| Market Value / Portfolio Value | `market_value.png` | [Tag Price](https://github.com/480-Design/Solar-Icon-Set/blob/44017167688b49109d88ae6a98979b23d8950db0/icons/SVG/Bold/Money/Tag%20Price.svg) |
| Investment P&L | `unrealised_gain.png` | [Plus, Minus](https://github.com/480-Design/Solar-Icon-Set/blob/44017167688b49109d88ae6a98979b23d8950db0/icons/SVG/Bold/School/Plus%2C%20Minus.svg) |

The investment allocation donut is generated locally as SVG.
