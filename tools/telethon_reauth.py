#!/usr/bin/env python3
"""
tools/telethon_reauth.py -- ручная реавторизация Telethon-сессии reader.py
(владелец, 2026-07-18: сессия `best_trade_reader.session` протухла --
launchd-процесс падает с EOFError на `input('Please enter your phone...')`,
т.к. запущен без stdin, см. /tmp/reader.log).

Делает ТОЛЬКО client.start() (интерактивный логин: телефон при первом
запуске сессии / код из Telegram / 2FA-пароль при необходимости) и сразу
отключается -- НЕ подключается к каналам, НЕ шлёт уведомление владельцу,
НЕ запускает Lookonchain-поток. reader.py (боевой демон) переиспользует
эту же сессию по тому же пути -- отдельного шага для него не нужно.

Запуск (владелец, интерактивно, СНАЧАЛА выгрузить launchd-джобу, чтобы
она не держала sqlite-файл сессии параллельно). TG_API_ID/TG_API_HASH --
те же значения, что в `~/Library/LaunchAgents/com.bestrade.reader.plist`
(EnvironmentVariables) -- НЕ вписаны сюда в открытом виде (секрет,
gitleaks справедливо блокирует коммит с ним), берём прямо из plist:

    launchctl unload ~/Library/LaunchAgents/com.bestrade.reader.plist
    cd ~/crypto-bot
    export TG_API_ID=$(plutil -extract EnvironmentVariables.TG_API_ID raw ~/Library/LaunchAgents/com.bestrade.reader.plist)
    export TG_API_HASH=$(plutil -extract EnvironmentVariables.TG_API_HASH raw ~/Library/LaunchAgents/com.bestrade.reader.plist)
    python3 tools/telethon_reauth.py
    # ввести номер телефона (если спросит) -> код из Telegram -> (если есть) пароль 2FA
    launchctl load ~/Library/LaunchAgents/com.bestrade.reader.plist
"""
import asyncio
import os
import sys

from telethon import TelegramClient

SESSION = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "best_trade_reader")
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
    print(f"Файл сессии: {SESSION}.session -- reader.py переиспользует его без изменений.")
    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
