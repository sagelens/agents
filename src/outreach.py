"""HITL-gated candidate outreach through the Gmail API."""

import base64
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import parseaddr
from html import escape
import hashlib
import json
import os
from pathlib import Path
import re
from uuid import uuid4

from google import genai
from google.genai import types
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from .approval import ApprovalRequest
from .google_workspace import GoogleWorkspace
from .resume_screening import CandidateRegistryAgent, RESULTS_TAB
from .store import DATA_DIRECTORY, append_outreach_run

GMAIL_SEND_SCOPE = "https://www.googleapis.com/auth/gmail.send"
EMAIL_IDENTITY_SCOPE = "https://www.googleapis.com/auth/userinfo.email"
EMAIL_SCOPES = [GMAIL_SEND_SCOPE, EMAIL_IDENTITY_SCOPE, "openid"]
TOKEN_FILE = DATA_DIRECTORY / "gmail_token.json"
DRAFT_DIRECTORY = DATA_DIRECTORY / "outreach_drafts"
ALLOWED_OUTREACH_STATUSES = {
    "NOT_PREPARED",
    "PENDING_APPROVAL",
    "EDITED_PENDING_APPROVAL",
    "REJECTED",
    "APPROVED",
    "SENDING",
    "SENT",
    "FAILED",
    "UNKNOWN",
}


class PreSendError(RuntimeError):
    """Failure known to occur before Gmail accepted a send request."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _required_company_config() -> dict:
    values = {
        "company_name": os.getenv("COMPANY_NAME", "").strip(),
        "sender_display_name": os.getenv("SENDER_DISPLAY_NAME", "").strip(),
        "sender_role": os.getenv("SENDER_ROLE", "").strip(),
        "reply_to_email": os.getenv("REPLY_TO_EMAIL", "").strip(),
    }
    missing = [key.upper() for key, value in values.items() if not value]
    placeholders = [
        key.upper()
        for key, value in values.items()
        if value.lower().startswith("replace_with")
    ]
    if missing or placeholders:
        names = sorted(set(missing + placeholders))
        raise RuntimeError(f"Configure real company identity values: {', '.join(names)}")
    if "@" not in values["reply_to_email"]:
        raise ValueError("REPLY_TO_EMAIL must be a valid email address.")
    return values


def _oauth_client_config() -> dict:
    required = {
        "client_id": os.getenv("CLIENT_ID", "").strip(),
        "client_secret": os.getenv("CLIENT_SECRET", "").strip(),
        "auth_uri": os.getenv(
            "AUTH_URI", "https://accounts.google.com/o/oauth2/auth"
        ).strip(),
        "token_uri": os.getenv(
            "TOKEN_URI", "https://oauth2.googleapis.com/token"
        ).strip(),
    }
    if not required["client_id"] or not required["client_secret"]:
        raise RuntimeError("Set CLIENT_ID and CLIENT_SECRET in .env.")
    redirect_uris = [
        value.strip()
        for value in os.getenv("REDIRECT_URIS", "http://localhost").split(",")
        if value.strip()
    ]
    return {
        "installed": {
            **required,
            "redirect_uris": redirect_uris,
        }
    }


def _save_credentials(credentials: Credentials) -> None:
    DATA_DIRECTORY.mkdir(parents=True, exist_ok=True)
    temporary = TOKEN_FILE.with_suffix(".tmp")
    temporary.write_text(credentials.to_json(), encoding="utf-8")
    temporary.chmod(0o600)
    temporary.replace(TOKEN_FILE)
    TOKEN_FILE.chmod(0o600)


def authorize_email() -> dict:
    """Run local-browser OAuth and persist a refreshable Gmail token."""
    _required_company_config()
    flow = InstalledAppFlow.from_client_config(_oauth_client_config(), EMAIL_SCOPES)
    try:
        credentials = flow.run_local_server(
            host="localhost",
            port=0,
            open_browser=True,
            authorization_prompt_message="Opening Google authorization in your browser...",
            success_message=(
                "Google returned to the application. You may close this window "
                "and check the terminal for the final authorization result."
            ),
            prompt="consent select_account",
            access_type="offline",
            include_granted_scopes="false",
        )
    except Warning as error:
        raise RuntimeError(
            "Google did not grant gmail.send. Run /authorize-email again and "
            "enable the 'Send email on your behalf' permission on the consent screen."
        ) from error
    if not credentials.has_scopes(EMAIL_SCOPES):
        raise RuntimeError(
            "Authorization returned without gmail.send. Re-authorize and select "
            "the Gmail sending permission."
        )
    _save_credentials(credentials)
    identity = get_email_sender_identity()
    return {"authorized": True, **identity}


def _load_credentials() -> Credentials:
    if not TOKEN_FILE.exists():
        raise RuntimeError("Email is not authorized. Run /authorize-email first.")
    credentials = Credentials.from_authorized_user_file(str(TOKEN_FILE), EMAIL_SCOPES)
    if credentials.expired and credentials.refresh_token:
        credentials.refresh(Request())
        _save_credentials(credentials)
    if not credentials.valid:
        raise RuntimeError("Saved email authorization is invalid; authorize again.")
    return credentials


def get_email_sender_identity() -> dict:
    """Return only the authorized account's email address."""
    credentials = _load_credentials()
    service = build("oauth2", "v2", credentials=credentials, cache_discovery=False)
    profile = service.userinfo().get().execute()
    email = str(profile.get("email", "")).strip()
    if not email:
        raise RuntimeError("Google did not return an authorized sender email.")
    return {"email": email, "token_file": str(TOKEN_FILE)}


