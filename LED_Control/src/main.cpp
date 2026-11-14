// XOR-шифрование картинки на светодиодной панели 16x16 (WS2812B), Arduino Nano
// Библиотека: FastLED
//
// Цикл работы:
//   1. Показывается оригинальная картинка.
//   2. Змейкой, группами по GROUP_SIZE пикселей, каждый байт цвета (R, G, B)
//      XOR-ится с 8-битным ключом, который читается с пинов D2..D9 перед каждым шагом.
//   3. Дойдя до конца, программа идёт обратно и расшифровывает теми же действиями
//      (XOR обратим: (a ^ k) ^ k = a). Ключ при этом снова читается с пинов —
//      если его поменять, картинка восстановится неправильно.
//   4. Результат сравнивается с оригиналом (видно в мониторе порта, 115200),
//      и цикл начинается заново.

#include <Arduino.h>
#include <FastLED.h>
#include <avr/pgmspace.h>

// ================== НАСТРОЙКИ ПАНЕЛИ ==================
#define DATA_PIN      11
#define WIDTH         16
#define HEIGHT        16
#define NUM_LEDS      (WIDTH * HEIGHT)
#define LED_TYPE      WS2812B
#define COLOR_ORDER   GRB
#define BRIGHTNESS    10
#define MAX_MILLIAMPS 4000
#define SERPENTINE    true
#define FLIP_X        true
#define FLIP_Y        true

// ================== НАСТРОЙКИ ШИФРОВАНИЯ ==================
#define GROUP_SIZE    4        // пикселей за один шаг (256 должно делиться нацело)
#define NUM_GROUPS    (NUM_LEDS / GROUP_SIZE)

// Режим шифрования:
//   0 — один и тот же ключ для всех байтов. Видно, что одинаковые цвета дают
//       одинаковый шифротекст — контуры картинки остаются различимы.
//   1 — "поток ключей": из ключа и номера пикселя делается своя маска
//       для каждого байта. Картинка превращается в шум, и даже смена
//       младшего бита ключа меняет в среднем половину битов каждого цвета —
//       соседние ключи дают совершенно разные картинки.
#define CIPHER_MODE   1

// Гашение пикселей по битам ключа (только на этапе шифрования).
// Пиксель с номером s по змейке соответствует биту (7 - s % 8) ключа:
// идя по змейке, 8 пикселей подряд "читают" ключ от старшего бита к младшему.
// Где бит = 0 — пиксель гаснет. Его зашифрованный цвет не теряется:
// при расшифровке он пересчитывается из оригинала и сохранённого ключа.
#define BLANK_PIXELS  false

#define KEY_INVERT    false     // true: пины с подтяжкой, замкнутый на GND переключатель = бит 1

#define SHOW_CURSOR   true     // подсвечивать обрабатываемую группу белым
#define CURSOR_TIME   60       // мс подсветки курсора
#define ANIM_FRAMES   6        // кадров плавного перехода старый цвет -> новый (0 — мгновенно)
#define ANIM_DELAY    15       // мс между кадрами перехода
#define STEP_DELAY    30       // пауза после каждого шага, мс
#define HOLD_TIME     2000     // пауза на оригинале / полностью зашифрованной картинке, мс
// ===========================================================

typedef struct {
  uint8_t r;
  uint8_t g;
  uint8_t b;
} Pixel;

// Массивы изображений находятся отдельно, в images.h.
#include "images.h" 

CRGB leds[NUM_LEDS];
uint8_t usedKey[NUM_GROUPS];   // каким ключом шифровалась каждая группа (для проверки)

// ---------- Ключ с пинов D2..D9 ----------
// D2..D7 = PD2..PD7 -> биты 0..5, D8, D9 = PB0, PB1 -> биты 6, 7
uint8_t readKey() {
  uint8_t k = (uint8_t)((PIND >> 2) | (PINB << 6));
  return KEY_INVERT ? (uint8_t)~k : k;
}

// ---------- Координаты ----------
// (x, y) логические: x — слева направо, y — сверху вниз, как в массиве img[y][x]
uint16_t XY(uint8_t x, uint8_t y) {
  if (x >= WIDTH || y >= HEIGHT) return NUM_LEDS;
  if (FLIP_X) x = WIDTH - 1 - x;
  if (FLIP_Y) y = HEIGHT - 1 - y;
  if (SERPENTINE && (y & 1)) return y * WIDTH + (WIDTH - 1 - x);
  return y * WIDTH + x;
}

