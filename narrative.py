"""
BEST TRADE — «Разбор»: rule-based генератор связного русского абзаца (3-5 предложений)
из уже посчитанного результата fa_engine.build_full_analysis(). Никаких внешних API и
никакого LLM — чисто шаблонная сборка готовых фактов, уже лежащих в блоках result:
каждое предложение подставляет только реальные числа/метки из соответствующего блока;
если блок пуст/недоступен ("ok": False или пустой список) — предложение просто
пропускается (см. докстринг fa_engine.py: "нет данных != придуманное значение").

Четыре шаблона (в этом порядке, каждый — отдельное предложение):
  1. Структура: BOS/CHoCH (block3_smc) + свежий свип (sweep_1h/sweep_4h) -- "снята
     ликвидность снизу/сверху/в обе стороны".
  2. Позиция цены относительно ближайшей POI-зоны (block4_poi) -- "торгуется над/под
     {ТФ}-зоной спроса/предложения X-Y".
  3. Сценарий из вердикта (block11_trade_plan) -- если сетапа нет, переиспользуем уже
     двусценарный текст fa_engine ("wait_for": "пробой = ..., удержание = ...");
     если сетап есть -- конкретный план (вход/стоп/цель/R:R).
  4. Честность по чеклисту (block5_checklist): score < 4/6 -- "подтверждений мало,
     наблюдаю", а не выдуманная уверенность.

Результат экранируется html.escape() и рассчитан на вставку в карточку с parse_mode=
"HTML" под заголовком <b>Разбор</b> (см. render_narrative_block) -- ОТДЕЛЬНО от
остальной карточки fa_engine.render_full_analysis_card(), которая рендерится Markdown'ом
(смешивать HTML-теги с Markdown-разбором Telegram нельзя — вызывающая сторона должна
отправлять этот блок отдельным сообщением с parse_mode="HTML").
"""

import html

import ta_extra


def _fmt(v) -> str:
    if v is None:
        return "?"
    if abs(v) >= 1000:
        return f"{v:,.1f}"
    if abs(v) >= 1:
        s = f"{v:,.4f}"
        return s.rstrip("0").rstrip(".") if "." in s else s
    return f"{v:.8g}"


def _fmt_range(lo: float, hi: float) -> str:
    return f"{_fmt(lo)}-{_fmt(hi)}"


def _tf_label(sources) -> str:
    order = (("1d", "1D"), ("4h", "4H"), ("1h", "1H"))
    src = sources or []
    labels = [lbl for key, lbl in order if key in src]
    if labels:
        return "+".join(labels)
    if "ema" in src:
        return "EMA"
    return ""


def _structure_sentence(result: dict) -> str:
    """Шаблон 1: BOS/CHoCH (block3_smc) + свежий свип (sweep_1h/sweep_4h) -- "снята
    ликвидность снизу/сверху/в обе стороны". Пропускается, если ни свипа, ни
    определённой SMC-структуры нет вообще."""
    symbol = result.get("symbol", "?")
    sweep_1h = result.get("sweep_1h")
    sweep_4h = result.get("sweep_4h")
    swept_low = any(s and s.get("type") == "sweep_low" for s in (sweep_1h, sweep_4h))
    swept_high = any(s and s.get("type") == "sweep_high" for s in (sweep_1h, sweep_4h))

    parts = []
    if swept_low and swept_high:
        parts.append("снята ликвидность в обе стороны")
    elif swept_low:
        parts.append("снята ликвидность снизу")
    elif swept_high:
        parts.append("снята ликвидность сверху")

    b3 = result.get("block3_smc") or {}
    smc_type = b3.get("type")
    if smc_type in ("BOS_bull", "BOS_bear"):
        parts.append("структура пробита по тренду (BOS)")
    elif smc_type in ("CHoCH_bull", "CHoCH_bear"):
        parts.append("характер сменился против текущего bias (CHoCH)")
    elif smc_type == "range":
        parts.append("рынок в диапазоне (равные хаи/лои)")

    if not parts:
        return ""
    return f"{symbol}: " + ", ".join(parts) + "."


