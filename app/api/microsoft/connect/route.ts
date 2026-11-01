import { NextRequest, NextResponse } from "next/server";
import {
  MICROSOFT_CLIENT_ID,
  MICROSOFT_SCOPES,
  MICROSOFT_TENANT_ID,
  database,
  ensureMicrosoftSchema,
  pkceChallenge,
  randomToken,
  setSetting,
} from "@/app/lib/microsoft";

export async function GET(request: NextRequest) {
  try {
    const DB = database();
    await ensureMicrosoftSchema(DB);
    const verifier = randomToken(64);
    const state = randomToken(32);
    const challenge = await pkceChallenge(verifier);
    const redirectUri = new URL("/api/microsoft/callback", request.url).toString();
    await Promise.all([
      setSetting(DB, "microsoft_oauth_state", state),
      setSetting(DB, "microsoft_pkce_verifier", verifier),
      setSetting(DB, "microsoft_redirect_uri", redirectUri),
    ]);

    const authorize = new URL(
      `https://login.microsoftonline.com/${MICROSOFT_TENANT_ID}/oauth2/v2.0/authorize`,
    );
    authorize.search = new URLSearchParams({
      client_id: MICROSOFT_CLIENT_ID,
      response_type: "code",
      redirect_uri: redirectUri,
      response_mode: "query",
      scope: MICROSOFT_SCOPES,
      state,
      code_challenge: challenge,
      code_challenge_method: "S256",
      login_hint: "team@videocoding.ai",
    }).toString();
    return NextResponse.redirect(authorize);
  } catch (error) {
    return NextResponse.json(
      { error: error instanceof Error ? error.message : "无法开始 Microsoft 连接" },
      { status: 500 },
    );
  }
}
