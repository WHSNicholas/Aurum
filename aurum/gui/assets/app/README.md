# Aurum App Icon

An abstract pair of sculpted curves suggests balance and the movement of money. Champagne gold and
muted bronze sit on the slate teal used by Aurum's Sunset Hues interface.

- `aurum-master.png`: original generated artwork with transparency.
- `aurum.png`: 1024-pixel PNG for application use.
- `aurum.icns`: native macOS icon with standard and Retina sizes from 16 to 1024 pixels.

`aurum/scripts/app.py` loads the PNG as Qt's application icon. The installed macOS launcher stores the
ICNS as `Contents/Resources/Aurum.icns`, selected by `CFBundleIconFile` in `Contents/Info.plist`.
The project-root app shortcut points to this launcher and inherits its Finder icon. Restart the app to
refresh the running Dock icon after changing its artwork.

Created with the built-in image-generation tool. PNG resizing and ICNS packaging use macOS `sips` and
`iconutil`. This is original artwork, not an Apple logo or a copy of an existing app icon.

Design reference: [Apple app icon guidance](https://developer.apple.com/design/human-interface-guidelines/app-icons/).

## Generation Prompt

Design one original macOS application icon for Aurum, a refined personal-finance app. This is a NEW abstract direction. Absolutely no letters, monograms, numbers or text.

Central symbol: two broad, beautifully sculpted curved forms in a balanced, gently interlocking composition. Together they suggest a continuous exchange, stewardship and a sense of balance. Think of two smooth crescent-like ribbons, one turning inward from upper-left, the other from lower-right, creating a generous soft negative space through the middle. Their relationship should be visually memorable and effortless, like a small piece of modern sculpture. NOT a literal yin-yang, not a recycling symbol, not arrows, not an infinity sign, not a chain link, not a currency symbol. Avoid a perfectly circular coin outline. One unified abstract silhouette, no tiny details, no extra decoration.

The main upper curve is satin champagne gold with warm ivory highlights (#F7F3EB), subdued gold midtones (#D2BE99), and restrained warm rose reflections (#C9ADA1). The lower curve has a slightly deeper champagne-bronze finish, maintaining clear contrast with the background. Smooth broad surfaces, shallow physical depth, precise soft bevels and subtle contact shadows. These are solid, elegant dimensional forms, not thin strokes or flat vector squiggles. Strongly legible at 32-pixel Dock size.

Place the symbol on an opaque, softly dimensional slate-teal macOS rounded-square tile, #4D6A6D, with a restrained top-left highlight and lower edge shade #3D5558. Smooth continuous corners, straight-on view, perfectly centred, tile occupies about 86 percent of the square canvas, all edges visible, generous safe margins. Symbol occupies about 57 percent of the canvas width. True transparent area outside the tile with perfectly clean edges, no coloured fringe or stray pixels.

The result should have the restraint and tactile precision of a beautifully made native Mac app icon: calm, contemporary, clear and original. Use subtle materials rather than extravagant shine. No Apple logo or copying existing app icons. No A, no letters at all, no wordmark, no bank, no coins, no bars or charts, no dollar sign, no wallet, no mockup or device frame, no background scene, no checkerboard. One icon only, high-resolution square artwork.
