# S21 Peer-Review Notifier

Асинхронный Telegram-бот для студентов Школы 21 (кампус Ташкент).  
Мониторит внутренний календарь платформы и присылает уведомления о Пир-Ревью.

## Возможности

- Авторизация через `/login` с проверкой логина/пароля в Keycloak
- Пароли хранятся в SQLite **только** в зашифрованном виде (Fernet)
- Опрос GraphQL-календаря каждые 30 секунд (APScheduler)
- Три состояния слота: ожидание пира → запись → завершение
- Время в уведомлениях в часовом поясе **Asia/Tashkent** (например: `30 сентября, 13:30`)

## Структура

```
.
├── bot/
│   ├── core/          # config, утилиты времени
│   ├── database/      # SQLite (users, known_events_state)
│   ├── handlers/      # /start, /login, /logout, /help
│   └── services/      # crypto, s21_api, scheduler
├── main.py
├── .env.example
├── requirements.txt
└── .gitignore
```

## Быстрый старт

### 1. Python 3.10+

```bash
python3 --version
```

### 2. Виртуальное окружение и зависимости

```bash
cd "/path/to/S21 Reviewer Code"
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 3. Конфигурация

```bash
cp .env.example .env
```

Сгенерируй ключ шифрования:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Отредактируй `.env`:

```env
BOT_TOKEN=<токен от @BotFather>
ENCRYPTION_KEY=<сгенерированный Fernet-ключ>
DATABASE_PATH=data/bot.db
POLL_INTERVAL_SECONDS=30
SCHOOL_ID=bad03b39-ffd4-4217-9d24-65535fe1f293
```

### 4. Запуск

```bash
python main.py
```

В Telegram: `/start` → `/login`, затем пошагово введи логин и пароль.

## Команды бота

| Команда | Описание |
|---------|----------|
| `/start` | Выбор языка или главное меню для авторизованного пользователя |
| `/login` | Пошагово привязать аккаунт платформы |
| `/logout` | Удалить данные и остановить мониторинг |
| `/help` | Справка |

## Безопасность

1. Пароль **не** пишется в БД открытым текстом.
2. При `/login` сообщение с паролем по возможности удаляется.
3. Для опроса API пароль расшифровывается только в RAM, затем забывается.
4. Не коммить `.env` и `*.db` (уже в `.gitignore`).

## Уведомления

1. **Ожидание пира** — слот выставлен, `bookings` пуст  
2. **Новая запись** — появился booking (не `CANCELED`), с логином пира и Online/Offline  
3. **Завершение** — время прошло или статус `COMPLETED` / `CANCELED` / `VERIFIER_WAS_ABSENT` / `STUDENT_WAS_ABSENT`

Пустой слот создаётся только с датой и временем. Формат Онлайн/Офлайн
относится к записи на проверку. Кнопка смены формата у занятого слота
показывает подсказку: «Смена формата на онлайн пока в разработке».
В архиве нет подтверждённой GraphQL-мутации для этой операции.
Минимальная длительность создаваемого или изменяемого слота — 30 минут.

## Примечание по GraphQL

Запрос `calendarGetEvents` лежит в `bot/services/s21_api.py`.  
Если платформа изменит схему, скорректируй поля в `CALENDAR_GET_EVENTS_QUERY` — парсер уже устойчив к небольшим отличиям вложенности.
