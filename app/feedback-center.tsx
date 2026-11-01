"use client";
import { useEffect, useState } from "react";

type Feedback = {
 id:number; sender_email:string; channel_name:string; received_at:string; category:string;
 summary:string; evidence:string; review_state:string; status:string; target_version:string;
 followup_status:string; notes:string; web_link:string|null; do_not_contact:number;
 registered_email:string|null; usage_verification_status:string|null; newer_messages:number;
};
const categories:Record<string,string> = {access:'绑卡／开通',performance:'速度／超时',reference_input:'参考素材',quota:'额度',verification:'验证失败',language:'语言效果',positive:'正面评价',negative_fit:'效果不适合'};
const reviews:Record<string,string> = {candidate:'待核实',reviewed:'已核实',dismissed:'非有效反馈'};
const states:Record<string,string> = {new:'待处理',investigating:'调查中',planned:'已排期',fixed:'已修复待验证',closed:'已关闭'};
const followups:Record<string,string> = {not_due:'暂不回访',ready:'升级后待回访',contacted:'已回访',done:'回访完成'};
export function FeedbackCenter() {
 const [rows,setRows]=useState<Feedback[]>([]),[error,setError]=useState(''),[loading,setLoading]=useState(true);
 const [query,setQuery]=useState(''),[category,setCategory]=useState(''),[review,setReview]=useState('reviewed');
 const [selected,setSelected]=useState<Feedback|null>(null),[saving,setSaving]=useState(false),[notice,setNotice]=useState('');
 useEffect(()=>{fetch('/api/feedback').then(async r=>{const d=await r.json();if(!r.ok)throw Error(d.error);setRows(d.rows);}).catch(e=>setError(String(e.message))).finally(()=>setLoading(false));},[]);
 const shown=rows.filter(r=>(!category||r.category===category)&&(!review||r.review_state===review)&&`${r.sender_email} ${r.channel_name} ${r.summary} ${r.evidence} ${r.notes}`.toLowerCase().includes(query.toLowerCase()));
 async function save(){if(!selected)return;setSaving(true);setError('');setNotice('');try{
  const response=await fetch('/api/feedback',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(selected)});
  const d=await response.json();if(!response.ok)throw Error(d.error);
  setRows(old=>old.map(r=>r.id===selected.id?selected:r));setNotice('已保存；未发送邮件。');
 }catch(e){setError(e instanceof Error?e.message:'保存失败');}finally{setSaving(false);}}
 return <div className="page-stack">
  <section className="panel"><h2>博主反馈 · 产品升级与回访</h2>
   <p>保留原始反馈，跟踪修复版本，升级后再回访对应博主。候选不是已确认缺陷；修改状态不会发送邮件。</p>
   <p>反馈记录 {rows.length} 条 · 涉及 {new Set(rows.map(r=>r.sender_email)).size} 个邮箱 · 已核实 {rows.filter(r=>r.review_state==='reviewed').length} 条</p>
   <label>搜索 <input value={query} onChange={e=>setQuery(e.target.value)} placeholder="博主、邮箱、问题或备注" /></label>{' '}
   <label>问题类型 <select value={category} onChange={e=>setCategory(e.target.value)}><option value="">全部</option>{Object.entries(categories).map(([k,v])=><option key={k} value={k}>{v}</option>)}</select></label>{' '}
   <label>审核 <select value={review} onChange={e=>setReview(e.target.value)}><option value="">全部</option>{Object.entries(reviews).map(([k,v])=><option key={k} value={k}>{v}</option>)}</select></label>
  </section>
  {error&&<p role="alert">{error}</p>}{notice&&<p role="status">{notice}</p>}
  {loading?<p>加载反馈中…</p>:<div className="table-scroll"><table className="contact-table"><thead><tr><th>博主／邮箱</th><th>反馈</th><th>状态</th><th>来源时间</th><th>查看</th></tr></thead><tbody>
   {shown.map(r=><tr key={r.id}><td>{r.channel_name||r.sender_email}<br/><small>{r.sender_email}</small>{!!r.do_not_contact&&<p>禁止联系</p>}</td><td>{categories[r.category]||r.category}<br/>{r.summary}</td><td>{reviews[r.review_state]} · {states[r.status]}<br/>{followups[r.followup_status]} {r.target_version}</td><td>{new Date(r.received_at).toLocaleString('zh-CN')}</td><td><button onClick={()=>{setSelected({...r});setNotice('');}}>查看原文／记录进度</button></td></tr>)}
  </tbody></table>{!shown.length&&<p>当前筛选没有记录。</p>}</div>}
  {selected&&<section className="panel" aria-label="反馈详情"><h3>{selected.channel_name||selected.sender_email}</h3><p>{selected.sender_email}</p>
   <p>登记邮箱：{selected.registered_email||'尚未确认'} · 本地测试记录：{selected.usage_verification_status||'未核实'}</p>
   {!!selected.newer_messages&&<p>此反馈之后还有 {selected.newer_messages} 封来信；回访前请在“所有会话”检查最新上下文。</p>}
   {!!selected.do_not_contact&&<p>此联系人禁止联系；反馈可用于内部改进，不可自动回访。</p>}
   <pre style={{whiteSpace:'pre-wrap',overflowWrap:'anywhere',maxHeight:360,overflow:'auto'}}>{selected.evidence}</pre>
   {selected.web_link?.startsWith('https://outlook.office.com/')&&<p><a href={selected.web_link} target="_blank" rel="noreferrer">查看原始邮件</a></p>}
   <label>审核 <select value={selected.review_state} onChange={e=>setSelected({...selected,review_state:e.target.value})}>{Object.entries(reviews).map(([k,v])=><option key={k} value={k}>{v}</option>)}</select></label>{' '}
   <label>处理 <select value={selected.status} onChange={e=>setSelected({...selected,status:e.target.value})}>{Object.entries(states).map(([k,v])=><option key={k} value={k}>{v}</option>)}</select></label>{' '}
   <label>回访 <select value={selected.followup_status} onChange={e=>setSelected({...selected,followup_status:e.target.value})}>{Object.entries(followups).map(([k,v])=><option key={k} value={k}>{v}</option>)}</select></label>
   <p><label>目标升级版本 <input maxLength={120} value={selected.target_version} onChange={e=>setSelected({...selected,target_version:e.target.value})}/></label></p>
   <p><label>处理／回访备注 <textarea rows={4} style={{width:'100%'}} value={selected.notes} onChange={e=>setSelected({...selected,notes:e.target.value})}/></label></p>
   <button disabled={saving} onClick={save}>{saving?'保存中…':'保存记录（不发邮件）'}</button>{' '}<button disabled={saving} onClick={()=>setSelected(null)}>关闭</button>
  </section>}
 </div>;
}
