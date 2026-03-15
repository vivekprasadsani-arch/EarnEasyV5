#!/usr/bin/env node

import axios from "axios";
import crypto from "node:crypto";
import { google } from "googleapis";
import http from "node:http";
import path from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";
import { setTimeout as sleep } from "node:timers/promises";
import { HttpsProxyAgent } from "https-proxy-agent";
import { SocksProxyAgent } from "socks-proxy-agent";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const GOOGLE_CREDENTIALS_FILE =
  process.env.GOOGLE_CREDENTIALS_FILE ??
  path.join(__dirname, "oasis-numbers-465216-a9028800962f.json");
const GOOGLE_CREDENTIALS_JSON = process.env.GOOGLE_CREDENTIALS_JSON ?? null;
const GOOGLE_SPREADSHEET_ID = extractSpreadsheetId(
  process.env.GOOGLE_SPREADSHEET_ID ??
    "https://docs.google.com/spreadsheets/d/1WzGkrTgEjchqgC2_ZImeyd-mFoiCobmU-MmTG3KXzDQ/edit",
);

const TELEGRAM_BOT_TOKEN = process.env.EARNHEAP_BOT_TOKEN ?? "";
const ADMIN_USER_ID = String(
  process.env.EARNHEAP_ADMIN_USER_ID ?? "5079903193",
);
const DEFAULT_PASSWORD =
  process.env.EARNHEAP_DEFAULT_PASSWORD ?? "53561106";
const DEFAULT_INVITATION_CODE =
  process.env.EARNHEAP_DEFAULT_INVITATION_CODE ?? "51607760";

const API_BASE = "https://clserver.earnheap.cc";
const WEB_ORIGIN = "https://web.earnheap.com";
const CHANNEL = "0";
const AES_KEY = "G7d9kLm2QpXz4vT1";
const AES_IV = "1234567890abcdef";
const USERCOMPRE = 'EarnHeap/1.0.1 ({"platform": "web"})';

const LINK_TIMEOUT_MS = 5 * 60 * 1000;
const LINK_POLL_INTERVAL_MS = 5 * 1000;
const SHEETS_FLUSH_DEBOUNCE_MS = 5000;
const SHEETS_FLUSH_RETRY_MS = 15000;
const HEALTH_PORT = Number(process.env.PORT ?? 10000);

const activeJobs = new Map();
const actorQueues = new Map();
let state = null;
let sheetsApi = null;
let sheetsInitPromise = null;
let saveQueue = Promise.resolve();
let flushTimer = null;
let flushPendingPromise = null;
let dirtySheets = new Set();
let healthServer = null;

const SHEET_TABLES = {
  meta: { title: "meta", keyColumn: "key" },
  users: { title: "users", keyColumn: "id" },
  pendingRequests: { title: "pending_requests", keyColumn: "id" },
  conversations: { title: "conversations", keyColumn: "user_id" },
  jobs: { title: "link_jobs", keyColumn: "id" },
};

function extractSpreadsheetId(input) {
  if (!input) {
    throw new Error("Missing Google Spreadsheet ID.");
  }
  const match = String(input).match(/\/spreadsheets\/d\/([a-zA-Z0-9-_]+)/);
  return match ? match[1] : String(input);
}

function startHealthServer() {
  if (healthServer) return healthServer;
  healthServer = http.createServer((req, res) => {
    const body = JSON.stringify({
      ok: true,
      service: "telegram-bot",
      startedAt: process.uptime(),
    });
    res.writeHead(200, {
      "content-type": "application/json; charset=utf-8",
      "content-length": Buffer.byteLength(body),
    });
    res.end(body);
  });
  healthServer.listen(HEALTH_PORT, "0.0.0.0");
  return healthServer;
}

async function getSheetsApi() {
  if (sheetsApi) return sheetsApi;
  const authOptions = {
    scopes: ["https://www.googleapis.com/auth/spreadsheets"],
  };
  if (GOOGLE_CREDENTIALS_JSON) {
    authOptions.credentials = JSON.parse(GOOGLE_CREDENTIALS_JSON);
  } else {
    authOptions.keyFile = GOOGLE_CREDENTIALS_FILE;
  }
  const auth = new google.auth.GoogleAuth(authOptions);
  sheetsApi = google.sheets({ version: "v4", auth });
  return sheetsApi;
}

async function ensureSheetsStorage() {
  if (sheetsInitPromise) return sheetsInitPromise;
  sheetsInitPromise = (async () => {
    const api = await getSheetsApi();
    const meta = await api.spreadsheets.get({
      spreadsheetId: GOOGLE_SPREADSHEET_ID,
    });
    const existing = meta.data.sheets ?? [];
    const desiredTitles = Object.values(SHEET_TABLES).map((table) => table.title);
    const existingByTitle = new Map(
      existing.map((sheet) => [sheet.properties?.title, sheet.properties]),
    );
    const requests = [];

    if (!existingByTitle.has(desiredTitles[0]) && existing.length > 0) {
      const first = existing[0].properties;
      if (first?.sheetId != null && !desiredTitles.includes(first.title)) {
        requests.push({
          updateSheetProperties: {
            properties: {
              sheetId: first.sheetId,
              title: desiredTitles[0],
            },
            fields: "title",
          },
        });
        existingByTitle.set(desiredTitles[0], {
          ...first,
          title: desiredTitles[0],
        });
        existingByTitle.delete(first.title);
      }
    }

    for (const title of desiredTitles) {
      if (existingByTitle.has(title)) continue;
      requests.push({
        addSheet: {
          properties: {
            title,
          },
        },
      });
    }

    if (requests.length > 0) {
      await api.spreadsheets.batchUpdate({
        spreadsheetId: GOOGLE_SPREADSHEET_ID,
        requestBody: { requests },
      });
    }
  })();

  return sheetsInitPromise;
}

function parseSheetObjects(values) {
  if (!values || values.length === 0) return [];
  const headers = values[0];
  return values.slice(1).filter((row) => row.some((cell) => cell !== "")).map((row) => {
    const out = {};
    for (let i = 0; i < headers.length; i += 1) {
      out[headers[i]] = row[i] ?? "";
    }
    return out;
  });
}

async function getSheetValues(title) {
  await ensureSheetsStorage();
  const api = await getSheetsApi();
  const res = await api.spreadsheets.values.get({
    spreadsheetId: GOOGLE_SPREADSHEET_ID,
    range: `'${title}'!A:ZZ`,
  });
  return res.data.values ?? [];
}

async function clearSheet(title) {
  await ensureSheetsStorage();
  const api = await getSheetsApi();
  await api.spreadsheets.values.clear({
    spreadsheetId: GOOGLE_SPREADSHEET_ID,
    range: `'${title}'!A:ZZ`,
  });
}

