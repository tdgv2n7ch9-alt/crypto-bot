#!/usr/bin/env python3
"""
tools/telethon_reauth_temp.py -- Telethon план Б (владелец, 2026-07-18): основной
аккаунт (`best_trade_reader.session`) заблокирован на 2FA-восстановлении
(чужая/неопознанная почта, повторных попыток НЕ будет, сброс через 7 дней) --
временная авторизация ридера на ДРУГОМ (личном) аккаунте владельца, чтобы
монитор каналов не простаивал всю неделю.

Копия `tools/telethon_reauth.py` с ОДНИМ отличием -- session-файл
`best_trade_reader_temp` вместо `best_trade_reader`. Основную сессию (боевую,
best_trade_reader.session) этот скрипт НЕ трогает и НЕ читает -- полностью
отдельный sqlite-файл, безопасно запускать параллельно с уже загруженной
launchd-джобой `com.bestrade.reader` (та использует старый путь по умолчанию,
см. `reader.py` -- `READER_SESSION_PATH` с дефолтом на `best_trade_reader`).

Делает ТОЛЬКО client.start() (интерактивный логин: телефон ДРУГОГО аккаунта /
код из Telegram / 2FA-пароль при необходимости) и сразу отключается -- НЕ
подключается к каналам, НЕ шлёт уведомление владельцу.

Запуск (владелец, интерактивно -- ЛИЧНЫЙ номер телефона другого аккаунта, не
основной). TG_API_ID/TG_API_HASH -- те же значения приложения, что и для
основной сессии (один и тот же Telegram API app, разные аккаунты входа
допустимы), берём из того же plist:

    cd ~/crypto-bot
    export TG_API_ID=$(plutil -extract EnvironmentVariables.TG_API_ID raw ~/Library/LaunchAgents/com.bestrade.reader.plist)
    export TG_API_HASH=$(plutil -extract EnvironmentVariables.TG_API_HASH raw ~/Library/LaunchAgents/com.bestrade.reader.plist)
    python3 tools/telethon_reauth_temp.py
    # ввести номер телефона ДРУГОГО (temp) аккаунта -> код из Telegram -> (если есть) пароль 2FA

Переключение самого демона (`com.bestrade.reader`) на эту temp-сессию --
ОТДЕЛЬНЫЙ шаг, только по явному "да" владельца ПОСЛЕ живой проверки (см.
PROGRESS.md) -- этот скрипт сам по себе launchd/plist не трогает.
"""
import asyncio
import os
import sys

from telethon import TelegramClient

SESSION = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "best_trade_reader_temp")
API_ID = int(os.getenv("TG_API_ID", "0"))
API_HASH = os.getenv("TG_API_HASH", "")


async def main():
    if not API_ID or not API_HASH:
        print("ОШИБКА: TG_API_ID/TG_API_HASH не заданы -- см. докстринг для команды запуска.")
        sys.exit(1)

    client = TelegramClient(SESSION, API_ID, API_HASH)
    await client.start()
    me = await client.get_me()
    print(f"OK: сессия авторизована как {me.first_name} (id={me.id}, phone={me.phone}).")
    print(f"Файл сессии: {SESSION}.session -- ЭТО TEMP-СЕССИЯ, не основная.")
    print("Демон com.bestrade.reader ещё НЕ переключён на неё -- отдельный шаг, "
          "только после живой проверки и явного 'да' владельца.")
    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
