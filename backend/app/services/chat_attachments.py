import hashlib
import io
import json
import mimetypes
import re
import uuid
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime
from html import unescape
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

from fastapi import UploadFile

from app.core.config import settings


MAX_CHAT_ATTACHMENT_BYTES = 25 * 1024 * 1024
CHAT_ATTACHMENT_ROOT = settings.data_dir / "chat_attachments"

IMAGE_CONTENT_TYPES = {
    "image/gif",
    "image/jpeg",
    "image/png",
    "image/webp",
}

TEXT_CONTENT_TYPES = {
    "application/json",
    "application/xml",
    "text/csv",
    "text/html",
    "text/markdown",
    "text/plain",
    "text/tab-separated-values",
    "text/xml",
}

DOCUMENT_CONTENT_TYPES = {
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}

ALLOWED_CONTENT_TYPES = IMAGE_CONTENT_TYPES | TEXT_CONTENT_TYPES | DOCUMENT_CONTENT_TYPES
DOCX_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


class ChatAttachmentError(RuntimeError):
    """Raised when a chat attachment cannot be stored or read safely."""


@dataclass(frozen=True)
class ChatAttachment:
    id: str
    filename: str
    storage_name: str
    content_type: str
    size_bytes: int
    kind: str
    source: str
    owner_user_id: str
    created_at: str
    sha256: str

    def to_public_dict(self) -> dict[str, Any]:
        url = f"/api/v1/ai/chat/files/{self.id}"
        return {
            "id": self.id,
            "filename": self.filename,
            "content_type": self.content_type,
            "size_bytes": self.size_bytes,
            "kind": self.kind,
            "source": self.source,
            "url": url,
            "download_url": url,
            "created_at": self.created_at,
        }


class ChatAttachmentStore:
    def __init__(self, root: Path = CHAT_ATTACHMENT_ROOT) -> None:
        self.root = root

    async def save_upload(
        self,
        upload: UploadFile,
        *,
        owner_user_id: str,
        session_id: str | None = None,
    ) -> ChatAttachment:
        content = await upload.read(MAX_CHAT_ATTACHMENT_BYTES + 1)
        if len(content) > MAX_CHAT_ATTACHMENT_BYTES:
            raise ChatAttachmentError("File is too large. Maximum upload size is 25 MB.")
        if not content:
            raise ChatAttachmentError("File is empty.")

        filename = _safe_filename(upload.filename or "attachment")
        content_type = _normalize_content_type(upload.content_type, filename)
        if content_type not in ALLOWED_CONTENT_TYPES:
            raise ChatAttachmentError(f"Unsupported file type: {content_type}")

        return self.save_generated(
            filename=filename,
            content=content,
            content_type=content_type,
            owner_user_id=owner_user_id,
            source="upload",
            session_id=session_id,
        )

    def save_generated(
        self,
        *,
        filename: str,
        content: bytes,
        content_type: str,
        owner_user_id: str,
        source: str = "generated",
        session_id: str | None = None,
    ) -> ChatAttachment:
        if len(content) > MAX_CHAT_ATTACHMENT_BYTES:
            raise ChatAttachmentError("Generated file is too large. Maximum size is 25 MB.")
        if not content:
            raise ChatAttachmentError("Generated file is empty.")

        self.root.mkdir(parents=True, exist_ok=True)
        file_id = uuid.uuid4().hex
        safe_name = _safe_filename(filename)
        content_type = _normalize_content_type(content_type, safe_name)
        if content_type not in ALLOWED_CONTENT_TYPES:
            raise ChatAttachmentError(f"Unsupported file type: {content_type}")

        suffix = Path(safe_name).suffix.lower()
        storage_name = f"content{suffix}" if suffix else "content.bin"
        file_dir = self._attachment_dir(file_id)
        file_dir.mkdir(parents=True, exist_ok=False)
        data_path = file_dir / storage_name
        data_path.write_bytes(content)

        attachment = ChatAttachment(
            id=file_id,
            filename=safe_name,
            storage_name=storage_name,
            content_type=content_type,
            size_bytes=len(content),
            kind=_attachment_kind(content_type),
            source=source,
            owner_user_id=owner_user_id,
            created_at=datetime.now(tz=UTC).isoformat(),
            sha256=hashlib.sha256(content).hexdigest(),
        )
        metadata = {
            **attachment.__dict__,
            "session_id": session_id,
        }
        (file_dir / "metadata.json").write_text(
            json.dumps(metadata, sort_keys=True, indent=2),
            encoding="utf-8",
        )
        return attachment

    def get(self, file_id: str) -> ChatAttachment:
        metadata = self._read_metadata(file_id)
        return ChatAttachment(
            id=str(metadata["id"]),
            filename=str(metadata["filename"]),
            storage_name=str(metadata["storage_name"]),
            content_type=str(metadata["content_type"]),
            size_bytes=int(metadata["size_bytes"]),
            kind=str(metadata["kind"]),
            source=str(metadata["source"]),
            owner_user_id=str(metadata["owner_user_id"]),
            created_at=str(metadata["created_at"]),
            sha256=str(metadata["sha256"]),
        )

    def data_path(self, attachment: ChatAttachment) -> Path:
        path = self._attachment_dir(attachment.id) / attachment.storage_name
        if not path.exists() or not path.is_file():
            raise ChatAttachmentError("Attachment content is missing.")
        return path

    def read_bytes(self, file_id: str, *, owner_user_id: str) -> tuple[ChatAttachment, bytes]:
        attachment = self.get(file_id)
        self.require_access(attachment, owner_user_id)
        return attachment, self.data_path(attachment).read_bytes()

    def read_text(
        self,
        file_id: str,
        *,
        owner_user_id: str,
        max_chars: int = 12000,
    ) -> tuple[ChatAttachment, str]:
        attachment, content = self.read_bytes(file_id, owner_user_id=owner_user_id)
        if attachment.content_type in TEXT_CONTENT_TYPES:
            return attachment, _decode_text(content, max_chars=max_chars)
        if attachment.content_type == DOCX_CONTENT_TYPE:
            return attachment, _extract_docx_text(content, max_chars=max_chars)
        if attachment.content_type == "application/pdf":
            return attachment, _extract_pdf_text(content, max_chars=max_chars)
        raise ChatAttachmentError("This attachment is not a readable text document.")

    def require_access(self, attachment: ChatAttachment, owner_user_id: str) -> None:
        if attachment.owner_user_id != owner_user_id:
            raise ChatAttachmentError("Attachment not found.")

    def _read_metadata(self, file_id: str) -> dict[str, Any]:
        if not re.fullmatch(r"[a-f0-9]{32}", file_id):
            raise ChatAttachmentError("Attachment not found.")
        metadata_path = self._attachment_dir(file_id) / "metadata.json"
        if not metadata_path.exists():
            raise ChatAttachmentError("Attachment not found.")
        try:
            return json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ChatAttachmentError("Attachment metadata is unreadable.") from exc

    def _attachment_dir(self, file_id: str) -> Path:
        return self.root / file_id