async function writeSheet(title, values) {
  await ensureSheetsStorage();
  const api = await getSheetsApi();
  await api.spreadsheets.values.update({
    spreadsheetId: GOOGLE_SPREADSHEET_ID,
    range: `'${title}'!A1`,
    valueInputOption: "RAW",
    requestBody: { values },
  });
}

async function loadMetaSheet() {
  const rows = parseSheetObjects(await getSheetValues(SHEET_TABLES.meta.title));
  const out = {};
  for (const row of rows) {
    if (!row.key) continue;
    try {
      out[row.key] = JSON.parse(row.value ?? "null");
    } catch {
      out[row.key] = row.value ?? null;
    }
  }
  return out;
}

async function loadJsonSheet(title, keyColumn) {
  const rows = parseSheetObjects(await getSheetValues(title));
  const out = {};
  for (const row of rows) {
    const key = row[keyColumn];
    if (!key) continue;
    try {
      out[String(key)] = JSON.parse(row.data ?? "{}");
    } catch {
      out[String(key)] = {};
    }
  }
  return out;
}

function buildMetaValues(snapshot) {
  return [
    ["key", "value"],
    ["settings", JSON.stringify(snapshot.settings)],
    ["lastUpdateId", JSON.stringify(snapshot.lastUpdateId ?? 0)],
  ];
}

function buildJsonTableValues(keyColumn, records) {
  const values = [[keyColumn, "data"]];
  for (const [key, value] of Object.entries(records)) {
    values.push([String(key), JSON.stringify(value)]);
  }
  return values;
}

function enqueueSave(work) {
  saveQueue = saveQueue.catch(() => {}).then(work);
  return saveQueue;
}

async function writeTableSnapshot(sheetKey, snapshot) {
  if (sheetKey === "meta") {
    await clearSheet(SHEET_TABLES.meta.title);
    await writeSheet(SHEET_TABLES.meta.title, buildMetaValues(snapshot));
    return;
  }

  const table = SHEET_TABLES[sheetKey];
  const records = snapshot[sheetKey] ?? {};
  await clearSheet(table.title);
  await writeSheet(
    table.title,
    buildJsonTableValues(table.keyColumn, records),
  );
}

function scheduleSheetsFlush(delayMs = SHEETS_FLUSH_DEBOUNCE_MS) {
  if (flushTimer) {
    return flushPendingPromise ?? Promise.resolve();
  }

  flushPendingPromise = new Promise((resolve) => {
    flushTimer = setTimeout(() => {
      flushTimer = null;
      resolve(
        enqueueSave(async () => {
          if (dirtySheets.size === 0) return;
          const pendingKeys = Array.from(dirtySheets);
          dirtySheets = new Set();
          const snapshot = normalizeState(state);

          try {
            for (const sheetKey of pendingKeys) {
              await writeTableSnapshot(sheetKey, snapshot);
            }
          } catch (error) {
            for (const sheetKey of pendingKeys) {
              dirtySheets.add(sheetKey);
            }
            console.error(`Sheets flush failed: ${error.message}`);
            scheduleSheetsFlush(SHEETS_FLUSH_RETRY_MS);
          }
        }),
      );
      flushPendingPromise = null;
    }, delayMs);
  });

  return flushPendingPromise;
}

async function flushSheetsNow() {
  if (flushTimer) {
    clearTimeout(flushTimer);
    flushTimer = null;
    flushPendingPromise = null;
  }
  if (dirtySheets.size === 0) return;
  const pendingKeys = Array.from(dirtySheets);
  dirtySheets = new Set();
  const snapshot = normalizeState(state);

  await enqueueSave(async () => {
    for (const sheetKey of pendingKeys) {
      await writeTableSnapshot(sheetKey, snapshot);
    }
  });
}

function defaultState() {
  return {
    settings: {
      defaultPassword: DEFAULT_PASSWORD,
      defaultInvitationCode: DEFAULT_INVITATION_CODE,
      earnHeapProxyUrl: null,
    },
    lastUpdateId: 0,
    users: {},
    pendingRequests: {},
    conversations: {},
    jobs: {},
  };
}

function normalizeState(raw) {
  const base = defaultState();
  const next = raw && typeof raw === "object" ? raw : {};
  return {
    settings: {
      ...base.settings,
      ...(next.settings ?? {}),
    },
    lastUpdateId: Number(next.lastUpdateId ?? 0),
    users: next.users && typeof next.users === "object" ? next.users : {},
    pendingRequests:
      next.pendingRequests && typeof next.pendingRequests === "object"
        ? next.pendingRequests
        : {},
    conversations:
      next.conversations && typeof next.conversations === "object"
        ? next.conversations
        : {},
    jobs: next.jobs && typeof next.jobs === "object" ? next.jobs : {},
  };
}

async function loadState() {
  const base = defaultState();
  await ensureSheetsStorage();
  const meta = await loadMetaSheet();
  const fromDb = normalizeState({
    settings: meta.settings ?? base.settings,
    lastUpdateId: meta.lastUpdateId ?? 0,
    users: await loadJsonSheet(SHEET_TABLES.users.title, SHEET_TABLES.users.keyColumn),
    pendingRequests: await loadJsonSheet(
      SHEET_TABLES.pendingRequests.title,
      SHEET_TABLES.pendingRequests.keyColumn,
    ),
    conversations: await loadJsonSheet(
      SHEET_TABLES.conversations.title,
      SHEET_TABLES.conversations.keyColumn,
    ),
    jobs: await loadJsonSheet(SHEET_TABLES.jobs.title, SHEET_TABLES.jobs.keyColumn),
  });

  const hasRows =
    Object.keys(fromDb.users).length > 0 ||
    Object.keys(fromDb.pendingRequests).length > 0 ||
    Object.keys(fromDb.conversations).length > 0 ||
    Object.keys(fromDb.jobs).length > 0 ||
    fromDb.lastUpdateId > 0;

  if (hasRows) {
    return fromDb;
  }
  return fromDb;
}

async function persistLoadedState(nextState) {
  const snapshot = normalizeState(nextState);
  await enqueueSave(async () => {
    await writeTableSnapshot("meta", snapshot);
    await writeTableSnapshot("users", snapshot);
    await writeTableSnapshot("pendingRequests", snapshot);
    await writeTableSnapshot("conversations", snapshot);
    await writeTableSnapshot("jobs", snapshot);
  });
}

function persistState(tableKeys = ["meta", "users", "pendingRequests", "conversations", "jobs"]) {
  for (const key of tableKeys) {
    dirtySheets.add(key);
  }
  scheduleSheetsFlush();
  return Promise.resolve();
}

