InlineKeyboardButton("📆 За 7д", callback_data="period_7d"),
    ]]

    for cid in chat_ids:
        try:
            for i, msg in enumerate(msgs):
                markup = InlineKeyboardMarkup(kb) if i == len(msgs) - 1 else None
                await bot.send_message(
                    chat_id=cid,
                    text=msg,
                    parse_mode="Markdown",
                    disable_web_page_preview=True,
                    reply_markup=markup
                )
            log.info(f"Отправлено в {cid}")
        except Exception as e:
            log.error(f"Ошибка отправки в {cid}: {e}")

# ==============================================================
# MAIN
# ==============================================================
def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CallbackQueryHandler(callback_handler))

    scheduler = AsyncIOScheduler(timezone=MOSCOW_TZ)
    bot = app.bot

    scheduler.add_job(
        lambda: asyncio.create_task(send_scheduled(bot, "🔄 Обновление каждые 30 минут")),
        "interval",
        minutes=30
    )
    scheduler.start()
    log.info("✅ Бот запущен! Топ-500 CMC. Рассылка каждые 30 минут.")

    app.run_polling(drop_pending_updates=True)

if name == "__main__":
    main()
