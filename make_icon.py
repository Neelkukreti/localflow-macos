"""Draw LocalFlow's app icon with AppKit — no image files, no dependencies.

A dark rounded square with the same 🎙️ the menu bar uses, rendered at every
size an .icns wants. Run via make_app.sh.
"""

import os
import sys

from AppKit import (
    NSBitmapImageRep, NSGraphicsContext, NSColor, NSBezierPath, NSMakeRect,
    NSFont, NSMutableParagraphStyle, NSCenterTextAlignment, NSPNGFileType,
    NSCalibratedRGBColorSpace,
)
from Foundation import NSString

SIZES = [16, 32, 64, 128, 256, 512, 1024]


def draw(size, path):
    rep = NSBitmapImageRep.alloc().initWithBitmapDataPlanes_pixelsWide_pixelsHigh_bitsPerSample_samplesPerPixel_hasAlpha_isPlanar_colorSpaceName_bytesPerRow_bitsPerPixel_(
        None, size, size, 8, 4, True, False, NSCalibratedRGBColorSpace, 0, 0
    )
    ctx = NSGraphicsContext.graphicsContextWithBitmapImageRep_(rep)
    NSGraphicsContext.saveGraphicsState()
    NSGraphicsContext.setCurrentContext_(ctx)

    inset = size * 0.045
    rect = NSMakeRect(inset, inset, size - inset * 2, size - inset * 2)
    NSColor.colorWithCalibratedRed_green_blue_alpha_(0.11, 0.12, 0.15, 1.0).set()
    NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
        rect, size * 0.22, size * 0.22
    ).fill()

    style = NSMutableParagraphStyle.alloc().init()
    style.setAlignment_(NSCenterTextAlignment)
    font = NSFont.fontWithName_size_("Apple Color Emoji", size * 0.56) \
        or NSFont.systemFontOfSize_(size * 0.56)
    attrs = {"NSFont": font, "NSParagraphStyle": style}
    text = NSString.stringWithString_("🎙")
    bounds = text.sizeWithAttributes_(attrs)
    text.drawInRect_withAttributes_(
        NSMakeRect(0, (size - bounds.height) / 2, size, bounds.height), attrs
    )

    NSGraphicsContext.restoreGraphicsState()
    data = rep.representationUsingType_properties_(NSPNGFileType, {})
    data.writeToFile_atomically_(path, True)


def main(iconset):
    os.makedirs(iconset, exist_ok=True)
    for size in SIZES:
        draw(size, os.path.join(iconset, f"icon_{size}x{size}.png"))
        if size <= 512:  # @2x variants are the next size up
            draw(size * 2, os.path.join(iconset, f"icon_{size}x{size}@2x.png"))
    print(f"wrote {len(SIZES) * 2 - 1} pngs into {iconset}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "LocalFlow.iconset")