// Номер шага змейки s (0..255) -> логические (x, y):
// строка 0 слева направо, строка 1 справа налево и т.д.
void snakeXY(uint8_t s, uint8_t &x, uint8_t &y) {
  y = s / WIDTH;
  x = (y & 1) ? (WIDTH - 1 - s % WIDTH) : (s % WIDTH);
}

// Номер изображения, которое сейчас обрабатывается (0..4).
uint8_t currentImage = 0;

// Чтение выбранного изображения из PROGMEM.
// Используем switch, чтобы не хранить таблицу указателей во Flash/SRAM.
Pixel readImg(uint8_t x, uint8_t y) {
  Pixel p = {0, 0, 0};

  switch (currentImage) {
    case 0: memcpy_P(&p, &img1[y][x], sizeof(Pixel)); break;
    case 1: memcpy_P(&p, &img2[y][x], sizeof(Pixel)); break;
    case 2: memcpy_P(&p, &img3[y][x], sizeof(Pixel)); break;
    case 3: memcpy_P(&p, &img4[y][x], sizeof(Pixel)); break;
    case 4: memcpy_P(&p, &img5[y][x], sizeof(Pixel)); break;
    default: break;
  }

  return p;
}

void loadImage() {
  for (uint8_t y = 0; y < HEIGHT; y++)
    for (uint8_t x = 0; x < WIDTH; x++) {
      Pixel p = readImg(x, y);
      leds[XY(x, y)] = CRGB(p.r, p.g, p.b);
    }
}

// ---------- Маска для конкретного байта ----------
// s — номер пикселя по змейке, c — канал (0=R, 1=G, 2=B)
uint8_t keyMask(uint8_t key, uint8_t s, uint8_t c) {
#if CIPHER_MODE == 0
  (void)s; (void)c;
  return key;
#else
  // Перемешивание: умножение на нечётное число и сдвиг вправо обратимы,
  // поэтому разные ключи всегда дают разные маски, а каждый бит ключа
  // влияет на все биты результата.
  uint8_t x = key ^ (uint8_t)(s * 37) ^ (uint8_t)(c * 101) ^ 0x5A;
  x ^= x >> 4;  x *= 0x6B;
  x ^= x >> 4;  x *= 0x35;
  x ^= x >> 4;  x *= 0xA7;
  x ^= x >> 3;
  return x;
#endif
}

// Бит ключа, отвечающий за пиксель s (MSB первым)
bool keyBit(uint8_t key, uint8_t s) {
  return (key >> (7 - (s & 7))) & 1;
}

// c XOR маска ключа (для всех трёх каналов)
CRGB xorColor(CRGB c, uint8_t key, uint8_t s) {
  return CRGB(c.r ^ keyMask(key, s, 0),
              c.g ^ keyMask(key, s, 1),
              c.b ^ keyMask(key, s, 2));
}

// Зашифрованный цвет пикселя: оригинал из флеша XOR ключ.
// Нужен, т.к. погашенный пиксель на панели черный, а настоящее значение надо помнить.
CRGB encryptedColor(uint8_t s, uint8_t key) {
  uint8_t x, y;
  snakeXY(s, x, y);
  Pixel p = readImg(x, y);
  return xorColor(CRGB(p.r, p.g, p.b), key, s);
}

void animateGroup(const uint16_t *idx, const CRGB *to);

// ---------- Шаг шифрования ----------
void encryptGroup(uint8_t g, uint8_t key) {
  uint16_t idx[GROUP_SIZE];
  CRGB to[GROUP_SIZE];
  for (uint8_t i = 0; i < GROUP_SIZE; i++) {
    uint8_t s = g * GROUP_SIZE + i, x, y;
    snakeXY(s, x, y);
    idx[i] = XY(x, y);
    to[i]  = encryptedColor(s, key);
    if (BLANK_PIXELS && !keyBit(key, s)) to[i] = CRGB::Black;   // гасим
  }
  animateGroup(idx, to);
}

// ---------- Шаг расшифровки ----------
// Берём настоящий шифротекст (оригинал XOR ключ шифрования) и XOR-им текущим ключом.
// Если ключ тот же — получится оригинал, если другой — искажённые цвета.
void decryptGroup(uint8_t g, uint8_t key) {
  uint16_t idx[GROUP_SIZE];
  CRGB to[GROUP_SIZE];
  for (uint8_t i = 0; i < GROUP_SIZE; i++) {
    uint8_t s = g * GROUP_SIZE + i, x, y;
    snakeXY(s, x, y);
    idx[i] = XY(x, y);
    to[i]  = xorColor(encryptedColor(s, usedKey[g]), key, s);
  }
  animateGroup(idx, to);
}

