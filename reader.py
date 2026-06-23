import asyncio,logging,os,re
from datetime import datetime,timezone,timedelta
from telethon import TelegramClient,events
API_ID=29856958
API_HASH="fdae011e5ea18379975bde6927c46a07"
BOT_TOKEN=os.environ.get("BOT_TOKEN","")
CHANNEL_ID=os.environ.get("CHANNEL_ID","")
TZ=timezone(timedelta(hours=3))
logging.basicConfig(level=logging.INFO,format="%(asctime)s [READER] %(message)s")
log=logging.getLogger("reader")
SOURCE_CHANNELS=["https://t.me/+nubqP8HBLLg5Yzhi"]
LONG_KW=["лонг","long","покупка","buy","входим"]
SHORT_KW=["шорт","short","продажа","sell"]
SKIP={"USDT","USDC","BUSD","DAI","USD","EUR","TP","SL"}
def parse_signal(text):
    if not text or len(text)<10: return None
    tl=text.lower();tu=text.upper()
    is_long=any(k in tl for k in LONG_KW)
    is_short=any(k in tl for k in SHORT_KW)
    if not is_long and not is_short: return None
    sym=None
    for pat in [r'\b([A-Z]{2,10})USDT\b',r'#([A-Z]{2,10})\b']:
        for s in re.findall(pat,tu):
            if s not in SKIP and len(s)>=2: sym=s;break
        if sym: break
    if not sym: return None
    return {"symbol":sym,"direction":"long" if is_long else "short"}
async def main():
    client=TelegramClient("best_trade_reader",API_ID,API_HASH)
    await client.connect()
    if not await client.is_user_authorized():
        log.info("Авторизация через QR-код...")
        qr=await client.qr_login()
        import qrcode as qrc
        q=qrc.QRCode()
        q.add_data(qr.url)
        q.make()
        print("\n"+"="*50)
        print("ОТСКАНИРУЙ В TELEGRAM:")
        print("Настройки → Устройства → Подключить устройство")
        print("="*50)
        q.print_ascii(invert=True)
        print("="*50+"\n")
        try:
            await asyncio.wait_for(qr.wait(),timeout=120)
            log.info("QR отсканирован! Авторизация успешна!")
        except asyncio.TimeoutError:
            log.error("Время вышло. Запусти снова.")
            return
    log.info("Авторизован!")
    entities=[]
    for ch in SOURCE_CHANNELS:
        try:
            e=await client.get_entity(ch)
            entities.append(e)
            log.info(f"Подключён: {getattr(e,'title',ch)}")
        except Exception as ex:
            log.error(f"Ошибка {ch}: {ex}")
    if not entities: log.error("Нет каналов"); return
    from telegram import Bot
    bot=Bot(token=BOT_TOKEN) if BOT_TOKEN else None
    ids=set(e.id for e in entities)
    names={e.id:getattr(e,"title",str(e.id)) for e in entities}
    @client.on(events.NewMessage(chats=list(ids)))
    async def handler(event):
        text=event.message.text or ""
        sig=parse_signal(text)
        if not sig: return
        src=names.get(event.chat_id,"?")
        sym=sig["symbol"];d=sig["direction"]
        emoji="🟢" if d=="long" else "🔴"
        side="LONG" if d=="long" else "SHORT"
        now=datetime.now(TZ).strftime("%d.%m.%Y %H:%M UTC+3")
        msg=f"{emoji} *{sym}USDT* {side}\n🕐 {now}\n📡 Аналитика BEST TRADE\n📌 {src}\n\n_{text[:300]}_"
        log.info(f"Сигнал: {sym} {side} из {src}")
        if bot and CHANNEL_ID:
            try: await bot.send_message(CHANNEL_ID,msg,parse_mode="Markdown")
            except Exception as ex: log.error(f"Ошибка: {ex}")
    log.info(f"Мониторю {len(entities)} каналов...")
    await client.run_until_disconnected()
asyncio.run(main())
