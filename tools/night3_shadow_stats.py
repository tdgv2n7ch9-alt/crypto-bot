"""
НОЧЬ#3, блок Н1 (владелец): свежие срезы shadow-статистики по всем контурам
для SHADOW_ANALYSIS.md -- tz13, Патч 05 (BPR), Патч 09 (OI/funding/L-S),
EMA-стек. Только чтение journal/shadow_signals.json (уже синкается с
GitHub в бою) + git log для временных якорей (деплой Пакета 14, фикс
EMA-стек потока). Ничего не считает "решением" -- только цифры, честные
н/д при недостатке данных, пороги -- по методологии, УЖЕ зафиксированной
в SHADOW_ANALYSIS.md (владелец, Пакет 14 DoD), не изобретаются заново.

Запуск: python3 tools/night3_shadow_stats.py
"""
import json
import statistics
from datetime import datetime, timezone

SHADOW_PATH = "journal/shadow_signals.json"

# Временные якоря (git log, см. SHADOW_ANALYSIS.md ночную запись):
# Пакет 14 (tz13 деплой) -- коммит 815c3e8, 2026-07-13T15:40:54+03:00.
PACKET14_DEPLOY_TS = datetime.fromisoformat("2026-07-13T15:40:54+03:00").timestamp()
# EMA-стек поток "починка" -- коммит 347c0a7 (print->log.error + health-счётчик),
# 2026-07-13T10:34:19+03:00 -- ближайший к моменту находки М3/М4 (1879b00,
# 09:44:31) фикс, после которого поток официально должен писать записи снова.
EMA_FIX_TS = datetime.fromisoformat("2026-07-13T10:34:19+03:00").timestamp()
EMA_WINDOW_SEC = 3 * 24 * 3600


def load_records():
    with open(SHADOW_PATH, encoding="utf-8") as f:
        data = json.load(f)
    return data["records"]


def fmt_pct(n, total):
    if total == 0:
        return "н/д (n=0)"
    return f"{n / total * 100:.1f}%"


def section_tz13(records):
    since = [r for r in records if r.get("ts", 0) >= PACKET14_DEPLOY_TS]
    with_score = [r for r in since if r.get("tz13_score") is not None]
    n = len(with_score)

    lines = [
        "## tz13 (Пакет 14, 13-блочный shadow-движок)",
        "",
        f"- Окно: с деплоя Пакета 14 (`815c3e8`, {datetime.fromtimestamp(PACKET14_DEPLOY_TS, tz=timezone.utc).astimezone().isoformat()}) по текущий момент.",
        f"- Всего shadow-записей send_scheduled в этом окне: {len(since)}.",
        f"- Записей с непустым `tz13_score` (tz13 реально досчитал): **n={n}**.",
    ]

    if n == 0:
        lines.append("- Дальше считать нечего -- честное н/д, порог (100 записей ИЛИ 5 суток "
                      "с деплоя) не достигнут ни по одному критерию.")
        return "\n".join(lines), n

    agree_pool = [r for r in with_score
                  if r.get("direction") in ("long", "short")
                  and r.get("tz13_direction") in ("long", "short")]
    agree = sum(1 for r in agree_pool if r["direction"] == r["tz13_direction"])
    lines.append(f"- Совпадение направления (live `direction` vs `tz13_direction`, "
                  f"только где оба определены): {agree}/{len(agree_pool)} = "
                  f"{fmt_pct(agree, len(agree_pool))}.")

    scores = [r["tz13_score"] for r in with_score]
    lines.append(f"- Распределение `tz13_score` (чеклист 0-6): среднее "
                  f"{statistics.mean(scores):.2f}, медиана {statistics.median(scores):.1f}, "
                  f"мин {min(scores)}, макс {max(scores)}.")
    lines.append("  (честная оговорка: у live-пути `real_full_analysis()` нет прямого "
                  "числового аналога tz13-чеклиста для дельты \"score - score\" -- "
                  "сравнивается распределение самого tz13_score, не разница с чем-то.)")

    has_setup = sum(1 for r in with_score
                     if (r.get("tz13_shadow") or {}).get("block13_verdict", {}).get("has_setup"))
    lines.append(f"- `has_setup=true` у tz13: {has_setup}/{n} = {fmt_pct(has_setup, n)}.")
    promoted = sum(1 for r in with_score if r.get("promoted_live"))
    lines.append(f"- `promoted_live=true` (боевой путь реально отправил сигнал) в том же "
                  f"окне: {promoted}/{n} = {fmt_pct(promoted, n)}.")

    setup_types = {}
    for r in with_score:
        st = r.get("tz13_setup_type") or "None"
        setup_types[st] = setup_types.get(st, 0) + 1
    dist = ", ".join(f"{k}: {v} ({fmt_pct(v, n)})" for k, v in sorted(setup_types.items(), key=lambda x: -x[1]))
    lines.append(f"- Распределение `tz13_setup_type`: {dist}.")

    rr_diffs = []
    for r in with_score:
        live_rr = r.get("rr_tp1_live")
        tz13_rr = (r.get("tz13_shadow") or {}).get("block11_tp_rr", {}).get("rr_tp1")
        if live_rr is not None and tz13_rr is not None:
            rr_diffs.append(abs(live_rr - tz13_rr))
    if rr_diffs:
        lines.append(f"- |R:R live − R:R tz13| (n={len(rr_diffs)}): медиана "
                      f"{statistics.median(rr_diffs):.2f}, p90 "
                      f"{sorted(rr_diffs)[int(len(rr_diffs) * 0.9)]:.2f}.")
    else:
        lines.append("- |R:R live − R:R tz13|: н/д (нет записей с обоими значениями)")

    threshold_note = ("порог 100 записей ИЛИ 5 суток с деплоя ДОСТИГНУТ" if n >= 100
                       else f"порог НЕ достигнут (n={n} < 100), 5-суточный дедлайн -- "
                            f"{datetime.fromtimestamp(PACKET14_DEPLOY_TS + 5*86400, tz=timezone.utc).astimezone().date()}")
    lines.append(f"- Статус порога отчёта (методология SHADOW_ANALYSIS.md, Пакет 14 DoD): {threshold_note}.")
    lines.append("- WR/PF по закрытым сделкам -- НЕ считаются в этом срезе (нужна отдельная "
                  "форвардная симуляция по свечам после `ts`, как в `shadow_outcome_analysis.py` -- "
                  "не входит в объём этого ночного блока, честно не выдумываю числа).")
    return "\n".join(lines), n