def _safe_filename(filename: str) -> str:
    name = Path(filename).name.strip() or "attachment"
    name = re.sub(r"[^A-Za-z0-9._ -]+", "_", name)
    name = re.sub(r"\s+", " ", name).strip(" .")
    if not name:
        name = "attachment"
    stem = Path(name).stem[:90].strip(" .") or "attachment"
    suffix = Path(name).suffix.lower()[:16]
    return f"{stem}{suffix}"


def _normalize_content_type(content_type: str | None, filename: str) -> str:
    detected = (content_type or "").split(";", 1)[0].strip().lower()
    if detected in {"", "application/octet-stream"}:
        detected = (mimetypes.guess_type(filename)[0] or "").lower()
    if Path(filename).suffix.lower() == ".md":
        return "text/markdown"
    if Path(filename).suffix.lower() == ".docx":
        return DOCX_CONTENT_TYPE
    return detected or "application/octet-stream"


def _attachment_kind(content_type: str) -> str:
    if content_type in IMAGE_CONTENT_TYPES:
        return "image"
    if content_type in TEXT_CONTENT_TYPES:
        return "text"
    return "document"


def _decode_text(content: bytes, *, max_chars: int) -> str:
    for encoding in ("utf-8", "utf-16", "latin-1"):
        try:
            text = content.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise ChatAttachmentError("Could not decode the document text.")
    return _clamp_text(text, max_chars=max_chars)


def _extract_docx_text(content: bytes, *, max_chars: int) -> str:
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            document = archive.read("word/document.xml")
    except Exception as exc:
        raise ChatAttachmentError("Could not read the DOCX document text.") from exc

    try:
        root = ElementTree.fromstring(document)
    except ElementTree.ParseError as exc:
        raise ChatAttachmentError("Could not parse the DOCX document text.") from exc

    namespace = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    paragraphs: list[str] = []
    for paragraph in root.iter(f"{namespace}p"):
        parts = [node.text or "" for node in paragraph.iter(f"{namespace}t")]
        if parts:
            paragraphs.append("".join(parts))
    return _clamp_text("\n".join(paragraphs), max_chars=max_chars)


def _extract_pdf_text(content: bytes, *, max_chars: int) -> str:
    text = content.decode("latin-1", errors="ignore")
    candidates = re.findall(r"\(([^()]{1,400})\)", text)
    cleaned = "\n".join(
        unescape(item.replace("\\(", "(").replace("\\)", ")").replace("\\n", "\n"))
        for item in candidates
        if any(char.isalpha() for char in item)
    )
    if not cleaned.strip():
        raise ChatAttachmentError("PDF text extraction found no readable text.")
    return _clamp_text(cleaned, max_chars=max_chars)


def _clamp_text(text: str, *, max_chars: int) -> str:
    normalized = re.sub(r"\r\n?", "\n", text).strip()
    if len(normalized) <= max_chars:
        return normalized
    return f"{normalized[:max_chars].rstrip()}\n\n[truncated]"


chat_attachment_store = ChatAttachmentStore()
