# git-hooks

Пакет SECURITY-HARDENING М2 (владелец "да"). `.git/hooks/` не версионируется git —
хуки живут здесь, в репозитории, и подключаются симлинком.

## Установка (один раз на машине)

```
ln -sf ../../scripts/git-hooks/pre-commit .git/hooks/pre-commit
chmod +x .git/hooks/pre-commit
```

На этой машине уже сделано. На новой машине/клоне — выполнить команду выше.

## pre-commit

Секрет-скан staged-изменений через `gitleaks protect --staged` перед каждым коммитом.
Если `gitleaks` не установлен локально (`brew install gitleaks`) — хук честно
предупреждает и пропускает коммит, не блокирует работу на машинах без gitleaks.

Живой тест (2026-07-13): коммит с явным `API_KEY = "<32-символьный hex>"` —
заблокирован. Чистый коммит — прошёл. См. `PROGRESS.md`, Пакет SECURITY-HARDENING М2.
