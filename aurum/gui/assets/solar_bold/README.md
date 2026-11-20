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

Aurum adapts the single artwork colour in fills and strokes to `currentColor` and removes fixed dimensions
so icons follow workspace colours and control sizes. The chat icon's original paths are combined with even-odd
fill to make its three white dots transparent cutouts, readable with both light and dark icon tints.
Path coordinates and their 24×24 coordinate system are preserved. Month/disclosure arrows keep the original
four thin open chevrons because Solar Bold's alternative arrows are filled triangles.
No external images, scripts or runtime network requests are used.

The refresh role uses Refresh Circle, whose even-odd arrow cutouts stay transparent under theme tinting.
The previous Solar Refresh artwork remains in `archive/refresh.svg`; its original source and hash are
retained in `sources.json`. Copy that file back to `refresh.svg` to restore that individual icon.

## Restore the original icons

The original outline files remain in `../ui/`, alongside the Solar outline artwork for newer roles.
The two legacy stylesheet SVGs are in `../`.
Set `ICON_SET = "outline"` in `aurum/gui/icons.py` and restart Aurum to restore that set throughout the GUI.
Set it back to `"solar_bold"` to use this set. Report PDF artwork is independent of this desktop choice.

The new outline files use the same designs and pinned author revision as their bold counterparts.
`manual` uses Pen New Square in both sets, reusing the original bold `pen.svg` and a dedicated outline SVG.
`investment_disposal` uses the author's Linear version of Hand Money. The per-file source paths and hashes
are recorded in the [outline manifest](../ui/solar-sources.json). Both variants use the licence above.

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
| `refresh` | Refresh Circle |
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
| `analysis` | Graph Up |
| `reports` | Documents |
| `securities` | Case Minimalistic |
| `theme` | Palette |
| `report_settings` | Tuning Square |
| `accounts` | Cardholder |
| `budget` | Pie Chart |
| `connect_bank` | Plug Circle |
| `sync_bank` | Transfer Horizontal |
| `check_connection` | Shield Check |
| `retry` | Restart Square |
| `edit` | Pen |
| `attach_evidence` | Paperclip |
| `investment_disposal` | Hand Money |
| `post` | Bill Check |
| `undo` | Undo Left Round |
| `choose_pdf` | File |
