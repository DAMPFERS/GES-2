# GES-2 — Контекст проекта для агента

> Этот файл — краткая документация для AI-агента. Читай его первым делом перед любой задачей по проекту.

---

## Суть проекта

**Мастер-класс для школьников** — собрать устройство визуализации XOR-шифрования.  
Школьник выбирает картинку, загружает её на Arduino Nano, которая выводит изображение на светодиодную панель 16×16 и в реальном времени шифрует/расшифровывает XOR-ом, пока ученик переключает физические тумблеры ключа.

---

## Структура репозитория

```
GES-2/                          ← корень репо (git@github.com:DAMPFERS/GES-2.git)
│
├── AGENT_CONTEXT.md            ← этот файл
├── README.md                   ← инструкция по переносу arduino-cli на другой ПК
├── img2arduino.py              ← CLI: картинка → C-массив 16×16 для прошивки
├── app.py                      ← GUI-приложение (PyQt6). Запуск: python app.py
│
└── LED_Control/                ← PlatformIO-проект (Arduino Nano)
    ├── platformio.ini          ← board: nanoatmega328new, monitor_speed: 115200
    └── src/
        ├── main.cpp            ← прошивка (не трогать без причины)
        ├── images.h            ← 5 изображений PROGMEM (перезаписывается app.py)
        ├── flash.py            ← CLI-загрузчик через arduino-cli
        └── test.cpp            ← не используется в сборке
```

---

## Железо

| Параметр | Значение |
|---|---|
| Плата | Arduino Nano (ATmega328P) |
| Панель | WS2812B, 16×16 = 256 светодиодов |
| DATA\_PIN | 11, GRB, 5V, до 4000 mA |
| Раскладка | Змейка (SERPENTINE=true), FLIP\_X=true, FLIP\_Y=true |
| Яркость | 10 из 255 (ограничение по току) |
| Ключ шифрования | 8 бит, пины D2..D9 (INPUT\_PULLUP, бит 0 = D2, бит 7 = D9) |
| Serial | 115200 baud |

---

## Прошивка (`main.cpp`)

**Цикл loop() для каждого из 5 изображений:**
1. Показать оригинал (`HOLD_TIME` = 2000 мс)
2. Шифровать змейкой вперёд — группами по `GROUP_SIZE=4` пикселя, читая ключ с пинов перед каждым шагом
3. Держать зашифрованный вид (`HOLD_TIME` мс)
4. Расшифровать в обратном порядке (если ключ не менялся — восстановится точно)
5. Сравнить с оригиналом, вывести результат в Serial
6. Перейти к следующему изображению (`currentImage` 0..4, циклично)

**Алгоритм шифрования:**
- `CIPHER_MODE=1` (активен) — поточный: маска = `hash(key, pixel_index, channel)`, картинка → шум
- `CIPHER_MODE=0` — простой XOR одним ключом (контуры видны)
- `BLANK_PIXELS=false` — гашение пикселей по битам ключа (выключено)

**Serial-вывод платы (115200 baud):**
```
=== IMAGE 1 / 5 ===
--- original ---
enc group 0  key 0xB4  0b10110100
enc group 1  key 0xB4  0b10110100
...
--- encrypted ---
dec group 63  key 0xB4  0b10110100
...
--- decrypted: OK, image restored ---
```
Regex для парсинга ключа: `key\s+0x([0-9A-Fa-f]{2})`

---

## `images.h`

- 5 массивов: `img1[16][16]`..`img5[16][16]`, тип `Pixel {uint8_t r,g,b}`, PROGMEM
- `img1` — реальное изображение (пример: канделябр)
- `img2`..`img5` — чёрные шаблоны по умолчанию
- **Файл полностью перезаписывается** при нажатии «Загрузить на плату» в `app.py`
- Формат строки пикселя: `{R, G, B}`, строки: `{ {R,G,B}, ... },  // y = N`

---

## `flash.py`

```bash
python flash.py                             # автовыбор порта, старый загрузчик
python flash.py --port COM5                 # указать порт
python flash.py --port COM5 --new-bootloader  # новый загрузчик
python flash.py --list-ports                # список портов (JSON от arduino-cli)
python flash.py --no-install                # не устанавливать core/библиотеки
```

Логика: `main.cpp` → `Firmware.ino` + `images.h` во временную папку → `arduino-cli compile` → `arduino-cli upload`.

**Загрузчики:**
- Старый (по умолчанию): `cpu=atmega328old` — большинство клонов Nano с CH340
- Новый: `cpu=atmega328` — оригинальные или новые платы

---

## `img2arduino.py`

