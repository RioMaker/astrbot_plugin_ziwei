"""Wake-aware fallback for command names glued to their arguments."""

from astrbot.api.event import filter

from .compact_commands import parse_compact_command


class CompactCommandFilter(filter.CustomFilter):
    def filter(self, event, cfg):
        # WakingCheck already removes the configured wake prefix. Do not strip
        # slash here or accept an unawakened message via a global regex listener.
        if not event.is_at_or_wake_command:
            return False
        text = event.get_message_str().strip()
        if text.startswith("/"):
            return False
        command = parse_compact_command(text)
        if command is None:
            return False
        remainder = text[len(command.command) :]
        # Whitespace-separated names belong exclusively to CommandFilter.
        return bool(remainder and not remainder[0].isspace() and command.compact)


def claim_command(event, quiet):
    # Every explicit command belongs to this plugin, including errors/help.
    # AstrBot's True flag disables its automatic default-model fallback.
    event.should_call_llm(True)
    event.stop_event()
