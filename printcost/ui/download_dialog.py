import time

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QProgressBar, QProgressDialog

MB = 1048576


def _duration(seconds):
    seconds = max(1, round(seconds))
    if seconds < 60:
        return f"{seconds} s"
    minutes, seconds = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes} min {seconds} s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours} h {minutes} min"


class DownloadDialog(QProgressDialog):
    def __init__(self, parent, title, text):
        super().__init__(text, "Cancel", 0, 100, parent)
        bar = QProgressBar(self)
        bar.setTextVisible(False)
        self.setBar(bar)
        self.setRange(0, 100)
        self.setWindowTitle(title)
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.setMinimumDuration(0)
        self.setAutoClose(False)
        self.setAutoReset(False)
        self.setFixedSize(460, 150)
        self._text = text
        self._samples = []
        self.setValue(0)

    def update_progress(self, done, total):
        now = time.monotonic()
        self._samples = [s for s in self._samples if now - s[0] <= 4] + [(now, done)]
        t0, d0 = self._samples[0]
        speed = (done - d0) / (now - t0) if now - t0 > 0.3 else 0
        if total:
            self.setValue(int(done * 100 / total))
        lines = [f"{done / MB:.1f} of {total / MB:.1f} MB" if total else f"{done / MB:.1f} MB"]
        if speed > 0:
            lines[0] += f" · {speed / MB:.1f} MB/s"
            if total:
                lines.append(f"About {_duration((total - done) / speed)} left")
        self.setLabelText(self._text + "\n\n" + "\n".join(lines))