def section_patch05_bpr(records):
    with_bpr = [r for r in records if r.get("bpr_zone_count") is not None]
    n = len(with_bpr)
    lines = ["## Патч 05 -- BPR (Balanced Price Range) confluence", "",
              f"- Записей с посчитанным `bpr_zone_count`: **n={n}** (порог владельца: 200)."]
    if n == 0:
        lines.append("- Честное н/д -- ни одной записи с BPR-полем.")
        return "\n".join(lines), n
    counts = [r["bpr_zone_count"] for r in with_bpr]
    lines.append(f"- `bpr_zone_count`: среднее {statistics.mean(counts):.1f}, "
                  f"медиана {statistics.median(counts):.1f}, макс {max(counts)}.")
    confluence_known = [r for r in with_bpr if r.get("bpr_confluence") is not None]
    confluence_true = sum(1 for r in confluence_known if r["bpr_confluence"])
    lines.append(f"- `bpr_confluence=true` (свежий BPR пересекается с зоной входа): "
                  f"{confluence_true}/{len(confluence_known)} = "
                  f"{fmt_pct(confluence_true, len(confluence_known))}.")
    bpr_mentions = sum(1 for r in with_bpr
                        if any("bpr" in d.lower() for d in (r.get("discrepancy") or [])))
    lines.append(f"- Записей, где BPR попал в `discrepancy` (confluence с зоной входа "
                  f"отмечен как расхождение с live): {bpr_mentions}/{n} = {fmt_pct(bpr_mentions, n)}.")
    status = "порог 200 ДОСТИГНУТ" if n >= 200 else f"порог НЕ достигнут (n={n} < 200)"
    lines.append(f"- Статус порога: {status}.")
    return "\n".join(lines), n


