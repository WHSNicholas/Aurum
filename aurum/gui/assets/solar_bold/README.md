# Solar Bold icons for Aurum

Solar Icon Set by **480 Design / Damir Tuarshe**, licensed under
[Creative Commons Attribution 4.0 International](https://creativecommons.org/licenses/by/4.0/).

- Requested collection: https://www.svgrepo.com/collection/solar-bold-icons/
- Creator: https://www.figma.com/community/file/1166831539721848736
- Original artwork: https://github.com/480-Design/Solar-Icon-Set
- Author source revision: `44017167688b49109d88ae6a98979b23d8950db0`

The catalogue was reviewed on SVG Repo. Its HTML download pages returned HTTP 429, so the selected
SVG artwork was downloaded from the creator's original repository. Per-icon paths, download URLs and
original file SHA-256 hashes are recorded in `sources.json`.

Aurum adapts the single fixed fill colour to `currentColor` and removes fixed width/height attributes
so icons follow workspace colours and control sizes. The chat icon's original paths are combined with even-odd
fill to make its three white dots transparent cutouts, readable with both light and dark icon tints.
Path coordinates and their 24×24 coordinate system are preserved. Month/disclosure arrows keep the original
four thin open chevrons because Solar Bold's alternative arrows are filled triangles.
No external images, scripts or runtime network requests are used.

## Restore the original icons

The original set remains unchanged in `../ui/`; the two legacy stylesheet SVGs in `../` are retained too.
Set `ICON_SET = "outline"` in `aurum/gui/icons.py` and restart Aurum to restore that set throughout the GUI.
Set it back to `"solar_bold"` to use this set. Report PDF artwork is independent of this desktop choice.

## Role mapping

| Aurum asset | Solar Bold icon |
| --- | --- |
| `home` | Home 2 |
| `settings` | Settings |
| `chat` | Chat Round Dots |
| `document` | Document Text |
| `checklist` | Clipboard Check |
| `pen` | Pen New Square |
| `ledger` | Notebook Minimalistic |
| `chart` | Chart Square |
| `wallet` | Wallet Money |
| `cash` | Banknote |
| `arrow_down_left` | Arrow Left Down |
| `arrow_up_right` | Arrow Right Up |
| `vault` | Safe Square |
| `import` | Download Minimalistic |
| `process` | Play |
| `refresh` | Refresh |
| `folder` | Folder Open |
| `person` | User Rounded |
| `check` | Check Circle |
| `error` | Danger Triangle |
| `clock` | History |
| `details` | Info Circle |
| `add` | Add Circle |
| `remove` | Trash Bin Minimalistic |
| `save` | Diskette |
| `login` | Login 2 |
| `copy` | Copy |
| `search` | Magnifer |
| `close` | Close Circle |
| `fit` | Full Screen |
