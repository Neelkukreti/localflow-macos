"""The menu-bar logo, drawn at runtime and tinted per state.

An emoji title can't change colour and renders differently across macOS
versions. We draw a microphone glyph instead and hand the status item a real
NSImage, so idle/listening/working are told apart by colour at a glance.

Images are cached per (state, dark-mode) and written next to the app, because
rumps' `App.icon` setter takes a path.
"""

import os

from AppKit import (
    NSBitmapImageRep, NSGraphicsContext, NSColor, NSBezierPath, NSMakeRect,
    NSPNGFileType, NSCalibratedRGBColorSpace, NSMakePoint,
)

HERE = os.path.dirname(os.path.abspath(__file__))
ICON_DIR = os.path.join(HERE, ".menubar")

# Status colours. Idle is near-black: macOS inverts a template-ish dark glyph
# for dark menu bars, and these read cleanly either way.
COLORS = {
    "idle":       (0.15, 0.16, 0.20),
    "recording":  (0.90, 0.22, 0.24),   # red: capturing
    "hands_free": (0.96, 0.60, 0.10),   # amber: listening, waiting for your click
    "working":    (0.35, 0.45, 0.95),   # blue: transcribing / cleaning up
    "command":    (0.55, 0.35, 0.90),   # violet: Command Mode
    "error":      (0.55, 0.55, 0.58),
}

SIZE = 18  # menu-bar points; drawn at 2x for Retina


def _draw(color, path, size=SIZE, scale=2):
    px = size * scale
    rep = NSBitmapImageRep.alloc().initWithBitmapDataPlanes_pixelsWide_pixelsHigh_bitsPerSample_samplesPerPixel_hasAlpha_isPlanar_colorSpaceName_bytesPerRow_bitsPerPixel_(
        None, px, px, 8, 4, True, False, NSCalibratedRGBColorSpace, 0, 0
    )
    ctx = NSGraphicsContext.graphicsContextWithBitmapImageRep_(rep)
    NSGraphicsContext.saveGraphicsState()
    NSGraphicsContext.setCurrentContext_(ctx)

    r, g, b = color
    NSColor.colorWithCalibratedRed_green_blue_alpha_(r, g, b, 1.0).set()

    u = px / 18.0          # one menu-bar point
    cx = px / 2.0
    # Capsule body
    body = NSMakeRect(cx - 3.1 * u, 8.2 * u, 6.2 * u, 7.6 * u)
    NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(body, 3.1 * u, 3.1 * u).fill()
    # Cradle: an open arc under the body
    cradle = NSBezierPath.bezierPath()
    cradle.setLineWidth_(1.7 * u)
    cradle.appendBezierPathWithArcWithCenter_radius_startAngle_endAngle_(
        NSMakePoint(cx, 8.6 * u), 5.2 * u, 200, 340
    )
    cradle.stroke()
    # Stem and base
    NSBezierPath.bezierPathWithRect_(NSMakeRect(cx - 0.85 * u, 1.9 * u, 1.7 * u, 2.4 * u)).fill()
    NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
        NSMakeRect(cx - 3.4 * u, 1.2 * u, 6.8 * u, 1.7 * u), 0.85 * u, 0.85 * u
    ).fill()

    NSGraphicsContext.restoreGraphicsState()
    rep.representationUsingType_properties_(NSPNGFileType, {}).writeToFile_atomically_(path, True)


def icon_for(state):
    """Path to the menu-bar image for a state, drawing it once and caching."""
    color = COLORS.get(state, COLORS["idle"])
    os.makedirs(ICON_DIR, exist_ok=True)
    path = os.path.join(ICON_DIR, f"{state}.png")
    if not os.path.exists(path):
        try:
            _draw(color, path)
        except Exception:
            return None
    return path


def prewarm():
    """Draw every state up front, on the main thread, so a dictation never pays
    for it mid-flight."""
    for state in COLORS:
        icon_for(state)
