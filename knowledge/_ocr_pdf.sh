#!/bin/bash
# Извлекает текст из PDF: сперва pdftotext (дёшево), если пусто -- OCR (pdftoppm+tesseract).
set -e
PDF="$1"
OUT="$2"
TXT=$(pdftotext "$PDF" - 2>/dev/null)
if [ "${#TXT}" -gt 200 ]; then
  echo "$TXT" > "$OUT"
  echo "TEXT_LAYER"
  exit 0
fi
TMPDIR=$(mktemp -d)
pdftoppm -png -r 150 "$PDF" "$TMPDIR/page" 2>/dev/null
> "$OUT"
for img in "$TMPDIR"/page-*.png; do
  tesseract "$img" - -l rus+eng 2>/dev/null >> "$OUT" || true
  echo "" >> "$OUT"
done
rm -rf "$TMPDIR"
echo "OCR"
