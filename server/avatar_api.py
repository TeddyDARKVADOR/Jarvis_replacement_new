"""
server/avatar_api.py — the face's files, for a phone that cannot carry them.

    GET /api/avatar/manifest   what is installed: the manifest the page reads,
                               and the active model's file, SHA-256 and size
    GET /api/avatar/model      the active model's bytes, and nothing else

WHY THE MODEL COMES FROM THE SERVER AND NOT FROM THE APK
    A downloaded .glb belongs to someone (see .gitignore, "Modeles 3D") and an
    APK is a redistribution. So the phone fetches, once, the model the user
    installed on this server with `python -m presence.install_model`.

NO SECOND MODEL MECHANISM
    Everything here is read through presence/models.py: the manifest
    (`migrate`), the active model (`manifest.model.file`), and above all its
    profile — whose `sha256` is the fingerprint `install_model` measured. The
    phone checks the bytes it receives against that number before using them.

THE SERVER CHECKS FIRST, TOO
    `check_profile` re-hashes the file (cached on size + mtime). A file that no
    longer matches its profile is not offered at all: `model` is null with the
    reason, and the phone keeps its 2D core. Serving it with the old
    fingerprint would only move the refusal to the phone; serving it with a new
    one would bless a file nobody profiled.

WHAT THE PHONE MANIFEST LEAVES OUT
    `gestures`: the clips live in avatar/gestures/, are not downloaded in this
    first version, and a missing clip costs a capability, never the face
    (avatar/checks/asset_robustness.py). Procedural gestures still play.

ONLY THE ACTIVE MODEL, BY CONSTRUCTION
    /model takes no path. There is nothing to traverse.
"""
from __future__ import annotations

import json
from pathlib import Path

# At module level on purpose: with `from __future__ import annotations`,
# FastAPI resolves `req: Request` in this module's globals. Imported inside
# attach(), it is not found and `req` silently becomes a query parameter (422).
try:
    from fastapi import Request
    from fastapi.responses import FileResponse, JSONResponse
except ImportError:          # the server without fastapi cannot serve anyway
    Request = FileResponse = JSONResponse = None

BASE_DIR = Path(__file__).resolve().parent.parent
AVATAR_DIR = BASE_DIR / "avatar"

#: Licences that allow a copy of the model onto a phone. The profile's
#: `license` must START with one of them. « inconnue — à renseigner », or a
#: test asset marked « ne pas redistribuer », is never served — even when it
#: is the active model on this machine: the desktop may show it, the phone
#: will keep its 2D core. The lab (jarvis-preprod/tools/avatar_lab_dir.py)
#: applies this same list.
PHONE_LICENCES = ("MIT", "CC0", "CC-BY")


def licence_allows_phone(licence: str) -> bool:
    return str(licence or "").strip().upper().startswith(PHONE_LICENCES)


class _Catalog:
    """What is installed, read from disk each time, verified with a cache."""

    def __init__(self, avatar_dir: Path):
        self.avatar_dir = Path(avatar_dir)
        self._verified: dict = {}          # path -> (size, mtime, ok, reason)

    def _manifest(self) -> dict:
        from presence import models
        return models.migrate(models.read_json(self.avatar_dir / "manifest.json"))

    def describe(self) -> dict:
        from presence import models
        manifest = self._manifest()
        rel = (manifest.get("model") or {}).get("file") or ""
        phone_manifest = json.loads(json.dumps(manifest))
        phone_manifest["gestures"] = {}
        if not rel:
            return {"manifest": phone_manifest, "model": None, "reason": "aucun modele actif"}
        path = (self.avatar_dir / "models" / rel).resolve()
        models_root = (self.avatar_dir / "models").resolve()
        if models_root not in path.parents or not path.is_file():
            return {"manifest": phone_manifest, "model": None, "reason": f"fichier absent : {rel}"}
        stat = path.stat()
        key = str(path)
        cached = self._verified.get(key)
        if not cached or cached[:2] != (stat.st_size, stat.st_mtime):
            ok, reason = models.check_profile(path)
            cached = (stat.st_size, stat.st_mtime, ok, reason)
            self._verified[key] = cached
        if not cached[2]:
            return {"manifest": phone_manifest, "model": None, "reason": cached[3]}
        profile = models.read_profile(path)
        if not licence_allows_phone(profile.get("license")):
            return {"manifest": phone_manifest, "model": None,
                    "reason": f"licence « {profile.get('license')} » : pas de copie vers un telephone"}
        return {
            "manifest": phone_manifest,
            "model": {"file": rel, "sha256": profile["sha256"], "size": stat.st_size,
                      "id": profile.get("id"), "license": profile.get("license")},
            "reason": cached[3],
        }

    def model_path(self) -> Path | None:
        info = self.describe()
        if not info["model"]:
            return None
        return (self.avatar_dir / "models" / info["model"]["file"]).resolve()


def attach(dashboard, *, avatar_dir: Path | None = None, log=print) -> None:
    """Add the two routes to `dashboard.app`. Call once, before serve()."""
    if Request is None:
        raise RuntimeError("fastapi is not installed")
    app = dashboard.app
    if getattr(app.state, "avatar_attached", False):
        return
    app.state.avatar_attached = True
    catalog = _Catalog(avatar_dir or AVATAR_DIR)

    def _authorised(req: Request) -> bool:
        # The same question server/api.py asks: a bearer the dashboard issued.
        tok = req.headers.get("authorization", "").removeprefix("Bearer ").strip()
        return bool(tok) and tok in dashboard._tokens

    async def manifest_ep(req: Request) -> JSONResponse:
        if not _authorised(req):
            return JSONResponse({"error": "Unauthorized"}, status_code=401)
        return JSONResponse(catalog.describe())

    async def model_ep(req: Request):
        if not _authorised(req):
            return JSONResponse({"error": "Unauthorized"}, status_code=401)
        info = catalog.describe()
        if not info["model"]:
            return JSONResponse({"error": "no verified model", "reason": info["reason"]},
                                status_code=404)
        path = catalog.model_path()
        return FileResponse(path, media_type="model/gltf-binary",
                            headers={"X-Model-Sha256": info["model"]["sha256"],
                                     "Cache-Control": "no-store"})

    app.add_api_route("/api/avatar/manifest", manifest_ep, methods=["GET"])
    app.add_api_route("/api/avatar/model", model_ep, methods=["GET"])
    log("[Avatar] /api/avatar/manifest et /api/avatar/model")