def email_auth_status() -> dict:
    try:
        identity = get_email_sender_identity()
        return {"authorized": True, **identity}
    except Exception as error:
        return {"authorized": False, "error": str(error)}


def _draft_content(draft: dict) -> dict:
    return {
        "draft_id": draft["draft_id"],
        "to": draft["to"],
        "from": draft["from"],
        "reply_to": draft["reply_to"],
        "subject": draft["subject"],
        "text_body": draft["text_body"],
        "html_body": draft["html_body"],
        "candidate_row": draft["candidate_row"],
    }


def _draft_hash(draft: dict) -> str:
    encoded = json.dumps(
        _draft_content(draft),
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _draft_path(draft_id: str) -> Path:
    if not re.fullmatch(r"[0-9a-f-]{36}", draft_id):
        raise ValueError("Invalid draft ID.")
    return DRAFT_DIRECTORY / f"{draft_id}.json"


def _save_draft(draft: dict) -> None:
    DRAFT_DIRECTORY.mkdir(parents=True, exist_ok=True)
    DRAFT_DIRECTORY.chmod(0o700)
    path = _draft_path(draft["draft_id"])
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(draft, indent=2, ensure_ascii=False), encoding="utf-8")
    temporary.chmod(0o600)
    temporary.replace(path)
    path.chmod(0o600)


def _load_draft(draft_id: str) -> dict:
    path = _draft_path(draft_id)
    if not path.exists():
        raise ValueError("Unknown outreach draft.")
    return json.loads(path.read_text(encoding="utf-8"))


