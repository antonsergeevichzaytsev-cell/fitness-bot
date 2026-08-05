# Деплой Cloudflare Worker — вручную через дашборд

API Cloudflare недоступен из среды, где пишется этот код (egress
allowlist блокирует api.cloudflare.com) — деплой делается через
браузер, не автоматизирован.

## 1. Создать Worker

1. dash.cloudflare.com -> в левом меню **Workers & Pages**
2. **Create application** -> **Create Worker**
3. Имя: `fitness-bot-webhook` (или любое) -> **Deploy** (сначала
   деплоится дефолтный "Hello World", это нормально — код заменим
   следующим шагом)

## 2. Вставить код

1. Открой только что созданный Worker -> **Edit code** (или "Quick edit")
2. Удали весь дефолтный код
3. Вставь целиком содержимое `cloudflare-worker/worker.js` из этого
   репозитория
4. **Deploy** (кнопка сверху)

## 3. Секреты Worker'а

Worker -> **Settings** -> **Variables and Secrets** -> **Add variable**:

- `GITHUB_TOKEN` — Personal Access Token с правом `repo` (или минимум
  `workflow`), тип **Secret** (encrypt), не Text
- `TELEGRAM_SECRET_TOKEN` — любая случайная строка (например
  сгенерированная `openssl rand -hex 20`), тип **Secret**. Та же строка
  понадобится на шаге 4 при регистрации webhook в Telegram — они
  должны совпадать буквально.

**Save and deploy** после добавления обеих переменных.

## 4. Зарегистрировать webhook в Telegram

Открой в браузере (замени `<WORKER_URL>` на реальный URL Worker'а,
Cloudflare покажет его после деплоя — вида
`https://fitness-bot-webhook.<твой-субдомен>.workers.dev`, и
`<SECRET>` на ту же строку, что в TELEGRAM_SECRET_TOKEN):

```
https://api.telegram.org/bot<BOT_TOKEN>/setWebhook?url=<WORKER_URL>&secret_token=<SECRET>
```

Должно вернуть `{"ok":true,"result":true,"description":"Webhook was set"}`.

Проверить, что webhook встал: открой

```
https://api.telegram.org/bot<BOT_TOKEN>/getWebhookInfo
```

`url` должен быть равен `<WORKER_URL>`, `pending_update_count` — 0
(если недавно ничего не писали боту).

## Как это работает дальше

Telegram -> POST на Worker при каждом сообщении -> Worker проверяет
secret_token -> дёргает `workflow_dispatch` на `fitness_bot.yml` в
GitHub -> `bot.py` запускается, сам вызывает `getUpdates` (Telegram
хранит недоставленные апдейты некоторое время) -> обрабатывает как
обычно.

Если что-то в цепочке сломается — GitHub Actions run просто не
появится после сообщения в Telegram. Проверять: Actions -> Fitness
bot -> есть ли новый run с недавним временем.