// ---------- Анимация группы: курсор + плавный переход ----------
void animateGroup(const uint16_t *idx, const CRGB *to) {
  CRGB from[GROUP_SIZE];
  for (uint8_t i = 0; i < GROUP_SIZE; i++) from[i] = leds[idx[i]];

#if SHOW_CURSOR
  for (uint8_t i = 0; i < GROUP_SIZE; i++) leds[idx[i]] = CRGB::White;
  FastLED.show();
  delay(CURSOR_TIME);
#endif

  for (uint8_t f = 1; f < ANIM_FRAMES; f++) {
    uint8_t amount = (uint16_t)f * 255 / ANIM_FRAMES;
    for (uint8_t i = 0; i < GROUP_SIZE; i++) leds[idx[i]] = blend(from[i], to[i], amount);
    FastLED.show();
    delay(ANIM_DELAY);
  }

  // Точное значение обязательно: от него зависит последующая расшифровка
  for (uint8_t i = 0; i < GROUP_SIZE; i++) leds[idx[i]] = to[i];
  FastLED.show();
  delay(STEP_DELAY);
}

// Сколько пикселей не совпало с оригиналом
uint16_t countMismatch() {
  uint16_t bad = 0;
  for (uint8_t y = 0; y < HEIGHT; y++)
    for (uint8_t x = 0; x < WIDTH; x++) {
      Pixel p = readImg(x, y);
      CRGB c = leds[XY(x, y)];
      if (c.r != p.r || c.g != p.g || c.b != p.b) bad++;
    }
  return bad;
}

void printKey(const __FlashStringHelper *what, uint8_t g, uint8_t key) {
  Serial.print(what);
  Serial.print(F(" group "));
  Serial.print(g);
  Serial.print(F("  key 0x"));
  if (key < 0x10) Serial.print('0');
  Serial.print(key, HEX);
  Serial.print(F("  0b"));
  for (int8_t b = 7; b >= 0; b--) Serial.print((key >> b) & 1);
  Serial.println();
}

void setup() {
  delay(500);
  // D2..D9 — входы ключа с подтяжкой к +5В
  for (uint8_t pin = 2; pin <= 9; pin++) pinMode(pin, INPUT_PULLUP);

  FastLED.addLeds<LED_TYPE, DATA_PIN, COLOR_ORDER>(leds, NUM_LEDS);
  FastLED.setMaxPowerInVoltsAndMilliamps(5, MAX_MILLIAMPS);
  FastLED.setBrightness(BRIGHTNESS);
  FastLED.setDither(DISABLE_DITHER);
  FastLED.clear(true);

  Serial.begin(115200);
  Serial.println(F("XOR panel start"));
}

void loop() {
  // Выбираем одно из пяти изображений. После полного цикла loop()
  // currentImage увеличивается, поэтому изображения показываются по очереди.
  Serial.print(F("\n=== IMAGE "));
  Serial.print(currentImage + 1);
  Serial.println(F(" / 5 ==="));

  // 1. Оригинал
  loadImage();
  FastLED.show();
  Serial.println(F("--- original ---"));
  delay(HOLD_TIME);

  // 2. Шифрование змейкой вперёд
  for (uint8_t g = 0; g < NUM_GROUPS; g++) {
    uint8_t key = readKey();
    usedKey[g] = key;
    printKey(F("enc"), g, key);
    encryptGroup(g, key);
  }
  Serial.println(F("--- encrypted ---"));
  delay(HOLD_TIME);

  // 3. Расшифровка в обратном порядке
  uint8_t wrongKeys = 0;
  for (int16_t g = NUM_GROUPS - 1; g >= 0; g--) {
    uint8_t key = readKey();
    if (key != usedKey[g]) wrongKeys++;
    printKey(F("dec"), g, key);
    decryptGroup(g, key);
  }

  // 4. Проверка
  uint16_t bad = countMismatch();
  Serial.print(F("--- decrypted: "));
  if (bad == 0) {
    Serial.println(F("OK, image restored ---"));
  } else {
    Serial.print(bad);
    Serial.print(F(" pixels differ, wrong key in "));
    Serial.print(wrongKeys);
    Serial.println(F(" groups ---"));
  }
  delay(HOLD_TIME);

  // Следующий проход loop() будет работать со следующим изображением.
  currentImage++;
  if (currentImage >= 5) currentImage = 0;
}
