#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ГЭС-2 — Приложение для загрузки изображений на LED-панель 16×16.

Требования:
  - Python 3.9+
  - PyQt6
  - pyserial
  - Pillow
  - arduino-cli в PATH
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path
from typing import Optional

import serial
import serial.tools.list_ports
from PIL import Image, ImageOps
from PyQt6.QtCore import QSize, Qt, QThread, pyqtSignal, QMutex, QMutexLocker
from PyQt6.QtGui import QColor, QFont, QImage, QPainter, QPixmap, QPen
from PyQt6.QtWidgets import (
    QApplication, QFileDialog, QHBoxLayout, QLabel,
    QMainWindow, QMessageBox, QPushButton, QRadioButton,
    QTextEdit, QVBoxLayout, QWidget, QComboBox, QButtonGroup,
    QFrame, QGroupBox,
)

# ─────────────────── Константы ───────────────────────────────

GRID_SIZE  = 16
NUM_SLOTS  = 5
PREVIEW_PX = 160
CELL_PX    = PREVIEW_PX // GRID_SIZE   # 10 px на ячейку

FLASH_PY   = Path(__file__).resolve().parent / "LED_Control" / "src" / "flash.py"
IMAGES_H   = Path(__file__).resolve().parent / "LED_Control" / "src" / "images.h"

SERIAL_BAUD = 115200
_KEY_RE     = re.compile(r"key\s+0x([0-9A-Fa-f]{2})")

# ─────────────────── Вспомогательные функции ─────────────────

def image_to_pil_16x16(path: str, fit: str = "crop", gamma: float = 1.0) -> Image.Image:
    """Загружает картинку и масштабирует до 16×16."""
    img = Image.open(path)
    img = ImageOps.exif_transpose(img)
    img = img.convert("RGBA")
    base = Image.new("RGBA", img.size, (0, 0, 0, 255))
    img = Image.alpha_composite(base, img).convert("RGB")
    rs = Image.Resampling.LANCZOS
    if fit == "crop":
        img = ImageOps.fit(img, (GRID_SIZE, GRID_SIZE), method=rs)
    elif fit == "pad":
        img = ImageOps.pad(img, (GRID_SIZE, GRID_SIZE), method=rs, color=(0, 0, 0))
    else:
        img = img.resize((GRID_SIZE, GRID_SIZE), rs)
    if gamma != 1.0:
        lut = [round(255 * (i / 255) ** gamma) for i in range(256)]
        img = img.point(lut * 3)
    return img


def pil_to_qpixmap(img16: Image.Image, size: int = PREVIEW_PX) -> QPixmap:
    """PIL Image 16×16 → увеличенный QPixmap с сеткой."""
    cell = size // GRID_SIZE
    total = cell * GRID_SIZE
    px = img16.load()
    qimg = QImage(total, total, QImage.Format.Format_RGB888)
    painter = QPainter(qimg)
    for y in range(GRID_SIZE):
        for x in range(GRID_SIZE):
            r, g, b = px[x, y]
            painter.fillRect(x * cell, y * cell, cell, cell, QColor(r, g, b))
    pen = QPen(QColor(50, 50, 50))
    pen.setWidth(1)
    painter.setPen(pen)
    for i in range(GRID_SIZE + 1):
        painter.drawLine(i * cell, 0, i * cell, total)
        painter.drawLine(0, i * cell, total, i * cell)
    painter.end()
    return QPixmap.fromImage(qimg)


def make_black_slot(name: str) -> str:
    """Возвращает C-код чёрного 16×16 изображения."""
    rows = []
    for y in range(GRID_SIZE):
        row = ", ".join("{ 0, 0, 0}" for _ in range(GRID_SIZE))
        comma = "," if y < GRID_SIZE - 1 else ""
        rows.append(f"  {{ {row} }}{comma}  // y = {y}")
    return f"const Pixel {name}[{GRID_SIZE}][{GRID_SIZE}] PROGMEM = {{\n" + "\n".join(rows) + "\n};"


def pil_to_c_array(img: Image.Image, name: str) -> str:
    """PIL Image 16×16 → строка C-массива PROGMEM."""
    px = img.load()
    lines = [f"const Pixel {name}[{GRID_SIZE}][{GRID_SIZE}] PROGMEM = {{"]
    for y in range(GRID_SIZE):
        row = ", ".join("{{{:3d},{:3d},{:3d}}}".format(*px[x, y]) for x in range(GRID_SIZE))
        comma = "," if y < GRID_SIZE - 1 else ""
        lines.append(f"  {{ {row} }}{comma}  // y = {y}")
    lines.append("};")
    return "\n".join(lines)


