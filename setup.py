"""py2app build for LocalFlow.

Built in ALIAS mode (`./make_app.sh`), so the bundle points at the sources in
this folder rather than copying the 1.1 GB venv. The point of using py2app at
all is identity: the bundle gets its own executable, so macOS sees "LocalFlow"
— its name, its icon, its Microphone/Accessibility grants — instead of
attributing everything to the shared Python.app binary.
"""

from setuptools import setup

setup(
    app=["app.py"],
    options={
        "py2app": {
            "argv_emulation": False,
            "iconfile": "LocalFlow.icns",
            "plist": {
                "CFBundleName": "LocalFlow",
                "CFBundleDisplayName": "LocalFlow",
                "CFBundleIdentifier": "com.localflow.app",
                "CFBundleShortVersionString": "2.0",
                "CFBundleVersion": "2.0",
                "LSMinimumSystemVersion": "12.0",
                # Menu-bar only: no Dock icon, no app switcher entry.
                "LSUIElement": True,
                # GUI launches don't inherit a shell PATH, and ollama lives in Homebrew.
                "LSEnvironment": {
                    "PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin",
                },
                "NSMicrophoneUsageDescription":
                    "LocalFlow records your voice locally to turn it into text. "
                    "Audio never leaves this Mac.",
                "NSAppleEventsUsageDescription":
                    "LocalFlow pastes finished text into the app you are typing in.",
            },
        }
    },
    setup_requires=["py2app"],
)
