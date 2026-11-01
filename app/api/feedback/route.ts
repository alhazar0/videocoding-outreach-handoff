import { NextRequest, NextResponse } from "next/server";
import { feedbackDb } from "@/app/lib/feedback-db";

export async function GET() {
  try {
    const data = await feedbackDb().prepare(`SELECT f.*,c.channel_name,c.channel_url,c.do_not_contact,
      p.registered_email,p.usage_verification_status,p.membership_status,e.web_link,
      (SELECT COUNT(*) FROM inbox_events n WHERE n.contact_id=f.contact_id AND datetime(n.received_at)>datetime(f.received_at)) AS newer_messages
      FROM creator_feedback f LEFT JOIN contacts c ON c.id=f.contact_id
      LEFT JOIN creator_profiles p ON p.contact_id=f.contact_id
      LEFT JOIN inbox_events e ON e.id=f.inbox_event_id
      ORDER BY f.received_at DESC,f.id DESC`).all();
    return NextResponse.json({ rows: data.results }, { headers: { "Cache-Control": "no-store" } });
  } catch { return NextResponse.json({ error: "无法读取反馈记录，请稍后重试" }, { status: 500 }); }
}

export async function POST(request: NextRequest) {
  if (request.headers.get("origin") && request.headers.get("origin") !== request.nextUrl.origin)
    return NextResponse.json({ error: "不允许跨站修改" }, { status: 403 });
  try {
    const b = await request.json();
    if (!Number.isSafeInteger(b.id) || !['candidate','reviewed','dismissed'].includes(b.review_state) ||
        !['new','investigating','planned','fixed','closed'].includes(b.status) ||
        !['not_due','ready','contacted','done'].includes(b.followup_status) ||
        typeof b.notes !== 'string' || b.notes.length>10000 || typeof b.target_version !== 'string' || b.target_version.length>120)
      return NextResponse.json({ error: "字段无效" }, { status: 400 });
    const result = await feedbackDb().prepare(`UPDATE creator_feedback SET review_state=?,status=?,
      followup_status=?,target_version=?,notes=?,updated_at=datetime('now') WHERE id=?`)
      .bind(b.review_state,b.status,b.followup_status,b.target_version,b.notes,b.id).run();
    if (!result.meta.changes) return NextResponse.json({ error: "记录不存在" }, { status: 404 });
    return NextResponse.json({ ok: true });
  } catch { return NextResponse.json({ error: "保存失败，请重试" }, { status: 500 }); }
}