def generate_images_h(slots: list[Optional[Image.Image]]) -> str:
    """Генерирует полное содержимое images.h для списка из 5 слотов."""
    lines = [
        "// Изображения для панели 16x16 WS2812B.",
        "// Сгенерировано автоматически приложением GES-2.",
        "// Формат пикселя: RGB, 3 байта. Все изображения размещаются во Flash через PROGMEM.",
        "// Тип Pixel должен быть объявлен до подключения images.h.",
        "#ifndef IMAGES_H",
        "#define IMAGES_H",
        "",
    ]
    for i, img in enumerate(slots, start=1):
        name = f"img{i}"
        if img is not None:
            lines.append(f"// Изображение {i}")
            lines.append(pil_to_c_array(img, name))
        else:
            lines.append(f"// Изображение {i} — чёрный шаблон.")
            lines.append(make_black_slot(name))
        lines.append("")
    lines.append("#endif // IMAGES_H")
    return "\n".join(lines)


# ─────────────────── Поток чтения Serial ─────────────────────

class SerialReaderThread(QThread):
    """Читает Serial-порт в фоне, парсит ключ и испускает сигналы."""
    key_received  = pyqtSignal(int)   # значение ключа 0-255
    line_received = pyqtSignal(str)   # сырая строка

    def __init__(self, port: str, baud: int = SERIAL_BAUD):
        super().__init__()
        self._port    = port
        self._baud    = baud
        self._running = True
        self._mutex   = QMutex()

    def stop(self):
        with QMutexLocker(self._mutex):
            self._running = False

    def run(self):
        try:
            ser = serial.Serial(self._port, self._baud, timeout=1)
        except serial.SerialException as e:
            self.line_received.emit(f"[Serial] Ошибка открытия {self._port}: {e}")
            return
        self.line_received.emit(f"[Serial] Подключено к {self._port} @ {self._baud}")
        while True:
            with QMutexLocker(self._mutex):
                if not self._running:
                    break
            try:
                raw = ser.readline()
                if not raw:
                    continue
                line = raw.decode("utf-8", errors="replace").strip()
                if line:
                    self.line_received.emit(line)
                    m = _KEY_RE.search(line)
                    if m:
                        self.key_received.emit(int(m.group(1), 16))
            except serial.SerialException as e:
                self.line_received.emit(f"[Serial] Потеря соединения: {e}")
                break
            except Exception as e:
                self.line_received.emit(f"[Serial] Ошибка: {e}")
                break
        try:
            ser.close()
        except Exception:
            pass
        self.line_received.emit(f"[Serial] Порт {self._port} закрыт.")


# ─────────────────── Поток загрузки прошивки ─────────────────

class FlashThread(QThread):
    """Запускает flash.py как subprocess, транслирует вывод в сигналы."""
    log_line = pyqtSignal(str)
    finished = pyqtSignal(bool, str)   # (success, message)

    def __init__(self, port: str, new_bootloader: bool):
        super().__init__()
        self._port           = port
        self._new_bootloader = new_bootloader

    def run(self):
        cmd = [sys.executable, str(FLASH_PY),
               "--port", self._port, "--no-install"]
        if self._new_bootloader:
            cmd.append("--new-bootloader")
        self.log_line.emit("> " + " ".join(cmd))
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace",
            )
            for line in proc.stdout:
                self.log_line.emit(line.rstrip())
            proc.wait()
            if proc.returncode == 0:
                self.finished.emit(True,  "✅ Прошивка успешно загружена на плату!")
            else:
                self.finished.emit(False, f"❌ flash.py завершился с кодом {proc.returncode}.")
        except FileNotFoundError:
            self.finished.emit(False, f"❌ Не найден Python или flash.py:\n{FLASH_PY}")
        except Exception as e:
            self.finished.emit(False, f"❌ Ошибка запуска: {e}")


# ─────────────────── Виджет одного слота ─────────────────────

