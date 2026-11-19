"""Импорт/экспорт субтитров SRT, ASS/SSA, VTT с определением персонажей."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


@dataclass
class SubLine:
    start: float
    end: float
    text: str
    speaker: str = ""   # Actor/Name из ASS, <v> из VTT, префикс «Имя:» из SRT
    style: str = ""     # стиль ASS


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
