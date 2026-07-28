/**
 * Fitness bot webhook relay.
 *
 * Telegram шлёт сюда POST при каждом новом сообщении/callback_query
 * (настроено через setWebhook). Worker НЕ обрабатывает сам текст —
 * вся логика (parser.py, workouts.py, progression.py, session.py)
 * остаётся в GitHub-репозитории, уже написана и протестирована (92
 * теста). Worker — тонкий прокси: получил webhook -> дёрнул
 * workflow_dispatch на bot.py -> тот сам заново читает то же
 * сообщение через getUpdates (Telegram хранит недоставленные апдейты
 * some время) и обрабатывает как обычно.
 *
 * Секреты (GITHUB_TOKEN, TELEGRAM_SECRET_TOKEN) хранятся в Cloudflare
 * Worker Secrets (Settings -> Variables -> Encrypt), не в этом файле.
 */

const GITHUB_OWNER = "antonsergeevichzaytsev-cell";
const GITHUB_REPO = "fitness-bot";
const WORKFLOW_FILE = "fitness_bot.yml";

export default {
  async fetch(request, env) {
    if (request.method !== "POST") {
      return new Response("ok", { status: 200 });
    }

    // Telegram позволяет задать secret_token при setWebhook — Telegram
    // присылает его в заголовке X-Telegram-Bot-Api-Secret-Token на
    // КАЖДОМ запросе. Проверка защищает от того, что кто-то посторонний
    // найдёт URL Worker'а и начнёт слать поддельные "апдейты", которые
    // без этой проверки дёргали бы GitHub Actions вхолостую (и жгли
    // бы минуты CI на чужие запросы).
    const receivedSecret = request.headers.get("X-Telegram-Bot-Api-Secret-Token");
    if (receivedSecret !== env.TELEGRAM_SECRET_TOKEN) {
      return new Response("forbidden", { status: 403 });
    }

    const dispatchUrl = `https://api.github.com/repos/${GITHUB_OWNER}/${GITHUB_REPO}/actions/workflows/${WORKFLOW_FILE}/dispatches`;

    const resp = await fetch(dispatchUrl, {
      method: "POST",
      headers: {
        "Authorization": `Bearer ${env.GITHUB_TOKEN}`,
        "Accept": "application/vnd.github+json",
        "Content-Type": "application/json",
        "User-Agent": "fitness-bot-worker",
      },
      body: JSON.stringify({ ref: "main" }),
    });

    if (!resp.ok) {
      const body = await resp.text();
      console.log(`workflow_dispatch failed: ${resp.status} ${body}`);
      // Telegram ретраит webhook, если получает не-2xx — намеренно
      // возвращаем 200 даже при сбое dispatch, чтобы Telegram не
      // забомбардировал повторами один и тот же неудачный webhook.
      // Ошибка видна в Worker logs (console.log выше), не теряется молча.
    }

    return new Response("ok", { status: 200 });
  },
};
