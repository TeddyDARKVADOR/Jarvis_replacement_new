"""
Developer / Debug — deliberately a separate window.

The brief asks for the technical surface to be somewhere other than the panel,
and that is the right instinct for a reason worth writing down: the panel is
furniture the user glances at while working, and every number added to it makes
the one thing it exists to show — *is JARVIS there, and is it listening* —
slightly harder to read. Counters belong where someone has gone looking for
them.

This window is also the only place the client can be *configured*, which is why
it is reachable from the panel and from the tray rather than hidden behind a
flag: on a machine that has never been paired, this is the first-run screen.

**The one number here that is not obvious.** `RX` and `PLAYED` are separate on
purpose, and the gap between them is the whole diagnostic: received climbing
while played stays flat means the socket is fine and the sound card is not,
which is a completely different fault from nothing arriving at all. Conflating
them hides exactly the case worth seeing.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ..audio import list_devices
from ..config import Settings, settings_path
from ..state import Snapshot
from .theme import HEX, PANEL_STYLESHEET


class DebugWindow(QWidget):
    """Numbers, logs, and the only way to pair this machine."""

    connect_requested = pyqtSignal()
    disconnect_requested = pyqtSignal()
    settings_changed = pyqtSignal()
    wake_word_install_requested = pyqtSignal()
    autostart_toggled = pyqtSignal(bool)

    def __init__(self, settings: Settings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._settings = settings
        self._log_lines = 0

        self.setWindowTitle("JARVIS — Developer / Debug")
        self.setStyleSheet(PANEL_STYLESHEET)
        self.resize(520, 680)

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(10)

        root.addWidget(self._build_connection())
        root.addWidget(self._build_counters())
        root.addWidget(self._build_audio())
        root.addWidget(self._build_log(), 1)

        path = QLabel(f"Réglages : {settings_path()}")
        path.setObjectName("hint")
        path.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        root.addWidget(path)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(250)
        self._snapshot = Snapshot()

    # ── sections ─────────────────────────────────────────────────────────────

    def _build_connection(self) -> QGroupBox:
        box = QGroupBox("Connexion")
        form = QFormLayout(box)
        form.setSpacing(6)

        self._host = QLineEdit(self._settings.host)
        self._host.setPlaceholderText("jarvis-vps.tailnet-XXXX.ts.net")
        form.addRow("Hôte", self._host)

        self._port = QSpinBox()
        self._port.setRange(1, 65535)
        self._port.setValue(self._settings.port)
        form.addRow("Port", self._port)

        self._token = QLineEdit(self._settings.device_token)
        self._token.setEchoMode(QLineEdit.EchoMode.Password)
        self._token.setPlaceholderText("python -m server.run_headless --pairing")
        form.addRow("Jeton", self._token)

        self._tls = QCheckBox("TLS (uniquement si config/certs/ est rempli)")
        self._tls.setChecked(self._settings.use_tls)
        form.addRow("", self._tls)

        self._autostart = QCheckBox("Démarrer JARVIS avec Windows")
        self._autostart.toggled.connect(self.autostart_toggled.emit)
        form.addRow("", self._autostart)

        row = QHBoxLayout()
        save = QPushButton("Enregistrer")
        save.clicked.connect(self._save)
        connect = QPushButton("Connecter")
        connect.setObjectName("confirm")
        connect.clicked.connect(self.connect_requested.emit)
        disconnect = QPushButton("Déconnecter")
        disconnect.setObjectName("danger")
        disconnect.clicked.connect(self.disconnect_requested.emit)
        row.addWidget(save)
        row.addWidget(connect)
        row.addWidget(disconnect)
        form.addRow("", self._wrap(row))

        self._link_label = QLabel("—")
        form.addRow("État", self._link_label)
        return box

    def _build_counters(self) -> QGroupBox:
        box = QGroupBox("TX / RX")
        grid = QGridLayout(box)
        grid.setSpacing(5)
        self._counters: dict[str, QLabel] = {}
        fields = [
            ("TX trames", "frames_sent"), ("TX octets", "bytes_sent"),
            ("RX trames", "frames_received"), ("RX octets", "bytes_received"),
            ("JOUÉ trames", "frames_played"), ("JOUÉ octets", "bytes_played"),
            ("Perdues", "frames_dropped"), ("Downlink Hz", "downlink_rate"),
            ("Tentative", "attempt"), ("Nouvel essai", "next_retry_seconds"),
        ]
        for index, (label, key) in enumerate(fields):
            row, column = divmod(index, 2)
            name = QLabel(label)
            name.setObjectName("hint")
            value = QLabel("0")
            value.setStyleSheet(f"color: {HEX['sky']};")
            grid.addWidget(name, row, column * 2)
            grid.addWidget(value, row, column * 2 + 1)
            self._counters[key] = value
        return box

    def _build_audio(self) -> QGroupBox:
        box = QGroupBox("Audio")
        layout = QVBoxLayout(box)
        layout.setSpacing(6)

        form = QFormLayout()
        inputs, outputs = list_devices()
        self._input_device = QComboBox()
        self._input_device.addItem("(défaut Windows)", None)
        for name in inputs:
            self._input_device.addItem(name, name)
        self._select(self._input_device, self._settings.input_device)
        form.addRow("Micro", self._input_device)

        self._output_device = QComboBox()
        self._output_device.addItem("(défaut Windows)", None)
        for name in outputs:
            self._output_device.addItem(name, name)
        self._select(self._output_device, self._settings.output_device)
        form.addRow("Sortie", self._output_device)
        layout.addLayout(form)

        self._mic_bar = self._meter("Micro")
        self._speaker_bar = self._meter("JARVIS")
        layout.addLayout(self._mic_bar[0])
        layout.addLayout(self._speaker_bar[0])

        wake_row = QHBoxLayout()
        self._wake_label = QLabel("Wake word : —")
        self._wake_label.setObjectName("hint")
        install = QPushButton("Installer le wake word")
        install.clicked.connect(self.wake_word_install_requested.emit)
        wake_row.addWidget(self._wake_label, 1)
        wake_row.addWidget(install)
        layout.addLayout(wake_row)
        return box

    def _meter(self, label: str) -> tuple[QHBoxLayout, QProgressBar]:
        row = QHBoxLayout()
        name = QLabel(label)
        name.setObjectName("hint")
        name.setFixedWidth(52)
        bar = QProgressBar()
        bar.setRange(0, 100)
        bar.setTextVisible(False)
        bar.setFixedHeight(8)
        bar.setStyleSheet(
            f"QProgressBar {{ background: {HEX['surface_variant']};"
            f" border: none; border-radius: 4px; }}"
            f"QProgressBar::chunk {{ background: {HEX['sky']}; border-radius: 4px; }}"
        )
        row.addWidget(name)
        row.addWidget(bar, 1)
        return row, bar

    def _build_log(self) -> QGroupBox:
        box = QGroupBox("Journal")
        layout = QVBoxLayout(box)
        self._log = QPlainTextEdit()
        self._log.setReadOnly(True)
        self._log.setMaximumBlockCount(400)
        self._log.setStyleSheet(
            f"font-family: Consolas, monospace; font-size: 11px;"
            f" color: {HEX['muted']};"
        )
        layout.addWidget(self._log)
        return box

    @staticmethod
    def _wrap(layout) -> QWidget:  # noqa: ANN001
        holder = QWidget()
        holder.setLayout(layout)
        return holder

    @staticmethod
    def _select(combo: QComboBox, value: str | None) -> None:
        index = combo.findData(value)
        combo.setCurrentIndex(index if index >= 0 else 0)

    # ── wiring ───────────────────────────────────────────────────────────────

    def set_autostart_checked(self, enabled: bool) -> None:
        self._autostart.blockSignals(True)
        self._autostart.setChecked(enabled)
        self._autostart.blockSignals(False)

    def _save(self) -> None:
        self._settings.host = self._host.text().strip()
        self._settings.port = self._port.value()
        self._settings.device_token = self._token.text().strip()
        self._settings.use_tls = self._tls.isChecked()
        self._settings.input_device = self._input_device.currentData()
        self._settings.output_device = self._output_device.currentData()
        self.settings_changed.emit()

    def apply(self, snapshot: Snapshot) -> None:
        self._snapshot = snapshot

    def _tick(self) -> None:
        snapshot = self._snapshot
        for key, label in self._counters.items():
            label.setText(f"{getattr(snapshot, key, 0):,}".replace(",", " "))

        self._link_label.setText(
            f'<span style="color:{HEX["sky"]}">{snapshot.display_state}</span>'
            + (f'  <span style="color:{HEX["error"]}">{snapshot.last_error}</span>'
               if snapshot.last_error else "")
        )
        self._mic_bar[1].setValue(int(snapshot.mic_level * 100))
        self._speaker_bar[1].setValue(int(snapshot.speaker_level * 100))

        name = snapshot.wake_word_name or "non installé"
        self._wake_label.setText(f"Wake word : {name}")

        if len(snapshot.log) != self._log_lines:
            new = snapshot.log[self._log_lines:]
            self._log_lines = len(snapshot.log)
            for line in new:
                self._log.appendPlainText(line)
