import re

from voice2text.segments import Paragraph, Segment

_SENTENCE_END = (".", "?", "!", "…")


def _normalize(text: str) -> str:
    return re.sub(r"\W+", "", text.lower())


def drop_repeats(segments: list[Segment]) -> list[Segment]:
    """Drop empty segments and consecutive duplicates (Whisper hallucination loops)."""
    result: list[Segment] = []
    prev_norm = ""
    for seg in segments:
        norm = _normalize(seg.text)
        if not norm or norm == prev_norm:
            continue
        result.append(seg)
        prev_norm = norm
    return result


def build_paragraphs(
    segments: list[Segment], pause_s: float = 2.0, max_chars: int = 700
) -> list[Paragraph]:
    paragraphs: list[Paragraph] = []
    parts: list[str] = []
    speaker: int | None = None
    prev: Segment | None = None

    def flush() -> None:
        if parts:
            paragraphs.append(Paragraph(" ".join(parts), speaker))
            parts.clear()

    for seg in segments:
        text = seg.text.strip()
        if not text:
            continue
        if prev is not None:
            current = " ".join(parts)
            if (
                seg.start - prev.end >= pause_s
                or seg.speaker != speaker
                or (len(current) >= max_chars and current.endswith(_SENTENCE_END))
            ):
                flush()
        if not parts:
            speaker = seg.speaker
        parts.append(text)
        prev = seg
    flush()
    return paragraphs
