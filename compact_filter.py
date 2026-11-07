"""Wake-aware fallback for compact commands and real leading bot mentions."""

from astrbot.api.event import filter
from astrbot.api.message_components import At, Plain, Reply

from .compact_commands import parse_compact_command


def _mentioned_text(event):
    """Read only plain text following real leading @self components.

    Never infer a mention from an @ nickname, a CQ string or quoted contents.
    Do not change the event used by authorization, archiving or other plugins.
    """
    get_messages = getattr(event, "get_messages", None)
    get_self_id = getattr(event, "get_self_id", None)
    if not callable(get_messages) or not callable(get_self_id):
        return None
    self_id = get_self_id()
    if self_id is None or not str(self_id):
        return None
    mentioned, started = False, False
    parts = []
    for component in get_messages():
        if isinstance(component, Reply) and not mentioned and not started:
            continue
        if isinstance(component, At) and not started:
            if str(component.qq) != str(self_id):
                return None
            mentioned = True
        elif isinstance(component, Plain):
            if not started and not component.text.strip():
                continue
            if not mentioned:
                return None
            started = True
            parts.append(component.text)
        else:
            return None
    return "".join(parts).strip() if mentioned else None


def fallback_command(event):
    if not event.is_at_or_wake_command:
        return None
    text = event.get_message_str().strip()
    if not text.startswith("/"):
        command = parse_compact_command(text)
        if command is not None:
            remainder = text[len(command.command) :]
            # A standard name is owned by CommandFilter, even when @ woke it.
            if not remainder or remainder[0].isspace():
                return None
            return command if command.compact else None
    mentioned = _mentioned_text(event)
    if mentioned is None:
        return None
    # @self supplies the wake condition; allow one optional slash after it.
    return parse_compact_command(mentioned)


class CompactCommandFilter(filter.CustomFilter):
    def filter(self, event, cfg):
        return fallback_command(event) is not None


def claim_command(event, quiet):
    # Every explicit command belongs to this plugin, including errors/help.
    # AstrBot's True flag disables its automatic default-model fallback.
    event.should_call_llm(True)
    event.stop_event()
