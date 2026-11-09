"""Draw the Voice2Text app icon (1024x1024 PNG) with QPainter."""

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QBrush,
    QColor,
    QGuiApplication,
    QImage,
    QLinearGradient,
    QPainter,
    QPen,
)

SIZE = 1024


def draw(image: QImage) -> None:
    p = QPainter(image)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)

    # Rounded square background with a vertical gradient.
    margin = 40
    rect = QRectF(margin, margin, SIZE - 2 * margin, SIZE - 2 * margin)
    grad = QLinearGradient(0, rect.top(), 0, rect.bottom())
    grad.setColorAt(0.0, QColor("#4f8cff"))
    grad.setColorAt(1.0, QColor("#2a4fc4"))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(grad))
    p.drawRoundedRect(rect, 220, 220)

    white = QColor("#ffffff")

    # Microphone: capsule body, holder arc, stem and base.
    p.setBrush(white)
    p.drawRoundedRect(QRectF(392, 190, 240, 360), 120, 120)
    pen = QPen(white, 38, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawArc(QRectF(332, 330, 360, 330), 180 * 16, 180 * 16)
    p.drawLine(QPointF(512, 660), QPointF(512, 725))
    p.drawLine(QPointF(430, 725), QPointF(594, 725))

    # Text lines below the microphone.
    pen = QPen(QColor(255, 255, 255, 230), 34, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
    p.setPen(pen)
    p.drawLine(QPointF(300, 805), QPointF(724, 805))
    p.drawLine(QPointF(360, 880), QPointF(664, 880))
    p.end()


def main() -> int:
    app = QGuiApplication.instance() or QGuiApplication(sys.argv[:1])
    image = QImage(SIZE, SIZE, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    draw(image)
    out = Path(__file__).with_name("icon.png")
    if not image.save(str(out), "PNG"):
        print(f"failed to write {out}", file=sys.stderr)
        return 1
    print(out)
    del app
    return 0


if __name__ == "__main__":
    sys.exit(main())
