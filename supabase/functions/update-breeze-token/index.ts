import { createClient } from "https://esm.sh/@supabase/supabase-js@2";

type StatePayload = {
  exp: number;
  scope: string;
};

const corsHeaders = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers": "authorization, x-client-info, apikey, content-type",
  "Content-Type": "application/json",
};

function decodeBase64Url(input: string): Uint8Array {
  const normalized = input.replace(/-/g, "+").replace(/_/g, "/");
  const pad = normalized.length % 4 === 0 ? "" : "=".repeat(4 - (normalized.length % 4));
  const raw = atob(normalized + pad);
  return Uint8Array.from(raw, (c) => c.charCodeAt(0));
}

function encodeHex(bytes: Uint8Array): string {
  return Array.from(bytes)
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
}

async function hmacSha256Hex(secret: string, message: string): Promise<string> {
  const keyData = new TextEncoder().encode(secret);
  const msgData = new TextEncoder().encode(message);
  const key = await crypto.subtle.importKey("raw", keyData, { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  const sig = await crypto.subtle.sign("HMAC", key, msgData);
  return encodeHex(new Uint8Array(sig));
}

function constantTimeEqual(a: string, b: string): boolean {
  if (a.length !== b.length) return false;
  let mismatch = 0;
  for (let i = 0; i < a.length; i += 1) {
    mismatch |= a.charCodeAt(i) ^ b.charCodeAt(i);
  }
  return mismatch === 0;
}

async function verifyState(state: string | null, secret: string): Promise<void> {
  if (!secret) return;
  if (!state) throw new Error("Missing signed state");
  const parts = state.split(".");
  if (parts.length !== 2) throw new Error("Invalid state format");
  const [payloadEncoded, sigHex] = parts;
  const expectedSig = await hmacSha256Hex(secret, payloadEncoded);
  if (!constantTimeEqual(expectedSig, sigHex)) {
    throw new Error("Invalid state signature");
  }
  const payloadJson = new TextDecoder().decode(decodeBase64Url(payloadEncoded));
  const payload = JSON.parse(payloadJson) as StatePayload;
  if (payload.scope !== "breeze-token-update-v1") {
    throw new Error("Invalid state scope");
  }
  if (!payload.exp || payload.exp < Math.floor(Date.now() / 1000)) {
    throw new Error("Expired state");
  }
}

function parseApiSession(raw: string): string {
  const text = raw.trim().replace(/^['"]|['"]$/g, "");
  if (!text) throw new Error("Empty token input");
  if (text.includes("://") || text.startsWith("http")) {
    const u = new URL(text);
    const keys = ["API_Session", "apisession", "api_session", "session_token"];
    for (const key of keys) {
      const val = u.searchParams.get(key);
      if (val && val.trim()) return val.trim();
    }
    throw new Error("No API_Session found in URL query string");
  }
  return text;
}

function renderForm(actionUrl: string, state: string | null): Response {
  const stateInput = state
    ? `<input type="hidden" name="state" value="${state.replace(/"/g, "&quot;")}"/>`
    : "";
  const html = `<!doctype html>
<html>
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>Update Breeze Token</title>
    <style>
      body { font-family: Arial, sans-serif; margin: 24px; max-width: 720px; }
      textarea { width: 100%; min-height: 120px; }
      button { margin-top: 12px; padding: 10px 14px; }
      .hint { color: #555; font-size: 14px; }
    </style>
  </head>
  <body>
    <h2>Update Breeze Session Token</h2>
    <p class="hint">Paste API_Session directly, or paste full redirect URL containing API_Session.</p>
    <form method="post" action="${actionUrl.replace(/"/g, "&quot;")}">
      ${stateInput}
      <textarea name="token_input" placeholder="Paste API_Session or full redirect URL"></textarea>
      <br />
      <button type="submit">Update Token</button>
    </form>
  </body>
</html>`;
  return new Response(html, { status: 200, headers: { "Content-Type": "text/html; charset=utf-8" } });
}

Deno.serve(async (req) => {
  if (req.method === "OPTIONS") {
    return new Response("ok", { headers: corsHeaders });
  }

  const supabaseUrl = Deno.env.get("SUPABASE_URL") ?? "";
  const serviceRoleKey = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY") ?? "";
  const stateSecret = Deno.env.get("STATE_SIGNING_SECRET") ?? "";
  if (!supabaseUrl || !serviceRoleKey) {
    return new Response(JSON.stringify({ error: "Missing Supabase env configuration" }), {
      status: 500,
      headers: corsHeaders,
    });
  }

  const url = new URL(req.url);
  const stateFromQuery = url.searchParams.get("state");
  if (req.method === "GET" && !url.searchParams.get("token_input")) {
    try {
      await verifyState(stateFromQuery, stateSecret);
      if (url.searchParams.get("format") === "json") {
        return new Response(
          JSON.stringify({
            ok: true,
            message: "Provide token_input via query param or send POST JSON.",
            example_get:
              `${url.origin}${url.pathname}?state=<state>&token_input=<API_Session_or_redirect_url>`,
            example_post: {
              method: "POST",
              content_type: "application/json",
              body: {
                state: "<state>",
                token_input: "<API_Session_or_redirect_url>",
              },
            },
          }),
          { status: 200, headers: corsHeaders },
        );
      }
      const actionUrl = `${url.origin}${url.pathname}`;
      return renderForm(actionUrl, stateFromQuery);
    } catch (e) {
      return new Response(JSON.stringify({ error: (e as Error).message }), { status: 401, headers: corsHeaders });
    }
  }

  let tokenInput = "";
  let stateValue: string | null = stateFromQuery;
  if (req.method === "POST") {
    const ctype = req.headers.get("content-type") ?? "";
    if (ctype.includes("application/json")) {
      const body = await req.json();
      tokenInput = String(body?.token_input ?? body?.api_session ?? body?.redirect_url ?? "").trim();
      stateValue = body?.state ? String(body.state) : stateValue;
    } else {
      const form = await req.formData();
      tokenInput = String(form.get("token_input") ?? "").trim();
      stateValue = String(form.get("state") ?? stateValue ?? "");
    }
  } else if (req.method === "GET") {
    tokenInput = String(url.searchParams.get("token_input") ?? "").trim();
  } else {
    return new Response(JSON.stringify({ error: "Method not allowed" }), { status: 405, headers: corsHeaders });
  }

  try {
    await verifyState(stateValue, stateSecret);
    const apiSession = parseApiSession(tokenInput);
    const admin = createClient(supabaseUrl, serviceRoleKey, { auth: { persistSession: false } });
    const { error } = await admin
      .from("session_config")
      .upsert({ id: 1, breeze_session_token: apiSession, updated_at: new Date().toISOString() }, { onConflict: "id" });
    if (error) throw new Error(error.message);

    return new Response(JSON.stringify({ ok: true, message: "Breeze token updated" }), {
      status: 200,
      headers: corsHeaders,
    });
  } catch (e) {
    return new Response(JSON.stringify({ error: (e as Error).message }), {
      status: 400,
      headers: corsHeaders,
    });
  }
});
