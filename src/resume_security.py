"""Prompt-injection analysis and durable manual overrides for resume PDFs."""

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import unicodedata

import fitz

from .store import DATA_DIRECTORY

SECURITY_POLICY_VERSION = "resume-security-v1"
OVERRIDE_FILE = DATA_DIRECTORY / "resume_security_overrides.json"
ZERO_WIDTH = {"\u200b", "\u200c", "\u200d", "\u2060", "\ufeff"}
BIDI_CONTROLS = {
    "\u202a", "\u202b", "\u202c", "\u202d", "\u202e",
    "\u2066", "\u2067", "\u2068", "\u2069",
}
INSTRUCTION_PATTERNS = {
    "instruction_override": re.compile(
        r"\b(ignore|disregard|override|bypass)\b.{0,60}\b"
        r"(instruction|prompt|rule|system|developer)\b",
        re.IGNORECASE | re.DOTALL,
    ),
    "prompt_exfiltration": re.compile(
        r"\b(reveal|show|print|repeat|leak|output)\b.{0,60}\b"
        r"(system prompt|developer message|api key|secret|credential)\b",
        re.IGNORECASE | re.DOTALL,
    ),
    "score_manipulation": re.compile(
        r"\b(score|rate|rank|shortlist|candidate)\b.{0,50}\b"
        r"(100|maximum|highest|approve|select|five|5/5)\b",
        re.IGNORECASE | re.DOTALL,
    ),
    "tool_action": re.compile(
        r"\b(call|invoke|execute|run|send|write|delete)\b.{0,50}\b"
        r"(tool|function|email|gmail|sheet|spreadsheet|api)\b",
        re.IGNORECASE | re.DOTALL,
    ),
    "role_impersonation": re.compile(
        r"\b(you are now|act as|developer mode|system message|assistant message)\b",
        re.IGNORECASE,
    ),
    "forged_protocol": re.compile(
        r"\b(function_call|function_response|tool_call|tool_result|system_instruction)\b",
        re.IGNORECASE,
    ),
}
ENCODED_BLOCK = re.compile(
    r"(?<![A-Za-z0-9+/=])(?:[A-Za-z0-9+/]{4}){8,}={0,2}(?![A-Za-z0-9+/=])"
    r"|(?<![0-9A-Fa-f])(?:[0-9A-Fa-f]{2}){24,}(?![0-9A-Fa-f])"
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_resume_text(text: str) -> tuple[str, list[str]]:
    """Normalize text while reporting invisible/control-character signals."""
    reasons = []
    if any(value in text for value in ZERO_WIDTH):
        reasons.append("ZERO_WIDTH_CHARACTERS")
    if any(value in text for value in BIDI_CONTROLS):
        reasons.append("BIDI_CONTROL_CHARACTERS")
    normalized = unicodedata.normalize("NFKC", text)
    normalized = "".join(
        " " if value in ZERO_WIDTH or value in BIDI_CONTROLS else value
        for value in normalized
        if value in "\n\t" or unicodedata.category(value) not in {"Cc", "Cf"}
    )
    normalized = re.sub(r"[ \t]{4,}", " ", normalized)
    normalized = re.sub(r"\n{4,}", "\n\n", normalized)
    normalized = re.sub(r"(.)\1{30,}", lambda match: match.group(1) * 10, normalized)
    return normalized.strip(), reasons


def _instruction_codes(text: str) -> list[str]:
    codes = [
        f"TEXT_{name.upper()}"
        for name, pattern in INSTRUCTION_PATTERNS.items()
        if pattern.search(text)
    ]
    if ENCODED_BLOCK.search(text):
        codes.append("ENCODED_PAYLOAD_PATTERN")
    # Common scrambled variants used to bypass literal filters.
    compact = re.sub(r"[^a-z]", "", text.lower())
    if "ignroeallprevoius" in compact or "bpyassall" in compact:
        codes.append("OBFUSCATED_INSTRUCTION_PATTERN")
    return codes


class ResumeSecurityAnalyzer:
    """Inspect PDF spans and produce immutable visible-text chunks."""

    def analyze(self, path: Path) -> dict:
        try:
            document = fitz.open(path)
            if document.page_count > 30:
                document.close()
                return {
                    "status": "SECURITY_ANALYSIS_ERROR",
                    "reason_codes": ["PDF_PAGE_LIMIT"],
                    "normalized_text_hash": "",
                    "normalized_text": "",
                    "visible_chunks": [],
                    "indicators": [],
                    "deterministic_review": True,
                    "policy_version": SECURITY_POLICY_VERSION,
                    "error": "PDF exceeds security analysis page limit.",
                }
            raw_visible_lines = []
            hidden_instruction = False
            reasons: list[str] = []
            indicators: list[dict] = []
            chunks = []
            chunk_number = 0
            for page_number, page in enumerate(document, start=1):
                page_rect = page.rect
                page_dict = page.get_text("dict")
                page_lines = []
                for block in page_dict.get("blocks", []):
                    for line in block.get("lines", []):
                        visible_parts = []
                        for span in line.get("spans", []):
                            text = str(span.get("text", ""))
                            if not text.strip():
                                continue
                            bbox = fitz.Rect(span.get("bbox", (0, 0, 0, 0)))
                            size = float(span.get("size", 0) or 0)
                            alpha = int(span.get("alpha", 255) or 0)
                            color = int(span.get("color", 0) or 0)
                            off_page = not page_rect.intersects(bbox)
                            tiny = size < 3
                            transparent = alpha < 20
                            white = color in {0xFFFFFF, 0xFEFEFE, 0xFDFDFD}
                            codes = _instruction_codes(text)
                            # White text can be legitimate on a dark design. Treat it
                            # as hidden only when it is also instruction-like.
                            hidden = off_page or tiny or transparent or (white and bool(codes))
                            if hidden:
                                hidden_code = (
                                    "HIDDEN_OFF_PAGE_TEXT" if off_page
                                    else "HIDDEN_TINY_TEXT" if tiny
                                    else "HIDDEN_TRANSPARENT_TEXT" if transparent
                                    else "HIDDEN_LOW_CONTRAST_TEXT"
                                )
                                reasons.append(hidden_code)
                                indicators.append(
                                    {
                                        "code": hidden_code,
                                        "page": page_number,
                                        "span_hash": hashlib.sha256(
                                            text.encode("utf-8", errors="ignore")
                                        ).hexdigest()[:16],
                                        "instruction_like": bool(codes),
                                    }
                                )
                                if codes:
                                    hidden_instruction = True
                                    reasons.extend(codes)
                                continue
                            visible_parts.append(text)
                        line_text = " ".join(visible_parts).strip()
                        if line_text:
                            page_lines.append(line_text)
                raw_visible_lines.extend(page_lines)
                for line_text in page_lines:
                    normalized, normalize_reasons = normalize_resume_text(line_text)
                    reasons.extend(normalize_reasons)
                    if not normalized:
                        continue
                    chunk_number += 1
                    if chunk_number > 300:
                        reasons.append("VISIBLE_CHUNK_LIMIT")
                        continue
                    chunk_id = f"p{page_number}-c{chunk_number}"
                    chunks.append(
                        {
                            "chunk_id": chunk_id,
                            "page": page_number,
                            "text": normalized[:1_500],
                            "hash": hashlib.sha256(normalized.encode()).hexdigest()[:16],
                        }
                    )
            document.close()
            normalized_text, normalization_reasons = normalize_resume_text(
                "\n".join(raw_visible_lines)
            )
            if len(normalized_text) > 40_000:
                normalized_text = normalized_text[:40_000]
                reasons.append("VISIBLE_TEXT_LIMIT")
            reasons.extend(normalization_reasons)
            text_codes = _instruction_codes(normalized_text)
            reasons.extend(text_codes)
            reasons = sorted(set(reasons))
            deterministic_review = hidden_instruction or any(
                value.startswith("TEXT_") for value in reasons
            )
            independent_signals = {
                value
                for value in reasons
                if value.startswith("TEXT_")
                or value in {
                    "ENCODED_PAYLOAD_PATTERN",
                    "OBFUSCATED_INSTRUCTION_PATTERN",
                    "ZERO_WIDTH_CHARACTERS",
                    "BIDI_CONTROL_CHARACTERS",
                }
            }
            if len(independent_signals) >= 2:
                deterministic_review = True
            return {
                "status": (
                    "INJECTION_REVIEW_REQUIRED" if deterministic_review else "CLEAN"
                ),
                "reason_codes": reasons,
                "normalized_text_hash": hashlib.sha256(
                    normalized_text.encode()
                ).hexdigest(),
                "normalized_text": normalized_text,
                "visible_chunks": chunks,
                "indicators": indicators,
                "deterministic_review": deterministic_review,
                "policy_version": SECURITY_POLICY_VERSION,
            }
        except Exception as error:
            return {
                "status": "SECURITY_ANALYSIS_ERROR",
                "reason_codes": ["PDF_SECURITY_ANALYSIS_ERROR"],
                "normalized_text_hash": "",
                "normalized_text": "",
                "visible_chunks": [],
                "indicators": [],
                "deterministic_review": True,
                "policy_version": SECURITY_POLICY_VERSION,
                "error": str(error)[:500],
            }


def load_security_overrides() -> dict:
    if not OVERRIDE_FILE.exists():
        return {}
    return json.loads(OVERRIDE_FILE.read_text(encoding="utf-8"))


def save_security_override(
    file_id: str,
    modified_time: str,
    decision: str,
    reviewer: str,
) -> dict:
    if decision not in {"allow", "quarantine", "replace"}:
        raise ValueError("Security decision must be allow, quarantine, or replace.")
    overrides = load_security_overrides()
    value = {
        "file_id": file_id,
        "modified_time": modified_time,
        "decision": decision,
        "reviewer": reviewer,
        "timestamp": _now(),
        "policy_version": SECURITY_POLICY_VERSION,
    }
    overrides[file_id] = value
    DATA_DIRECTORY.mkdir(parents=True, exist_ok=True)
    temporary = OVERRIDE_FILE.with_suffix(".tmp")
    temporary.write_text(json.dumps(overrides, indent=2), encoding="utf-8")
    temporary.chmod(0o600)
    temporary.replace(OVERRIDE_FILE)
    OVERRIDE_FILE.chmod(0o600)
    return value


def matching_override(file_id: str, modified_time: str) -> dict | None:
    value = load_security_overrides().get(file_id)
    if not value or value.get("modified_time") != modified_time:
        return None
    if value.get("policy_version") != SECURITY_POLICY_VERSION:
        return None
    return value
