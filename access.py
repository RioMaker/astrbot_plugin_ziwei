"""Group-only allowlist backed by AstrBot's plugin KV store."""

import asyncio
import hashlib
import json
from dataclasses import dataclass


def field(value, key, default=None):
    return (
        value.get(key, default)
        if isinstance(value, dict)
        else getattr(value, key, default)
    )


@dataclass(frozen=True)
class GroupScope:
    platform_id: str
    platform_name: str
    group_id: str
    user_id: str
    astrbot_admin: bool
    role: str
    bot: object
    umo: str = ""

    @property
    def key(self):
        if not self.platform_id or not self.group_id:
            raise ValueError("白名单需要平台实例和群标识")
        identity = json.dumps([self.platform_id, self.group_id], ensure_ascii=False)
        return (
            "group_enabled_v1_" + hashlib.sha256(identity.encode("utf-8")).hexdigest()
        )


def capture_group(event) -> GroupScope:
    """Capture routing and trusted adapter metadata before the first await."""
    raw = field(event.message_obj, "raw_message")
    sender = field(raw, "sender")
    return GroupScope(
        platform_id=str(event.get_platform_id() or "").strip(),
        platform_name=str(event.get_platform_name() or ""),
        group_id=str(event.get_group_id() or "").strip(),
        user_id=str(event.get_sender_id() or "").strip(),
        astrbot_admin=bool(event.is_admin()),
        role=str(field(sender, "role", "") or "").lower(),
        bot=getattr(event, "bot", None),
        umo=str(getattr(event, "unified_msg_origin", "") or ""),
    )


async def can_manage(scope: GroupScope) -> bool:
    if not scope.platform_id or not scope.group_id:
        return False
    if scope.astrbot_admin:
        return True
    # Only OneBot has the verified owner/admin role and member lookup below.
    if scope.platform_name != "aiocqhttp":
        return False
    if scope.role:
        return scope.role in {"owner", "admin"}
    lookup = getattr(scope.bot, "call_action", None)
    if (
        not callable(lookup)
        or not scope.group_id.isdecimal()
        or not scope.user_id.isdecimal()
    ):
        return False
    try:
        result = await asyncio.wait_for(
            lookup(
                action="get_group_member_info",
                group_id=int(scope.group_id),
                user_id=int(scope.user_id),
                no_cache=True,
            ),
            timeout=8,
        )
        if field(result, "status") == "failed" or field(result, "retcode", 0) != 0:
            return False
        member = field(result, "data", result)
        return field(member, "role", "") in {"owner", "admin"}
    except Exception:
        return False


class GroupAccess:
    def __init__(self, get, put, delete):
        self.get, self.put, self.delete = get, put, delete
        self.lock = asyncio.Lock()

    async def enabled(self, scope: GroupScope) -> bool:
        async with self.lock:
            # Missing or malformed state never authorizes use.
            return await self.get(scope.key, False) is True

    async def set_enabled(self, scope: GroupScope, enabled: bool):
        async with self.lock:
            if enabled:
                await self.put(scope.key, True)
            else:
                await self.delete(scope.key)