```bash
python img2arduino.py photo.jpg                        # crop до 16×16, stdout
python img2arduino.py photo.jpg --fit pad --bg 000000  # вписать с чёрными полями
python img2arduino.py photo.jpg --gamma 2.2            # гамма-коррекция для LED
python img2arduino.py photo.jpg --preview out.png      # сохранить увеличенное превью
python img2arduino.py photo.jpg --name img3            # имя C-массива
```

Вывод копируется вручную в нужный слот `images.h`. Поддерживает: png, jpg, bmp, gif, webp, tiff.

---

## `app.py` (PyQt6 GUI)

**Запуск:** `python app.py` из корня GES-2.

**Зависимости (все установлены):**

| Пакет | Версия |
|---|---|
| Python | 3.14.5 |
| PyQt6 | 6.11.0 |
| pyserial | 3.5 |
| Pillow | 12.1.1 |
| OS | Windows 11 |

**Классы и функции:**

| Имя | Роль |
|---|---|
| `image_to_pil_16x16(path, fit, gamma)` | Загрузить и масштабировать картинку до 16×16 |
| `pil_to_qpixmap(img16, size)` | PIL 16×16 → QPixmap 160×160 с пиксельной сеткой |
| `generate_images_h(slots)` | Список из 5 PIL Image (или None) → текст `images.h` |
| `pil_to_c_array(img, name)` | PIL Image → строка C-массива PROGMEM |
| `make_black_slot(name)` | Строка чёрного C-массива для пустых слотов |
| `SerialReaderThread` | QThread: читает COM-порт, парсит ключ, сигналы `key_received(int)`, `line_received(str)` |
| `FlashThread` | QThread: запускает `flash.py` как subprocess, стримит вывод в `log_line(str)` |
| `ImageSlot` | QFrame 160×160: ЛКМ=выбрать файл, ПКМ=очистить |
| `KeyDisplay` | QWidget: 8-битный ключ — HEX, BIN, DEC + 8 цветных квадратиков-битов с подписью пина D2..D9 |
| `MainWindow` | Главное окно: левая панель (порт, загрузчик, ключ, кнопка Flash) + правая (5 слотов, лог) |

**Ключевые константы:**
```python
GRID_SIZE   = 16        # размер панели
NUM_SLOTS   = 5         # слотов изображений
PREVIEW_PX  = 160       # размер превью в пикселях
SERIAL_BAUD = 115200
FLASH_PY    = <root>/LED_Control/src/flash.py
IMAGES_H    = <root>/LED_Control/src/images.h
```

**Workflow загрузки:**
1. Пользователь кликает по слотам (ЛКМ — выбрать файл, ПКМ — очистить)
2. Выбирает COM-порт и тип загрузчика (radio button)
3. Нажимает «⚡ Загрузить на плату»
4. `app.py`: останавливает Serial → генерирует `images.h` → запускает `FlashThread` → лог в реальном времени → диалог по результату

**Serial-монитор в приложении:**
- Кнопка «▶ Слушать Serial» (toggle) — подключается к выбранному COM-порту
- Всё что приходит с платы — в лог
- Строки с `key 0xNN` обновляют `KeyDisplay`
- При нажатии «Загрузить» Serial останавливается автоматически

---

## Инструменты сборки

| Инструмент | Статус |
|---|---|
| `arduino-cli` | должен быть в PATH (используется `flash.py`) |
| PlatformIO | установлен в VS Code (для разработки прошивки) |

**Данные arduino-cli на текущей машине:**
- Платформы: `C:\Users\vladr\AppData\Local\Arduino15\`
- Библиотеки (FastLED): `C:\Users\vladr\Documents\Arduino\libraries\`

---

## Правила для агента

**Не трогать без явного запроса:**
- `main.cpp` — прошивка финальная
- `platformio.ini`
- `lib/FastLED/`

**Перезаписывается автоматически:**
- `LED_Control/src/images.h` — каждый раз при нажатии «Загрузить» в `app.py`

**Соглашения по коду (`app.py`):**
- Стиль тёмной темы: фон `#1a1a1a`, акцент `#0af`, зелёный лог `#0f0`
- Кнопки через `MainWindow._btn_style(bg, bg_hover, bold, font_size)`
- GroupBox через `MainWindow._groupbox_style()`
- Новые фоновые операции — только через QThread, не блокировать UI
- Все пути — через `Path(__file__).resolve().parent / ...`

**Типичные задачи:**
- Добавить функцию в app.py → смотри архитектуру выше, соблюдай стиль
- Изменить алгоритм шифрования → `main.cpp`, константы `CIPHER_MODE`, `GROUP_SIZE`, `BLANK_PIXELS`
- Поменять изображения по умолчанию → `images.h` или `img2arduino.py`
- Добавить тип платы → `flash.py`, новый аргумент с нужным FQBN
- Изменить параметры панели → `main.cpp`, секция `НАСТРОЙКИ ПАНЕЛИ`
