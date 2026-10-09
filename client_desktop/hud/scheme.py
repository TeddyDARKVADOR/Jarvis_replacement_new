"""
`jarvis-hud://hud/` — the HUD's own origin, for the same reasons as the face's.

`ui/avatar_scheme.py` documents the trap at length: a page loaded from `file://`
gets an opaque origin and every ES module import fails silently. The HUD is ES
modules too, so it gets a scheme of its own rather than a second host on
`jarvis://` — that handler belongs to the face and is not edited here.

Two roots are served, and nothing else:

    jarvis-hud://hud/<path>          client_desktop/hud/web/<path>
    jarvis-hud://hud/vendor/<path>   avatar/vendor/<path>   (three.js, read-only)

Reusing the vendored three.js rather than copying it keeps one copy of a 600 KB
library in the repository and one version of it on the screen.

Like `register()` in `avatar_scheme`, `register()` here must run before the
`QApplication` exists. `hud/__main__.py` calls both on the line above it.
"""

from __future__ import annotations

from pathlib import Path

try:
    from PyQt6.QtCore import QBuffer, QByteArray, QIODevice, QUrl
    from PyQt6.QtWebEngineCore import (
        QWebEngineUrlRequestJob,
        QWebEngineUrlScheme,
        QWebEngineUrlSchemeHandler,
    )
    AVAILABLE = True
except Exception:  # pragma: no cover - depends on the local install
    AVAILABLE = False
    QWebEngineUrlSchemeHandler = object  # type: ignore[assignment,misc]

SCHEME = b"jarvis-hud"
HOST = "hud"
WEB_ROOT = Path(__file__).resolve().parent / "web"
VENDOR_ROOT = Path(__file__).resolve().parent.parent.parent / "avatar" / "vendor"

INDEX_URL = f"{SCHEME.decode()}://{HOST}/index.html"

# Explicit, never `mimetypes`: the Windows registry has `.js` as text/plain on
# some machines, and Chromium refuses to execute a text/plain module.
_TYPES = {
    ".html": b"text/html",
    ".js": b"text/javascript",
    ".mjs": b"text/javascript",
    ".json": b"application/json",
    ".css": b"text/css",
    ".svg": b"image/svg+xml",
    ".png": b"image/png",
    ".woff2": b"font/woff2",
}

_registered = False
_handler = None


def resolve(path: str) -> Path | None:
    """Map a URL path to a file on disk, or None if it must be refused.

    Pure, so the selftest can throw traversal attempts at it without a browser.
    """
    relative = path.lstrip("/") or "index.html"
    if relative.startswith("vendor/"):
        root, relative = VENDOR_ROOT, relative[len("vendor/"):]
    else:
        root = WEB_ROOT
    try:
        target = (root / relative).resolve()
        target.relative_to(root.resolve())
    except (ValueError, OSError):
        return None
    return target if target.is_file() else None


def register() -> bool:
    """Declare the scheme. Before `QApplication`. Idempotent."""
    global _registered
    if not AVAILABLE or _registered:
        return _registered
    if QWebEngineUrlScheme.schemeByName(SCHEME).name() == SCHEME:
        _registered = True
        return True
    scheme = QWebEngineUrlScheme(SCHEME)
    scheme.setSyntax(QWebEngineUrlScheme.Syntax.HostAndPort)
    scheme.setDefaultPort(QWebEngineUrlScheme.SpecialPort.PortUnspecified.value)
    scheme.setFlags(
        QWebEngineUrlScheme.Flag.SecureScheme
        | QWebEngineUrlScheme.Flag.LocalAccessAllowed
        | QWebEngineUrlScheme.Flag.CorsEnabled
        | QWebEngineUrlScheme.Flag.FetchApiAllowed
    )
    QWebEngineUrlScheme.registerScheme(scheme)
    _registered = True
    return True


class _Handler(QWebEngineUrlSchemeHandler):  # type: ignore[misc]
    def requestStarted(self, job: "QWebEngineUrlRequestJob") -> None:  # noqa: N802
        url: QUrl = job.requestUrl()
        if url.host() != HOST:
            job.fail(QWebEngineUrlRequestJob.Error.UrlNotFound)
            return
        target = resolve(url.path())
        if target is None:
            job.fail(QWebEngineUrlRequestJob.Error.UrlNotFound)
            return
        try:
            payload = QByteArray(target.read_bytes())
        except OSError:
            job.fail(QWebEngineUrlRequestJob.Error.RequestFailed)
            return
        try:
            job.setAdditionalResponseHeaders([
                (QByteArray(b"Access-Control-Allow-Origin"), QByteArray(b"*")),
                (QByteArray(b"Cache-Control"), QByteArray(b"no-cache")),
            ])
        except (AttributeError, TypeError):
            pass
        # Parented to the job: Chromium reads asynchronously, and a buffer freed
        # early truncates large files — three.js first among them.
        buffer = QBuffer(job)
        buffer.setData(payload)
        buffer.open(QIODevice.OpenModeFlag.ReadOnly)
        job.reply(_TYPES.get(target.suffix.lower(), b"application/octet-stream"), buffer)


def install(profile) -> bool:  # noqa: ANN001
    global _handler
    if not AVAILABLE or not _registered:
        return False
    if _handler is None:
        _handler = _Handler()
    profile.installUrlSchemeHandler(SCHEME, _handler)
    return True