class CandidateOutreachAgent:
    """Prepare invitation copy without any Gmail capability."""

    def __init__(self, api_key: str, model: str) -> None:
        self.client = genai.Client(api_key=api_key)
        self.model = model

    def _personalization(self, candidate: dict) -> str:
        evidence = candidate.get("validated_evidence", [])
        if not evidence:
            return ""
        try:
            response = self.client.models.generate_content(
                model=self.model,
                contents=json.dumps(
                    {
                        "candidate_first_name": candidate["first_name"],
                        "validated_professional_evidence": evidence,
                    },
                    ensure_ascii=False,
                ),
                config=types.GenerateContentConfig(
                    system_instruction=(
                        "Return JSON with personalization_sentence and evidence_chunk_ids. "
                        "Write at most one short sentence grounded only in supplied evidence. "
                        "Do not mention scores, ranking, protected traits, other candidates, "
                        "or facts absent from the input. Return an empty sentence when the "
                        "evidence is too vague."
                    ),
                    response_mime_type="application/json",
                    response_schema={
                        "type": "object",
                        "properties": {
                            "personalization_sentence": {"type": "string"},
                            "evidence_chunk_ids": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                        },
                        "required": [
                            "personalization_sentence",
                            "evidence_chunk_ids",
                        ],
                    },
                    temperature=0,
                ),
            )
            value = json.loads(response.text or "{}")
            sentence = str(value.get("personalization_sentence", "")).strip()[:300]
            allowed_ids = {item["chunk_id"] for item in evidence}
            cited_ids = {
                str(item) for item in value.get("evidence_chunk_ids", [])
            }
            forbidden = re.compile(
                r"https?://|www\.|[\w.+-]+@[\w.-]+|"
                r"\b(prompt|instruction|system message|score|rank|ignore|"
                r"execute|click|open|send|call a tool)\b",
                re.IGNORECASE,
            )
            if (
                not sentence
                or not cited_ids
                or not cited_ids.issubset(allowed_ids)
                or forbidden.search(sentence)
                or len(re.findall(r"[.!?]", sentence)) > 1
            ):
                return ""
            return sentence
        except Exception:
            return ""

    def prepare(self, candidate: dict, sender_email: str) -> dict:
        config = _required_company_config()
        personalization = self._personalization(candidate)
        paragraphs = [
            f"Hi {candidate['first_name']},",
            (
                f"Thank you for your interest in the Full-Stack AI Engineer opportunity "
                f"at {config['company_name']}. We reviewed your experience and would like "
                "to invite you to the next stage of our process."
            ),
        ]
        if personalization:
            paragraphs.append(personalization)
        paragraphs.extend(
            [
                (
                    "Please reply with a few time slots when you would be available "
                    "for an initial conversation."
                ),
                (
                    f"Best regards,\n{config['sender_display_name']}\n"
                    f"{config['sender_role']}\n{config['company_name']}"
                ),
            ]
        )
        text_body = "\n\n".join(paragraphs)
        html_body = "".join(
            f"<p>{escape(paragraph).replace(chr(10), '<br>')}</p>"
            for paragraph in paragraphs
        )
        draft = {
            "draft_id": str(uuid4()),
            "candidate_row": candidate["row"],
            "candidate_name": candidate["candidate_name"],
            "to": candidate["email"],
            "from": sender_email,
            "reply_to": config["reply_to_email"],
            "subject": (
                "Next steps for the Full-Stack AI Engineer opportunity at "
                f"{config['company_name']}"
            ),
            "text_body": text_body,
            "html_body": html_body,
            "personalization_evidence": {
                "chunk_ids": [
                    item["chunk_id"]
                    for item in candidate.get("validated_evidence", [])[:8]
                ],
            },
            "status": "PENDING_APPROVAL",
            "approval": None,
            "gmail_message_id": "",
            "created_at": _now(),
            "updated_at": _now(),
        }
        draft["draft_hash"] = _draft_hash(draft)
        _save_draft(draft)
        return draft


class OutreachRegistry:
    """Read eligible candidates and update only outreach columns U:AC."""

    def __init__(self, workspace: GoogleWorkspace) -> None:
        self.workspace = workspace
        CandidateRegistryAgent(workspace).initialize()

    def candidates(self) -> list[dict]:
        rows = self.workspace.get_values(f"'{RESULTS_TAB}'!A2:AH")
        candidates = []
        for row_number, row in enumerate(rows, start=2):
            row = list(row) + [""] * (34 - len(row))
            if row[10] != "SHORTLISTED" or not str(row[6]).strip():
                continue
            if row[29] != "CLEAN":
                continue
            status = str(row[20] or "NOT_PREPARED")
            try:
                provenance = json.loads(row[32] or "{}")
            except json.JSONDecodeError:
                provenance = {}
            chunks = provenance.get("chunks", {})
            validated_evidence = [
                {"chunk_id": chunk_id, "text": str(text)[:500]}
                for chunk_id, text in list(chunks.items())[:8]
            ]
            raw_first_name = (str(row[5]).strip().split() or ["Candidate"])[0]
            first_name = re.sub(r"[^A-Za-z'-]", "", raw_first_name)[:60] or "Candidate"
            candidates.append(
                {
                    "row": row_number,
                    "candidate_name": str(row[5]).strip() or "Candidate",
                    "first_name": first_name,
                    "email": str(row[6]).strip(),
                    "validated_evidence": validated_evidence,
                    "outreach_status": status,
                    "draft_id": str(row[21]),
                }
            )
        return candidates

    def update(self, row: int, draft: dict, error: str = "") -> None:
        approval = draft.get("approval") or {}
        values = [
            draft["status"],
            draft["draft_id"],
            draft["draft_hash"],
            draft["subject"],
            approval.get("approval_id", ""),
            approval.get("approved_at", ""),
            draft.get("sent_at", ""),
            draft.get("gmail_message_id", ""),
            error,
        ]
        self.workspace.update_values(f"'{RESULTS_TAB}'!U{row}:AC{row}", [values])

    def status_counts(self) -> dict:
        counts: dict[str, int] = {}
        for candidate in self.candidates():
            status = candidate["outreach_status"]
            counts[status] = counts.get(status, 0) + 1
        return counts


