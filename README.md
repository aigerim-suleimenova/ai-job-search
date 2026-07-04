# AI Job Search

Локальный агент поиска вакансий. Веб-страница настроек, сбор вакансий из ATS-платформ,
агрегаторов и careers-страниц компаний, оценка совпадения через **Claude Code CLI**,
советы по правкам CV и LinkedIn, дайджест в Telegram.

## Запуск

```bash
./run.sh
```

Откройте http://127.0.0.1:8765 — там всё настраивается: профиль, CV, локации,
порог совпадения, источники, Telegram и расписание.

Требования: Python 3.10+, установленный и залогиненный [Claude Code CLI](https://claude.com/claude-code)
(команда `claude` должна работать в терминале).

## Как это работает

1. **Сбор.** Компании из вашего списка: если URL указывает на ATS
   (Greenhouse, Lever, Ashby, Workable, SmartRecruiters, Recruitee, Personio) —
   вакансии забираются через публичный API; иначе страница краулится и вакансии
   извлекает Claude. Плюс агрегаторы: Remotive, Arbeitnow, HN Who is Hiring,
   опционально Adzuna и Jooble (бесплатные ключи).
2. **Фильтры.** Локация (понимает «EU» и «USA» как регионы), стоп-слова,
   эвристика рекрутинговых агентств, дедупликация, отсев уже виденного (SQLite).
3. **Скоринг.** Дешёвый лексический отбор → LLM-триаж пачками (haiku) с процентом
   совпадения → глубокий разбор топ-N вакансий (модель по умолчанию): точный %,
   почему подходит, правки CV и LinkedIn, на что сделать упор в отклике.
4. **Дайджест.** Telegram-сообщение: сначала вакансии напрямую от компаний,
   затем агрегаторы/агентства. Полные детали — на странице «Результаты».

## Telegram

1. В Telegram напишите боту `@BotFather` команду `/newbot`, получите token.
2. Вставьте token в настройки, сохраните.
3. Напишите своему боту любое сообщение, нажмите «Определить chat id».
4. «Отправить тест» — проверка.

## Автозапуск при включении Mac (launchd)

Расписание внутри приложения работает, пока оно запущено. Чтобы приложение
поднималось само, создайте `~/Library/LaunchAgents/com.user.aijobsearch.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.user.aijobsearch</string>
  <key>ProgramArguments</key>
  <array><string>/Users/viktor/Projects/ai-job-search/run.sh</string></array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>/tmp/aijobsearch.log</string>
  <key>StandardErrorPath</key><string>/tmp/aijobsearch.log</string>
</dict>
</plist>
```

Затем: `launchctl load ~/Library/LaunchAgents/com.user.aijobsearch.plist`

## Данные

Всё локально, в `data/`: `config.json` (настройки, включая токены), `cv.*` и
`cv.txt` (загруженное CV и извлечённый текст), `jobs.db` (SQLite: виденные
вакансии и история прогонов). Наружу текст CV уходит только в Claude через
ваш Claude Code CLI и ни в какие другие сервисы.
