from dataclasses import dataclass


@dataclass(frozen=True)
class Segment:
    start: float
    end: float
    text: str
    speaker: int | None = None


@dataclass(frozen=True)
class Paragraph:
    text: str
    speaker: int | None