def _poi_position_sentence(result: dict) -> str:
    """Шаблон 2: позиция цены относительно ближайшей POI-зоны (block4_poi, уже
    отсортирован K-LVL и по расстоянию -- первый элемент и есть ближайшая зона).
    Пропускается, если чётких зон нет."""
    symbol = result.get("symbol", "?")
    b4 = result.get("block4_poi") or {}
    poi_list = b4.get("poi") or []
    if not poi_list:
        return ""
    p = poi_list[0]
    zone = p.get("_zone")
    if zone and zone.get("lo") is not None and zone.get("hi") is not None:
        lo, hi = ta_extra.smart_round(zone["lo"]), ta_extra.smart_round(zone["hi"])
        tf = _tf_label(zone.get("sources"))
        tf_part = f"{tf}-" if tf else ""
    else:
        level = p.get("price")
        if level is None:
            return ""
        width = abs(level) * 0.003
        lo, hi = ta_extra.smart_round(level - width), ta_extra.smart_round(level + width)
        tf_part = ""
    role_word = "спроса" if p.get("side") == "below" else "предложения"
    rel_word = "над" if p.get("side") == "below" else "под"
    klvl_note = " (K-LVL)" if p.get("klvl") else ""
    return (f"{symbol} торгуется {rel_word} {tf_part}зоной {role_word} "
           f"{_fmt_range(lo, hi)}{klvl_note}.")


def _scenario_sentence(result: dict) -> str:
    """Шаблон 3: сценарий из вердикта (block11_trade_plan). Без сетапа -- переиспользуем
    уже двусценарный текст fa_engine ("wait_for": "пробой = ..., удержание = ...", см.
    fa_engine._dual_scenario_text). С сетапом -- конкретный план (вход/стоп/цель/R:R).
    Пропускается, если блок недоступен."""
    b11 = result.get("block11_trade_plan") or {}
    if not b11.get("ok"):
        return ""
    if b11.get("has_setup"):
        return (f"План: вход от {_fmt(b11['entry1'])}, стоп {_fmt(b11['sl'])}, "
               f"цель {_fmt(b11['tp1'])} (R:R 1:{b11['rr_tp1']:.2f}).")
    wait_for = b11.get("wait_for")
    if not wait_for:
        return ""
    return f"Сценарий: {wait_for}."


def _checklist_honesty_sentence(result: dict) -> str:
    """Шаблон 4: если чеклист < 4/6 -- честное "подтверждений мало, наблюдаю", вместо
    выдуманной уверенности. Пропускается при 4+/6 (тогда сетап и так уже описан в
    шаблоне 3) или если блок недоступен."""
    b5 = result.get("block5_checklist") or {}
    if not b5.get("ok"):
        return ""
    score = b5.get("score", 0)
    if score >= 4:
        return ""
    return f"Подтверждений мало ({score}/6) — наблюдаю, не тороплюсь."


def build_narrative(result: dict) -> str:
    """Связный русский абзац (3-5 предложений) по 4 шаблонам выше, HTML-escaped.
    Возвращает "" (не HTML-обёрнутое), если result не ok либо ни один шаблон не дал
    предложения -- вызывающая сторона в этом случае просто не вставляет блок "Разбор"."""
    if not result or not result.get("ok"):
        return ""
    sentences = [
        _structure_sentence(result),
        _poi_position_sentence(result),
        _scenario_sentence(result),
        _checklist_honesty_sentence(result),
    ]
    sentences = [s for s in sentences if s]
    if not sentences:
        return ""
    return html.escape(" ".join(sentences))


def render_narrative_block(result: dict) -> str:
    """"<b>Разбор</b>\\n{текст}" -- готово к отправке отдельным сообщением с
    parse_mode="HTML" (см. модульный докстринг про несовместимость с Markdown-карточкой
    fa_engine). "" (пустая строка), если строить нечего -- вызывающая сторона должна
    пропустить отправку в этом случае, а не слать пустой блок."""
    text = build_narrative(result)
    if not text:
        return ""
    return f"<b>Разбор</b>\n{text}"