class ImageSlot(QFrame):
    """Один слот изображения: кликабельный квадрат с превью.
    ЛКМ — открыть файл, ПКМ — очистить."""
    image_changed = pyqtSignal(int)   # номер слота 0-based

    def __init__(self, slot_index: int, parent=None):
        super().__init__(parent)
        self._index    = slot_index
        self._img_pil  : Optional[Image.Image] = None
        self._img_path : Optional[str]         = None

        self.setFixedSize(PREVIEW_PX + 2, PREVIEW_PX + 38)
        self.setFrameShape(QFrame.Shape.Box)
        self.setFrameShadow(QFrame.Shadow.Raised)
        self.setStyleSheet(
            "ImageSlot { border:2px solid #555; border-radius:6px; background:#1e1e1e; }"
            "ImageSlot:hover { border:2px solid #0af; }"
        )
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(1, 1, 1, 1)
        layout.setSpacing(2)

        self._preview = QLabel()
        self._preview.setFixedSize(PREVIEW_PX, PREVIEW_PX)
        self._preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._preview.setStyleSheet("border:none;")
        layout.addWidget(self._preview)

        self._label = QLabel(f"Слот {slot_index + 1}\n[нажмите]")
        self._label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._label.setStyleSheet("color:#888; font-size:10px; border:none;")
        self._label.setWordWrap(True)
        layout.addWidget(self._label)

        self._draw_empty()

    # ── публичный интерфейс ──────────────────────────────────

    @property
    def pil_image(self) -> Optional[Image.Image]:
        return self._img_pil

    @property
    def image_path(self) -> Optional[str]:
        return self._img_path

    def clear_slot(self):
        self._img_pil  = None
        self._img_path = None
        self._draw_empty()
        self._label.setText(f"Слот {self._index + 1}\n[нажмите]")
        self._label.setStyleSheet("color:#888; font-size:10px; border:none;")
        self.image_changed.emit(self._index)

    # ── события ─────────────────────────────────────────────

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._open_file()
        elif event.button() == Qt.MouseButton.RightButton:
            self.clear_slot()

    def _open_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            f"Выбрать изображение для слота {self._index + 1}",
            "",
            "Изображения (*.png *.jpg *.jpeg *.bmp *.gif *.webp *.tiff);;Все файлы (*)"
        )
        if not path:
            return
        try:
            img = image_to_pil_16x16(path)
        except Exception as e:
            QMessageBox.critical(self, "Ошибка", f"Не удалось открыть изображение:\n{e}")
            return
        self._img_pil  = img
        self._img_path = path
        self._preview.setPixmap(pil_to_qpixmap(img, PREVIEW_PX))
        self._label.setText(Path(path).name)
        self._label.setStyleSheet("color:#ccc; font-size:9px; border:none;")
        self.image_changed.emit(self._index)

    # ── рисование пустого слота ──────────────────────────────

    def _draw_empty(self):
        qimg = QImage(PREVIEW_PX, PREVIEW_PX, QImage.Format.Format_RGB888)
        qimg.fill(QColor(30, 30, 30))
        painter = QPainter(qimg)
        cell = PREVIEW_PX // GRID_SIZE
        pen = QPen(QColor(55, 55, 55))
        pen.setWidth(1)
        painter.setPen(pen)
        for i in range(GRID_SIZE + 1):
            painter.drawLine(i * cell, 0, i * cell, PREVIEW_PX)
            painter.drawLine(0, i * cell, PREVIEW_PX, i * cell)
        painter.setPen(QPen(QColor(80, 80, 80)))
        font = QFont()
        font.setPointSize(28)
        painter.setFont(font)
        painter.drawText(qimg.rect(), Qt.AlignmentFlag.AlignCenter, "+")
        painter.end()
        self._preview.setPixmap(QPixmap.fromImage(qimg))


# ──────────────────── Виджет индикатора ключа ────────────────