function setLastUpdateId(nextOffset) {
  state.lastUpdateId = Number(nextOffset ?? 0);
  persistState(["meta"]);
}

function nowIso() {
  return new Date().toISOString();
}

function makeRequestId() {
  return crypto.randomBytes(6).toString("hex");
}

function normalizePhone(raw) {
  const trimmed = String(raw ?? "").trim();
  if (!trimmed) return "";
  if (trimmed.startsWith("+")) {
    const digits = trimmed.slice(1).replace(/\D/g, "");
    return digits ? `+${digits}` : "";
  }
  const compact = trimmed.replace(/\s+/g, "");
  if (compact.startsWith("00")) {
    const digits = compact.slice(2).replace(/\D/g, "");
    return digits ? `+${digits}` : "";
  }
  const digits = compact.replace(/\D/g, "");
  return digits ? `+${digits}` : "";
}

function toDigitsOnlyPhone(phone) {
  return String(phone ?? "").replace(/\D/g, "");
}

function buildPhoneCandidates(targetPhone) {
  const normalized = normalizePhone(targetPhone);
  const digits = toDigitsOnlyPhone(normalized);
  const out = new Set();
  if (!digits) return [];

  out.add(`+${digits}`);
  out.add(digits);
  out.add(`00${digits}`);

  for (let ccLen = 1; ccLen <= 3; ccLen += 1) {
    if (digits.length <= ccLen + 1) continue;
    if (digits[ccLen] !== "0") continue;
    const stripped = digits.slice(0, ccLen) + digits.slice(ccLen + 1);
    out.add(`+${stripped}`);
    out.add(stripped);
    out.add(`00${stripped}`);
  }

  return Array.from(out);
}

function extractPhone(text) {
  const parts = String(text ?? "")
    .split(/[\n,]+/)
    .map((part) => part.trim())
    .filter(Boolean);
  for (const part of parts) {
    const normalized = normalizePhone(part);
    if (toDigitsOnlyPhone(normalized).length >= 8) {
      return normalized;
    }
  }
  return "";
}

function encryptPassword(plainPassword) {
  const cipher = crypto.createCipheriv(
    "aes-128-cbc",
    Buffer.from(AES_KEY, "utf8"),
    Buffer.from(AES_IV, "utf8"),
  );
  const encrypted = Buffer.concat([
    cipher.update(String(plainPassword), "utf8"),
    cipher.final(),
  ]);
  return encrypted.toString("base64");
}

function isAdmin(userId) {
  return String(userId) === ADMIN_USER_ID;
}

function ensureUserRecord(user) {
  const userId = String(user.id);
  const existing = state.users[userId] ?? {
    id: userId,
    status: isAdmin(userId) ? "approved" : "new",
    firstSeenAt: nowIso(),
    ownSettings: {},
  };
  state.users[userId] = {
    ...existing,
    id: userId,
    username: user.username ?? existing.username ?? null,
    firstName: user.first_name ?? existing.firstName ?? null,
    lastName: user.last_name ?? existing.lastName ?? null,
    ownSettings:
      existing.ownSettings && typeof existing.ownSettings === "object"
        ? existing.ownSettings
        : {},
    isAdmin: isAdmin(userId),
    updatedAt: nowIso(),
  };
  if (isAdmin(userId)) {
    state.users[userId].status = "approved";
  }
  return state.users[userId];
}

function getEffectiveSettings(userId) {
  const userRecord = state.users[String(userId)] ?? {};
  const ownSettings =
    userRecord.ownSettings && typeof userRecord.ownSettings === "object"
      ? userRecord.ownSettings
      : {};

  return {
    password: ownSettings.defaultPassword ?? state.settings.defaultPassword,
    invitationCode:
      ownSettings.defaultInvitationCode ??
      state.settings.defaultInvitationCode,
  };
}

function getConversation(userId) {
  return state.conversations[String(userId)] ?? null;
}

function setConversation(userId, conversation) {
  state.conversations[String(userId)] = {
    ...conversation,
    updatedAt: nowIso(),
  };
  return persistState(["conversations"]);
}

function clearConversation(userId) {
  delete state.conversations[String(userId)];
  return persistState(["conversations"]);
}

function upsertJob(job) {
  state.jobs[String(job.id)] = {
    ...job,
    id: String(job.id),
    userId: String(job.userId),
    updatedAt: nowIso(),
  };
  return persistState(["jobs"]);
}

function deleteJob(jobId) {
  delete state.jobs[String(jobId)];
  return persistState(["jobs"]);
}

function queueActorTask(actorId, task) {
  const key = String(actorId ?? "global");
  const previous = actorQueues.get(key) ?? Promise.resolve();
  const next = previous
    .catch(() => {})
    .then(task)
    .finally(() => {
      if (actorQueues.get(key) === next) {
        actorQueues.delete(key);
      }
    });
  actorQueues.set(key, next);
  return next;
}

function buildEarnHeapAxios(proxyUrl) {
  const config = {
    timeout: 30_000,
    validateStatus: () => true,
  };
  if (!proxyUrl) {
    return axios.create(config);
  }

  const lower = proxyUrl.toLowerCase();
  const agent = lower.startsWith("socks")
    ? new SocksProxyAgent(proxyUrl)
    : new HttpsProxyAgent(proxyUrl);

  return axios.create({
    ...config,
    httpAgent: agent,
    httpsAgent: agent,
    proxy: false,
  });
}

class TelegramBot {
  constructor(token) {
    this.token = token;
    this.offset = 0;
    this.http = axios.create({
      timeout: 40_000,
      validateStatus: () => true,
    });
  }

  async api(method, payload = {}) {
    const url = `https://api.telegram.org/bot${this.token}/${method}`;
    const response = await this.http.post(url, payload);
    if (response.status < 200 || response.status >= 300) {
      throw new Error(`Telegram HTTP ${response.status} at ${method}`);
    }
    const data = response.data;
    if (!data?.ok) {
      throw new Error(
        `Telegram API ${method} failed: ${JSON.stringify(data)}`,
      );
    }
    return data.result;
  }

  async getUpdates(timeout = 25) {
    return this.api("getUpdates", {
      offset: this.offset,
      timeout,
      allowed_updates: ["message", "callback_query"],
    });
  }

  async sendMessage(chatId, text, extra = {}) {
    return this.api("sendMessage", {
      chat_id: chatId,
      text,
      ...extra,
    });
  }

  async editMessageText(chatId, messageId, text, extra = {}) {
    return this.api("editMessageText", {
      chat_id: chatId,
      message_id: messageId,
      text,
      ...extra,
    });
  }

  async answerCallbackQuery(callbackQueryId, text, showAlert = false) {
    return this.api("answerCallbackQuery", {
      callback_query_id: callbackQueryId,
      text,
      show_alert: showAlert,
    });
  }
}