def section_patch09_oi(records):
    with_oi = [r for r in records
               if r.get("oi_funding_ls_shadow") is not None
               and "error" not in (r.get("oi_funding_ls_shadow") or {})]
    n = len(with_oi)
    lines = ["## Патч 09 -- OI/Funding/L-S shadow", "",
              f"- Записей с посчитанным `oi_funding_ls_shadow` (без ошибки): **n={n}** "
              f"(порог владельца: 100)."]
    if n == 0:
        lines.append("- Честное н/д -- ни одной записи.")
        return "\n".join(lines), n
    deltas = [r["oi_funding_ls_shadow"]["total_delta"] for r in with_oi
              if r["oi_funding_ls_shadow"].get("total_delta") is not None]
    if deltas:
        lines.append(f"- `total_delta` (насколько изменился бы Rocket Score, если бы OI/"
                      f"funding/L-S подключили к боевому скорингу): среднее "
                      f"{statistics.mean(deltas):+.2f}, медиана {statistics.median(deltas):+.1f}, "
                      f"диапазон [{min(deltas):+d}, {max(deltas):+d}].")
        positive = sum(1 for d in deltas if d > 0)
        negative = sum(1 for d in deltas if d < 0)
        zero = sum(1 for d in deltas if d == 0)
        lines.append(f"- Знак дельты: {positive} положительных ({fmt_pct(positive, len(deltas))}), "
                      f"{negative} отрицательных ({fmt_pct(negative, len(deltas))}), "
                      f"{zero} нулевых ({fmt_pct(zero, len(deltas))}).")
    else:
        lines.append("- `total_delta`: н/д (ни одной записи со значением)")
    status = "порог 100 ДОСТИГНУТ" if n >= 100 else f"порог НЕ достигнут (n={n} < 100)"
    lines.append(f"- Статус порога: {status}.")
    return "\n".join(lines), n


def section_ema_stack(records):
    now_ts = datetime.now(timezone.utc).timestamp()
    window_end = EMA_FIX_TS + EMA_WINDOW_SEC
    elapsed_hours = (min(now_ts, window_end) - EMA_FIX_TS) / 3600
    in_window = [r for r in records
                 if r.get("type") == "ema_stack_shadow" and EMA_FIX_TS <= r.get("ts", 0) <= now_ts]
    n = len(in_window)
    lines = ["## EMA-стек shadow (окно 3 суток с починки потока)", "",
              f"- Починка потока (print->log.error + health-счётчик): коммит `347c0a7`, "
              f"{datetime.fromtimestamp(EMA_FIX_TS, tz=timezone.utc).astimezone().isoformat()}.",
              f"- Окно 3 суток от починки: с этого момента по "
              f"{datetime.fromtimestamp(window_end, tz=timezone.utc).astimezone().isoformat()}.",
              f"- **Честно: с момента починки прошло ~{elapsed_hours:.1f} часов из 72 -- "
              f"окно ещё НЕ закрыто**, ниже -- срез на текущий момент, не финальный отчёт.",
              f"- Записей `type=ema_stack_shadow` в прошедшей части окна: **n={n}**."]
    if n == 0:
        lines.append("- Честное н/д -- ни одной записи с момента починки. Причина низкого "
                      "объёма СТРУКТУРНАЯ, не обязательно поломка: "
                      "`log_ema_stack_shadow_async()` вызывается только внутри "
                      "`_confirm_pump_reversal()` (`pump_detector.py`) -- редкое событие "
                      "(подтверждённый разворот памп/дамп), не на каждый AUTO-сигнал. "
                      "До починки за всю историю файла (1890 записей) накопилась всего "
                      "1 запись этого типа -- сравнение объёма имеет смысл только "
                      "после накопления заметно бОльшего числа pump/dump-реверсалов.")
        return "\n".join(lines), n
    for r in in_window:
        score_old = r.get("pro_score_old")
        score_new = r.get("pro_score_new")
        dir_old = r.get("direction_old")
        dir_new = r.get("direction_new")
        diverges = r.get("diverges")
        lines.append(f"  - {r.get('symbol', '?')}: score {score_old}->{score_new}, "
                      f"направление {dir_old}->{dir_new}, diverges={diverges}")
    return "\n".join(lines), n


def main():
    records = load_records()
    now = datetime.now(timezone.utc).astimezone()
    header = (
        f"## НОЧЬ#3, Н1 -- срез shadow-статистики, {now.isoformat()}\n\n"
        f"Источник: `journal/shadow_signals.json`, всего записей в файле: {len(records)}.\n"
        f"Только цифры -- решения по переводу чего-либо в бой не мои, см. пороги ниже "
        f"каждого раздела (методология -- уже зафиксированная в этом файле ранее, "
        f"Пакет 14 DoD, не изобретена заново этой ночью).\n"
    )

    tz13_text, tz13_n = section_tz13(records)
    bpr_text, bpr_n = section_patch05_bpr(records)
    oi_text, oi_n = section_patch09_oi(records)
    ema_text, ema_n = section_ema_stack(records)

    body = "\n\n".join([header, tz13_text, bpr_text, oi_text, ema_text])
    print(body)
    return body


if __name__ == "__main__":
    main()
