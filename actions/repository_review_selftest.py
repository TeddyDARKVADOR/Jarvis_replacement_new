"""Offline tests for bounded repository selection and sensitive-file exclusion."""
from __future__ import annotations

import tempfile
from pathlib import Path

from actions.repository_review import _ranked_sources


def run() -> int:
    with tempfile.TemporaryDirectory(prefix="matthew-assistant-repo-review-") as tmp:
        root = Path(tmp)
        (root / "README.md").write_text("Example app and architecture overview.", encoding="utf-8")
        (root / "login.py").write_text("def login(user):\n    return user\n", encoding="utf-8")
        (root / "api_key.json").write_text('{"gemini_api_key":"never include"}', encoding="utf-8")
        (root / "memory").mkdir()
        (root / "memory" / "long_term.json").write_text('{"private":"never include"}', encoding="utf-8")
        (root / "node_modules").mkdir()
        (root / "node_modules" / "login.js").write_text("third party", encoding="utf-8")

        rows = _ranked_sources(root, "login user flow", overview=False)
        paths = {path.as_posix() for path, _content, _score in rows}
        assert "login.py" in paths, paths
        assert "api_key.json" not in paths and "memory/long_term.json" not in paths
        assert all("node_modules" not in path for path in paths)
        overview = _ranked_sources(root, "", overview=True)
        assert any(path.name == "README.md" for path, _content, _score in overview)
    print("repository_review selftest: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
