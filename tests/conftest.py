"""
Глобальные pytest-фикстуры проекта. Автоиспользуемая (autouse) изоляция
модульного состояния, которое иначе "утекало" бы между тестами разных
файлов (все тесты выполняются в одном процессе -- module-level переменные
персистентны на весь прогон pytest, если их явно не сбрасывать).
"""
import pytest


@pytest.fixture(autouse=True)
def _reset_shadow_engine_sync_gate():
    """Батчинг shadow-sync коммитов (владелец, ДА, 2026-07-15, окно 60-120с):
    shadow_engine._sync_to_github_sync() теперь молча пропускает реальный
    GET+PUT, если с прошлой попытки прошло меньше GITHUB_SYNC_MIN_INTERVAL_SEC
    (module-level _last_github_sync_attempt_ts). Без сброса между тестами
    первый тест в прогоне "тратил" гейт, и все последующие тесты (в ЛЮБОМ
    файле, не только shadow-специфичном) получали бы False/skip вместо
    настоящего сетевого мок-вызова, который они проверяют."""
    import shadow_engine
    shadow_engine._last_github_sync_attempt_ts = 0.0
    yield
    shadow_engine._last_github_sync_attempt_ts = 0.0
