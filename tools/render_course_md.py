"""
tools/render_course_md.py -- Пакет П-Обучение (владелец, 2026-07-15).

Генерирует knowledge/course/*.md ИЗ course_content.py -- single source of
truth (см. докстринг course_content.py). НЕ редактировать сгенерированные
.md файлы руками -- любое расхождение с тем, что видит подписчик в боте,
чинится только правкой course_content.py и повторным запуском:

    python3 tools/render_course_md.py

Конвертация Telegram legacy Markdown (*bold*) -> обычный Markdown (**bold**)
для читаемости в GitHub/редакторе -- единственная трансформация, содержание
не меняется.
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import course_content as cc

OUT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "knowledge", "course")

_BOLD_RE = re.compile(r"\*([^\*\n]+)\*")


def _tg_to_md(text: str) -> str:
    """*bold* (Telegram legacy Markdown) -> **bold** (обычный Markdown)."""
    return _BOLD_RE.sub(r"**\1**", text)


def _module_filename(module: dict) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", module["title"].lower()).strip("_")
    # транслитерация не нужна -- используем id + первые буквы латиницей нельзя,
    # поэтому имя файла = module_NN.md, заголовок модуля -- внутри файла.
    return f"module_{module['id']:02d}.md"


def render_module_md(module: dict) -> str:
    lines = [f"# Модуль {module['id']}. {module['title'].upper()}", ""]
    if not module["lessons"]:
        # Модуль 16 -- Шпаргалка + Приложение, содержимое отдельными файлами
        lines.append("См. `shpargalka.md` и `prilozhenie_svechnye_patterny.md` в этой "
                      "же директории.")
        return "\n".join(lines) + "\n"
    for lesson in module["lessons"]:
        lines.append(f"## Урок {lesson['num']}. {lesson['title']}")
        lines.append("")
        lines.append(_tg_to_md(lesson["body"]))
        if lesson.get("methodology_note"):
            lines.append("")
            lines.append(f"> 📎 **Связь с METHODOLOGY_CORE.md**: {_tg_to_md(lesson['methodology_note'])}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def render_toc_md() -> str:
    lines = ["# Криптотрейдинг — полный курс", "", cc.INTRO_TEXT, "", "## Содержание", ""]
    for module in cc.MODULES:
        if module["lessons"]:
            lesson_range = f"уроки {', '.join(l['num'] for l in module['lessons'])}"
        else:
            lesson_range = "шпаргалка + приложение"
        lines.append(f"{module['id']}. [{module['title']}]({_module_filename(module)}) ({lesson_range})")
    lines.append("")
    lines.append(f"Источник: PDF владельца (\"Криптотрейдинг курс 64 урока.pdf\"), tier author, "
                 f"контент применён как есть. Сверено: 64 пронумерованных урока (1-64) + 2 "
                 f"бонусных урока (33-Д, 52-Д) + шпаргалка + приложение = {cc.total_lesson_count()} "
                 f"уроков всего в {len(cc.MODULES)} модулях.")
    return "\n".join(lines) + "\n"


def render_cheatsheet_md() -> str:
    return f"# {cc.CHEATSHEET['title']}\n\n{_tg_to_md(cc.CHEATSHEET['body'])}\n"


def render_appendix_md() -> str:
    return f"# {cc.APPENDIX['title']}\n\n{_tg_to_md(cc.APPENDIX['body'])}\n"


def render_all():
    os.makedirs(OUT_DIR, exist_ok=True)
    written = []

    toc_path = os.path.join(OUT_DIR, "README.md")
    with open(toc_path, "w", encoding="utf-8") as f:
        f.write(render_toc_md())
    written.append(toc_path)

    for module in cc.MODULES:
        path = os.path.join(OUT_DIR, _module_filename(module))
        with open(path, "w", encoding="utf-8") as f:
            f.write(render_module_md(module))
        written.append(path)

    cheat_path = os.path.join(OUT_DIR, "shpargalka.md")
    with open(cheat_path, "w", encoding="utf-8") as f:
        f.write(render_cheatsheet_md())
    written.append(cheat_path)

    app_path = os.path.join(OUT_DIR, "prilozhenie_svechnye_patterny.md")
    with open(app_path, "w", encoding="utf-8") as f:
        f.write(render_appendix_md())
    written.append(app_path)

    return written


if __name__ == "__main__":
    files = render_all()
    print(f"Написано {len(files)} файлов в {OUT_DIR}:")
    for f in files:
        print(f"  {os.path.relpath(f)}")
