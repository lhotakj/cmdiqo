"""Terminal formatter translating Slack mrkdwn to ANSI styled console output.

Converts:
    - *bold*            -> ANSI bold (\033[1m...\033[0m)
    - _italics_         -> ANSI italic (\033[3m...\033[0m)
    - ~strike~          -> ANSI strikethrough (\033[9m...\033[0m)
    - `inline code`     -> ANSI cyan (\033[96m...\033[0m)
    - ```code block```  -> Light gray framed text block (\033[37m)
    - :emoji:           -> Unicode emoji glyphs (✅, ⏳, 💻, etc.)
"""

from __future__ import annotations

import re

ANSI_RESET = "\033[0m"
ANSI_BOLD = "\033[1m"
ANSI_DIM = "\033[2m"
ANSI_ITALIC = "\033[3m"
ANSI_UNDERLINE = "\033[4m"
ANSI_STRIKE = "\033[9m"

# Text Colors
ANSI_LIGHT_GRAY = "\033[37m"
ANSI_DARK_GRAY = "\033[90m"
ANSI_CYAN = "\033[96m"
ANSI_GREEN = "\033[92m"
ANSI_YELLOW = "\033[93m"
ANSI_RED = "\033[91m"
ANSI_BLUE = "\033[94m"
ANSI_MAGENTA = "\033[95m"

# Common Slack emoji shortcodes to Unicode mappings
SLACK_EMOJI_MAP: dict[str, str] = {
    ":white_check_mark:": "✅",
    ":hourglass_flowing_sand:": "⏳",
    ":hourglass:": "⌛",
    ":satellite_antenna:": "📡",
    ":computer:": "💻",
    ":desktop_computer:": "🖥️",
    ":bar_chart:": "📊",
    ":whale:": "🐳",
    ":gear:": "⚙️",
    ":book:": "📖",
    ":no_entry_sign:": "🚫",
    ":question:": "❓",
    ":information_source:": "ℹ️",
    ":warning:": "⚠️",
    ":star:": "⭐",
    ":sparkles:": "✨",
    ":rocket:": "🚀",
    ":zap:": "⚡",
    ":wrench:": "🔧",
    ":package:": "📦",
    ":robot_face:": "🤖",
    ":fire:": "🔥",
    ":lock:": "🔒",
    ":unlock:": "🔓",
    ":shield:": "🛡️",
    ":stopwatch:": "⏱️",
    ":link:": "🔗",
    ":white_circle:": "⚪",
    ":black_circle:": "⚫",
    ":heavy_check_mark:": "✔",
    ":x:": "❌",
    ":memo:": "📝",
}


def replace_emojis(text: str) -> str:
    """Replace Slack emoji shortcodes like :white_check_mark: with unicode emojis."""
    for code, glyph in SLACK_EMOJI_MAP.items():
        text = text.replace(code, glyph)
    return text


def _render_code_block(match: re.Match[str], use_color: bool = True) -> str:
    """Format fenced code block with light gray text and subtle border."""
    content = match.group(1).rstrip()
    if not use_color:
        return f"\n---\n{content}\n---\n"

    lines = content.split("\n")
    # Clean zero-width spaces added by reply_output
    lines = [line.replace("\u200b```", "```") for line in lines]

    border_color = ANSI_DARK_GRAY
    text_color = ANSI_LIGHT_GRAY
    reset = ANSI_RESET

    rendered_lines = [f"{border_color}│{reset} {text_color}{line}{reset}" for line in lines]
    top_border = f"{border_color}┌{'─' * 60}┐{reset}"
    bottom_border = f"{border_color}└{'─' * 60}┘{reset}"

    return "\n" + top_border + "\n" + "\n".join(rendered_lines) + "\n" + bottom_border + "\n"


def format_slack_to_ansi(text: str, use_color: bool = True) -> str:
    """Translate Slack mrkdwn to terminal ANSI styled text.

    Args:
        text: Raw Slack mrkdwn string.
        use_color: If True, include ANSI color and style escapes.

    Returns:
        Formatted string for terminal display.
    """
    if not text:
        return ""

    # 1. First replace emojis
    result = replace_emojis(text)

    # 2. Extract and preserve code blocks (```...```) before other inline formatting
    code_blocks: list[str] = []

    def save_block(m: re.Match[str]) -> str:
        idx = len(code_blocks)
        rendered = _render_code_block(m, use_color=use_color)
        code_blocks.append(rendered)
        return f"__SLACK_CODE_BLOCK_{idx}__"

    result = re.sub(r"```(.*?)```", save_block, result, flags=re.DOTALL)

    # 3. Extract and preserve inline code (`...`)
    inline_codes: list[str] = []

    def save_inline(m: re.Match[str]) -> str:
        idx = len(inline_codes)
        raw_code = m.group(1)
        if use_color:
            formatted = f"{ANSI_CYAN}{raw_code}{ANSI_RESET}"
        else:
            formatted = f"`{raw_code}`"
        inline_codes.append(formatted)
        return f"__SLACK_INLINE_CODE_{idx}__"

    result = re.sub(r"`([^`]+)`", save_inline, result)

    if use_color:
        # 4. Bold: *bold* -> ANSI Bold
        # Match *text* ensuring it doesn't cross newlines inappropriately
        result = re.sub(r"(?<!\w)\*([^*\n]+)\*(?!\w)", rf"{ANSI_BOLD}\1{ANSI_RESET}", result)

        # 5. Italics: _italic_ -> ANSI Italic
        result = re.sub(r"(?<!\w)_([^_ \n][^_\n]*[^_ \n]|[^_ \n])_(?!\w)", rf"{ANSI_ITALIC}\1{ANSI_RESET}", result)

        # 6. Strikethrough: ~strike~ -> ANSI Strikethrough
        result = re.sub(r"(?<!\w)~([^~\n]+)~(?!\w)", rf"{ANSI_STRIKE}\1{ANSI_RESET}", result)

        # 7. Lists & bullet points
        result = re.sub(r"^[ \t]*•[ \t]*", f"  {ANSI_GREEN}•{ANSI_RESET} ", result, flags=re.MULTILINE)
    else:
        # Strip Slack styling characters when colors disabled
        result = re.sub(r"(?<!\w)\*([^*\n]+)\*(?!\w)", r"\1", result)
        result = re.sub(r"(?<!\w)_([^_ \n][^_\n]*[^_ \n]|[^_ \n])_(?!\w)", r"\1", result)
        result = re.sub(r"(?<!\w)~([^~\n]+)~(?!\w)", r"\1", result)

    # 8. Restore inline code
    for idx, code_str in enumerate(inline_codes):
        result = result.replace(f"__SLACK_INLINE_CODE_{idx}__", code_str)

    # 9. Restore code blocks
    for idx, block_str in enumerate(code_blocks):
        result = result.replace(f"__SLACK_CODE_BLOCK_{idx}__", block_str)

    return result