class EarnHeapSession {
  constructor(settings, deviceId = crypto.randomUUID()) {
    this.deviceId = deviceId;
    this.http = buildEarnHeapAxios(settings.earnHeapProxyUrl);
    this.token = null;
  }

  buildHeaders(token = null) {
    const headers = {
      accept: "*/*",
      "content-type": "application/json",
      "device-id": this.deviceId,
      language: "en",
      origin: WEB_ORIGIN,
      platform: "web",
      referer: `${WEB_ORIGIN}/`,
      usercompre: USERCOMPRE,
      "x-requested-with": "XMLHttpRequest",
    };
    if (token) {
      headers.Authorization = `Bearer ${token}`;
    }
    return headers;
  }

  async postJson(pathname, body, token = null) {
    const response = await this.http.post(`${API_BASE}${pathname}`, body, {
      headers: this.buildHeaders(token),
    });

    if (response.status < 200 || response.status >= 300) {
      throw new Error(`EarnHeap HTTP ${response.status} at ${pathname}`);
    }

    const payload =
      typeof response.data === "string"
        ? JSON.parse(response.data)
        : response.data;
    return payload;
  }

  async checkUser(phone) {
    return this.postJson("/task-clint/auto/checkUser", { phone });
  }

  async loginOrRegister(phone, plainPassword, invitationCode) {
    const payload = await this.postJson(
      "/task-clint/auto/loginInPhoneWithPasswordV2",
      {
        phone,
        password: encryptPassword(plainPassword),
        channelV2: CHANNEL,
        invitationCode,
      },
    );

    if (payload.code !== 200 || !payload?.data?.accessToken) {
      throw new Error(`EarnHeap login failed: ${JSON.stringify(payload)}`);
    }

    this.token = payload.data.accessToken;
    return payload.data;
  }

  async requestPairingCode(phone) {
    return this.postJson(
      "/task-clint/whatsappAccount/getWhatsappVerificationCode",
      { phone },
      this.token,
    );
  }

  async requestPairingCodeWithFallback(targetPhone) {
    const candidates = buildPhoneCandidates(targetPhone);
    const tried = [];

    for (const phone of candidates) {
      const response = await this.requestPairingCode(phone);
      tried.push({
        phone,
        code: response?.code ?? null,
        msg: String(response?.msg ?? ""),
        data: response?.data ?? null,
      });

      const msg = String(response?.msg ?? "").toLowerCase();
      if (!msg.includes("phone number format")) {
        return {
          acceptedPhone: phone,
          response,
          tried,
        };
      }
    }

    return {
      acceptedPhone: null,
      response:
        tried[tried.length - 1] ?? {
          code: 500,
          msg: "phone number format",
        },
      tried,
    };
  }

  async queryBindingList() {
    const payload = await this.postJson(
      "/task-clint/taskInfo/queryWhatsappCardPageQuery",
      {
        pageNo: 1,
        pageSize: 20,
      },
      this.token,
    );

    if (payload.code !== 200) {
      throw new Error(`Binding list failed: ${JSON.stringify(payload)}`);
    }

    return payload?.data?.whatsappAccountList ?? [];
  }
}

function isBound(list, targetPhone) {
  const normalizedTarget = normalizePhone(targetPhone);
  return list.find((row) => {
    const account = normalizePhone(row?.account ?? "");
    return account === normalizedTarget && Number(row?.bindStatus) === 1;
  });
}

function buildReplyKeyboard(userId) {
  const rows = isAdmin(userId)
    ? [
        ["📲 Link WhatsApp", "📄 My Status"],
        ["🌐 Set Proxy", "📣 Broadcast"],
        ["👥 Users", "🗑 Remove User"],
        ["ℹ️ Help", "❌ Cancel"],
      ]
    : [
        ["📲 Link WhatsApp", "📄 My Status"],
        ["ℹ️ Help", "❌ Cancel"],
      ];

  return {
    keyboard: rows.map((row) => row.map((text) => ({ text }))),
    resize_keyboard: true,
    is_persistent: true,
  };
}

function buildUserApprovalKeyboard(requestId) {
  return {
    inline_keyboard: [
      [
        {
          text: "✅ Approve",
          callback_data: `user:${requestId}:approve`,
        },
        {
          text: "❌ Reject",
          callback_data: `user:${requestId}:reject`,
        },
      ],
    ],
  };
}

function buildSettingApprovalKeyboard(requestId) {
  return {
    inline_keyboard: [
      [
        {
          text: "✅ Approve",
          callback_data: `setting:${requestId}:approve`,
        },
        {
          text: "❌ Reject",
          callback_data: `setting:${requestId}:reject`,
        },
      ],
    ],
  };
}

function buildCopyCodeKeyboard(code) {
  return {
    inline_keyboard: [
      [
        {
          text: "📋 Copy",
          copy_text: {
            text: code,
          },
        },
      ],
    ],
  };
}

function profileLabel(userRecord) {
  const bits = [
    userRecord.firstName,
    userRecord.lastName,
    userRecord.username ? `@${userRecord.username}` : null,
    `ID:${userRecord.id}`,
  ].filter(Boolean);
  return bits.join(" | ");
}

async function sendMainMenu(bot, chatId, userId, text) {
  return bot.sendMessage(chatId, text, {
    reply_markup: buildReplyKeyboard(userId),
  });
}

async function createAccessRequest(bot, from) {
  const userId = String(from.id);
  const requestId = makeRequestId();
  state.pendingRequests[requestId] = {
    id: requestId,
    type: "user_access",
    requestedBy: userId,
    requestedAt: nowIso(),
  };
  state.users[userId].status = "pending";
  await persistState(["users", "pendingRequests"]);

  await bot.sendMessage(
    ADMIN_USER_ID,
    `🆕 New user approval required\n${profileLabel(state.users[userId])}`,
    {
      reply_markup: buildUserApprovalKeyboard(requestId),
    },
  );
}

async function createSettingRequest(bot, from, settingKey, value) {
  const requestId = makeRequestId();
  state.pendingRequests[requestId] = {
    id: requestId,
    type: "setting_change",
    scope: "user",
    settingKey,
    value,
    requestedBy: String(from.id),
    targetUserId: String(from.id),
    requestedAt: nowIso(),
  };
  await persistState(["pendingRequests"]);

  const label =
    settingKey === "defaultPassword"
      ? "personal password"
      : "personal invitation code";
  await bot.sendMessage(
    ADMIN_USER_ID,
    `⚠️ Setting change approval required\n${profileLabel(state.users[String(from.id)])}\n${label}: ${value}`,
    {
      reply_markup: buildSettingApprovalKeyboard(requestId),
    },
  );
}

