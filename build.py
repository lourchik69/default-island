"""Build Default Island: the program (PyInstaller) and its installer (Inno Setup).

    python build.py

Needs the packages from requirements.txt plus PyInstaller, and Inno Setup 6
(winget install JRSoftware.InnoSetup). The result is dist/DefaultIsland-Setup.exe.
"""
import os
import re
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def app_version():
    with open(os.path.join(HERE, "island_core.py"), encoding="utf-8") as handle:
        match = re.search(r'^APP_VERSION = "([^"]+)"', handle.read(), re.MULTILINE)
    if not match:
        raise SystemExit("APP_VERSION not found in island_core.py")
    return match.group(1)


def find_iscc():
    candidates = [
        shutil.which("ISCC"),
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Inno Setup 6", "ISCC.exe"),
        r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
        r"C:\Program Files\Inno Setup 6\ISCC.exe",
    ]
    for path in candidates:
        if path and os.path.isfile(path):
            return path
    raise SystemExit("Inno Setup 6 not found: winget install JRSoftware.InnoSetup")


def prepare_uia_wrappers():
    """Generate comtypes' UI Automation module, which the exe bundles (it
    answers calls by pressing the buttons of the call notification)."""
    import comtypes.client

    comtypes.client.GetModule("UIAutomationCore.dll")


def make_wizard_art():
    """The pictures of the installer: a tall one for the welcome page and a
    small one for the top of the other pages, drawn from scratch."""
    from PyQt6.QtCore import QRectF, Qt
    from PyQt6.QtGui import QColor, QFont, QGuiApplication, QImage, QLinearGradient, QPainter, QPen

    app = QGuiApplication.instance() or QGuiApplication(["build"])
    folder = os.path.join(HERE, "build", "art")
    os.makedirs(folder, exist_ok=True)

    large = QImage(328, 628, QImage.Format.Format_ARGB32)
    painter = QPainter(large)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    sky = QLinearGradient(0, 0, 328, 628)
    sky.setColorAt(0.0, QColor(36, 44, 104))
    sky.setColorAt(0.55, QColor(110, 64, 140))
    sky.setColorAt(1.0, QColor(232, 122, 86))
    painter.fillRect(large.rect(), sky)
    # The island itself, collapsed and open.
    painter.setPen(QPen(QColor(255, 255, 255, 60), 2))
    painter.setBrush(QColor(12, 12, 14))
    painter.drawRoundedRect(QRectF(74, 120, 180, 52), 26, 26)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(232, 69, 91))
    painter.drawRoundedRect(QRectF(86, 131, 30, 30), 8, 8)
    for index, height in enumerate((14, 24, 18, 28, 16)):
        painter.drawRoundedRect(QRectF(206 + index * 7, 146 - height / 2, 3.5, height), 1.7, 1.7)
    painter.setPen(QPen(QColor(255, 255, 255, 60), 2))
    painter.setBrush(QColor(12, 12, 14))
    painter.drawRoundedRect(QRectF(34, 210, 260, 150), 40, 40)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(232, 69, 91))
    painter.drawRoundedRect(QRectF(58, 234, 64, 64), 14, 14)
    painter.setBrush(QColor(255, 255, 255, 220))
    painter.drawRoundedRect(QRectF(136, 246, 120, 12), 6, 6)
    painter.setBrush(QColor(255, 255, 255, 110))
    painter.drawRoundedRect(QRectF(136, 270, 84, 10), 5, 5)
    painter.drawRoundedRect(QRectF(58, 318, 212, 6), 3, 3)
    painter.setBrush(QColor(255, 255, 255, 230))
    painter.drawRoundedRect(QRectF(58, 318, 90, 6), 3, 3)
    painter.setPen(QColor(255, 255, 255))
    font = QFont("Segoe UI", 30, QFont.Weight.Bold)
    painter.setFont(font)
    painter.drawText(QRectF(0, 440, 328, 50), Qt.AlignmentFlag.AlignCenter, "Default")
    painter.drawText(QRectF(0, 488, 328, 50), Qt.AlignmentFlag.AlignCenter, "Island")
    painter.end()
    large.save(os.path.join(folder, "wizard-large.png"))

    # A small island, collapsed: a dark capsule with artwork and bars.
    small = QImage(110, 110, QImage.Format.Format_ARGB32)
    small.fill(Qt.GlobalColor.transparent)
    painter = QPainter(small)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(QPen(QColor(255, 255, 255, 70), 2))
    painter.setBrush(QColor(14, 14, 16))
    painter.drawRoundedRect(QRectF(5, 33, 100, 44), 22, 22)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(232, 69, 91))
    painter.drawRoundedRect(QRectF(15, 42, 26, 26), 7, 7)
    for index, height in enumerate((12, 22, 16, 24)):
        painter.drawRoundedRect(QRectF(66 + index * 8, 55 - height / 2, 4, height), 2, 2)
    painter.end()
    small.save(os.path.join(folder, "wizard-small.png"))
    del app


def main():
    version = app_version()
    iscc = find_iscc()
    prepare_uia_wrappers()
    subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "DefaultIsland.spec"],
                   cwd=HERE, check=True)
    make_wizard_art()
    subprocess.run([iscc, "/Q", f"/DAppVersion={version}", "setup.iss"], cwd=HERE, check=True)
    shutil.rmtree(os.path.join(HERE, "build"), ignore_errors=True)
    print(f"Built Default Island {version}: dist\\DefaultIsland-Setup.exe")


if __name__ == "__main__":
    main()
