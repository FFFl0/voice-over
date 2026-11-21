"""Импорт/экспорт субтитров SRT, ASS/SSA, VTT с определением персонажей."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class SubLine:
    start: float
    end: float
    text: str
    speaker: str = ""   # Actor/Name из ASS, <v> из VTT, префикс «Имя:» из SRT
    style: str = ""     # стиль ASS
    words: list = field(default_factory=list)   # [[начало, конец, слово]] от Whisper


def read_text_file(path: str | Path) -> str:
    raw = Path(path).read_bytes()
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return raw.decode("utf-16")
    for enc in ("utf-8-sig", "cp1251"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


# ---------------------------------------------------------------- время

_TS_RE = re.compile(r"(?:(\d+):)?(\d{1,2}):(\d{1,2})[.,](\d{1,3})")


def parse_ts(s: str) -> float:
    m = _TS_RE.search(s.strip())
    if not m:
        raise ValueError(f"Неверная метка времени: {s!r}")
    h, mi, se, frac = m.groups()
    frac_v = int(frac) / (10 ** len(frac))
    return int(h or 0) * 3600 + int(mi) * 60 + int(se) + frac_v


def fmt_ts(t: float, sep: str = ",", ms_digits: int = 3) -> str:
    t = max(0.0, t)
    total_ms = int(round(t * 1000))
    h, rem = divmod(total_ms, 3600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    if ms_digits == 2:
        return f"{h}:{m:02d}:{s:02d}{sep}{ms // 10:02d}"
    return f"{h:02d}:{m:02d}:{s:02d}{sep}{ms:03d}"


def fmt_clock(t: float) -> str:
    """Короткий формат для интерфейса: Ч:ММ:СС.мс"""
    t = max(0.0, t)
    h, rem = divmod(t, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{int(h)}:{int(m):02d}:{s:06.3f}"
    return f"{int(m):02d}:{s:06.3f}"


# ---------------------------------------------------------------- очистка текста

_ASS_TAG_RE = re.compile(r"\{[^}]*\}")
_HTML_TAG_RE = re.compile(r"</?[a-zA-Z][^>]*>")
_SPEAKER_RE = re.compile(
    r"^\s*(?:\[(?P<b>[^\]\n]{1,40})\]|(?P<c>[A-ZА-ЯЁ0-9][\w .'\-]{0,30}?))\s*:\s+(?P<rest>.+)$",
    re.S,
)
_BRACKET_SPEAKER_RE = re.compile(r"^\s*\[(?P<b>[^\]\n]{1,40})\]\s*(?P<rest>.+)$", re.S)


def clean_ass_text(text: str) -> str:
    text = _ASS_TAG_RE.sub("", text)
    text = text.replace("\\N", " ").replace("\\n", " ").replace("\\h", " ")
    return re.sub(r"\s+", " ", text).strip()


def clean_html_text(text: str) -> str:
    text = _HTML_TAG_RE.sub("", text)
    text = _ASS_TAG_RE.sub("", text)
    return re.sub(r"\s+", " ", text.replace("\n", " ")).strip()


def split_speaker(text: str) -> tuple[str, str]:
    """«Имя: реплика» или «[Имя] реплика» -> (Имя, реплика)."""
    m = _SPEAKER_RE.match(text)
    if m:
        name = (m.group("b") or m.group("c") or "").strip()
        return name, m.group("rest").strip()
    m = _BRACKET_SPEAKER_RE.match(text)
    if m:
        return m.group("b").strip(), m.group("rest").strip()
    return "", text


# ---------------------------------------------------------------- SRT

def parse_srt(content: str, detect_speakers: bool = True) -> list[SubLine]:
    out: list[SubLine] = []
    blocks = re.split(r"\r?\n\s*\r?\n", content.replace("\r\n", "\n").strip())
    for block in blocks:
        rows = [r for r in block.split("\n")]
        idx = next((i for i, r in enumerate(rows) if "-->" in r), None)
        if idx is None:
            continue
        a, b = rows[idx].split("-->", 1)
        try:
            start, end = parse_ts(a), parse_ts(b)
        except ValueError:
            continue
        text = clean_html_text("\n".join(rows[idx + 1:]))
        if not text:
            continue
        speaker = ""
        if detect_speakers:
            speaker, text = split_speaker(text)
        out.append(SubLine(start, end, text, speaker))
    return out


# ---------------------------------------------------------------- VTT

_VTT_VOICE_RE = re.compile(r"<v(?:\.[^\s>]*)?\s+([^>]+)>")


def parse_vtt(content: str, detect_speakers: bool = True) -> list[SubLine]:
    out: list[SubLine] = []
    blocks = re.split(r"\n\s*\n", content.replace("\r\n", "\n").strip())
    for block in blocks:
        rows = block.split("\n")
        idx = next((i for i, r in enumerate(rows) if "-->" in r), None)
        if idx is None:
            continue
        a, rest = rows[idx].split("-->", 1)
        b = rest.strip().split()[0] if rest.strip() else ""
        try:
            start, end = parse_ts(a), parse_ts(b)
        except ValueError:
            continue
        raw = "\n".join(rows[idx + 1:])
        speaker = ""
        m = _VTT_VOICE_RE.search(raw)
        if m:
            speaker = m.group(1).strip()
        text = clean_html_text(raw)
        if not text:
            continue
        if not speaker and detect_speakers:
            speaker, text = split_speaker(text)
        out.append(SubLine(start, end, text, speaker))
    return out


# ---------------------------------------------------------------- ASS/SSA

def parse_ass(content: str, speaker_field: str = "auto") -> list[SubLine]:
    """speaker_field: 'name' (поле Actor/Name), 'style', 'auto' (Name, иначе Style)."""
    out: list[SubLine] = []
    fmt: list[str] = []
    in_events = False
    for raw in content.replace("\r\n", "\n").split("\n"):
        line = raw.strip()
        if line.startswith("["):
            in_events = line.lower() == "[events]"
            continue
        if not in_events:
            continue
        if line.lower().startswith("format:"):
            fmt = [f.strip().lower() for f in line.split(":", 1)[1].split(",")]
            continue
        if not line.lower().startswith("dialogue:"):
            continue  # Comment: и прочее пропускаем
        if not fmt:
            fmt = ["layer", "start", "end", "style", "name", "marginl", "marginr",
                   "marginv", "effect", "text"]
        parts = line.split(":", 1)[1].split(",", len(fmt) - 1)
        if len(parts) < len(fmt):
            continue
        rec = {k: v.strip() if k != "text" else v for k, v in zip(fmt, parts)}
        raw_text = rec.get("text", "")
        if re.search(r"\\p[1-9]", raw_text):
            continue  # векторная графика
        text = clean_ass_text(raw_text)
        if not text:
            continue
        try:
            start, end = parse_ts(rec.get("start", "")), parse_ts(rec.get("end", ""))
        except ValueError:
            continue
        name = rec.get("name", rec.get("actor", ""))
        style = rec.get("style", "")
        if speaker_field == "name":
            speaker = name
        elif speaker_field == "style":
            speaker = style
        else:
            speaker = name
        out.append(SubLine(start, end, text, speaker, style))

    if speaker_field == "auto" and out and not any(s.speaker for s in out):
        styles = {s.style for s in out}
        if len(styles) > 1:  # стили используются как роли
            for s in out:
                s.speaker = s.style
    return out


# ---------------------------------------------------------------- общий вход

def _drop_rare_speakers(lines: list[SubLine], min_count: int = 2) -> None:
    """Имя из префикса «Имя:» считаем персонажем, только если оно встречается
    несколько раз — иначе это, скорее всего, часть фразы («Слушай: ...»)."""
    counts: dict[str, int] = {}
    for s in lines:
        if s.speaker:
            counts[s.speaker.lower()] = counts.get(s.speaker.lower(), 0) + 1
    for s in lines:
        if s.speaker and counts[s.speaker.lower()] < min_count:
            s.text = f"{s.speaker}: {s.text}"
            s.speaker = ""


def load_subtitles(path: str | Path, detect_speakers: bool = True,
                   ass_speaker_field: str = "auto") -> list[SubLine]:
    p = Path(path)
    content = read_text_file(p)
    ext = p.suffix.lower()
    if ext in (".ass", ".ssa"):
        lines = parse_ass(content, ass_speaker_field)
    elif ext == ".vtt":
        lines = parse_vtt(content, detect_speakers)
    else:
        lines = parse_srt(content, detect_speakers)
        _drop_rare_speakers(lines)
    lines.sort(key=lambda s: (s.start, s.end))
    return lines


def write_srt(path: str | Path, items: list[tuple[float, float, str, str]],
              with_speaker: bool = True) -> None:
    """items: (start, end, text, speaker)."""
    chunks = []
    for i, (s, e, text, spk) in enumerate(items, 1):
        t = f"{spk}: {text}" if with_speaker and spk else text
        chunks.append(f"{i}\n{fmt_ts(s)} --> {fmt_ts(e)}\n{t}\n")
    Path(path).write_text("\n".join(chunks), encoding="utf-8")


# ---------------------------------------------------------------- экспорт для релиза

def _ass_escape(text: str) -> str:
    """Фигурные скобки в ASS — теги; переводы строк — \\N."""
    return (text.replace("\\", "\\\\").replace("{", "(").replace("}", ")")
            .replace("\r", "").replace("\n", "\\N"))


def _ass_color(hex_rgb: str) -> str:
    """#RRGGBB → &H00BBGGRR (порядок ASS)."""
    h = hex_rgb.lstrip("#")
    if len(h) != 6:
        return "&H00FFFFFF"
    return f"&H00{h[4:6]}{h[2:4]}{h[0:2]}".upper()