async function handleStart(bot, message) {
  const userRecord = ensureUserRecord(message.from);
  await persistState(["users"]);

  if (isAdmin(userRecord.id)) {
    await sendMainMenu(
      bot,
      message.chat.id,
      userRecord.id,
      "👑 Admin panel ready.",
    );
    return;
  }

  if (userRecord.status === "approved") {
    await sendMainMenu(
      bot,
      message.chat.id,
      userRecord.id,
      "✅ Access approved. Send a number or tap `📲 Link WhatsApp`.",
    );
    return;
  }

  if (userRecord.status === "pending") {
    await sendMainMenu(
      bot,
      message.chat.id,
      userRecord.id,
      "⏳ Your access approval is still pending.",
    );
    return;
  }

  if (userRecord.status === "rejected" || userRecord.status === "removed") {
    await sendMainMenu(
      bot,
      message.chat.id,
      userRecord.id,
      "⛔ Your access is blocked.",
    );
    return;
  }

  await createAccessRequest(bot, message.from);
  await sendMainMenu(
    bot,
    message.chat.id,
    userRecord.id,
    "⏳ Your request has been sent to the admin.",
  );
}

async function handleHelp(bot, message) {
  const text = isAdmin(message.from.id)
    ? "ℹ️ Admin shortcuts\n\n• `📲 Link WhatsApp` starts a number flow\n• `🌐 Set Proxy` sets or clears the service proxy\n• `📣 Broadcast` sends a message to approved users\n• `🗑 Remove User` removes user access"
    : "ℹ️ How to use\n\n• Tap `📲 Link WhatsApp`\n• Send a number in almost any format\n• The bot will create a fresh account\n• Copy the pairing code and enter it in WhatsApp\n• `/setpassword` and `/setinvite` apply only to your own account after admin approval";

  await sendMainMenu(bot, message.chat.id, message.from.id, text);
}

async function handleStatus(bot, message) {
  const userId = String(message.from.id);
  const userRecord = state.users[userId];
  const lines = [
    `👤 ${profileLabel(userRecord)}`,
    `🔐 Status: ${userRecord.status ?? "unknown"}`,
  ];
  const ownSettings =
    userRecord.ownSettings && typeof userRecord.ownSettings === "object"
      ? userRecord.ownSettings
      : {};

  if (!isAdmin(userId)) {
    lines.push(
      `🧩 Personal password: ${
        ownSettings.defaultPassword ? "custom" : "global default"
      }`,
    );
    lines.push(
      `🎟 Personal invite code: ${
        ownSettings.defaultInvitationCode ? "custom" : "global default"
      }`,
    );
  }

  if (isAdmin(userId)) {
    lines.push("🛠 Global credentials: active");
    lines.push(
      `🌐 Proxy: ${
        state.settings.earnHeapProxyUrl
          ? state.settings.earnHeapProxyUrl
          : "not set"
      }`,
    );
    const approvedCount = Object.values(state.users).filter(
      (entry) => entry.status === "approved" && !entry.isAdmin,
    ).length;
    const pendingCount = Object.values(state.pendingRequests).filter(
      (entry) => entry.type === "user_access" || entry.type === "setting_change",
    ).length;
    lines.push(`👥 Approved users: ${approvedCount}`);
    lines.push(`🕓 Pending approvals: ${pendingCount}`);
  }

  await sendMainMenu(bot, message.chat.id, userId, lines.join("\n"));
}

function validateProxyUrl(raw) {
  const url = new URL(raw);
  const protocol = url.protocol.toLowerCase();
  if (
    protocol !== "http:" &&
    protocol !== "https:" &&
    protocol !== "socks:" &&
    protocol !== "socks4:" &&
    protocol !== "socks5:"
  ) {
    throw new Error("Proxy protocol must be http/https/socks/socks4/socks5.");
  }
  return raw;
}

function generateCandidateIndianPhone() {
  const starts = ["6", "7", "8", "9"];
  const first = starts[crypto.randomInt(0, starts.length)];
  let rest = "";
  for (let i = 0; i < 9; i += 1) {
    rest += String(crypto.randomInt(0, 10));
  }
  return `+91${first}${rest}`;
}

async function generateUniqueEarnHeapPhone(session) {
  for (let attempt = 0; attempt < 40; attempt += 1) {
    const candidate = generateCandidateIndianPhone();
    const check = await session.checkUser(candidate);
    if (check.code !== 200) continue;
    if (Number(check?.data?.isNewUser ?? 0) === 1) {
      return candidate;
    }
  }
  throw new Error("Could not generate a unique account number.");
}

async function notifyAdminLinkSuccess(bot, requester, accountPhone, targetPhone) {
  if (String(requester.id) === ADMIN_USER_ID) return;

  await bot.sendMessage(
    ADMIN_USER_ID,
    `✅ Link success\nUser: ${profileLabel(state.users[String(requester.id)])}\nAccount: ${accountPhone}\nLinked: ${targetPhone}`,
  );
}

function startLinkJob(bot, message, targetPhone) {
  const effectiveSettings = getEffectiveSettings(message.from.id);
  const job = {
    id: makeRequestId(),
    userId: String(message.from.id),
    chatId: String(message.chat.id),
    deviceId: crypto.randomUUID(),
    profileId: crypto.randomUUID(),
    requester: {
      id: String(message.from.id),
      username: message.from.username ?? null,
      firstName: message.from.first_name ?? null,
      lastName: message.from.last_name ?? null,
    },
    targetPhone,
    password: effectiveSettings.password,
    invitationCode: effectiveSettings.invitationCode,
    status: "starting",
    createdAt: nowIso(),
  };

  runLinkJob(bot, message, targetPhone, job).catch((error) => {
    console.error(`Job ${job.id} failed: ${error.message}`);
  });
  return job;
}

