"""
`jarvis://avatar/` — a real origin for the body, and no listening socket.

THE PROBLEM, WHICH IS NOT OBVIOUS UNTIL IT BITES
    `avatar/index.html` is ES modules and one `fetch` of the manifest. Loaded
    from `file://`, Chromium gives the document an *opaque* origin: `fetch`
    happens to work, and every `import` fails with

        Failed to resolve module specifier './js/main.js'. The base URL is
        about:blank because import() is called from a CORS-cross-origin script.

    Nothing is logged as an error, the page loads, `loadFinished` reports True,
    and `window.JARVIS` is simply never defined. A body that fails this way
    looks exactly like a body that is working and has nothing to say — which is
    the reason this file exists rather than a `--allow-file-access-from-files`
    flag buried in an environment variable.

THE THREE WAYS OUT, AND WHY THIS ONE
    * bundle everything into one file   no build step in this project, and the
                                        structure is the documentation.
    * a local HTTP server               a listening socket on a workstation,
                                        for a face. No.
    * a custom scheme                   Chromium's own answer. Working ES
                                        modules, and the only thing that can
                                        reach it is this process. It does not
                                        buy `fetch` — see the next section.

    The phone will need the same thing and has the same answer — Android's
    `WebViewAssetLoader` serves app assets under `https://appassets.android
    platform.net/`, for exactly this reason. One more place where hosting the
    renderer twice is cheaper than writing it twice.

THE ONE RULE FOR CALLERS
    `register()` must run BEFORE `QApplication` is constructed. Chromium builds
    its scheme registry once, at startup, and a scheme declared afterwards is
    accepted by the API and then ignored — silently, again. `__main__._run_app`
    calls it on the line above the QApplication, and `selftest` asserts the
    ordering so it cannot drift back.

WHAT QtWebEngine 6.7 DOES NOT HONOUR, MEASURED RATHER THAN ASSUMED
    `registerScheme()` returns without complaint and keeps none of the scheme's
    properties. In the page that results:

        location.origin      "jarvis://"      (no host)
        isSecureContext      false
        import './x.js'      works
        XMLHttpRequest       works, status 200
        fetch('./x.json')    fails — "Failed to fetch" — and on some call
                             paths takes the whole process down with
                             STATUS_STACK_BUFFER_OVERRUN rather than rejecting

    The flags below are set anyway: they are correct, they cost nothing, and a
    later Qt that honours them turns the last line green without a code change.
    What the renderer must not do is *depend* on them — so `avatar/js/main.js`
    never calls `fetch` unless the page is on http(s), and the manifest is
    injected by `avatar_view._inject_manifest` instead. three.js reads models
    through XMLHttpRequest, which is why a real `.glb` still loads here.

SECURITY, SUCH AS IT IS
    The handler serves one directory and nothing else. Every request is
    resolved, and anything that lands outside `avatar/` after resolution is
    refused — `..%2f..%2f` in a URL is a path traversal even when the page
    asking is one we wrote, because the page is also whatever a downloaded
    model's embedded URI says it is.
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

SCHEME = b"jarvis"
HOST = "avatar"
ROOT = Path(__file__).resolve().parent.parent.parent / "avatar"

#: The page's own entry point, as the view should ask for it.
INDEX_URL = f"{SCHEME.decode()}://{HOST}/index.html"

#: Content types, by extension. Deliberately explicit rather than
#: `mimetypes.guess_type`: that function consults the Windows registry, where
#: `.js` has been `text/plain` on more than one machine — and a `text/plain`
#: module is a module Chromium refuses to execute.
_TYPES = {
    ".html": b"text/html",
    ".js":   b"text/javascript",
    ".mjs":  b"text/javascript",
    ".json": b"application/json",
    ".css":  b"text/css",
    ".wasm": b"application/wasm",
    ".glb":  b"model/gltf-binary",
    ".gltf": b"model/gltf+json",
    ".bin":  b"application/octet-stream",
    ".png":  b"image/png",
    ".jpg":  b"image/jpeg",
    ".jpeg": b"image/jpeg",
    ".webp": b"image/webp",
    ".ktx2": b"image/ktx2",
    ".hdr":  b"image/vnd.radiance",
    ".svg":  b"image/svg+xml",
}

_registered = False
_handler = None   # gardee en vie : Qt ne possede pas le handler installe


def register() -> bool:
    """Declare the scheme. Must run before `QApplication`. Idempotent.

    Returns False when WebEngine is absent, which is not a failure: the panel
    falls back to the 2D core and never asks for a `jarvis://` URL.
    """
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
        # Secure : sans cela le contexte n'est pas "secure" et WebGL, les
        # modules et fetch sont diversement restreints.
        QWebEngineUrlScheme.Flag.SecureScheme
        # LocalAccessAllowed : le document peut charger ses propres fichiers.
        | QWebEngineUrlScheme.Flag.LocalAccessAllowed
        # CorsEnabled + FetchApiAllowed : c'est la paire qui debloque
        # `import` et `fetch('./manifest.json')`, le defaut meme de file://.
        | QWebEngineUrlScheme.Flag.CorsEnabled
        | QWebEngineUrlScheme.Flag.FetchApiAllowed
    )
    QWebEngineUrlScheme.registerScheme(scheme)
    _registered = True
    return True


class _Handler(QWebEngineUrlSchemeHandler):  # type: ignore[misc]
    """Serves `avatar/` and refuses everything else."""

    def requestStarted(self, job: "QWebEngineUrlRequestJob") -> None:  # noqa: N802
        url: QUrl = job.requestUrl()
        if url.host() != HOST:
            job.fail(QWebEngineUrlRequestJob.Error.UrlNotFound)
            return

        relative = url.path().lstrip("/") or "index.html"
        try:
            target = (ROOT / relative).resolve()
            target.relative_to(ROOT.resolve())
        except (ValueError, OSError):
            # Hors de avatar/ apres resolution : traversee de chemin, refusee.
            job.fail(QWebEngineUrlRequestJob.Error.RequestDenied)
            return

        if not target.is_file():
            job.fail(QWebEngineUrlRequestJob.Error.UrlNotFound)
            return

        try:
            payload = QByteArray(target.read_bytes())
        except OSError:
            job.fail(QWebEngineUrlRequestJob.Error.RequestFailed)
            return

        # CORS, although everything here is same-origin.
        #
        # `CorsEnabled` puts every request on this scheme through Chromium's CORS
        # checks, and a response with no `Access-Control-Allow-Origin` fails them
        # in cases that are hard to predict from the outside — `fetch()` of the
        # manifest failed with a bare "Failed to fetch" while `import` of the
        # same directory succeeded. Answering the check explicitly is one line
        # and removes the whole category.
        #
        # `no-cache` matters for a different reason: the entire extension story
        # is "drop a file in and reload". A cached manifest or a cached .glb
        # would make that quietly not work.
        try:
            job.setAdditionalResponseHeaders([
                (QByteArray(b"Access-Control-Allow-Origin"), QByteArray(b"*")),
                (QByteArray(b"Cache-Control"), QByteArray(b"no-cache")),
            ])
        except (AttributeError, TypeError):
            # Qt < 6.6 has no such method. Same-origin requests still work;
            # this is an improvement, not a requirement.
            pass

        # Le QBuffer est parente au job : Chromium lit de maniere asynchrone et
        # un tampon detruit trop tot donne une reponse tronquee, au hasard, sur
        # les gros fichiers — c'est-a-dire exactement sur three.js et les .glb.
        buffer = QBuffer(job)
        buffer.setData(payload)
        buffer.open(QIODevice.OpenModeFlag.ReadOnly)
        job.reply(_TYPES.get(target.suffix.lower(), b"application/octet-stream"), buffer)


def install(profile) -> bool:  # noqa: ANN001
    """Attach the handler to a profile. Safe to call more than once."""
    global _handler
    if not AVAILABLE or not _registered:
        return False
    if _handler is None:
        _handler = _Handler()
    profile.installUrlSchemeHandler(SCHEME, _handler)
    return True
