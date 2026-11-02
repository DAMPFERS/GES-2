// #include <Arduino.h>

// // Управление адресной светодиодной панелью 16x16 (WS2812B) на Arduino Nano
// // Библиотека: FastLED (Менеджер библиотек -> "FastLED" -> Установить)

// #include "FastLED.h"

// // ================== НАСТРОЙКИ ==================
// #define DATA_PIN      11        // пин Nano, к которому подключён DIN панели
// #define WIDTH         16
// #define HEIGHT        16
// #define NUM_LEDS      (WIDTH * HEIGHT)
// #define LED_TYPE      WS2812B
// #define COLOR_ORDER   GRB      // если цвета перепутаны — попробуйте RGB или BRG

// #define BRIGHTNESS    10       // 0..255. Начинайте с малого!
// #define MAX_MILLIAMPS 4000     // ограничение тока под ваш блок питания (мА)

// #define SERPENTINE    true     // true — "змейка" (чётные строки слева направо, нечётные справа налево)
// #define FLIP_X        true    // зеркалить по горизонтали
// #define FLIP_Y        true    // зеркалить по вертикали

// #define EFFECT_TIME   10000    // сколько мс показывать каждый эффект
// #define TEST_MODE     false    // true — только тест разводки (бегущая точка)
// // ===============================================

// CRGB leds[NUM_LEDS];

// // Перевод координат (x, y) в номер светодиода в ленте
// uint16_t XY(uint8_t x, uint8_t y) {
//   if (x >= WIDTH || y >= HEIGHT) return NUM_LEDS;  // защита от выхода за границы
//   if (FLIP_X) x = WIDTH - 1 - x;
//   if (FLIP_Y) y = HEIGHT - 1 - y;
//   if (SERPENTINE && (y & 1)) {
//     return y * WIDTH + (WIDTH - 1 - x);
//   }
//   return y * WIDTH + x;
// }

// void setPixel(uint8_t x, uint8_t y, CRGB color) {
//   uint16_t i = XY(x, y);
//   if (i < NUM_LEDS) leds[i] = color;
// }

// // ---------- Тест разводки ----------
// // Точка должна идти по строкам слева направо, сверху вниз.
// // Если идёт иначе — меняйте SERPENTINE / FLIP_X / FLIP_Y.
// void testMapping() {
//   static uint16_t pos = 0;
//   FastLED.clear();
//   uint8_t x = pos % WIDTH;
//   uint8_t y = pos / WIDTH;
//   setPixel(0, 0, CRGB::Red);          // угол (0,0) всегда красный
//   setPixel(x, y, CRGB::White);
//   FastLED.show();
//   pos = (pos + 1) % NUM_LEDS;
//   delay(60);
// }

// // ---------- Эффект 1: диагональная радуга ----------
// void effectRainbow() {
//   static uint8_t hue = 0;
//   for (uint8_t y = 0; y < HEIGHT; y++)
//     for (uint8_t x = 0; x < WIDTH; x++)
//       setPixel(x, y, CHSV(hue + (x + y) * 8, 255, 255));
//   hue += 2;
// }

// // ---------- Эффект 2: плазма ----------
// void effectPlasma() {
//   static uint16_t t = 0;
//   for (uint8_t y = 0; y < HEIGHT; y++) {
//     for (uint8_t x = 0; x < WIDTH; x++) {
//       uint8_t v = sin8(x * 16 + t) + cos8(y * 16 + t / 2) + sin8((x + y) * 8 + t / 3);
//       setPixel(x, y, ColorFromPalette(PartyColors_p, v));
//     }
//   }
//   t += 3;
// }

// // ---------- Эффект 3: "Матрица" (падающие зелёные символы) ----------
// void effectMatrix() {
//   // сдвигаем всё на строку вниз
//   for (int8_t y = HEIGHT - 1; y > 0; y--)
//     for (uint8_t x = 0; x < WIDTH; x++)
//       leds[XY(x, y)] = leds[XY(x, y - 1)];
//   // верхняя строка: затухание + новые капли
//   for (uint8_t x = 0; x < WIDTH; x++) {
//     leds[XY(x, 0)].nscale8(160);
//     if (random8() < 20) leds[XY(x, 0)] = CRGB(0, 255, 40);
//   }
//   delay(40);
// }

// // ---------- Эффект 4: огонь ----------
// void effectFire() {
//   static uint8_t heat[WIDTH][HEIGHT];
//   for (uint8_t x = 0; x < WIDTH; x++) {
//     // остывание
//     for (uint8_t y = 0; y < HEIGHT; y++)
//       heat[x][y] = qsub8(heat[x][y], random8(0, 30));
//     // жар поднимается вверх (низ панели — y = HEIGHT-1)
//     for (uint8_t y = 0; y < HEIGHT - 2; y++)
//       heat[x][y] = (heat[x][y + 1] + heat[x][y + 2] + heat[x][y + 2]) / 3;
//     // новые искры снизу
//     if (random8() < 160)
//       heat[x][HEIGHT - 1] = qadd8(heat[x][HEIGHT - 1], random8(160, 255));
//     for (uint8_t y = 0; y < HEIGHT; y++)
//       setPixel(x, y, HeatColor(heat[x][y]));
//   }
//   delay(20);
// }

// // ---------- Эффект 5: конфетти ----------
// void effectConfetti() {
//   fadeToBlackBy(leds, NUM_LEDS, 15);
//   setPixel(random8(WIDTH), random8(HEIGHT), CHSV(random8(), 200, 255));
//   delay(15);
// }

// typedef void (*Effect)();
// Effect effects[] = { effectRainbow, effectPlasma, effectMatrix, effectFire, effectConfetti };
// const uint8_t EFFECT_COUNT = sizeof(effects) / sizeof(effects[0]);

// void setup() {
//   delay(500);  // даём питанию стабилизироваться
//   FastLED.addLeds<LED_TYPE, DATA_PIN, COLOR_ORDER>(leds, NUM_LEDS);
//   FastLED.setMaxPowerInVoltsAndMilliamps(5, MAX_MILLIAMPS);
//   FastLED.setBrightness(BRIGHTNESS);
//   FastLED.clear(true);
//   random16_add_entropy(analogRead(A0));
// }

// void loop() {
//   if (TEST_MODE) {
//     testMapping();
//     return;
//   }

//   static uint8_t current = 0;
//   static uint32_t lastSwitch = 0;

//   if (millis() - lastSwitch > EFFECT_TIME) {
//     lastSwitch = millis();
//     current = (current + 1) % EFFECT_COUNT;
//     FastLED.clear();
//   }

//   effects[current]();
//   FastLED.show();
// }

