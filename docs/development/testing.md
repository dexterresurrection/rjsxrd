# Тестирование

## Запуск тестов

```bash
cd source
pip install pytest pytest-cov pytest-asyncio pytest-xdist pytest-mock

pytest                       # Все тесты
pytest -v                    # Подробный вывод
pytest -n auto               # Параллельный запуск
```

### Фильтрация

```bash
pytest -m unit                # Быстрые unit-тесты (без сети)
pytest -m integration         # Интеграционные (требуют сеть)
pytest --cov=fetchers --cov=utils  # С отчётом о покрытии
```

## Структура тестов

Всего **554 теста** в 24 файлах (полный прогон ~35 с):

| Файл | Тестов | Область |
|------|--------|---------|
| `test_fetcher.py` | 16 | Загрузка, парсинг ответов, обработка ошибок |
| `test_file_utils.py` | 26+ | `extract_host_port`, `has_insecure_setting`, `deduplicate_configs`, `is_valid_vpn_config_url`, `filter_secure_configs` |
| `test_config_processor.py` | 45+ | `_try_decode_base64_content`, `prepare_config_content`, `_add_unique`, `write_configs_file`, пайплайн обработки |
| `test_simple_tester.py` | 25 | `extract_host_port` + `SimpleTester` (TCP-пинг) |
| `test_smart_eta.py` | 27 | SmartETA: 3-way estimate (window, global, EMA) + timeout floor |
| `test_telegram_proxy_scraper.py` | 27 | Извлечение MTProto и SOCKS5 из контента |
| `test_url_stats.py` | 11+ | `record_fetch`, `get_dead_urls`, персистентность |
| `test_security_filter.py` | 35+ | `has_insecure_setting`, все протоколы и edge-кейсы, 2022 key length validation |
| `test_yaml_converter.py` | 28 | Конвертация Clash/Surge YAML в VPN URL |
| `test_executor_cache.py` | 14+ | Пул тредов, shutdown, WSL-детекция |
| `test_ip_checker.py` | 14+ | Проверка IP, `_make_request` |
| `test_ip_verifier.py` | 6+ | env vars, TCP port polling |
| `test_logger.py` | 30+ | Логирование, уровни, формат |
| `test_process_registry.py` | 18+ | ProcessRegistry, cleanup, callbacks |
| `test_progress.py` | 6+ | tqdm-консолидация |
| `test_proxy_monitor.py` | 18+ | Мониторинг цепочек, stop-событие |
|| `test_xray_tester.py` | 5+ | Сигналы, startup-timeout |
|| `test_github_handler.py` | 22 | GitHub API, rate limits, 409 conflicts, batch |
|| `test_git_updater.py` | 26 | GitUpdater: init, pull, stage, commit, push, retry |

Покрытие: **46%** (5543 строки, ~3000 покрыто).

## Утилиты для ручного тестирования

Расположены в `source/scripts/`:

```bash
# Очистка URLS.txt от мёртвых ссылок (dry-run: без --apply ничего не удаляет)
python scripts/purge_dead_urls.py
python scripts/purge_dead_urls.py --apply

# Очистка по git timestamp (только GitHub URL)
python scripts/purge_stale_urls.py --days 60 --apply

# Анализ источников: топ по отдаче
python scripts/analyze_url_stats.py
python scripts/analyze_url_stats.py --top 30
python scripts/analyze_url_stats.py --dead  # только мёртвые

# Бенчмарк конфигов (TCP ping или Xray)
python scripts/benchmark_configs.py --mode tcp --count 500
python scripts/benchmark_configs.py --mode xray --count 200

# Тестирование Telegram-прокси
python scripts/test_telegram_proxies.py
```

## Локальное тестирование генератора

```bash
python main.py --dry-run
```

Выполняет все фазы, кроме загрузки в GitHub. Полезно для проверки изменений перед коммитом.

## GitHib Workflow

`.github/workflows/frequent_update.yml` — запуск каждые 2 дня в 00:00 UTC.
- Ubuntu latest
- 80 минут таймаут
- Флаги: `--use-git --no-proxy-check`
- Concurrency group для предотвращения overlapping
- Ручной запуск через `workflow_dispatch`
