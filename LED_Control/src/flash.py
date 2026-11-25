#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Сборка и загрузка прошивки на Arduino Nano (ATmega328P).

Требования:
  - Python 3.9+
  - Arduino CLI в PATH
  - main.cpp и images.h рядом с этим скриптом

Примеры:
  python flash.py
  python flash.py --port COM5
  python flash.py --port COM5 --new-bootloader
  python flash.py --list-ports
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent
SOURCE_CPP = ROOT / "main.cpp"
IMAGES_H = ROOT / "images.h"

DEFAULT_FQBN = "arduino:avr:nano"
DEFAULT_BOARD_OPTIONS = "cpu=atmega328old"


class FlashError(RuntimeError):
    pass


def run(command: list[str], *, capture: bool = False) -> subprocess.CompletedProcess:
    print("\n>", subprocess.list2cmdline(command))
    try:
        return subprocess.run(
            command,
            check=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE if capture else None,
            stderr=subprocess.STDOUT if capture else None,
        )
    except FileNotFoundError as exc:
        raise FlashError(
            "Не найден Arduino CLI. Установите Arduino CLI и добавьте arduino-cli "
            "в переменную PATH."
        ) from exc
    except subprocess.CalledProcessError as exc:
        output = exc.stdout or ""
        if output:
            print(output)
        raise FlashError(f"Команда завершилась с ошибкой (код {exc.returncode}).") from exc


def check_files() -> None:
    missing = [str(p) for p in (SOURCE_CPP, IMAGES_H) if not p.is_file()]
    if missing:
        raise FlashError(
            "Не найдены файлы проекта:\n  " + "\n  ".join(missing)
            + "\nПоложите flash.py, main.cpp и images.h в одну папку."
        )


def ensure_core_and_library() -> None:
    # Установка выполняется только если соответствующий пакет ещё не установлен.
    cores = run(["arduino-cli", "core", "list"], capture=True).stdout
    if "arduino:avr" not in cores:
        print("\nУстанавливаю платформу Arduino AVR...")
        run(["arduino-cli", "core", "update-index"])
        run(["arduino-cli", "core", "install", "arduino:avr"])

    libraries = run(["arduino-cli", "lib", "list"], capture=True).stdout
    if "FastLED" not in libraries:
        print("\nУстанавливаю библиотеку FastLED...")
        run(["arduino-cli", "lib", "install", "FastLED"])


def list_ports() -> list[dict]:
    result = run(
        ["arduino-cli", "board", "list", "--format", "json"],
        capture=True,
    )
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise FlashError("Не удалось разобрать список COM-портов от Arduino CLI.") from exc

    ports = []
    for detected in data.get("detected_ports", []):
        port_info = detected.get("port", {})
        address = port_info.get("address")
        if address:
            ports.append({
                "address": address,
                "label": port_info.get("label", ""),
                "protocol": port_info.get("protocol", ""),
            })
    return ports


def choose_port(requested: str | None) -> str:
    ports = list_ports()

    if requested:
        return requested

    if len(ports) == 1:
        selected = ports[0]["address"]
        print(f"\nНайден единственный порт: {selected}")
        return selected

    if not ports:
        raise FlashError(
            "Arduino не обнаружена. Подключите плату USB-кабелем и проверьте драйвер "
            "USB-UART (CH340/CP210x), затем запустите скрипт снова."
        )

    print("\nНайдено несколько последовательных портов:")
    for i, item in enumerate(ports, 1):
        extra = f" — {item['label']}" if item["label"] else ""
        print(f"  {i}. {item['address']}{extra}")

    answer = input("Введите номер нужного порта или его имя (например, COM5): ").strip()
    if answer.isdigit() and 1 <= int(answer) <= len(ports):
        return ports[int(answer) - 1]["address"]
    if answer:
        return answer
    raise FlashError("Порт не выбран.")


def make_sketch(work_dir: Path) -> Path:
    # Arduino CLI ожидает папку скетча с .ino-файлом, имя которого совпадает
    # с именем папки. Исходный main.cpp переносим в .ino без изменения кода.
    sketch_dir = work_dir / "Firmware"
    sketch_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(SOURCE_CPP, sketch_dir / "Firmware.ino")
    shutil.copy2(IMAGES_H, sketch_dir / "images.h")
    return sketch_dir


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Собрать прошивку и загрузить её на Arduino Nano."
    )
    parser.add_argument("--port", help="COM-порт платы, например COM5. По умолчанию определяется автоматически.")
    parser.add_argument(
        "--new-bootloader",
        action="store_true",
        help="Использовать ATmega328P (новый загрузчик); по умолчанию ATmega328P Old Bootloader.",
    )
    parser.add_argument(
        "--fqbn",
        default=DEFAULT_FQBN,
        help=f"Идентификатор платы (по умолчанию {DEFAULT_FQBN}).",
    )
    parser.add_argument(
        "--list-ports",
        action="store_true",
        help="Показать доступные последовательные порты и завершить работу.",
    )
    parser.add_argument(
        "--no-install",
        action="store_true",
        help="Не устанавливать автоматически Arduino AVR core и FastLED.",
    )
    args = parser.parse_args()

    check_files()

    # Проверка наличия Arduino CLI до любых дальнейших действий.
    run(["arduino-cli", "version"], capture=True)

    if args.list_ports:
        ports = list_ports()
        if not ports:
            print("Последовательные порты не обнаружены.")
        for item in ports:
            print(f"{item['address']}  {item['label']}")
        return 0

    port = choose_port(args.port)
    board_options = "cpu=atmega328" if args.new_bootloader else DEFAULT_BOARD_OPTIONS
    fqbn = f"{args.fqbn}:{board_options}" if args.fqbn == DEFAULT_FQBN else args.fqbn

    if not args.no_install:
        ensure_core_and_library()

    with tempfile.TemporaryDirectory(prefix="arduino_nano_build_") as tmp:
        work_dir = Path(tmp)
        sketch_dir = make_sketch(work_dir)
        build_dir = work_dir / "build"
        build_dir.mkdir()

        print("\n=== СБОРКА ===")
        run([
            "arduino-cli", "compile",
            "--fqbn", fqbn,
            "--output-dir", str(build_dir),
            str(sketch_dir),
        ])

        print("\n=== ЗАГРУЗКА ===")
        run([
            "arduino-cli", "upload",
            "--fqbn", fqbn,
            "--port", port,
            "--input-dir", str(build_dir),
            str(sketch_dir),
        ])

    print("\nГотово: прошивка успешно собрана и отправлена на плату.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except FlashError as error:
        print(f"\nОШИБКА: {error}", file=sys.stderr)
        raise SystemExit(1)
    except KeyboardInterrupt:
        print("\nОперация прервана пользователем.", file=sys.stderr)
        raise SystemExit(130)