class KeyDisplay(QWidget):
    """Показывает 8-битный ключ: HEX и визуальные биты (D2..D9)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        self._hex_label = QLabel("—")
        self._hex_label.setStyleSheet(
            "color:#0df; font-size:18px; font-family:monospace; font-weight:bold;"
        )
        layout.addWidget(self._hex_label)

        self._dec_label = QLabel("")
        self._dec_label.setStyleSheet("color:#888; font-size:10px; font-family:monospace;")
        layout.addWidget(self._dec_label)

        # Биты — 8 цветных квадратиков
        bits_row = QHBoxLayout()
        bits_row.setSpacing(3)
        self._bit_labels: list[QLabel] = []
        for _ in range(8):
            b = QLabel("0")
            b.setFixedSize(22, 22)
            b.setAlignment(Qt.AlignmentFlag.AlignCenter)
            b.setStyleSheet(self._bit_style(False))
            self._bit_labels.append(b)
            bits_row.addWidget(b)
        bits_row.addStretch()
        layout.addLayout(bits_row)

        # Подпись пинов
        pin_row = QHBoxLayout()
        pin_row.setSpacing(3)
        for i in range(7, -1, -1):
            lbl = QLabel(f"D{i+2}")
            lbl.setFixedWidth(22)
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl.setStyleSheet("color:#555; font-size:7px;")
            pin_row.addWidget(lbl)
        pin_row.addStretch()
        layout.addLayout(pin_row)

    @staticmethod
    def _bit_style(active: bool) -> str:
        if active:
            return ("background:#0af; color:#000; border-radius:3px; "
                    "font-weight:bold; font-family:monospace; font-size:11px;")
        return ("background:#2a2a2a; color:#555; border-radius:3px; "
                "font-family:monospace; font-size:11px;")

    def set_key(self, key: int):
        self._hex_label.setText(f"0x{key:02X}  0b{key:08b}")
        self._dec_label.setText(f"DEC: {key}")
        for i, lbl in enumerate(self._bit_labels):
            bit_pos = 7 - i
            active  = bool((key >> bit_pos) & 1)
            lbl.setText("1" if active else "0")
            lbl.setStyleSheet(self._bit_style(active))

    def clear(self):
        self._hex_label.setText("—")
        self._dec_label.setText("")
        for lbl in self._bit_labels:
            lbl.setText("0")
            lbl.setStyleSheet(self._bit_style(False))


# ─────────────────────── Главное окно ────────────────────────

class MainWindow(QMainWindow):

    def __init__(self):
        super().__init__()
        self.setWindowTitle("ГЭС-2 — Загрузчик LED-панели 16×16")
        self.setMinimumSize(QSize(1000, 640))
        self.setStyleSheet(self._app_style())

        self._serial_thread: Optional[SerialReaderThread] = None
        self._flash_thread:  Optional[FlashThread]        = None

        central = QWidget()
        self.setCentralWidget(central)
        root = QHBoxLayout(central)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(12)
        root.addWidget(self._build_left_panel(),  stretch=0)
        root.addWidget(self._build_right_panel(), stretch=1)

        self._refresh_ports()

    # ═══════════ Левая панель ════════════════════════════════

    def _build_left_panel(self) -> QWidget:
        panel = QWidget()
        panel.setFixedWidth(240)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)

        # Порт
        port_box = QGroupBox("Порт Arduino")
        port_box.setStyleSheet(self._groupbox_style())
        pl = QVBoxLayout(port_box)
        pl.setSpacing(6)
        self._port_combo = QComboBox()
        self._port_combo.setStyleSheet(self._combo_style())
        pl.addWidget(self._port_combo)
        btn_refresh = QPushButton("↺  Обновить список")
        btn_refresh.setStyleSheet(self._btn_style("#333", "#555"))
        btn_refresh.clicked.connect(self._refresh_ports)
        pl.addWidget(btn_refresh)
        self._btn_serial = QPushButton("▶  Слушать Serial")
        self._btn_serial.setCheckable(True)
        self._btn_serial.setStyleSheet(self._btn_style("#1a4a1a", "#2a7a2a"))
        self._btn_serial.toggled.connect(self._toggle_serial)
        pl.addWidget(self._btn_serial)
        layout.addWidget(port_box)

        # Загрузчик
        boot_box = QGroupBox("Загрузчик")
        boot_box.setStyleSheet(self._groupbox_style())
        bl = QVBoxLayout(boot_box)
        bl.setSpacing(4)
        self._rb_old = QRadioButton("Старый  (ATmega328P Old)")
        self._rb_new = QRadioButton("Новый   (ATmega328P)")
        self._rb_old.setChecked(True)
        for rb in (self._rb_old, self._rb_new):
            rb.setStyleSheet("color:#ccc; font-size:12px;")
            bl.addWidget(rb)
        self._boot_group = QButtonGroup(self)
        self._boot_group.addButton(self._rb_old, 0)
        self._boot_group.addButton(self._rb_new, 1)
        layout.addWidget(boot_box)

        # Ключ
        key_box = QGroupBox("Ключ шифрования")
        key_box.setStyleSheet(self._groupbox_style())
        kl = QVBoxLayout(key_box)
        self._key_display = KeyDisplay()
        kl.addWidget(self._key_display)
        layout.addWidget(key_box)

        layout.addStretch()

        # Кнопка загрузки
        self._btn_flash = QPushButton("⚡  Загрузить на плату")
        self._btn_flash.setFixedHeight(50)
        self._btn_flash.setStyleSheet(
            self._btn_style("#7a1a00", "#cc2200", bold=True, font_size=14))
        self._btn_flash.clicked.connect(self._on_flash_clicked)
        layout.addWidget(self._btn_flash)

        return panel

    # ═══════════ Правая панель ═══════════════════════════════

    def _build_right_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        slots_box = QGroupBox(
            "Изображения  (ЛКМ — выбрать файл,  ПКМ — очистить слот)"
        )
        slots_box.setStyleSheet(self._groupbox_style())
        sl = QHBoxLayout(slots_box)
        sl.setSpacing(8)
        self._slots: list[ImageSlot] = []
        for i in range(NUM_SLOTS):
            slot = ImageSlot(i)
            slot.image_changed.connect(self._on_image_changed)
            self._slots.append(slot)
            sl.addWidget(slot)
        sl.addStretch()
        layout.addWidget(slots_box)

        hint = QLabel("💡 Незаполненные слоты — чёрные (пустые) на панели.")
        hint.setStyleSheet("color:#666; font-size:11px;")
        layout.addWidget(hint)

        log_box = QGroupBox("Лог")
        log_box.setStyleSheet(self._groupbox_style())
        ll = QVBoxLayout(log_box)
        self._log = QTextEdit()
        self._log.setReadOnly(True)
        self._log.setStyleSheet(
            "background:#111; color:#0f0; "
            "font-family:Consolas,monospace; font-size:11px; border:none;"
        )
        self._log.setMinimumHeight(220)
        ll.addWidget(self._log)
        btn_clear = QPushButton("Очистить лог")
        btn_clear.setFixedWidth(130)
        btn_clear.setStyleSheet(self._btn_style("#2a2a2a", "#444"))
        btn_clear.clicked.connect(self._log.clear)
        ll.addWidget(btn_clear, alignment=Qt.AlignmentFlag.AlignRight)
        layout.addWidget(log_box, stretch=1)
        return panel

    # ═══════════ Порты ═══════════════════════════════════════

    def _refresh_ports(self):
        self._port_combo.clear()
        ports = serial.tools.list_ports.comports()
        if not ports:
            self._port_combo.addItem("— нет портов —")
            self._log_msg("[Порты] COM-порты не обнаружены.")
        else:
            for p in ports:
                desc = p.device
                if p.description and p.description != p.device:
                    desc += f"  ({p.description})"
                self._port_combo.addItem(desc, userData=p.device)
            self._log_msg(f"[Порты] Найдено: {len(ports)}")

    def _selected_port(self) -> Optional[str]:
        idx = self._port_combo.currentIndex()
        if idx < 0:
            return None
        return self._port_combo.itemData(idx)

    # ═══════════ Serial ══════════════════════════════════════

    def _toggle_serial(self, checked: bool):
        if checked:
            port = self._selected_port()
            if not port:
                self._btn_serial.setChecked(False)
                QMessageBox.warning(self, "Нет порта",
                                    "Выберите COM-порт перед подключением.")
                return
            self._start_serial(port)
            self._btn_serial.setText("⏹  Остановить Serial")
        else:
            self._stop_serial()
            self._btn_serial.setText("▶  Слушать Serial")

    def _start_serial(self, port: str):
        self._stop_serial()
        self._serial_thread = SerialReaderThread(port, SERIAL_BAUD)
        self._serial_thread.line_received.connect(self._log_msg)
        self._serial_thread.key_received.connect(self._key_display.set_key)
        self._serial_thread.start()

    def _stop_serial(self):
        if self._serial_thread and self._serial_thread.isRunning():
            self._serial_thread.stop()
            self._serial_thread.wait(2000)
        self._serial_thread = None

    # ═══════════ Слоты изображений ═══════════════════════════

    def _on_image_changed(self, slot_index: int):
        slot = self._slots[slot_index]
        if slot.pil_image:
            self._log_msg(f"[Слот {slot_index + 1}] {slot.image_path}")
        else:
            self._log_msg(f"[Слот {slot_index + 1}] Очищен.")

    # ═══════════ Загрузка прошивки ═══════════════════════════

    def _on_flash_clicked(self):
        port = self._selected_port()
        if not port:
            QMessageBox.warning(self, "Нет порта",
                                "Выберите COM-порт перед загрузкой.")
            return
        if self._flash_thread and self._flash_thread.isRunning():
            QMessageBox.information(self, "Загрузка идёт",
                                    "Дождитесь завершения текущей загрузки.")
            return
        if self._btn_serial.isChecked():
            self._btn_serial.setChecked(False)
        try:
            self._write_images_h()
        except Exception as e:
            QMessageBox.critical(self, "Ошибка генерации images.h", str(e))
            return
        new_boot = self._rb_new.isChecked()
        self._log_msg(
            f"\n[Загрузка] Порт: {port}  |  "
            f"Загрузчик: {'Новый' if new_boot else 'Старый'}"
        )
        self._btn_flash.setEnabled(False)
        self._btn_flash.setText("⏳  Загрузка...")
        self._flash_thread = FlashThread(port, new_boot)
        self._flash_thread.log_line.connect(self._log_msg)
        self._flash_thread.finished.connect(self._on_flash_finished)
        self._flash_thread.start()

    def _write_images_h(self):
        images_pil = [s.pil_image for s in self._slots]
        content    = generate_images_h(images_pil)
        IMAGES_H.write_text(content, encoding="utf-8")
        filled = sum(1 for img in images_pil if img is not None)
        self._log_msg(f"[images.h] Записано {filled} изображений → {IMAGES_H}")

    def _on_flash_finished(self, success: bool, message: str):
        self._log_msg(f"\n{message}\n")
        self._btn_flash.setEnabled(True)
        self._btn_flash.setText("⚡  Загрузить на плату")
        if success:
            QMessageBox.information(self, "Готово", message)
        else:
            QMessageBox.critical(self, "Ошибка загрузки", message)

    # ═══════════ Лог, закрытие ═══════════════════════════════

    def _log_msg(self, text: str):
        self._log.append(text)
        sb = self._log.verticalScrollBar()
        sb.setValue(sb.maximum())

    def closeEvent(self, event):
        self._stop_serial()
        if self._flash_thread and self._flash_thread.isRunning():
            self._flash_thread.wait(3000)
        event.accept()

    # ═══════════ Стили ═══════════════════════════════════════

    @staticmethod
    def _app_style() -> str:
        return """
            QMainWindow, QWidget { background:#1a1a1a; color:#ddd; }
            QComboBox QAbstractItemView {
                background:#2a2a2a; color:#ddd;
                selection-background-color:#0af;
            }
            QScrollBar:vertical { background:#1a1a1a; width:8px; }
            QScrollBar::handle:vertical { background:#444; border-radius:4px; }
        """

    @staticmethod
    def _groupbox_style() -> str:
        return """
            QGroupBox {
                border:1px solid #3a3a3a; border-radius:6px;
                margin-top:10px; padding:6px; background:#222;
                font-size:12px; font-weight:bold; color:#888;
            }
            QGroupBox::title {
                subcontrol-origin:margin; left:10px; padding:0 4px;
            }
        """

    @staticmethod
    def _combo_style() -> str:
        return """
            QComboBox {
                background:#2a2a2a; color:#ddd; border:1px solid #555;
                border-radius:4px; padding:4px 8px; font-size:12px;
            }
            QComboBox::drop-down { border:none; width:20px; }
        """

    @staticmethod
    def _btn_style(bg: str, bg_hover: str,
                   bold: bool = False, font_size: int = 12) -> str:
        w = "bold" if bold else "normal"
        return f"""
            QPushButton {{
                background:{bg}; color:#ddd; border:1px solid #555;
                border-radius:5px; padding:6px 10px;
                font-size:{font_size}px; font-weight:{w};
            }}
            QPushButton:hover {{ background:{bg_hover}; border-color:#888; }}
            QPushButton:pressed {{ background:#111; }}
            QPushButton:disabled {{ background:#222; color:#555; border-color:#333; }}
            QPushButton:checked {{ background:{bg_hover}; border-color:#0af; }}
        """


# ─────────────────────── Точка входа ─────────────────────────

def main():
    app = QApplication(sys.argv)
    app.setApplicationName("GES-2 LED Loader")
    if not FLASH_PY.exists():
        QMessageBox.critical(
            None, "Ошибка конфигурации",
            f"Не найден flash.py:\n{FLASH_PY}\n\n"
            "Убедитесь, что структура проекта не нарушена."
        )
        sys.exit(1)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