async function runLinkJob(bot, message, targetPhone, existingJob = null) {
  const userId = String(message.from.id);
  const job =
    existingJob ??
    {
      id: makeRequestId(),
      userId,
      chatId: String(message.chat.id),
      deviceId: crypto.randomUUID(),
      profileId: crypto.randomUUID(),
      requester: {
        id: String(message.from.id),
        username: message.from.username ?? null,
        firstName: message.from.first_name ?? null,
        lastName: message.from.last_name ?? null,
      },
      targetPhone,
      ...getEffectiveSettings(userId),
      status: "starting",
      createdAt: nowIso(),
    };

  if (activeJobs.has(job.id)) {
    await sendMainMenu(
      bot,
      message.chat.id,
      userId,
      `⏳ Job ${job.id} is already running.`,
    );
    return;
  }

  activeJobs.set(job.id, true);

  try {
    const deviceId = job.deviceId ?? crypto.randomUUID();
    const profileId = job.profileId ?? crypto.randomUUID();
    const session = new EarnHeapSession(state.settings, deviceId);
    job.deviceId = job.deviceId ?? deviceId;
    job.profileId = job.profileId ?? profileId;
    await upsertJob(job);

    let accountPhone = job.accountPhone ?? "";
    if (!accountPhone) {
      await bot.sendMessage(
        message.chat.id,
        `⏳ Creating a new account session for ${targetPhone}...\nJob: ${job.id}`,
      );
      accountPhone = await generateUniqueEarnHeapPhone(session);
      job.accountPhone = accountPhone;
      job.status = "account_created";
      await upsertJob(job);
    }

    await session.loginOrRegister(
      accountPhone,
      job.password,
      job.invitationCode,
    );

    let acceptedPhone = job.acceptedPhone ?? "";
    let deviceCode = job.deviceCode ?? "";

    if (!deviceCode) {
      const pairResult = await session.requestPairingCodeWithFallback(targetPhone);
      const codeResponse = pairResult.response;

      if (pairResult.acceptedPhone && pairResult.acceptedPhone !== targetPhone) {
        acceptedPhone = pairResult.acceptedPhone;
        await bot.sendMessage(
          message.chat.id,
          `📞 ${targetPhone} accepted as ${pairResult.acceptedPhone}\nJob: ${job.id}`,
        );
      }

      if (codeResponse.code !== 200) {
        const msg = String(codeResponse.msg ?? "");
        if (msg.toLowerCase().includes("phone number format")) {
          await deleteJob(job.id);
          await sendMainMenu(
            bot,
            message.chat.id,
            userId,
            `⚠️ ${targetPhone} was not accepted. Try strict E.164 format.\nJob: ${job.id}`,
          );
          return;
        }
        await deleteJob(job.id);
        await sendMainMenu(
          bot,
          message.chat.id,
          userId,
          `⚠️ ${targetPhone} pairing request failed: ${msg || "unknown error"}\nJob: ${job.id}`,
        );
        return;
      }

      deviceCode = codeResponse?.data?.deviceCode ?? "";
      if (!deviceCode) {
        await deleteJob(job.id);
        await sendMainMenu(
          bot,
          message.chat.id,
          userId,
          `⚠️ No pairing code was returned for ${targetPhone}.\nJob: ${job.id}`,
        );
        return;
      }

      job.deviceCode = deviceCode;
      job.acceptedPhone = acceptedPhone;
      job.status = "waiting_bind";
      await upsertJob(job);
    }

    await bot.sendMessage(message.chat.id, `${targetPhone}\n${deviceCode}`, {
      reply_markup: buildCopyCodeKeyboard(deviceCode),
    });

    const deadline = Date.now() + LINK_TIMEOUT_MS;
    while (Date.now() < deadline) {
      const list = await session.queryBindingList();
      const match = isBound(list, targetPhone);
      if (match) {
        const normalizedLinked = normalizePhone(match.account);
        const successText =
          `✅ Success\n` +
          `📱 Target: ${targetPhone}\n` +
          `🆔 Account: ${accountPhone}\n` +
          `🔗 Linked number: ${normalizedLinked}\n` +
          `🧵 Job: ${job.id}`;
        await sendMainMenu(bot, message.chat.id, userId, successText);
        await notifyAdminLinkSuccess(
          bot,
          message.from,
          accountPhone,
          normalizedLinked,
        );
        await deleteJob(job.id);
        return;
      }
      await sleep(LINK_POLL_INTERVAL_MS);
    }

    await deleteJob(job.id);
    await sendMainMenu(
      bot,
      message.chat.id,
      userId,
      `⌛ Bind success was not confirmed for ${targetPhone}.\nJob: ${job.id}`,
    );
  } catch (error) {
    await deleteJob(job.id);
    await sendMainMenu(
    bot,
    message.chat.id,
    userId,
    `❌ ${targetPhone} error: ${error.message}\nJob: ${job.id}`,
    );
  } finally {
    activeJobs.delete(job.id);
  }
}

async function resumePendingJobs(bot) {
  const resumableJobs = Object.values(state.jobs).filter((job) =>
    ["account_created", "waiting_bind"].includes(job.status),
  );

  for (const job of resumableJobs) {
    job.id = job.id ?? makeRequestId();
    if (activeJobs.has(String(job.id))) continue;
    const syntheticMessage = {
      chat: {
        id: job.chatId,
      },
      from: {
        id: job.requester?.id ?? job.userId,
        username: job.requester?.username ?? null,
        first_name: job.requester?.firstName ?? null,
        last_name: job.requester?.lastName ?? null,
      },
    };

    bot
      .sendMessage(
        job.chatId,
        `♻️ Resuming the pending link session for ${job.targetPhone}...\nJob: ${job.id}`,
      )
      .catch(() => {});

    runLinkJob(
      bot,
      syntheticMessage,
      job.targetPhone,
      job,
    ).catch((error) => {
      console.error(`Resume job failed for ${job.userId}: ${error.message}`);
    });
  }
}

