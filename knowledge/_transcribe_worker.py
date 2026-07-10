"""
Фоновый воркер Фазы 2 (транскрибация). Читает очередь _transcribe_queue.json,
пропускает уже готовые (checkpoint = наличие knowledge/transcripts/<slug>.md),
пишет транскрипт + минимальный frontmatter. Конспект (3-10 тезисов) добавляется
ОТДЕЛЬНЫМ проходом (чтение транскриптов и синтез) -- ASR сам по себе не умеет
в анализ, только в текст. Каждые 10 файлов -- строка в _transcribe_progress.log
для последующего обновления PROGRESS.md.
"""
import json, os, re, time, sys, traceback
from datetime import datetime, timezone

ROOT = os.path.expanduser("~/crypto-bot")
TRADING = os.path.join(ROOT, "Trading")
OUT_DIR = os.path.join(ROOT, "knowledge", "transcripts")
QUEUE_FILE = os.path.join(ROOT, "knowledge", "_transcribe_queue.json")
PROGRESS_LOG = os.path.join(ROOT, "knowledge", "_transcribe_progress.log")
MODEL_SIZE = "small"

def slugify(name):
    base = re.sub(r"\.mp4$", "", name, flags=re.I)
    base = re.sub(r"[^\w\-. а-яА-ЯёЁ]", "_", base)
    return base.strip()

def main():
    from faster_whisper import WhisperModel
    with open(QUEUE_FILE) as f:
        queue = json.load(f)

    os.makedirs(OUT_DIR, exist_ok=True)
    print(f"Loading model {MODEL_SIZE}...", flush=True)
    model = WhisperModel(MODEL_SIZE, device="cpu", compute_type="int8")
    print("Model loaded.", flush=True)

    done_count = 0
    for i, fname in enumerate(queue, 1):
        slug = slugify(fname)
        out_path = os.path.join(OUT_DIR, slug + ".md")
        if os.path.exists(out_path):
            continue  # checkpoint: уже сделано

        src_path = os.path.join(TRADING, fname)
        if not os.path.exists(src_path):
            print(f"[{i}/{len(queue)}] SKIP (not found): {fname}", flush=True)
            continue

        t0 = time.time()
        try:
            segments, info = model.transcribe(src_path, language="ru", beam_size=5)
            parts = []
            for seg in segments:
                parts.append(f"[{seg.start:.0f}s] {seg.text.strip()}")
            text = "\n".join(parts)
            elapsed = time.time() - t0

            with open(out_path, "w") as f:
                f.write(f"---\n")
                f.write(f"source: \"{fname}\"\n")
                f.write(f"duration_sec: {info.duration:.1f}\n")
                f.write(f"model: faster-whisper-{MODEL_SIZE}\n")
                f.write(f"transcribed_at: \"{datetime.now(timezone.utc).isoformat()}\"\n")
                f.write(f"transcribe_wall_sec: {elapsed:.1f}\n")
                f.write(f"status: transcript_only  # конспект добавляется отдельным проходом\n")
                f.write(f"---\n\n")
                f.write(f"# {fname}\n\n")
                f.write(text)

            done_count += 1
            print(f"[{i}/{len(queue)}] OK: {fname} ({info.duration:.0f}s audio in {elapsed:.0f}s wall)", flush=True)
        except Exception as e:
            print(f"[{i}/{len(queue)}] FAIL: {fname}: {e}", flush=True)
            traceback.print_exc()
            continue

        if done_count and done_count % 10 == 0:
            with open(PROGRESS_LOG, "a") as f:
                f.write(f"{datetime.now(timezone.utc).isoformat()} -- {done_count} новых транскриптов готово в этом запуске ({i}/{len(queue)} по очереди)\n")

    print("QUEUE COMPLETE", flush=True)

if __name__ == "__main__":
    main()
