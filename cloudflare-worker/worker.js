/**
 * Fitness bot webhook relay + proactive rest-timer cron.
 *
 * fetch(): Telegram шлёт сюда POST при каждом новом сообщении/
 * callback_query (настроено через setWebhook).
 *
 * 28.07.2026 ФИКС: изначально этот Worker дёргал workflow_dispatch,
 * а bot.py заново читал сообщение через getUpdates. Оказалось — это
 * не работает: getUpdates и активный webhook взаимоисключающи в
 * Telegram API (апдейты уходят либо туда, либо туда, не в оба места
 * сразу). Первое реальное сообщение прошло всю цепочку, но bot.py
 * внутри не увидел его — offset не двигался, бот молчал.
 *
 * Исправлено: Worker теперь передаёт САМО ТЕЛО update через
 * repository_dispatch client_payload — bot.py читает апдейт прямо
 * оттуда (переменная окружения TELEGRAM_UPDATE_JSON), не делает
 * повторный сетевой запрос к Telegram вообще.
 *
 * scheduled(): проактивный таймер отдыха. Cloudflare Cron Triggers не
 * поддерживают чаще 1 раза в минуту (минимальная гранулярность) —
 * Durable Objects дали бы точность до 30 сек, но требуют деплоя через
 * wrangler CLI, которого нет в нашей инфраструктуре (только телефон/
 * браузер, без Node.js). Компромисс: раз в минуту, точность ±30-60 сек,
 * достаточно для отдыха 45-90 сек между подходами. Сам Worker не решает,
 * истёк ли реально таймер — просто "будит" GitHub Actions каждую
 * минуту через cron_ping, вся логика в timer.py.
 */

const GITHUB_OWNER = "antonsergeevichzaytsev-cell";
const GITHUB_REPO = "fitness-bot";

async function dispatchToGitHub(env, eventType, clientPayload) {
  const dispatchUrl = `https://api.github.com/repos/${GITHUB_OWNER}/${GITHUB_REPO}/dispatches`;
  const resp = await fetch(dispatchUrl, {
    method: "POST",
    headers: {
      "Authorization": `Bearer ${env.GITHUB_TOKEN}`,
      "Accept": "application/vnd.github+json",
      "Content-Type": "application/json",
      "User-Agent": "fitness-bot-worker",
    },
    body: JSON.stringify({ event_type: eventType, client_payload: clientPayload }),
  });
  if (!resp.ok) {
    const body = await resp.text();
    console.log(`repository_dispatch (${eventType}) failed: ${resp.status} ${body}`);
  }
  return resp;
}

export default {
  async fetch(request, env) {
    if (request.method !== "POST") {
      return new Response("ok", { status: 200 });
    }

    const receivedSecret = request.headers.get("X-Telegram-Bot-Api-Secret-Token");
    if (receivedSecret !== env.TELEGRAM_SECRET_TOKEN) {
      return new Response("forbidden", { status: 403 });
    }

    let update;
    try {
      update = await request.json();
    } catch (e) {
      console.log(`failed to parse Telegram update body: ${e}`);
      return new Response("ok", { status: 200 });
    }

    // Telegram ретраит webhook при не-2xx ответе — намеренно всегда
    // возвращаем 200, даже если dispatchToGitHub залогировал сбой
    // внутри себя, чтобы Telegram не забомбардировал повторами один
    // неудачный webhook. Ошибка видна в Worker logs.
    await dispatchToGitHub(env, "telegram_update", { update });
    return new Response("ok", { status: 200 });
  },

  async scheduled(controller, env, ctx) {
    ctx.waitUntil(dispatchToGitHub(env, "cron_ping", {}));
  },
};
