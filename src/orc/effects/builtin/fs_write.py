"""`fs.write_file` — the reference executor.

Writes a file under the workspace's effects sandbox (`<workspace>/out/`). Needs no
external credential, so it proves the propose -> approve -> execute -> record loop
end to end. The target path is confined to the sandbox: traversal is rejected.
"""

from __future__ import annotations

from typing import Any

from orc.effects.registry import register
from orc.paths import workspace_effects_dir

_PARAMS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["path", "content"],
    "properties": {
        "path": {"type": "string"},
        "content": {"type": "string"},
    },
}


class FsWriteFile:
    id = "fs.write_file"
    version = 1
    params_schema = _PARAMS_SCHEMA
    required_credential: str | None = None

    def execute(
        self, *, params: dict[str, Any], credential: str | None, workspace: str
    ) -> dict[str, Any]:
        sandbox = workspace_effects_dir(workspace).resolve()
        target = (sandbox / params["path"]).resolve()
        # Confine to the sandbox: reject traversal / absolute escapes.
        if not target.is_relative_to(sandbox):
            raise ValueError(
                f"fs.write_file path escapes the workspace sandbox: {params['path']!r}"
            )
        target.parent.mkdir(parents=True, exist_ok=True)
        content = params["content"]
        target.write_text(content)
        return {"path": str(target), "bytes_written": len(content.encode("utf-8"))}


register(FsWriteFile())