def write_ass(path: str | Path, items: list[tuple], title: str = "",
              color_by_speaker: bool = False, play_res: tuple[int, int] = (1920, 1080)) -> None:
    """items: (start, end, text, speaker, color). Персонаж пишется в поле Name;
    при color_by_speaker текст реплики окрашивается цветом персонажа."""
    w, h = play_res
    head = f"""[Script Info]
; VoiceOver Studio
Title: {title or "Субтитры"}
ScriptType: v4.00+
WrapStyle: 0
ScaledBorderAndShadow: yes
PlayResX: {w}
PlayResY: {h}

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,{int(h * 0.055)},&H00FFFFFF,&H000000FF,&H00000000,&H64000000,0,0,0,0,100,100,0,0,1,{max(2, h // 400)},1,2,{w // 20},{w // 20},{int(h * 0.05)},204
Style: Original,Arial,{int(h * 0.04)},&H00D0D0D0,&H000000FF,&H00000000,&H64000000,0,1,0,0,100,100,0,0,1,{max(2, h // 500)},1,8,{w // 20},{w // 20},{int(h * 0.04)},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    rows = []
    for it in items:
        s, e, text, spk = it[:4]
        color = it[4] if len(it) > 4 else ""
        style = it[5] if len(it) > 5 else "Default"
        body = _ass_escape(text)
        if color_by_speaker and color and style == "Default":
            body = "{\\c" + _ass_color(color).replace("&H00", "&H") + "&}" + body
        name = _ass_escape(spk).replace(",", " ")
        rows.append(f"Dialogue: 0,{fmt_ts(s, '.', 2)},{fmt_ts(e, '.', 2)},{style},{name},0,0,0,,{body}")
    Path(path).write_text(head + "\n".join(rows) + "\n", encoding="utf-8-sig")


def build_subtitle_items(lines, character_of, text_mode: str = "text",
                         with_speaker: bool = True, only_enabled: bool = True) -> list[tuple]:
    """Реплики проекта → строки субтитров.
    text_mode: «text» — текст озвучки (перевод), «orig» — оригинал, «both» — перевод и оригинал."""
    out = []
    for ln in sorted(lines, key=lambda x: x.start):
        if only_enabled and not ln.enabled:
            continue
        ch = character_of(ln.character_id)
        spk = ch.name if (ch and with_speaker) else ""
        color = ch.color if ch else ""
        orig = ln.orig_text or ln.text
        if text_mode == "orig":
            text = orig
        elif text_mode == "both" and ln.orig_text and ln.orig_text != ln.text:
            text = f"{ln.text}\n{ln.orig_text}"
        else:
            text = ln.text
        if text.strip():
            out.append((ln.start, ln.end, text.strip(), spk, color))
    return out
