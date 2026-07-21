"""
tools/mfe_mae_ab_report.py -- владелец, 2026-07-22 (ночная очередь, инструмент
ВПРОК): классификация A/B закрытых УБЫТОЧНЫХ сделок по MFE (в R-мультиплах) --
"вход был прав, SL слишком тесный" (A, mfe_r >= +1R) vs "вход не сработал
в принципе" (B, mfe_r < +0.3R) vs середина. Читает `journal/signals.json`
НАПРЯМУЮ (реальные `entered_price`/`sl`/`mfe_price`/`mae_price`), переиспользует
`mfe_mae.running_mfe_mae_r()` -- ту же математику, что уже посчитана и
сохранена live-трекером, MFE НЕ пересчитывается заново по свечам.

Порог владельца min_losses=15 (см. `RETRO_WR_DIAGNOSIS.md`, Снимок 1: на n=7
результат был неопределённым, 3/3/1) -- НИЖЕ порога отчёт честно говорит
"недостаточно данных", не форсирует вывод. Инструмент готов к запуску ПРЯМО
СЕЙЧАС (проверено на реальных данных, py_compile+pytest чисто) -- результат
НЕ применяется автоматически ни к чему боевому, только кандидат для владельца.

Запуск: python3 tools/mfe_mae_ab_report.py
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import mfe_mae

MIN_LOSSES = 15
A_THRESHOLD_R = 1.0
B_THRESHOLD_R = 0.3

JOURNAL_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                             "journal", "signals.json")


def load_journal_records(path: str = None) -> dict:
    """{id: record}, int-ключи -- та же форма, что
    shadow_outcome_analysis.load_journal_records_from_disk(). Файла нет/битый
    JSON -- честно пустой словарь, не исключение."""
    path = path or JOURNAL_PATH
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return {int(k): v for k, v in (data.get("records") or {}).items()}
    except Exception:
        return {}


def classify(mfe_r) -> str:
    """None -- честный н/д (не форсирует классификацию на отсутствующем R)."""
    if mfe_r is None:
        return None
    if mfe_r >= A_THRESHOLD_R:
        return "A"
    if mfe_r < B_THRESHOLD_R:
        return "B"
    return "middle"


def build_ab_rows(journal_records: dict) -> list:
    """Для КАЖДОЙ закрытой SL_HIT-сделки с реальными mfe_price/mae_price --
    строка {journal_id, symbol, direction, mfe_r, mae_r, classification}.
    Честно пропускает сделки без mfe_price/mae_price/entered_price/sl -- не
    выдумывает данные там, где их нет (более старые записи, до появления
    running-трекера MFE/MAE)."""
    rows = []
    for jid, rec in journal_records.items():
        if rec.get("outcome") != "SL_HIT":
            continue
        mfe_price = rec.get("mfe_price")
        mae_price = rec.get("mae_price")
        if mfe_price is None or mae_price is None:
            continue
        mfe_r, mae_r = mfe_mae.running_mfe_mae_r(
            rec.get("direction"), rec.get("entered_price"), rec.get("sl"), mfe_price, mae_price)
        if mfe_r is None:
            continue
        rows.append({
            "journal_id": jid, "symbol": rec.get("symbol"), "direction": rec.get("direction"),
            "mfe_r": mfe_r, "mae_r": mae_r, "classification": classify(mfe_r),
        })
    return rows


def build_report(journal_records: dict, min_losses: int = MIN_LOSSES) -> dict:
    """Не решает ничего сама -- только считает и честно фиксирует, достаточно
    ли данных (`ready`) для содержательного A/B-вывода."""
    rows = build_ab_rows(journal_records)
    n = len(rows)
    a_count = sum(1 for r in rows if r["classification"] == "A")
    b_count = sum(1 for r in rows if r["classification"] == "B")
    mid_count = sum(1 for r in rows if r["classification"] == "middle")
    return {
        "n": n, "min_losses": min_losses, "ready": n >= min_losses,
        "rows": rows, "a_count": a_count, "b_count": b_count, "middle_count": mid_count,
    }


def format_report(report: dict) -> str:
    lines = [f"## MFE/MAE A/B диагноз -- вход vs управление "
             f"({report['n']} убытков с mfe/mae-данными)", ""]
    if not report["ready"]:
        lines.append(f"Недостаточно данных: {report['n']}/{report['min_losses']} убытков с "
                      f"заполненными mfe_price/mae_price. Честный н/д -- вывод НЕ форсируется, "
                      f"копить дальше.")
        return "\n".join(lines)
    n = report["n"]
    lines.append(f"- A (вход верный, SL тесный, MFE >= +{A_THRESHOLD_R:.1f}R): "
                 f"{report['a_count']} ({report['a_count']/n*100:.1f}%)")
    lines.append(f"- B (вход не сработал, MFE < +{B_THRESHOLD_R:.1f}R): "
                 f"{report['b_count']} ({report['b_count']/n*100:.1f}%)")
    lines.append(f"- Середина: {report['middle_count']} ({report['middle_count']/n*100:.1f}%)")
    lines.append("")
    lines.append("Только кандидат для владельца -- НЕ применяется автоматически ни к чему боевому.")
    return "\n".join(lines)


def main():
    journal_records = load_journal_records()
    report = build_report(journal_records)
    print(format_report(report))
    print()
    for r in sorted(report["rows"], key=lambda r: r["journal_id"]):
        print(f"  #{r['journal_id']:<4d} {r['symbol']:8s} mfe={r['mfe_r']:+.2f}R "
              f"mae={r['mae_r']:.2f}R -> {r['classification']}")


if __name__ == "__main__":
    main()
