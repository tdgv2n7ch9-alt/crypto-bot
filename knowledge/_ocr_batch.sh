#!/bin/bash
cd ~/crypto-bot/Trading
SCRIPT_PATH=~/crypto-bot/knowledge/_ocr_pdf.sh
OUT_DIR=~/crypto-bot/knowledge/_ocr
mkdir -p "$OUT_DIR"

FILES=(
"Урок 1. Терминология..pdf"
"Урок 6. Liquidity..pdf"
"Урок 11. Ордерблок..pdf"
"Урок 2. Structure..pdf"
"Урок 4. Fibonacci..pdf"
"Урок 10. Market Cycles..pdf"
"Урок 13. SC..pdf"
"Урок 14. Imbalance..pdf"
"Урок 3. Закон силы..pdf"
"Урок 5. Opens..pdf"
"Урок 8. PO3..pdf"
"Урок 9. Wyckoff..pdf"
"11. Риск-менеджмент.pdf"
"RISK ASSESSMENT v2.pdf"
"24. Психология.pdf"
"PSYCHO.pdf"
"Психология трейдинга .pdf"
"Психология трейдинга.pdf"
"brett_stinbardzher_psihologiya_t.pdf"
"Добро_пожаловать_в_тильт_В_Могилат.pdf"
"FOMO.pdf"
"Золотые правила.pdf"
"Rules .pdf"
"Правило_3-3-3_Маркетинг_RU.pdf"
"mentorship roadmap.pdf"
"12_Практический_модуль_Демо_торговля.pdf"
"Trading Education.pdf"
"trading guide.pdf"
"trading guide 2.pdf"
"trading guide 3.pdf"
"trading guide 4.pdf"
"Методичка 2 new.pdf"
"Словарь .pdf"
"Булковский_энциклопедия_паттернов.pdf"
"Инвестиции и анализ..pdf"
"ICO IDO EIO рус.pdf"
"Фарминг.pdf"
"тестнеты и ноды.pdf"
"методическое-пособие.pdf"
"Фазы на рынке и тренды..pdf"
)

TOTAL=${#FILES[@]}
I=0
for f in "${FILES[@]}"; do
  I=$((I+1))
  SLUG=$(echo "$f" | sed 's/\.pdf$//' | tr -c '[:alnum:]а-яА-ЯёЁ' '_')
  OUT="$OUT_DIR/${SLUG}.txt"
  if [ -f "$OUT" ]; then
    echo "[$I/$TOTAL] SKIP (уже есть): $f"
    continue
  fi
  if [ ! -f "$f" ]; then
    echo "[$I/$TOTAL] MISSING: $f"
    continue
  fi
  MODE=$("$SCRIPT_PATH" "$f" "$OUT" 2>&1 | tail -1)
  echo "[$I/$TOTAL] $MODE: $f"
done
echo "BATCH_COMPLETE"