def edit_draft(draft: dict, subject: str, text_body: str) -> dict:
    if subject.strip():
        draft["subject"] = subject.strip()[:300]
    if text_body.strip():
        draft["text_body"] = text_body.strip()[:10_000]
        draft["html_body"] = "".join(
            f"<p>{escape(value).replace(chr(10), '<br>')}</p>"
            for value in draft["text_body"].split("\n\n")
        )
    draft["status"] = "EDITED_PENDING_APPROVAL"
    draft["approval"] = None
    draft["updated_at"] = _now()
    draft["draft_hash"] = _draft_hash(draft)
    _save_draft(draft)
    return draft


def approve_draft(draft: dict, approval_id: str) -> dict:
    draft["approval"] = {
        "approval_id": approval_id,
        "draft_hash": draft["draft_hash"],
        "approved_at": _now(),
    }
    draft["status"] = "APPROVED"
    draft["updated_at"] = _now()
    _save_draft(draft)
    return draft


def reject_draft(draft: dict, approval_id: str) -> dict:
    draft["approval"] = {
        "approval_id": approval_id,
        "draft_hash": draft["draft_hash"],
        "rejected_at": _now(),
    }
    draft["status"] = "REJECTED"
    draft["updated_at"] = _now()
    _save_draft(draft)
    return draft


def send_approved_email(
    approval_id: str,
    draft_hash: str,
    draft_id: str,
) -> dict:
    """Reload, validate, and send exactly one approved immutable draft."""
    draft = _load_draft(draft_id)
    approval = draft.get("approval") or {}
    if draft["status"] != "APPROVED":
        raise PreSendError("Draft is not approved.")
    if approval.get("approval_id") != approval_id:
        raise PreSendError("Approval ID does not match the draft.")
    current_hash = _draft_hash(draft)
    if draft_hash != current_hash or approval.get("draft_hash") != current_hash:
        raise PreSendError("Draft changed after approval.")
    if draft.get("gmail_message_id"):
        raise PreSendError("Draft was already sent.")
    if parseaddr(draft["to"])[1] != draft["to"]:
        raise PreSendError("Draft recipient is invalid.")
    try:
        credentials = _load_credentials()
        identity = get_email_sender_identity()
    except Exception as error:
        raise PreSendError(str(error)) from error
    if identity["email"].lower() != draft["from"].lower():
        raise PreSendError("Authorized sender does not match the approved draft.")
    draft["status"] = "SENDING"
    draft["updated_at"] = _now()
    _save_draft(draft)
    message = EmailMessage()
    message["From"] = f"{_required_company_config()['sender_display_name']} <{draft['from']}>"
    message["To"] = draft["to"]
    message["Reply-To"] = draft["reply_to"]
    message["Subject"] = draft["subject"]
    domain = draft["from"].split("@")[-1]
    message["Message-ID"] = f"<{draft['draft_id']}@{domain}>"
    message.set_content(draft["text_body"])
    message.add_alternative(draft["html_body"], subtype="html")
    encoded = base64.urlsafe_b64encode(message.as_bytes()).decode()
    service = build("gmail", "v1", credentials=credentials, cache_discovery=False)
    response = (
        service.users()
        .messages()
        .send(userId="me", body={"raw": encoded})
        .execute(num_retries=0)
    )
    return {"gmail_message_id": response["id"], "thread_id": response.get("threadId", "")}


def _new_approval(draft: dict, run_id: str) -> ApprovalRequest:
    return ApprovalRequest(
        workflow_id=run_id,
        node_id=f"approve_{draft['draft_id']}",
        action=f"Send outreach email to {draft['to']}",
        reason="External email requires exact-content human approval.",
        input_preview={
            "draft_id": draft["draft_id"],
            "draft_hash": draft["draft_hash"],
            "to": draft["to"],
            "subject": draft["subject"],
        },
    )


