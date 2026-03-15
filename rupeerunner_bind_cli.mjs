#!/usr/bin/env node

import crypto from "node:crypto";
import process from "node:process";
import { setTimeout as sleep } from "node:timers/promises";
import readline from "node:readline/promises";
import { stdin as input, stdout as output } from "node:process";

const API_BASE = "https://clserver.rupeerunner.com";
const WEB_ORIGIN = "https://web.rupeerunner.cc";
const CHANNEL = "0";
const AES_KEY = "G7d9kLm2QpXz4vT1";
const AES_IV = "1234567890abcdef";

function parseArgs(argv) {
  const args = {};
  for (let i = 0; i < argv.length; i += 1) {
    const token = argv[i];
    if (!token.startsWith("--")) continue;
    const key = token.slice(2);
    const next = argv[i + 1];
    if (!next || next.startsWith("--")) {
      args[key] = "true";
      continue;
    }
    args[key] = next;
    i += 1;
  }
  return args;
}

function normalizePhone(raw) {
  const trimmed = String(raw ?? "").trim();
  if (!trimmed) return "";
  // Keep only digits and optional leading plus.
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
  return digits ? `+${digits}` : trimmed;
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

  // Try trunk-zero removal after assumed country-code lengths (1..3).
  for (let ccLen = 1; ccLen <= 3; ccLen += 1) {
    if (digits.length <= ccLen + 1) continue;
    if (digits[ccLen] !== "0") continue;
    const withoutTrunkZero = digits.slice(0, ccLen) + digits.slice(ccLen + 1);
    out.add(`+${withoutTrunkZero}`);
    out.add(withoutTrunkZero);
    out.add(`00${withoutTrunkZero}`);
  }

  return Array.from(out);
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

function buildHeaders(deviceId, token = null) {
  const headers = {
    accept: "*/*",
    "content-type": "application/json",
    "device-id": deviceId,
    language: "en",
    origin: WEB_ORIGIN,
    platform: "web",
    referer: `${WEB_ORIGIN}/`,
    usercompre: 'Rupeerunner/3.0.15 ({"platform": "web"})',
    "x-requested-with": "XMLHttpRequest",
  };
  if (token) {
    headers.Authorization = `Bearer ${token}`;
  }
  return headers;
}

async function postJson(path, body, headers) {
  const res = await fetch(`${API_BASE}${path}`, {
    method: "POST",
    headers,
    body: JSON.stringify(body),
  });

  const text = await res.text();
  let payload = null;
  try {
    payload = JSON.parse(text);
  } catch {
    throw new Error(`Non-JSON response from ${path}: ${text}`);
  }

  if (!res.ok) {
    throw new Error(`HTTP ${res.status} at ${path}: ${text}`);
  }
  return payload;
}

async function login(loginPhone, plainPassword, deviceId) {
  const encryptedPassword = encryptPassword(plainPassword);
  const payload = await postJson(
    "/task-clint/auto/loginInPhoneWithPasswordV2",
    {
      phone: loginPhone,
      password: encryptedPassword,
      channelV2: CHANNEL,
    },
    buildHeaders(deviceId),
  );

  if (payload.code !== 200 || !payload?.data?.accessToken) {
    throw new Error(`Login failed: ${JSON.stringify(payload)}`);
  }
  return payload.data.accessToken;
}

async function requestPairingCode(targetPhone, deviceId, token) {
  return postJson(
    "/task-clint/whatsappAccount/getWhatsappVerificationCode",
    { phone: targetPhone },
    buildHeaders(deviceId, token),
  );
}

async function requestPairingCodeWithFallback(targetPhone, deviceId, token) {
  const candidates = buildPhoneCandidates(targetPhone);
  const tried = [];

  for (const phone of candidates) {
    const resp = await requestPairingCode(phone, deviceId, token);
    tried.push({
      phone,
      code: resp?.code ?? null,
      msg: String(resp?.msg ?? ""),
      data: resp?.data ?? null,
    });

    const msg = String(resp?.msg ?? "").toLowerCase();
    const isFormatError = msg.includes("phone number format");
    if (!isFormatError) {
      return {
        acceptedPhone: phone,
        response: resp,
        tried,
      };
    }
  }

  return {
    acceptedPhone: null,
    response: tried[tried.length - 1] ?? { code: 500, msg: "phone number format" },
    tried,
  };
}

async function queryBindingList(deviceId, token) {
  const payload = await postJson(
    "/task-clint/taskInfo/queryWhatsappCardPageQuery",
    { pageNo: 1, pageSize: 20 },
    buildHeaders(deviceId, token),
  );

  if (payload.code !== 200) {
    throw new Error(`Binding-list API failed: ${JSON.stringify(payload)}`);
  }

  return payload?.data?.whatsappAccountList ?? [];
}

function isBound(list, targetPhone) {
  const normalizedTarget = normalizePhone(targetPhone);
  return list.find((row) => {
    const account = normalizePhone(row?.account ?? "");
    return account === normalizedTarget && Number(row?.bindStatus) === 1;
  });
}

async function getInput(args, rl) {
  const loginPhone = normalizePhone(
    args["login-phone"] ??
      (await rl.question("Login phone (example: +919661806356): ")),
  );
  const password =
    args.password ?? (await rl.question("Login password (plain text): "));
  const targetPhone = normalizePhone(
    args["target-phone"] ??
      (await rl.question("WhatsApp number to bind (example: +919097153825): ")),
  );

  if (!loginPhone || !password || !targetPhone) {
    throw new Error("Missing required input.");
  }

  return { loginPhone, password, targetPhone };
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  const pollIntervalSec = Number(args["poll-interval"] ?? 5);
  const timeoutSec = Number(args.timeout ?? 300);
  const skipCodeRequest = args["skip-code-request"] === "true";

  if (!Number.isFinite(pollIntervalSec) || pollIntervalSec <= 0) {
    throw new Error("Invalid --poll-interval value.");
  }
  if (!Number.isFinite(timeoutSec) || timeoutSec <= 0) {
    throw new Error("Invalid --timeout value.");
  }

  const rl = readline.createInterface({ input, output });
  try {
    const { loginPhone, password, targetPhone } = await getInput(args, rl);
    const deviceId = args["device-id"] ?? crypto.randomUUID();

    console.log("\n[1/4] Logging in...");
    const token = await login(loginPhone, password, deviceId);
    console.log("[OK] Login successful.");

    if (!skipCodeRequest) {
      console.log("\n[2/4] Requesting pairing code...");
      const pairResult = await requestPairingCodeWithFallback(
        targetPhone,
        deviceId,
        token,
      );
      const codeResponse = pairResult.response;
      if (pairResult.acceptedPhone && pairResult.acceptedPhone !== targetPhone) {
        console.log(
          `[INFO] Pairing request accepted format: ${pairResult.acceptedPhone}`,
        );
      }
      if (codeResponse.code === 200) {
        const codeData = codeResponse.data ?? {};
        const deviceCode = codeData.deviceCode ?? null;
        const apiCode = codeData.code ?? null;
        if (deviceCode) {
          console.log(`[PAIRING CODE] ${deviceCode}`);
          console.log("Type this code manually in WhatsApp (8-digit pairing flow).");
        } else {
          console.log(
            `[INFO] Server response code for pairing request: ${String(apiCode ?? "N/A")}`,
          );
          console.log(
            "No deviceCode was returned. If the number is already bound, monitoring will still continue.",
          );
        }
      } else {
        const msg = String(codeResponse.msg ?? "");
        if (msg.toLowerCase().includes("phone number format")) {
          const loginDigits = toDigitsOnlyPhone(loginPhone);
          const targetDigits = toDigitsOnlyPhone(targetPhone);
          const looksLikeIndianLogin = loginDigits.startsWith("91");
          const looksLikeIndianTarget = targetDigits.startsWith("91");
          console.log(
            `[ERROR] Server rejected this number format: ${targetPhone}`,
          );
          if (pairResult.tried.length > 1) {
            console.log(
              `[INFO] Tried ${pairResult.tried.length} format variants, all returned phone number format.`,
            );
          }
          if (looksLikeIndianLogin && !looksLikeIndianTarget) {
            console.log(
              "[HINT] This account/region appears to accept only Indian numbers (+91...) for pairing code requests.",
            );
          } else {
            console.log(
              "[HINT] Try strict E.164 format (e.g. +<countrycode><number>, no spaces/dashes).",
            );
          }
          process.exitCode = 3;
          return;
        }
        console.log(
          `[WARN] Pairing-code request was not accepted (code=${codeResponse.code}, msg=${codeResponse.msg ?? "N/A"}).`,
        );
        console.log(
          "Monitoring will continue, but you may need to retry later for a new pairing code.",
        );
      }
    } else {
      console.log("\n[2/4] Skipping pairing-code request (--skip-code-request=true).");
    }

    console.log("\n[3/4] Waiting for bind success...");
    const deadline = Date.now() + timeoutSec * 1000;
    let pollCount = 0;

    while (Date.now() < deadline) {
      pollCount += 1;
      const list = await queryBindingList(deviceId, token);
      const match = isBound(list, targetPhone);
      if (match) {
        console.log(
          `\n[SUCCESS] Bound confirmed for ${normalizePhone(match.account)} (bindStatus=${match.bindStatus}).`,
        );
        console.log("[4/4] Done.");
        return;
      }

      const remainingSec = Math.ceil((deadline - Date.now()) / 1000);
      console.log(
        `[WAIT] Poll #${pollCount}: not bound yet. Next check in ${pollIntervalSec}s (timeout in ${remainingSec}s).`,
      );
      await sleep(pollIntervalSec * 1000);
    }

    console.log("\n[TIMEOUT] Bind was not confirmed within the timeout window.");
    process.exitCode = 2;
  } finally {
    rl.close();
  }
}

main().catch((err) => {
  console.error(`\n[ERROR] ${err.message}`);
  process.exit(1);
});
