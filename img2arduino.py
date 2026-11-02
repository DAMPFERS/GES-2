#!/usr/bin/env python3
"""
Преобразует картинку в массив 16x16 для прошивки Arduino.

Установка:   pip install pillow
Примеры:
    python img2arduino.py cat.jpg
    python img2arduino.py sprite.png --resample nearest --preview check.png
    python img2arduino.py photo.jpg --fit pad --bg 000000 --gamma 2.2

Вывод скрипта (от "const Pixel" до "};") скопируйте в прошивку
вместо старого массива img.
"""
import argparse
import sys

from PIL import Image, ImageOps

RESAMPLE = {
    "nearest": Image.Resampling.NEAREST,   # для пиксель-арта
    "box": Image.Resampling.BOX,           # усреднение, без "звона"
    "lanczos": Image.Resampling.LANCZOS,   # для фотографий
}


def parse_color(s: str):
    s = s.lstrip("#")
    if len(s) != 6:
        raise argparse.ArgumentTypeError("цвет в виде RRGGBB, например 000000")
    return tuple(int(s[i:i + 2], 16) for i in (0, 2, 4))


def load_image(path, size, fit, resample, bg):
    img = Image.open(path)
    img = ImageOps.exif_transpose(img)          # учитываем поворот с телефона

    # прозрачность -> заливаем фоном
    img = img.convert("RGBA")
    base = Image.new("RGBA", img.size, bg + (255,))
    img = Image.alpha_composite(base, img).convert("RGB")

    rs = RESAMPLE[resample]
    if fit == "crop":       # обрезать до квадрата по центру
        img = ImageOps.fit(img, (size, size), method=rs)
    elif fit == "pad":      # вписать целиком, поля залить фоном
        img = ImageOps.pad(img, (size, size), method=rs, color=bg)
    else:                   # растянуть
        img = img.resize((size, size), rs)
    return img


def apply_gamma(img, gamma):
    if gamma == 1.0:
        return img
    lut = [round(255 * (i / 255) ** gamma) for i in range(256)]
    return img.point(lut * 3)


def to_c_array(img, name):
    w, h = img.size
    px = img.load()
    lines = [f"const Pixel {name}[{h}][{w}] PROGMEM = {{"]
    for y in range(h):
        row = ", ".join("{{{:3d},{:3d},{:3d}}}".format(*px[x, y]) for x in range(w))
        comma = "," if y < h - 1 else ""
        lines.append(f"  {{ {row} }}{comma}  // y = {y}")
    lines.append("};")
    return "\n".join(lines)


def main():
    p = argparse.ArgumentParser(description="Картинка -> массив 16x16 для Arduino")
    p.add_argument("image", help="путь к картинке (png, jpg, bmp, gif...)")
    p.add_argument("--size", type=int, default=16, help="размер стороны (по умолчанию 16)")
    p.add_argument("--fit", choices=["crop", "pad", "stretch"], default="crop",
                   help="crop — обрезать до квадрата, pad — вписать с полями, stretch — растянуть")
    p.add_argument("--resample", choices=RESAMPLE.keys(), default="lanczos",
                   help="nearest для пиксель-арта, lanczos для фото")
    p.add_argument("--bg", type=parse_color, default=(0, 0, 0),
                   help="цвет фона для прозрачности и полей, RRGGBB")
    p.add_argument("--gamma", type=float, default=1.0,
                   help="гамма-коррекция (2.2 — тёмные тона на панели будут ближе к экрану)")
    p.add_argument("--preview", help="сохранить увеличенное превью, например preview.png")
    p.add_argument("--name", default="img", help="имя массива в коде")
    a = p.parse_args()

    try:
        img = load_image(a.image, a.size, a.fit, a.resample, a.bg)
    except (OSError, ValueError) as e:
        sys.exit(f"Не удалось открыть картинку: {e}")

    if a.preview:
        img.resize((a.size * 20, a.size * 20), Image.Resampling.NEAREST).save(a.preview)
        print(f"// превью сохранено в {a.preview}", file=sys.stderr)

    print(to_c_array(apply_gamma(img, a.gamma), a.name))


if __name__ == "__main__":
    main()
