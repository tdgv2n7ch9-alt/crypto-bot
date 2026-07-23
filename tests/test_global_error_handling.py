"""
pytest -- владелец, ДА, 2026-07-23 (FIXLIST_INTERFACE.md п.1, живая находка):
корень "молча мёртвых кнопок" (РЫНОК/Зоны/Мои разметки) -- ДВА структурных
пробела: (а) в bot.py не было app.add_error_handler(); (б) BadRequest
"Can't parse entities" в свободном тексте, интерполированном в Markdown.
Покрывает: _wrap_markdown_retry() (чистая функция-обёртка), _install_
markdown_retry_wrappers() (патчит инстанс), _global_error_handler()
(best-effort лог+уведомление).
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("BOT_TOKEN", os.environ.get("BOT_TOKEN", "test-token-not-real"))

import bot
from telegram.error import BadRequest


def _run(coro):
    return asyncio.run(coro)


# ── _wrap_markdown_retry() ──

def test_wrap_markdown_retry_passes_through_on_success():
    calls = []

    async def ok(*a, **kw):
        calls.append((a, kw))
        return "sent"

    wrapped = bot._wrap_markdown_retry(ok, "send_message")
    result = _run(wrapped(123, "hello", parse_mode="Markdown"))
    assert result == "sent"
    assert len(calls) == 1


def test_wrap_markdown_retry_retries_without_parse_mode_on_entity_parse_error():
    calls = []

    async def flaky(*a, **kw):
        calls.append(dict(kw))
        if kw.get("parse_mode"):
            raise BadRequest("Can't parse entities: can't find end of the entity starting at byte offset 5")
        return "sent-plain"

    wrapped = bot._wrap_markdown_retry(flaky, "send_message")
    result = _run(wrapped(123, "bad_text", parse_mode="Markdown"))
    assert result == "sent-plain"
    assert len(calls) == 2
    assert calls[0]["parse_mode"] == "Markdown"
    assert calls[1]["parse_mode"] is None


def test_wrap_markdown_retry_reraises_other_bad_requests():
    async def boom(*a, **kw):
        raise BadRequest("Message is not modified")

    wrapped = bot._wrap_markdown_retry(boom, "edit_message_text")
    try:
        _run(wrapped(123, "x", parse_mode="Markdown"))
        assert False, "expected BadRequest to propagate"
    except BadRequest as e:
        assert "not modified" in str(e).lower()


def test_wrap_markdown_retry_reraises_when_no_parse_mode_was_used():
    """Без parse_mode в исходном вызове -- ретраить нечего, ошибка не наша."""
    async def boom(*a, **kw):
        raise BadRequest("Can't parse entities: something else")

    wrapped = bot._wrap_markdown_retry(boom, "send_message")
    try:
        _run(wrapped(123, "x"))
        assert False, "expected BadRequest to propagate"
    except BadRequest:
        pass


# ── _install_markdown_retry_wrappers() ──

class _FakeBotInstance:
    def __init__(self):
        self.send_message_calls = []
        self.edit_message_text_calls = []

    async def send_message(self, *a, **kw):
        self.send_message_calls.append(kw)
        if kw.get("parse_mode"):
            raise BadRequest("Can't parse entities: bad markdown")
        return "ok"

    async def edit_message_text(self, *a, **kw):
        self.edit_message_text_calls.append(kw)
        if kw.get("parse_mode"):
            raise BadRequest("Can't parse entities: bad markdown")
        return "ok"


def test_install_wrappers_patches_both_methods():
    fake = _FakeBotInstance()
    bot._install_markdown_retry_wrappers(fake)
    result = _run(fake.send_message(1, "x", parse_mode="Markdown"))
    assert result == "ok"
    assert len(fake.send_message_calls) == 2  # первая упала, вторая (plain) прошла

    result2 = _run(fake.edit_message_text(1, "y", parse_mode="Markdown"))
    assert result2 == "ok"
    assert len(fake.edit_message_text_calls) == 2


# ── _global_error_handler() ──

class _FakeChat:
    def __init__(self, chat_id):
        self.id = chat_id


class _FakeUpdate:
    def __init__(self, chat_id=999):
        self.effective_chat = _FakeChat(chat_id)


class _FakeCtx:
    def __init__(self, error):
        self.error = error
        self.sent = []

        class _FakeBot:
            async def send_message(_self, chat_id, text):
                self.sent.append((chat_id, text))
        self.bot = _FakeBot()


def test_global_error_handler_logs_and_notifies_chat():
    ctx = _FakeCtx(RuntimeError("boom"))
    update = _FakeUpdate(chat_id=555)
    _run(bot._global_error_handler(update, ctx))
    assert len(ctx.sent) == 1
    assert ctx.sent[0][0] == 555


def test_global_error_handler_never_raises_when_notify_fails():
    """Best-effort -- если ДАЖЕ уведомление об ошибке не отправилось, функция
    не должна падать сама (иначе PTB получит исключение из error_handler)."""
    class _BoomCtx:
        error = RuntimeError("boom")

        class bot:
            @staticmethod
            async def send_message(*a, **kw):
                raise ConnectionError("нет сети")

    update = _FakeUpdate(chat_id=555)
    _run(bot._global_error_handler(update, _BoomCtx()))  # не должно бросить исключение


def test_global_error_handler_skips_notify_when_update_has_no_chat():
    """update не является telegram.Update (или без effective_chat) -- не пытается
    слать уведомление, не падает."""
    ctx = _FakeCtx(RuntimeError("boom"))
    _run(bot._global_error_handler(None, ctx))
    assert ctx.sent == []
