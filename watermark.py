"""
BEST TRADE — Watermark (Пакет SECURITY-HARDENING М5, владелец "да")

Невидимая персональная метка на каждой сигнальной карточке VIP-подписчику --
zero-width unicode-символы, кодирующие chat_id получателя, дописываются в конец
текста карточки. Слил скрин/форвард текста в другой чат -- `/trace` (владелец)
декодирует, чей это экземпляр.

ЧЕСТНОЕ ограничение (важно, не преувеличивать): эта техника работает ТОЛЬКО для
ТЕКСТА -- копипаста подписи/сообщения в другой чат, форвард как текст. Zero-width
символы -- часть Unicode-текста, переживают copy-paste и Telegram-форвард текста/
подписи. Они НЕ переживают СКРИНШОТ (это уже растровое изображение, невидимые
символы физически не могут быть в пикселях) -- для защиты от скриншотов нужна
принципиально другая техника (стеганография в самом изображении графика), которая
не входит в этот модуль и не реализована. Про этот предел -- честно в
RUNBOOK_SECURITY.md и PROGRESS.md, не выдаётся за полное решение "любой слив
раскрывается".

Кодировка: ZW_MARK (начало/конец метки) + 1 бит знака + 44 бита |chat_id| в
двоичном виде, каждый бит -- ZW0 (0) или ZW1 (1). 44 бита с запасом покрывают
текущий диапазон Telegram user chat_id (см. ValueError при переполнении -- лучше
честно не поставить метку, чем поставить битую).
"""

ZW0 = "​"       # ZERO WIDTH SPACE -- бит 0
ZW1 = "‌"       # ZERO WIDTH NON-JOINER -- бит 1
ZW_MARK = "‍"   # ZERO WIDTH JOINER -- граница метки (начало и конец)

MAGNITUDE_BITS = 44
MAX_MAGNITUDE = (1 << MAGNITUDE_BITS) - 1


def encode_chat_id(chat_id: int) -> str:
    """Возвращает строку из zero-width символов (без ZW_MARK-границ) -- 1 бит знака
    + MAGNITUDE_BITS бит модуля. ValueError, если |chat_id| не помещается --
    вызывающий код должен обработать это честно (не отправлять карточку без
    метки молча, см. embed())."""
    magnitude = abs(chat_id)
    if magnitude > MAX_MAGNITUDE:
        raise ValueError(f"chat_id {chat_id} слишком большой для {MAGNITUDE_BITS}-битной метки")
    sign_bit = "1" if chat_id < 0 else "0"
    bits = sign_bit + format(magnitude, f"0{MAGNITUDE_BITS}b")
    return "".join(ZW1 if b == "1" else ZW0 for b in bits)


def suffix(chat_id: int) -> str:
    """Невидимый суффикс целиком (с ZW_MARK-границами), готовый к дописыванию в
    конец текста карточки."""
    return ZW_MARK + encode_chat_id(chat_id) + ZW_MARK


def embed(text: str, chat_id: int) -> str:
    """Дописывает невидимую метку в конец text. Если chat_id не влезает в кодировку
    (см. encode_chat_id) -- возвращает text БЕЗ метки, не бросает исключение наружу
    (не должно ронять отправку карточки из-за водяного знака)."""
    try:
        return text + suffix(chat_id)
    except ValueError:
        return text


def extract(text: str) -> int:
    """Декодирует chat_id из текста, если метка найдена и не повреждена. None,
    если метки нет, она обрезана (частичная пересылка) или содержит посторонние
    символы между границами."""
    if text is None:
        return None
    start = text.find(ZW_MARK)
    if start == -1:
        return None
    end = text.find(ZW_MARK, start + 1)
    if end == -1:
        return None
    payload = text[start + 1:end]
    if len(payload) != 1 + MAGNITUDE_BITS:
        return None
    bits = []
    for ch in payload:
        if ch == ZW1:
            bits.append("1")
        elif ch == ZW0:
            bits.append("0")
        else:
            return None  # посторонний символ внутри границ -- метка повреждена
    sign_bit, magnitude_bits = bits[0], "".join(bits[1:])
    magnitude = int(magnitude_bits, 2)
    return -magnitude if sign_bit == "1" else magnitude