async function handleHiddenCommand(bot, message, command, argText) {
  const userId = String(message.from.id);

  if (command === "/cancel") {
    await clearConversation(userId);
    await sendMainMenu(bot, message.chat.id, userId, "✅ Cancelled.");
    return true;
  }

  if (command === "/setpassword" || command === "/setinvite") {
    if (!argText) {
      await sendMainMenu(
        bot,
        message.chat.id,
        userId,
        "⚠️ Please provide a new value.",
      );
      return true;
    }

    const settingKey =
      command === "/setpassword"
        ? "defaultPassword"
        : "defaultInvitationCode";

    if (isAdmin(userId)) {
      state.settings[settingKey] = argText;
      await persistState(["meta"]);
      await sendMainMenu(
        bot,
        message.chat.id,
        userId,
        "✅ Global setting updated.",
      );
      return true;
    }

    await createSettingRequest(bot, message.from, settingKey, argText);
    await sendMainMenu(
      bot,
      message.chat.id,
      userId,
      "⏳ Your personal setting change request has been sent for admin approval.",
    );
    return true;
  }

  if (command === "/setproxy") {
    if (!isAdmin(userId)) {
      await sendMainMenu(bot, message.chat.id, userId, "⛔ Admin only.");
      return true;
    }
    if (!argText) {
      await setConversation(userId, { action: "awaiting_proxy" });
      await sendMainMenu(
        bot,
        message.chat.id,
        userId,
        "🌐 Send a proxy URL. Send `off` to clear it.",
      );
      return true;
    }
    try {
      state.settings.earnHeapProxyUrl =
        argText.toLowerCase() === "off" ? null : validateProxyUrl(argText);
      await persistState(["meta"]);
      await sendMainMenu(bot, message.chat.id, userId, "✅ Proxy updated.");
    } catch (error) {
      await sendMainMenu(
        bot,
        message.chat.id,
        userId,
        `❌ ${error.message}`,
      );
    }
    return true;
  }

  if (command === "/clearproxy") {
    if (!isAdmin(userId)) {
      await sendMainMenu(bot, message.chat.id, userId, "⛔ Admin only.");
      return true;
    }
    state.settings.earnHeapProxyUrl = null;
    await persistState(["meta"]);
    await sendMainMenu(bot, message.chat.id, userId, "✅ Proxy cleared.");
    return true;
  }

  if (command === "/broadcast") {
    if (!isAdmin(userId)) {
      await sendMainMenu(bot, message.chat.id, userId, "⛔ Admin only.");
      return true;
    }
    if (!argText) {
      await setConversation(userId, { action: "awaiting_broadcast" });
      await sendMainMenu(bot, message.chat.id, userId, "📣 Send the broadcast text.");
      return true;
    }
    await broadcastToApprovedUsers(bot, argText);
    await sendMainMenu(bot, message.chat.id, userId, "✅ Broadcast sent.");
    return true;
  }

  if (command === "/removeuser") {
    if (!isAdmin(userId)) {
      await sendMainMenu(bot, message.chat.id, userId, "⛔ Admin only.");
      return true;
    }
    if (!argText) {
      await setConversation(userId, { action: "awaiting_remove_user" });
      await sendMainMenu(bot, message.chat.id, userId, "🗑 Send the user ID.");
      return true;
    }
    await removeUserById(bot, message.chat.id, argText.trim());
    return true;
  }

  if (command === "/users") {
    if (!isAdmin(userId)) {
      await sendMainMenu(bot, message.chat.id, userId, "⛔ Admin only.");
      return true;
    }
    await sendUserList(bot, message.chat.id, userId);
    return true;
  }

  return false;
}

async function sendUserList(bot, chatId, userId) {
  const approved = Object.values(state.users)
    .filter((entry) => entry.status === "approved" && !entry.isAdmin)
    .map((entry) => `• ${profileLabel(entry)}`);
  const pending = Object.values(state.users)
    .filter((entry) => entry.status === "pending")
    .map((entry) => `• ${profileLabel(entry)}`);

  const text = [
    `👥 Approved: ${approved.length}`,
    approved.length ? approved.join("\n") : "• none",
    "",
    `🕓 Pending: ${pending.length}`,
    pending.length ? pending.join("\n") : "• none",
  ].join("\n");

  await sendMainMenu(bot, chatId, userId, text);
}

async function broadcastToApprovedUsers(bot, text) {
  const recipients = Object.values(state.users).filter(
    (entry) => entry.status === "approved",
  );

  for (const entry of recipients) {
    try {
      await bot.sendMessage(entry.id, text, {
        reply_markup: buildReplyKeyboard(entry.id),
      });
    } catch (error) {
      console.error(`Broadcast failed for ${entry.id}: ${error.message}`);
    }
  }
}

async function removeUserById(bot, chatId, rawUserId) {
  const userId = String(rawUserId);
  if (userId === ADMIN_USER_ID) {
    await sendMainMenu(bot, chatId, ADMIN_USER_ID, "⛔ The admin account cannot be removed.");
    return;
  }

  const entry = state.users[userId];
  if (!entry) {
    await sendMainMenu(bot, chatId, ADMIN_USER_ID, "⚠️ User not found.");
    return;
  }

  entry.status = "removed";
  delete state.conversations[userId];
  await persistState(["users", "conversations"]);

  await sendMainMenu(bot, chatId, ADMIN_USER_ID, `✅ Removed: ${profileLabel(entry)}`);
  try {
    await sendMainMenu(
      bot,
      userId,
      userId,
      "⛔ Your bot access has been removed.",
    );
  } catch {
    // Ignore unreachable users.
  }
}

async function handleConversation(bot, message, conversation) {
  const userId = String(message.from.id);
  const text = String(message.text ?? "").trim();

  if (conversation.action === "awaiting_target_phone") {
    const targetPhone = extractPhone(text);
    if (!targetPhone) {
      await sendMainMenu(
        bot,
        message.chat.id,
        userId,
        "⚠️ Please send a valid phone number.",
      );
      return true;
    }
    await clearConversation(userId);
    startLinkJob(bot, message, targetPhone);
    return true;
  }

  if (conversation.action === "awaiting_proxy") {
    await clearConversation(userId);
    try {
      state.settings.earnHeapProxyUrl =
        text.toLowerCase() === "off" ? null : validateProxyUrl(text);
      await persistState(["meta"]);
      await sendMainMenu(bot, message.chat.id, userId, "✅ Proxy updated.");
    } catch (error) {
      await sendMainMenu(
        bot,
        message.chat.id,
        userId,
        `❌ ${error.message}`,
      );
    }
    return true;
  }

  if (conversation.action === "awaiting_broadcast") {
    await clearConversation(userId);
    await broadcastToApprovedUsers(bot, text);
    await sendMainMenu(bot, message.chat.id, userId, "✅ Broadcast sent.");
    return true;
  }

  if (conversation.action === "awaiting_remove_user") {
    await clearConversation(userId);
    await removeUserById(bot, message.chat.id, text);
    return true;
  }

  return false;
}

