/**
 * Fitness bot webhook relay.
 *
 * Telegram шлёт сюда POST при каждом новом сообщении/callback_query
 * (настроено через setWebhook).
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
 */

const GITHUB_OWNER = "antonsergeevichzaytsev-cell";
const GITHUB_REPO = "fitness-bot";

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

    const dispatchUrl = `https://api.github.com/repos/${GITHUB_OWNER}/${GITHUB_REPO}/dispatches`;

    const resp = await fetch(dispatchUrl, {
      method: "POST",
      headers: {
        "Authorization": `Bearer ${env.GITHUB_TOKEN}`,
        "Accept": "application/vnd.github+json",
        "Content-Type": "application/json",
        "User-Agent": "fitness-bot-worker",
      },
      body: JSON.stringify({
        event_type: "telegram_update",
        client_payload: { update },
      }),
    });

    if (!resp.ok) {
      const body = await resp.text();
      console.log(`repository_dispatch failed: ${resp.status} ${body}`);
      // Telegram ретраит webhook при не-2xx ответе — намеренно
      // возвращаем 200 даже при сбое dispatch, чтобы не забомбардировал
      // повторами один неудачный webhook. Ошибка видна в Worker logs.
    }

    return new Response("ok", { status: 200 });
  },
};
