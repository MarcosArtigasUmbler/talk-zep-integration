"""Usuario do Zep = contato do Talk."""

from __future__ import annotations

import hashlib
import logging

from zep_cloud.core.api_error import ApiError

from app.talk.models import Contact
from app.talk.normalize import display_name, split_name
from app.zep.client import get_zep

log = logging.getLogger(__name__)


def contact_fingerprint(contact: Contact) -> str:
    raw = "|".join(
        (
            contact.name or "",
            contact.phone_number or "",
            contact.contact_type or "",
            ",".join(sorted(t.name or "" for t in contact.tags)),
        )
    )
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def _profile(contact: Contact) -> dict:
    first, last = split_name(display_name(contact))
    metadata = {
        "source": "umbler_talk",
        "talk_contact_id": contact.id or "",
        "phone": contact.phone_number or "",
        "contact_type": contact.contact_type or "",
    }
    tags = [t.name for t in contact.tags if t.name][:20]
    if tags:
        metadata["tags"] = tags
    return {"first_name": first, "last_name": last, "metadata": metadata}


async def ensure_user(user_id: str, contact: Contact, *, update: bool) -> str:
    """Cria o usuario; se ja existe e a ficha mudou, atualiza.

    Devolve ``created``, ``updated`` ou ``kept``.
    """
    zep = get_zep()
    profile = _profile(contact)
    try:
        await zep.user.add(user_id=user_id, **profile)
        log.info("zep: usuario criado %s", user_id)
        return "created"
    except ApiError as exc:
        if exc.status_code != 409:
            raise
    if update:
        await zep.user.update(user_id, **profile)
        log.info("zep: usuario atualizado %s", user_id)
        return "updated"
    return "kept"
