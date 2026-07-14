"""Локализация: язык интерфейса (ru/en) и язык результатов ИИ (ru/en/de)."""

# Как называется язык в переключателях
UI_LANGS = {"ru": "Русский", "en": "English"}
OUTPUT_LANGS = {"ru": "Русский", "en": "English", "de": "Deutsch"}

# Инструкция модели, на каком языке писать результаты (оценки, правки, дайджест)
OUTPUT_INSTRUCTION = {
    "ru": "на русском языке",
    "en": "in English",
    "de": "auf Deutsch",
}


def out_lang(cfg: dict) -> str:
    return OUTPUT_INSTRUCTION.get(cfg.get("ui", {}).get("output_lang", "ru"), OUTPUT_INSTRUCTION["ru"])


# Строки интерфейса. Ключ → {ru, en}. Отсутствующий перевод падает на ru.
TR = {
    # общее / навигация
    "app_title": {"ru": "AI Job Search", "en": "AI Job Search"},
    "nav_settings": {"ru": "Настройки", "en": "Settings"},
    "nav_results": {"ru": "Результаты", "en": "Results"},
    "nav_coverage": {"ru": "Покрытие", "en": "Coverage"},
    "save": {"ru": "💾 Сохранить настройки", "en": "💾 Save settings"},
    "saved": {"ru": "Настройки сохранены", "en": "Settings saved"},

    # статус / запуск
    "status": {"ru": "Статус:", "en": "Status:"},
    "status_idle": {"ru": "ожидание", "en": "idle"},
    "status_running": {"ru": "идёт поиск —", "en": "searching —"},
    "next_run": {"ru": "следующий запуск по расписанию:", "en": "next scheduled run:"},
    "run_now": {"ru": "▶ Запустить поиск сейчас", "en": "▶ Run search now"},
    "run_started": {"ru": "Поиск запущен", "en": "Search started"},
    "run_already": {"ru": "Поиск уже идёт", "en": "Search already running"},

    # профиль
    "profile": {"ru": "👤 Профиль", "en": "👤 Profile"},
    "profile_from_cv": {"ru": "✨ Заполнить пустые поля из CV", "en": "✨ Fill empty fields from CV"},
    "profile_from_cv_hint": {"ru": "Сначала загрузите CV внизу страницы — Claude сам предложит роли, уровень и описание.",
                             "en": "Upload a CV at the bottom first — Claude will suggest roles, level and summary."},
    "about": {"ru": "О себе: знания, опыт, стек, достижения",
              "en": "About you: skills, experience, stack, achievements"},
    "about_ph": {"ru": "Например: 8 лет backend-разработки на Python и Go, высоконагруженные сервисы, Kubernetes, руководил командой из 5 человек...",
                 "en": "E.g.: 8 years of backend development in Python and Go, high-load services, Kubernetes, led a team of 5..."},
    "roles": {"ru": "Желаемые роли (через запятую)", "en": "Desired roles (comma-separated)"},
    "skills": {"ru": "Ключевые навыки / технологии (через запятую)", "en": "Key skills / technologies (comma-separated)"},
    "skills_ph": {"ru": "SAP PI/PO, Java, REST, integration architecture", "en": "SAP PI/PO, Java, REST, integration architecture"},
    "level": {"ru": "Уровень", "en": "Level"},
    "salary_exp": {"ru": "Зарплатные ожидания", "en": "Salary expectations"},
    "work_format": {"ru": "Формат работы", "en": "Work format"},
    "fmt_any": {"ru": "любой", "en": "any"},
    "fmt_remote": {"ru": "удалённо", "en": "remote"},
    "fmt_hybrid": {"ru": "гибрид", "en": "hybrid"},
    "fmt_onsite": {"ru": "офис", "en": "on-site"},
    "langs": {"ru": "Языки", "en": "Languages"},
    "visa_required": {"ru": "Нужна виза / спонсорство", "en": "Need visa / sponsorship"},
    "visa_note": {"ru": "Комментарий про визу", "en": "Visa note"},

    # поиск
    "search_params": {"ru": "🌍 Параметры поиска", "en": "🌍 Search parameters"},
    "locations": {"ru": "Локации (через запятую: EU, USA, город, страна)",
                  "en": "Locations (comma-separated: EU, USA, city, country)"},
    "threshold": {"ru": "Порог совпадения, %", "en": "Match threshold, %"},
    "match_priority": {"ru": "На что опираться при поиске", "en": "What to match on"},
    "prio_role": {"ru": "На роль/должность", "en": "Role / job title"},
    "prio_skills": {"ru": "На навыки (роль вторична)", "en": "Skills (role is secondary)"},
    "prio_both": {"ru": "И роль, и навыки", "en": "Both role and skills"},
    "prio_hint": {"ru": "«На навыки» — для тех, у кого стек шире роли (напр. SAP-консультант со знанием Java). Тогда подойдут и вакансии с другим названием должности, но под ваши навыки.",
                  "en": "“Skills” — for those whose stack is broader than the role (e.g. a SAP consultant who knows Java). Then jobs with a different title but matching your skills also count."},
    "kw_include": {"ru": "Ключевые слова — обязательно искать", "en": "Keywords — must include"},
    "kw_exclude": {"ru": "Стоп-слова — исключать", "en": "Stop-words — exclude"},
    "incl_remote": {"ru": "Учитывать удалённые вакансии", "en": "Include remote jobs"},
    "drop_off_target": {"ru": "Отсеивать явно нерелевантные роли (продажи, HR, саппорт) до LLM-оценки",
                        "en": "Filter out clearly non-matching roles (sales, HR, support) before LLM scoring"},
    "triage_limit": {"ru": "Предел вакансий на LLM-оценку за прогон", "en": "Max jobs for LLM scoring per run"},
    "deep_top_n": {"ru": "Глубокий разбор (правки CV) для топ-N", "en": "Deep analysis (CV edits) for top-N"},
    "parallelism": {"ru": "Параллельных LLM-вызовов (быстрее, но нагрузка выше)",
                    "en": "Parallel LLM calls (faster, higher load)"},
    "parallelism_hint": {"ru": "При параллельной оценке за один прогон успевают оцениться все новые вакансии, а не только первые несколько десятков — очередь не копится.",
                         "en": "With parallel scoring, a single run evaluates all new jobs, not just the first few dozen — no backlog builds up."},
    "research_company": {"ru": "Искать зарплату и факты о компании в интернете (Glassdoor, Kununu, levels.fyi) для вакансий на глубоком разборе",
                         "en": "Research salary and company facts online (Glassdoor, Kununu, levels.fyi) for deeply-analyzed jobs"},
    "research_hint": {"ru": "Заметно замедляет глубокий разбор (реальный веб-поиск на каждую вакансию), но даёт то, чего нет в самом объявлении. Если данных не нашлось — честно пишет «не найдено».",
                      "en": "Noticeably slows deep analysis (real web search per job), but surfaces info not in the posting. If nothing found — honestly says so."},

    # источники
    "sources": {"ru": "🏢 Источники", "en": "🏢 Sources"},
    "companies_label": {"ru": "Компании для прямого мониторинга — по одной на строку: Название | URL страницы вакансий.",
                        "en": "Companies to monitor directly — one per line: Name | careers page URL."},
    "discover_per_run": {"ru": "Искать новых компаний за прогон (веб-поиск через Claude, 0 = выключить)",
                         "en": "Discover new companies per run (web search via Claude, 0 = off)"},
    "discover_now": {"ru": "🔍 Найти компании сейчас", "en": "🔍 Find companies now"},
    "sources_hint": {"ru": "Найденные компании добавляются в список выше. Один веб-поиск даёт ~8 компаний; для большего значения делается несколько поисков — дольше, но охват растёт быстрее. Что реально просмотрено — на странице «Покрытие».",
                     "en": "Discovered companies are added to the list above. One web search yields ~8 companies; larger values run several searches — slower, but coverage grows faster. What was actually scanned — on the Coverage page."},

    # LLM
    "llm": {"ru": "🤖 LLM (Claude Code CLI)", "en": "🤖 LLM (Claude Code CLI)"},
    "claude_bin": {"ru": "Команда claude", "en": "claude command"},
    "triage_model": {"ru": "Модель для триажа (дёшево, много вакансий)", "en": "Triage model (cheap, many jobs)"},
    "deep_model": {"ru": "Модель для глубокого разбора (пусто = по умолчанию)", "en": "Deep-analysis model (blank = default)"},

    # telegram
    "telegram": {"ru": "📨 Telegram", "en": "📨 Telegram"},
    "bot_token": {"ru": "Bot token (от @BotFather)", "en": "Bot token (from @BotFather)"},
    "chat_id": {"ru": "Chat ID", "en": "Chat ID"},
    "tg_detect": {"ru": "💾 Сохранить и определить chat id", "en": "💾 Save and detect chat id"},
    "tg_test": {"ru": "💾 Сохранить и отправить тест", "en": "💾 Save and send test"},
    "tg_hint": {"ru": "Создайте бота через @BotFather (/newbot), вставьте token, напишите боту любое сообщение — и нажмите «Сохранить и определить chat id».",
                "en": "Create a bot via @BotFather (/newbot), paste the token, message the bot, then click “Save and detect chat id”."},

    # язык
    "language": {"ru": "🌐 Язык", "en": "🌐 Language"},
    "ui_lang": {"ru": "Язык интерфейса", "en": "Interface language"},
    "output_lang": {"ru": "Язык результатов (оценки, правки CV, дайджест)",
                    "en": "Results language (scores, CV edits, digest)"},
    "lang_hint": {"ru": "Язык результатов — на нём ИИ пишет оценки, правки CV/LinkedIn и дайджест. Можно, например, интерфейс на русском, а правки CV — на английском.",
                  "en": "Results language is what the AI writes scores, CV/LinkedIn edits and the digest in. You can keep the UI in one language and get CV edits in another."},

    # расписание
    "schedule": {"ru": "⏰ Расписание", "en": "⏰ Schedule"},
    "sched_mode": {"ru": "Режим", "en": "Mode"},
    "mode_off": {"ru": "Вручную", "en": "Manual"},
    "mode_interval": {"ru": "По интервалу", "en": "By interval"},
    "mode_continuous": {"ru": "Непрерывно (одну за другой)", "en": "Continuous (one after another)"},
    "sched_every": {"ru": "Каждые", "en": "Every"},
    "sched_unit": {"ru": "Единица", "en": "Unit"},
    "unit_hours": {"ru": "часов", "en": "hours"},
    "unit_days": {"ru": "дней", "en": "days"},
    "unit_weeks": {"ru": "недель", "en": "weeks"},
    "cooldown": {"ru": "Пауза между прогонами, мин", "en": "Pause between runs, min"},
    "sched_hint": {"ru": "Работает, пока приложение запущено. Непрерывный режим: как только прогон закончился, через паузу начинается следующий — охват растёт сам, заходите проверять когда удобно. Меньше пауза = чаще прогоны = больше расход токенов. Автозапуск при включении Mac — см. README (launchd).",
                   "en": "Works while the app is running. Continuous mode: as soon as a run finishes, the next starts after the pause — coverage grows on its own, check in whenever. Shorter pause = more runs = more token usage. Auto-start on boot — see README (launchd)."},
    "status_continuous": {"ru": "непрерывный режим", "en": "continuous mode"},

    # CV
    "cv": {"ru": "📄 CV", "en": "📄 CV"},
    "cv_loaded": {"ru": "Загружено:", "en": "Loaded:"},
    "cv_none": {"ru": "CV ещё не загружено. Подойдёт PDF, TXT или MD. Туда же можно добавить экспорт LinkedIn-профиля.",
                "en": "No CV uploaded yet. PDF, TXT or MD work. You can also add a LinkedIn profile export."},
    "cv_chars": {"ru": "символов извлечено", "en": "characters extracted"},
    "upload": {"ru": "Загрузить", "en": "Upload"},

    # прогоны
    "recent_runs": {"ru": "🗂 Последние прогоны", "en": "🗂 Recent runs"},
    "col_start": {"ru": "Начало", "en": "Started"},
    "col_finish": {"ru": "Конец", "en": "Finished"},
    "col_found": {"ru": "Собрано", "en": "Collected"},
    "col_fresh": {"ru": "Новых", "en": "New"},
    "col_matched": {"ru": "Подошло", "en": "Matched"},
    "col_status": {"ru": "Статус", "en": "Status"},

    # результаты
    "results_shown": {"ru": "Показаны вакансии с оценкой ≥", "en": "Showing jobs with score ≥"},
    "export_csv": {"ru": "⤓ CSV (таблица)", "en": "⤓ CSV (spreadsheet)"},
    "export_report": {"ru": "⤓ Отчёт (HTML/PDF)", "en": "⤓ Report (HTML/PDF)"},
    "export_hint": {"ru": "Выгрузить показанные вакансии для кандидата: CSV — для отслеживания откликов, отчёт — открыть и распечатать в PDF.",
                    "en": "Export the shown jobs for the candidate: CSV for tracking applications, report to open and print to PDF."},
    "badge_verified": {"ru": "проверено", "en": "verified"},
    "badge_preliminary": {"ru": "предварительно", "en": "preliminary"},
    "verified_hint": {"ru": "«проверено» — точная оценка глубоким разбором; «предварительно» — быстрый триаж (обычно завышает).",
                      "en": "“verified” — precise deep-analysis score; “preliminary” — quick triage (usually optimistic)."},
    "suggest_banner": {"ru": "Выше порога {cur}% почти ничего. Ваш реальный диапазон ниже — покажите вакансии от {sugg}%:",
                       "en": "Almost nothing above {cur}%. Your realistic range is lower — show jobs from {sugg}%:"},
    "suggest_show": {"ru": "показать от {sugg}%", "en": "show from {sugg}%"},
    "suggest_apply": {"ru": "сделать {sugg}% порогом дайджеста", "en": "make {sugg}% the digest threshold"},
    "threshold_set": {"ru": "Порог дайджеста изменён на {sugg}%", "en": "Digest threshold set to {sugg}%"},
    "results_none": {"ru": "Ничего с такой оценкой нет. Попробуйте фильтр «все» или запустите поиск.",
                     "en": "Nothing at this score. Try the “all” filter or run a search."},
    "filter_all": {"ru": "все", "en": "all"},
    "badge_direct": {"ru": "напрямую от компании", "en": "directly from company"},
    "badge_agency": {"ru": "похоже на агентство", "en": "looks like an agency"},
    "badge_aggregator": {"ru": "агрегатор", "en": "aggregator"},
    "source": {"ru": "источник:", "en": "source:"},
    "run": {"ru": "прогон", "en": "run"},
    "loc_unknown": {"ru": "локация не указана", "en": "location not specified"},
    "salary_block": {"ru": "💰 Зарплата и факты о компании (веб-поиск, проверяйте по ссылкам)",
                     "en": "💰 Salary and company facts (web search, verify via links)"},
    "salary_label": {"ru": "Зарплата:", "en": "Salary:"},
    "sources_label": {"ru": "Источники:", "en": "Sources:"},
    "edits_block": {"ru": "Правки под эту вакансию", "en": "Edits for this job"},
    "cover_hint_label": {"ru": "✉️ В отклике:", "en": "✉️ In your application:"},
    "job_description": {"ru": "Описание вакансии", "en": "Job description"},
    "cv_button": {"ru": "📄 CV под вакансию", "en": "📄 Tailored CV"},
    "cv_button_hint": {"ru": "Сгенерировать адаптированное резюме под эту вакансию (откроется в новой вкладке, печатается в PDF). Первый раз занимает 1–2 минуты.",
                       "en": "Generate a CV tailored to this job (opens in a new tab, printable to PDF). First time takes 1–2 minutes."},
    "run_journal": {"ru": "Журнал последнего прогона", "en": "Last run log"},

    # дайджест Telegram (следует языку результатов output_lang: ru/en/de)
    "dg_none": {"ru": "🔍 Новых вакансий с совпадением ≥ {t}% не найдено.",
                "en": "🔍 No new jobs with match ≥ {t}% found.",
                "de": "🔍 Keine neuen Stellen mit Übereinstimmung ≥ {t}% gefunden."},
    "dg_near_only": {"ru": "🔍 Выше порога {t}% ничего, но были близкие варианты (оценка ниже порога):",
                     "en": "🔍 Nothing above {t}%, but there were close matches (below threshold):",
                     "de": "🔍 Nichts über {t}%, aber es gab knappe Treffer (unter der Schwelle):"},
    "dg_details": {"ru": "Подробности: {u}/results", "en": "Details: {u}/results", "de": "Details: {u}/results"},
    "dg_new_jobs": {"ru": "🎯 Новые вакансии: {n} (порог {t}%)",
                    "en": "🎯 New jobs: {n} (threshold {t}%)",
                    "de": "🎯 Neue Stellen: {n} (Schwelle {t}%)"},
    "dg_direct": {"ru": "🏢 Напрямую от компаний", "en": "🏢 Directly from companies", "de": "🏢 Direkt von Unternehmen"},
    "dg_rest": {"ru": "🤝 Агрегаторы и агентства", "en": "🤝 Aggregators and agencies", "de": "🤝 Aggregatoren und Agenturen"},
    "dg_below": {"ru": "🔻 Близко, но оценка ниже порога:", "en": "🔻 Close, but below threshold:",
                 "de": "🔻 Knapp, aber unter der Schwelle:"},
    "dg_loc_unknown": {"ru": "локация не указана", "en": "location not specified", "de": "Ort nicht angegeben"},
    "dg_cv": {"ru": "📝 CV:", "en": "📝 CV:", "de": "📝 CV:"},
    "dg_more": {"ru": "Подробности и правки LinkedIn: {u}/results",
                "en": "Details and LinkedIn edits: {u}/results",
                "de": "Details und LinkedIn-Anpassungen: {u}/results"},

    # страница «Покрытие»
    "cov_check_title": {"ru": "🔬 Проверить компанию: видит ли её скрипт?",
                        "en": "🔬 Check a company: can the script see it?"},
    "cov_check_hint": {"ru": "Вставьте компании, которые, по-вашему, должны быть в выдаче — по одной на строку. Можно название, ссылку на careers-страницу, или Название | URL. До 20 за раз.",
                       "en": "Paste companies you think should appear — one per line. Name, careers-page URL, or Name | URL. Up to 20 at once."},
    "cov_check_btn": {"ru": "Проверить покрытие", "en": "Check coverage"},
    "cov_col_company": {"ru": "Компания", "en": "Company"},
    "cov_col_jobs": {"ru": "Вакансий", "en": "Jobs"},
    "cov_col_how": {"ru": "Как читается", "en": "How it's read"},
    "cov_visible": {"ru": "✓ видим", "en": "✓ visible"},
    "cov_notfound": {"ru": "✗ не найдено", "en": "✗ not found"},
    "cov_zero": {"ru": "0 вакансий (JS-страница без API, либо нет открытых позиций)",
                 "en": "0 jobs (JS page without API, or no open positions)"},
    "cov_monitored": {"ru": "в мониторинге", "en": "monitored"},
    "cov_by_search": {"ru": "ссылка найдена поиском", "en": "URL found by search"},
    "cov_add": {"ru": "+ в мониторинг", "en": "+ monitor"},
    "cov_check_note": {"ru": "«✓ видим» — вакансии этой компании попадают в оценку. Кнопкой «+ в мониторинг» добавьте компанию в постоянный список.",
                       "en": "“✓ visible” means this company's jobs enter scoring. Use “+ monitor” to add it to the permanent list."},
    "cov_scanned": {"ru": "Что просмотрено в прогоне", "en": "What was scanned in run"},
    "cov_sources_n": {"ru": "Источников:", "en": "Sources:"},
    "cov_jobs_collected": {"ru": "вакансий собрано:", "en": "jobs collected:"},
    "cov_other_runs": {"ru": "другие прогоны:", "en": "other runs:"},
    "cov_col_source": {"ru": "Источник", "en": "Source"},
    "cov_col_type": {"ru": "Тип", "en": "Type"},
    "cov_col_found": {"ru": "Найдено", "en": "Found"},
    "cov_empty": {"ru": "пусто", "en": "empty"},
    "cov_ok": {"ru": "ок", "en": "ok"},
    "cov_no_runs": {"ru": "Ещё не было ни одного прогона.", "en": "No runs yet."},
    "cov_honest_title": {"ru": "Честно про охват", "en": "Honestly about coverage"},
    "cov_honest_1": {"ru": "Скрипт <b>не просматривает все компании мира</b> — их миллионы, и этого не делает ни один сервис (даже LinkedIn и Indeed видят лишь часть). Покрытие складывается из трёх потоков:",
                     "en": "The script <b>does not scan every company in the world</b> — there are millions, and no service does (even LinkedIn and Indeed see only a fraction). Coverage comes from three streams:"},
    "cov_honest_agg": {"ru": "<b>Агрегаторы</b> (Remotive, Arbeitnow, WeWorkRemotely, HN) — широкий, но неполный поток.",
                       "en": "<b>Aggregators</b> (Remotive, Arbeitnow, WeWorkRemotely, HN) — broad but incomplete."},
    "cov_honest_list": {"ru": "<b>Компании из вашего списка</b> — читаются напрямую через ATS-API или краулинг, полностью.",
                        "en": "<b>Companies on your list</b> — read directly via ATS API or crawling, in full."},
    "cov_honest_disc": {"ru": "<b>Автопоиск</b> — каждый прогон находит новые компании под ваш профиль и добавляет их.",
                        "en": "<b>Auto-discovery</b> — each run finds new companies for your profile and adds them."},
    "cov_honest_2": {"ru": "Признак, что до «всех» далеко: автопоиск <b>каждый прогон находит ранее неизвестные компании</b> — поток не насыщен. Лучший способ проверить конкретную — форма выше.",
                     "en": "Sign we're far from “all”: auto-discovery <b>finds previously unknown companies every run</b> — the stream isn't saturated. Best way to check a specific one is the form above."},

    # профили (несколько людей)
    "person": {"ru": "Человек", "en": "Person"},
    "add_person": {"ru": "+ Добавить человека", "en": "+ Add person"},
    "new_person_name": {"ru": "Имя нового человека", "en": "New person's name"},
    "create": {"ru": "Создать", "en": "Create"},
    "rename_person": {"ru": "Переименовать", "en": "Rename"},
    "delete_person": {"ru": "Удалить этого человека", "en": "Delete this person"},
    "delete_confirm": {"ru": "Удалить профиль и все его данные (CV, найденные вакансии)? Отменить нельзя.",
                       "en": "Delete this profile and all its data (CV, found jobs)? This cannot be undone."},
    "manage_people": {"ru": "👥 Люди", "en": "👥 People"},
    "manage_hint": {"ru": "Каждый человек — свой профиль: CV, настройки, компании, результаты и расписание. Переключайтесь наверху страницы.",
                    "en": "Each person is their own profile: CV, settings, companies, results and schedule. Switch at the top of the page."},
}


def t(lang: str, key: str) -> str:
    entry = TR.get(key)
    if not entry:
        return key
    return entry.get(lang) or entry.get("ru") or key