def run_outreach(
    api_key: str,
    model: str,
    approval_provider,
    resume: bool = False,
) -> dict:
    """Prepare, approve, and send outreach one shortlisted candidate at a time."""
    config = _required_company_config()
    identity = get_email_sender_identity()
    registry = OutreachRegistry(GoogleWorkspace())
    run_id = str(uuid4())
    events = []
    counts = {"eligible": 0, "sent": 0, "rejected": 0, "failed": 0, "skipped": 0}
    agent = CandidateOutreachAgent(api_key, model)
    for candidate in registry.candidates():
        status = candidate["outreach_status"]
        if status in {"SENT", "SENDING", "UNKNOWN"}:
            counts["skipped"] += 1
            continue
        if status == "REJECTED" and not resume:
            counts["skipped"] += 1
            continue
        counts["eligible"] += 1
        try:
            if candidate["draft_id"] and status in {
                "PENDING_APPROVAL",
                "EDITED_PENDING_APPROVAL",
                "FAILED",
                "APPROVED",
            }:
                draft = _load_draft(candidate["draft_id"])
                if status in {"FAILED", "APPROVED"}:
                    draft["status"] = "PENDING_APPROVAL"
                    draft["approval"] = None
                    draft["draft_hash"] = _draft_hash(draft)
                    _save_draft(draft)
            else:
                events.append(
                    {
                        "type": "agent_started",
                        "agent": "candidate_outreach_agent",
                        "candidate_row": candidate["row"],
                        "timestamp": _now(),
                    }
                )
                draft = agent.prepare(candidate, identity["email"])
            registry.update(candidate["row"], draft)
            while draft["status"] in {
                "PENDING_APPROVAL",
                "EDITED_PENDING_APPROVAL",
            }:
                approval = _new_approval(draft, run_id)
                events.append(
                    {
                        "type": "approval_requested",
                        "approval_id": approval.approval_id,
                        "draft_id": draft["draft_id"],
                        "draft_hash": draft["draft_hash"],
                        "recipient": draft["to"],
                        "timestamp": _now(),
                    }
                )
                decision = approval_provider(draft, approval.to_dict())
                action = decision.get("decision", "rejected")
                if action == "edited":
                    draft = edit_draft(
                        draft,
                        decision.get("subject", ""),
                        decision.get("text_body", ""),
                    )
                    registry.update(candidate["row"], draft)
                    continue
                if action == "approved":
                    draft = approve_draft(draft, approval.approval_id)
                    registry.update(candidate["row"], draft)
                    break
                draft = reject_draft(draft, approval.approval_id)
                registry.update(candidate["row"], draft)
                counts["rejected"] += 1
                break
            if draft["status"] != "APPROVED":
                continue
            try:
                result = send_approved_email(
                    draft["approval"]["approval_id"],
                    draft["draft_hash"],
                    draft["draft_id"],
                )
            except PreSendError as error:
                draft["status"] = "FAILED"
                _save_draft(draft)
                registry.update(candidate["row"], draft, str(error)[:1_000])
                counts["failed"] += 1
                continue
            except HttpError as error:
                status_code = getattr(error.resp, "status", 0)
                draft["status"] = "FAILED" if 400 <= status_code < 500 else "UNKNOWN"
                _save_draft(draft)
                registry.update(candidate["row"], draft, str(error)[:1_000])
                counts["failed"] += 1
                continue
            except Exception as error:
                draft["status"] = "UNKNOWN"
                _save_draft(draft)
                registry.update(candidate["row"], draft, str(error)[:1_000])
                counts["failed"] += 1
                continue
            draft["status"] = "SENT"
            draft["sent_at"] = _now()
            draft["gmail_message_id"] = result["gmail_message_id"]
            _save_draft(draft)
            registry.update(candidate["row"], draft)
            counts["sent"] += 1
            events.append(
                {
                    "type": "email_sent",
                    "agent": "email_delivery_agent",
                    "recipient": draft["to"],
                    "draft_id": draft["draft_id"],
                    "draft_hash": draft["draft_hash"],
                    "gmail_message_id": result["gmail_message_id"],
                    "timestamp": _now(),
                }
            )
        except Exception as error:
            counts["failed"] += 1
            events.append(
                {
                    "type": "outreach_error",
                    "candidate_row": candidate["row"],
                    "error": str(error)[:1_000],
                    "timestamp": _now(),
                }
            )
    output = {
        "run_id": run_id,
        "sender": identity["email"],
        "company": config["company_name"],
        "counts": counts,
        "events": events,
        "timestamp": _now(),
    }
    append_outreach_run(output)
    return output