async function handleCallbackQuery(bot, callbackQuery) {
  const fromId = String(callbackQuery.from.id);
  const [scope, requestId, decision] = String(
    callbackQuery.data ?? "",
  ).split(":");

  if (!isAdmin(fromId)) {
    await bot.answerCallbackQuery(
      callbackQuery.id,
      "Admin only.",
      true,
    );
    return;
  }

  const request = state.pendingRequests[requestId];
  if (!request) {
    await bot.answerCallbackQuery(
      callbackQuery.id,
      "Request not found.",
      true,
    );
    return;
  }

  if (scope === "user" && request.type === "user_access") {
    const target = state.users[request.requestedBy];
    if (!target) {
      delete state.pendingRequests[requestId];
      await persistState(["pendingRequests"]);
      await bot.answerCallbackQuery(callbackQuery.id, "User missing.", true);
      return;
    }

    target.status = decision === "approve" ? "approved" : "rejected";
    delete state.pendingRequests[requestId];
    await persistState(["users", "pendingRequests"]);

    const resultText =
      decision === "approve"
        ? "✅ User approved"
        : "❌ User rejected";
    await bot.answerCallbackQuery(callbackQuery.id, resultText);
    await bot.editMessageText(
      callbackQuery.message.chat.id,
      callbackQuery.message.message_id,
      `${callbackQuery.message.text}\n\n${resultText}`,
    );

    const userNotice =
      decision === "approve"
        ? "✅ Your access has been approved."
        : "⛔ Your access has been rejected.";
    try {
      await sendMainMenu(
        bot,
        target.id,
        target.id,
        userNotice,
      );
    } catch {
      // Ignore unreachable users.
    }
    return;
  }

  if (scope === "setting" && request.type === "setting_change") {
    const requester = state.users[request.requestedBy];
    if (decision === "approve") {
      const targetUserId = String(
        request.targetUserId ?? request.requestedBy,
      );
      const targetUser = state.users[targetUserId];
      if (request.scope === "user" && targetUser) {
        targetUser.ownSettings = {
          ...(targetUser.ownSettings ?? {}),
          [request.settingKey]: request.value,
        };
      } else if (request.scope === "global") {
        state.settings[request.settingKey] = request.value;
      }
    }
    delete state.pendingRequests[requestId];
    await persistState(
      request.scope === "global"
        ? ["meta", "pendingRequests"]
        : ["users", "pendingRequests"],
    );

    const approved = decision === "approve";
    const resultText = approved
      ? "✅ Setting updated"
      : "❌ Setting rejected";
    await bot.answerCallbackQuery(callbackQuery.id, resultText);
    await bot.editMessageText(
      callbackQuery.message.chat.id,
      callbackQuery.message.message_id,
      `${callbackQuery.message.text}\n\n${resultText}`,
    );

    if (requester) {
      try {
        await sendMainMenu(
          bot,
          requester.id,
          requester.id,
          approved
            ? "✅ Your personal setting has been applied."
            : "⛔ Your requested setting was rejected.",
        );
      } catch {
        // Ignore unreachable users.
      }
    }
    return;
  }

  await bot.answerCallbackQuery(callbackQuery.id, "Unknown callback.", true);
}

async function handleMessage(bot, message) {
  if (!message?.from?.id) return;
  ensureUserRecord(message.from);
  await persistState(["users"]);

  const userId = String(message.from.id);
  const text = String(message.text ?? "").trim();

  if (!text) return;

  if (text === "/start") {
    await handleStart(bot, message);
    return;
  }

  const commandMatch = text.match(/^\/\S+/);
  if (commandMatch) {
    const [command] = commandMatch;
    const argText = text.slice(command.length).trim();
    const handled = await handleHiddenCommand(bot, message, command, argText);
    if (handled) return;
  }

  const userRecord = state.users[userId];
  if (!isAdmin(userId) && userRecord.status !== "approved") {
    if (userRecord.status === "pending") {
      await sendMainMenu(
        bot,
        message.chat.id,
        userId,
        "⏳ Your approval is still pending.",
      );
      return;
    }
    await handleStart(bot, message);
    return;
  }

  if (text === "❌ Cancel") {
    await clearConversation(userId);
    await sendMainMenu(bot, message.chat.id, userId, "✅ Cancelled.");
    return;
  }

  const conversation = getConversation(userId);
  if (conversation) {
    const handled = await handleConversation(bot, message, conversation);
    if (handled) return;
  }

  if (text === "ℹ️ Help") {
    await handleHelp(bot, message);
    return;
  }

  if (text === "📄 My Status") {
    await handleStatus(bot, message);
    return;
  }

  if (text === "📲 Link WhatsApp") {
    await setConversation(userId, { action: "awaiting_target_phone" });
    await sendMainMenu(
      bot,
      message.chat.id,
      userId,
      "📱 Send the WhatsApp number.",
    );
    return;
  }

  if (isAdmin(userId) && text === "🌐 Set Proxy") {
    await setConversation(userId, { action: "awaiting_proxy" });
    await sendMainMenu(
      bot,
      message.chat.id,
      userId,
      "🌐 Send a proxy URL. Send `off` to clear it.",
    );
    return;
  }

  if (isAdmin(userId) && text === "📣 Broadcast") {
    await setConversation(userId, { action: "awaiting_broadcast" });
    await sendMainMenu(
      bot,
      message.chat.id,
      userId,
      "📣 Send the broadcast text.",
    );
    return;
  }

  if (isAdmin(userId) && text === "🗑 Remove User") {
    await setConversation(userId, { action: "awaiting_remove_user" });
    await sendMainMenu(
      bot,
      message.chat.id,
      userId,
      "🗑 Send the user ID you want to remove.",
    );
    return;
  }

  if (isAdmin(userId) && text === "👥 Users") {
    await sendUserList(bot, message.chat.id, userId);
    return;
  }

  const inferredPhone = extractPhone(text);
  if (inferredPhone) {
    startLinkJob(bot, message, inferredPhone);
    return;
  }

  await sendMainMenu(
    bot,
    message.chat.id,
    userId,
    "⚠️ I did not understand that. Use `📲 Link WhatsApp`.",
  );
}

async function dispatchUpdate(bot, update) {
  if (update.message?.from?.id) {
    return queueActorTask(update.message.from.id, () =>
      handleMessage(bot, update.message),
    );
  }

  if (update.callback_query?.from?.id) {
    return queueActorTask(update.callback_query.from.id, () =>
      handleCallbackQuery(bot, update.callback_query),
    );
  }

  return Promise.resolve();
}

async function main() {
  if (!TELEGRAM_BOT_TOKEN) {
    throw new Error("Missing EARNHEAP_BOT_TOKEN.");
  }

  startHealthServer();
  state = await loadState();
  state.users[ADMIN_USER_ID] = {
    ...(state.users[ADMIN_USER_ID] ?? {}),
    id: ADMIN_USER_ID,
    status: "approved",
    isAdmin: true,
    updatedAt: nowIso(),
  };
  await persistLoadedState(state);

  const bot = new TelegramBot(TELEGRAM_BOT_TOKEN);
  bot.offset = state.lastUpdateId;

  for (const signal of ["SIGINT", "SIGTERM"]) {
    process.on(signal, () => {
      flushSheetsNow()
        .catch((error) => {
          console.error(`Flush on ${signal} failed: ${error.message}`);
        })
        .finally(() => {
          process.exit(0);
        });
    });
  }

  await resumePendingJobs(bot);

  console.log("Telegram bot started.");

  while (true) {
    try {
      const updates = await bot.getUpdates();
      for (const update of updates) {
        bot.offset = update.update_id + 1;
        setLastUpdateId(bot.offset);
        dispatchUpdate(bot, update).catch((error) => {
          console.error(`Update dispatch error: ${error.message}`);
        });
      }
    } catch (error) {
      console.error(`Bot loop error: ${error.message}`);
      await sleep(3000);
    }
  }
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
