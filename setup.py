"""
Matthew — one-time setup of a desktop.

Installs the Python dependencies for THIS operating system only: the OS-specific
packages in requirements.txt carry `sys_platform` markers, so a macOS or Linux
user never pulls Windows-only libraries (and vice-versa). Then it fetches the
Playwright browsers needed for web automation (current-OS builds only).
On Linux it lists the system packages the features need (LINUX_PACKAGES);
`python setup.py --system` installs them with sudo.

The optional local wake word ("Hey Jarvis") is NOT installed here — it's a
one-click, opt-in download from the HUD's Paramètres → Audio.
The Oracle server is set up by deployment/install.sh, not by this file.
"""
import platform
import subprocess
import sys
from pathlib import Path

OS = platform.system()  # "Windows" | "Darwin" | "Linux"

#: Linux: what Windows ships and Linux leaves to the package manager (apt names).
#: `python setup.py --system` installs them all with sudo.
LINUX_PACKAGES = [
    ("libportaudio2", "microphone and voice (sounddevice)"),
    ("pulseaudio-utils", "volume, and « a meeting is using the mic » (pactl)"),
    ("brightnessctl", "screen brightness"),
    ("xdg-utils", "open files, folders and links (xdg-open)"),
    ("libnotify-bin", "desktop notifications (notify-send)"),
    ("at", "one-off reminders when systemd-run is missing"),
    ("wmctrl", "list and focus windows (X11)"),
    ("xdotool", "keyboard and mouse automation (X11)"),
    ("wtype", "typing under Wayland"),
    ("scrot", "screenshots (X11); grim under Wayland"),
    ("espeak-ng", "offline voice when Gemini and Oracle are down"),
    ("python3-gi", "Pilot: reading windows (AT-SPI)"),
    ("gir1.2-atspi-2.0", "Pilot: reading windows (AT-SPI)"),
    ("at-spi2-core", "Pilot: reading windows (AT-SPI)"),
    ("bubblewrap", "Code Studio sandbox (Linux twin of Windows Sandbox)"),
    ("libegl1", "the HUD (Qt WebEngine)"),
    ("libxkbcommon0", "the HUD (Qt WebEngine)"),
    ("libnss3", "the HUD (Qt WebEngine)"),
]


def _run(label: str, args: list[str]) -> None:
    print(f"\n▶ {label}")
    subprocess.run(args, check=True)


def main() -> None:
    print(f"⚙  Matthew setup — detected OS: {OS or 'unknown'}")

    # requirements.txt filters OS-specific extras by itself via pip markers.
    _run("Installing Python dependencies (OS-specific extras auto-filtered)…",
         [sys.executable, "-m", "pip", "install", "-r", "requirements.txt"])

    # Chromium covers Chrome/Edge/Opera/Brave/Vivaldi; Firefox for Firefox.
    # (Safari automation additionally needs: python -m playwright install webkit)
    _run("Installing Playwright browsers (chromium + firefox)…",
         [sys.executable, "-m", "playwright", "install", "chromium", "firefox"])

    # ── OS-specific post-install notes ────────────────────────────────────────
    if OS == "Windows":
        try:
            import win32com.client  # noqa: F401
        except ImportError:
            postinstall = Path(sys.executable).parent / "Scripts" / "pywin32_postinstall.py"
            print(
                "\n⚠️  pywin32 did not register correctly — desktop-shortcut "
                "creation will use a slower fallback. To fix it, run:\n"
                f'    "{sys.executable}" -m pip install --force-reinstall pywin32\n'
                f'    "{sys.executable}" "{postinstall}" -install'
            )
    elif OS == "Linux":
        apt = "sudo apt install -y " + " ".join(pkg for pkg, _ in LINUX_PACKAGES)
        print("\nℹ️  Linux — the native tools Matthew shells out to (Debian / Ubuntu names):")
        for pkg, why in LINUX_PACKAGES:
            print(f"    • {pkg:<22} {why}")
        print(f"\n    All at once:\n    {apt}")
        print("    Pilot reads windows through AT-SPI (python3-gi): create the venv with\n"
              "    `python3 -m venv --system-site-packages .venv` so it sees the system's gi.")
        if "--system" in sys.argv:
            _run("Installing the Linux system packages (sudo)…", apt.split())
    elif OS == "Darwin":
        print(
            "\nℹ️  macOS note — volume, brightness and reminders use the built-in "
            "'osascript' / LaunchAgents, so no extra tools are required.\n"
            "    For Safari automation only: python -m playwright install webkit"
        )

    print("\n✅ Setup complete!")
    print("   Matthew on Oracle :  python main.py")
    print("       → the HUD opens on Paramètres: server address + access token")
    print("         (token: `python -m server.run_headless --pairing` on Oracle)")
    print("   Matthew on this PC:  python main.py --local")
    print("       → put your Gemini key in config/api_keys.json (\"gemini_api_key\")")
    print("   (Optional) 'Hey Jarvis': HUD → Paramètres → Audio.")


if __name__ == "__main__":
    main()
