import { NextRequest, NextResponse } from "next/server";
import {
  MICROSOFT_CLIENT_ID,
  MICROSOFT_SCOPES,
  MICROSOFT_TENANT_ID,
  TokenPayload,
  database,
  ensureMicrosoftSchema,
  getSetting,
  setSetting,
} from "@/app/lib/microsoft";

type ProfilePayload = {
  mail?: string;
  userPrincipalName?: string;
};

function dashboardRedirect(request: NextRequest, result: "connected" | "error", detail?: string) {
  const target = new URL("/", request.url);
  target.searchParams.set("microsoft", result);
  if (detail) target.searchParams.set("detail", detail.slice(0, 180));
  return NextResponse.redirect(target);
}

export async function GET(request: NextRequest) {
  try {
    const DB = database();
    await ensureMicrosoftSchema(DB);
    const error = request.nextUrl.searchParams.get("error");
    if (error) {
      return dashboardRedirect(
        request,
        "error",
        request.nextUrl.searchParams.get("error_description") ?? error,
      );
    }
    const code = request.nextUrl.searchParams.get("code");
    const state = request.nextUrl.searchParams.get("state");
    const [savedState, verifier, redirectUri] = await Promise.all([
      getSetting(DB, "microsoft_oauth_state"),
      getSetting(DB, "microsoft_pkce_verifier"),
      getSetting(DB, "microsoft_redirect_uri"),
    ]);
    if (!code || !state || !savedState || state !== savedState || !verifier || !redirectUri) {
      return dashboardRedirect(request, "error", "Microsoft 授权校验失败，请重新连接");
    }

    const tokenResponse = await fetch(
      `https://login.microsoftonline.com/${MICROSOFT_TENANT_ID}/oauth2/v2.0/token`,
      {
        method: "POST",
        headers: { "content-type": "application/x-www-form-urlencoded" },
        body: new URLSearchParams({
          client_id: MICROSOFT_CLIENT_ID,
          grant_type: "authorization_code",
          code,
          redirect_uri: redirectUri,
          code_verifier: verifier,
          scope: MICROSOFT_SCOPES,
        }),
      },
    );
    const token = await tokenResponse.json() as TokenPayload;
    if (!tokenResponse.ok || !token.access_token || !token.refresh_token) {
      return dashboardRedirect(
        request,
        "error",
        token.error_description ?? "Microsoft 没有返回长期授权",
      );
    }
    const profileResponse = await fetch(
      "https://graph.microsoft.com/v1.0/me?$select=mail,userPrincipalName",
      { headers: { authorization: `Bearer ${token.access_token}` } },
    );
    const profile = await profileResponse.json() as ProfilePayload;
    const mailbox = profile.mail ?? profile.userPrincipalName ?? "team@videocoding.ai";
    await Promise.all([
      setSetting(DB, "microsoft_refresh_token", token.refresh_token),
      setSetting(DB, "microsoft_mailbox", mailbox),
      setSetting(DB, "microsoft_connected_at", new Date().toISOString()),
      setSetting(DB, "microsoft_connection_error", ""),
    ]);
    return dashboardRedirect(request, "connected");
  } catch (error) {
    return dashboardRedirect(
      request,
      "error",
      error instanceof Error ? error.message : "Microsoft 连接失败",
    );
  }
}
