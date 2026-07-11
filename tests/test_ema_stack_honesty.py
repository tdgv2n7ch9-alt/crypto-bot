"""
pytest для ta_extra._stack_label()/_tf_context()/ema_context() -- честность вердикта
EMA-стека (owner, 2026-07-11, найдено на живой карточке Pump-Reversal EVAA):
"стек бычий (4h)" отображался при цене НИЖЕ EMA20 и EMA50 (4h), потому что старая
формула проверяла ТОЛЬКО порядок EMA20>EMA50>EMA100>EMA200, игнорируя, где сейчас
находится цена. Раз EMA лагают за ценой, порядок может ещё не "догнать" свежий
разворот -- значит один лишь порядок недостаточен для честного бычий/медвежий
вердикта. Фикс требует цену ПОДТВЕРЖДАТЬ порядок (выше/ниже ОБЕИХ ближних EMA20/50),
иначе -- "смешанный".

Это НЕ просто текст карточки -- ta_extra.ema_stack_score_delta() читает "stack" и
даёт +-8 к Rocket Score в fa_engine.py/bot.py (real_full_analysis), так что фикс
меняет реальный скоринг живых сигналов, не только карточку Pump-радара. Владелец
одобрил фикс явно ("почини честность").
"""
import ta_extra


def _candles_from_closes(closes):
    return [{"open": c, "high": c, "low": c, "close": c, "vol": 0} for c in closes]


def _uptrend_closes(n=250, start=100.0, step=0.5):
    return [start + i * step for i in range(n)]


def _downtrend_closes(n=250, start=100.0, step=0.5):
    return [start - i * step for i in range(n)]


def test_stack_label_bullish_when_order_and_price_agree():
    # long, clean uptrend -- EMA order bullish AND last price (highest so far) confirms
    closes = _uptrend_closes()
    candles = _candles_from_closes(closes)
    ctx = ta_extra._tf_context(candles)
    assert ctx["stack"] == "бычий"


def test_stack_label_bearish_when_order_and_price_agree():
    closes = _downtrend_closes()
    candles = _candles_from_closes(closes)
    ctx = ta_extra._tf_context(candles)
    assert ctx["stack"] == "медвежий"


def test_stack_label_mixed_when_price_contradicts_bullish_order_evaa_scenario():
    # long uptrend (EMA order settles bullish), then a sharp drop in the last few bars --
    # EMA20/50 haven't caught up yet (still bullish order overall) but price is now
    # below both -- this is EXACTLY the EVAA bug scenario, must NOT say "бычий" anymore.
    closes = _uptrend_closes(n=240) + [220.0, 200.0, 180.0, 160.0, 140.0]
    candles = _candles_from_closes(closes)
    ctx = ta_extra._tf_context(candles)
    price = closes[-1]
    assert price < ctx["ema"][20]
    assert price < ctx["ema"][50]
    assert ctx["stack"] != "бычий", "цена под обеими EMA не должна давать честный 'бычий' вердикт"
    assert ctx["stack"] == "смешанный"


def test_stack_label_mixed_when_price_contradicts_bearish_order():
    closes = _downtrend_closes(n=240) + [-70.0, -50.0, -30.0, -10.0, 10.0]
    candles = _candles_from_closes(closes)
    ctx = ta_extra._tf_context(candles)
    price = closes[-1]
    assert price > ctx["ema"][20]
    assert price > ctx["ema"][50]
    assert ctx["stack"] != "медвежий"
    assert ctx["stack"] == "смешанный"


def test_stack_label_price_none_falls_back_to_pure_ema_order():
    # backward-compat path: no price passed -> old behavior (pure EMA order)
    last = {20: 100.0, 50: 90.0, 100: 80.0, 200: 70.0}
    assert ta_extra._stack_label(last) == "бычий"
    assert ta_extra._stack_label(last, price=None) == "бычий"
    # with price contradicting -- now downgraded to "смешанный"
    assert ta_extra._stack_label(last, price=50.0) == "смешанный"


def test_stack_label_insufficient_data_unaffected_by_price():
    last = {20: 100.0, 50: None, 100: 80.0, 200: 70.0}
    assert ta_extra._stack_label(last, price=150.0) == "недостаточно данных"


def test_stack_label_non_monotonic_ema_order_is_mixed_regardless_of_price():
    last = {20: 100.0, 50: 110.0, 100: 90.0, 200: 80.0}  # not strictly ordered either way
    assert ta_extra._stack_label(last, price=200.0) == "смешанный"
    assert ta_extra._stack_label(last, price=50.0) == "смешанный"


def test_ema_stack_score_delta_zero_for_mixed_evaa_scenario():
    # end-to-end: the EVAA scenario should give score delta 0 (mixed), not a false
    # -8/+8 that a dishonest "бычий" label would have produced for a short signal.
    closes = _uptrend_closes(n=240) + [220.0, 200.0, 180.0, 160.0, 140.0]
    candles = _candles_from_closes(closes)
    ema_ctx = ta_extra.ema_context(candles, candles)
    assert ta_extra.ema_stack_score_delta(ema_ctx, "long") == 0
    assert ta_extra.ema_stack_score_delta(ema_ctx, "short") == 0


def test_format_ema_stack_line_reflects_mixed_verdict():
    closes = _uptrend_closes(n=240) + [220.0, 200.0, 180.0, 160.0, 140.0]
    candles = _candles_from_closes(closes)
    ema_ctx = ta_extra.ema_context(candles, candles)
    line = ta_extra.format_ema_stack_line(ema_ctx)
    assert "смешанный" in line
    assert "бычий" not in line.replace("смешанный", "")  # honestly no false "бычий" anywhere
