"""Split explicit compact command boundaries without validating birth data."""

import re
from dataclasses import dataclass
from typing import Literal

from .personal_commands import split_personal_command

_NATAL_COMMANDS = ("紫微排盘", "紫薇排盘", "紫微", "紫薇", "ziwei")
_COMMANDS = (*_NATAL_COMMANDS, "人生", "我的命盘")
_COMMAND_PREFIX = re.compile(r"\A(?P<command>" + "|".join(_COMMANDS) + r")")
_MANAGEMENT = {"开启", "开", "关闭", "关", "状态"}
_HELP = {"帮助", "help", "?", "？"}
_CALENDARS = r"(?:公历|阳历|农历|阴历|solar|lunar)"
_OPTION = re.compile(
    r"(?:真太阳时|输出|经度|时区|闰月|子时|流年|流盘|流时|主题|运限|大限|大运|命盘)="
)
_JOINED_DATE_CLOCK = re.compile(
    rf"^(?:{_CALENDARS}\s+)?"
    r"(?:[0-9]{4}[-/](?:闰)?[0-9]{2}[-/][0-9]{2}|[0-9]{8})"
    r"(?P<clock>[0-9]{2}:[0-9]{2}(?::[0-9]{2})?)(?=$|\s|[男女])"
)
# This lexical shape deliberately includes malformed numeric date/time strings.
# Splitting only the sex boundary marks compact failures without guessing a date.
_BIRTH_AND_SEX = re.compile(
    rf"^(?:{_CALENDARS}\s+)?"
    r"[0-9][0-9:/\-\s闰子丑寅卯辰巳午未申酉戌亥时]*(?P<sex>[男女])"
)
_DISPLAY = r"(?:图片|文字|image|text)"
_NUMBER_AND_DISPLAY = re.compile(rf"\A[0-9]+(?P<display>{_DISPLAY})(?=\Z|\s)")
_OPTION_AND_DISPLAY = re.compile(
    rf"(?:\A|\s)(?:主题=(?:白天|夜间|day|night)|输出={_DISPLAY})"
    rf"(?P<display>{_DISPLAY})(?=\Z|\s)"
)


@dataclass(frozen=True)
class NormalizedArguments:
    args: str
    changed: bool = False


@dataclass(frozen=True)
class CompactCommand:
    command: str
    target: Literal["natal", "atlas", "manage", "help", "personal"]
    args: str
    compact: bool = False


def normalize_help_arguments(text: str) -> str | None:
    """Match whole help requests, including an omitted text-mode separator."""
    value = text.strip().lower()
    if value in _HELP:
        return value
    if re.fullmatch(r"帮助\s*文字", value):
        return "帮助 文字"
    if re.fullmatch(r"help\s*text", value):
        return "help text"
    return None


def _unquoted(text: str) -> str:
    """Keep positions while hiding quoted/escaped contents from boundary scans."""
    masked = list(text)
    quote = ""
    escaped = False
    for index, character in enumerate(text):
        if escaped:
            masked[index] = "\0"
            escaped = False
        elif character == "\\" and quote != "'":
            masked[index] = "\0"
            escaped = True
        elif quote:
            masked[index] = "\0"
            if character == quote:
                quote = ""
        elif character in {"'", '"'}:
            quote = character
            masked[index] = "\0"
    return "".join(masked)


def _split_at(text: str, positions: set[int]) -> str:
    return "".join(
        (" " if index in positions else "") + char for index, char in enumerate(text)
    )


def normalize_compact_arguments(text: str) -> NormalizedArguments:
    """Add spaces only at explicit calendar, sex, option and display boundaries.

    Invalid dates and unknown suffixes remain present for the strict command
    parser to reject. Trimming outer whitespace alone is not compact syntax.
    This function neither strips a command/subcommand nor fills missing data.
    """
    original = text.strip()
    normalized = original
    masked = _unquoted(normalized)
    calendar = re.match(rf"^{_CALENDARS}(?=[0-9])", masked)
    if calendar:
        normalized = _split_at(normalized, {calendar.end()})

    joined_clock = _JOINED_DATE_CLOCK.match(_unquoted(normalized))
    if joined_clock:
        normalized = _split_at(normalized, {joined_clock.start("clock")})

    masked = _unquoted(normalized)
    positions = {
        match.start()
        for match in _OPTION.finditer(masked)
        if match.start() and not normalized[match.start() - 1].isspace()
    }
    normalized = _split_at(normalized, positions)

    birth = _BIRTH_AND_SEX.match(_unquoted(normalized))
    if birth:
        positions = set()
        if not normalized[birth.start("sex") - 1].isspace():
            positions.add(birth.start("sex"))
        # Bare image/text selectors have an explicit token boundary only here.
        suffix = normalized[birth.end() :]
        if re.match(r"(?:图片|文字|image|text)(?=$|\s)", suffix):
            positions.add(birth.end())
        normalized = _split_at(normalized, positions)
    # Bound-chart years/decade numbers and known option values may be followed
    # directly by a display selector. Keep unknown suffixes and quoted data intact.
    masked = _unquoted(normalized)
    positions = {
        match.start("display")
        for pattern in (_NUMBER_AND_DISPLAY, _OPTION_AND_DISPLAY)
        for match in pattern.finditer(masked)
    }
    normalized = _split_at(normalized, positions)
    return NormalizedArguments(normalized, normalized != original)


def _explicit_argument_start(text: str) -> bool:
    return bool(
        text[:1] in "0123456789"
        and text
        or re.match(rf"^{_CALENDARS}(?=[0-9]|\s)", text)
        or _OPTION.match(text)
    )


def _explicit_atlas_subcommand(text: str) -> bool:
    if not text.startswith("图谱"):
        return False
    tail = text[len("图谱") :]
    return (
        not tail
        or tail[0].isspace()
        or _explicit_argument_start(tail)
        or normalize_help_arguments(tail) is not None
    )


def parse_compact_command(text: str) -> CompactCommand | None:
    """Recognize a whole command, optionally after the framework removes `/`.

    The caller must independently require its real event's command/wake state
    and group authorization. Ordinary words such as 人生感悟 or 紫薇花 do not
    establish a command boundary. Atlas help keeps target='atlas' so the caller
    can use the corresponding help text.
    """
    value = text.strip().removeprefix("/")
    match = _COMMAND_PREFIX.match(value)
    if match is None:
        return None
    command = match["command"]
    remainder = value[len(command) :]
    compact = bool(remainder and not remainder[0].isspace())
    if compact and not (
        _explicit_argument_start(remainder)
        or normalize_help_arguments(remainder) is not None
        or command in _NATAL_COMMANDS
        and (
            remainder in _MANAGEMENT
            or _explicit_atlas_subcommand(remainder)
            or split_personal_command(remainder) is not None
        )
        or command == "我的命盘"
        and split_personal_command(value) is not None
    ):
        return None
    args = remainder.strip()
    if command == "我的命盘":
        return CompactCommand(command, "personal", "我的命盘 " + args, compact)
    target = "atlas" if command == "人生" else "natal"
    if target == "natal" and args.startswith("图谱"):
        tail = args[len("图谱") :]
        compact |= bool(tail and not tail[0].isspace())
        args = tail.strip()
        target = "atlas"
    elif target == "natal" and args in _MANAGEMENT:
        target = "manage"
    elif target == "natal" and (not args or normalize_help_arguments(args) is not None):
        target = "help"
    normalized = normalize_compact_arguments(args)
    return CompactCommand(
        command, target, normalized.args, compact or normalized.changed
    )
