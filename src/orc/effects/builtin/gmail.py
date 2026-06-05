"""`gmail.send_draft` — send an existing Gmail draft. The external-credential executor.

Demonstrates the credential split: the OAuth token (GMAIL_TOKEN) lives only in the
effect-plane process. The analysis plane proposes `send_draft(draft_id=...)`; a human
approves; the worker/`orc execute` carries it out.

Sending is the mutation worth gating — so we send an *existing* draft (created by a
human in Gmail) rather than composing+sending in one step. Once sent the draft is
gone, so an accidental retry 404s rather than double-sending.
"""

from __future__ import annotations

from typing import Any

import httpx

from orc.effects.registry import register

_SEND_URL = "https://gmail.googleapis.com/gmail/v1/users/me/drafts/send"

_PARAMS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["draft_id"],
    "additionalProperties": False,
    "properties": {"draft_id": {"type": "string"}},
}


class GmailSendDraft:
    id = "gmail.send_draft"
    version = 1
    params_schema = _PARAMS_SCHEMA
    required_credential = "GMAIL_TOKEN"

    def execute(
        self, *, params: dict[str, Any], credential: str | None, workspace: str
    ) -> dict[str, Any]:
        response = httpx.post(
            _SEND_URL,
            headers={"Authorization": f"Bearer {credential}"},
            json={"id": params["draft_id"]},
            timeout=30.0,
        )
        if response.status_code >= 400:
            # Surface only the status — the Gmail error body can carry account
            # metadata, and this message is persisted to last_error in the DB.
            raise RuntimeError(f"Gmail API error: HTTP {response.status_code}")
        data = response.json()
        return {"message_id": data.get("id"), "thread_id": data.get("threadId")}


register(GmailSendDraft())
