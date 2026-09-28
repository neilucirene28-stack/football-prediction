import 'dotenv/config';
import { chromium } from 'playwright';
import fs from 'node:fs/promises';
import path from 'node:path';

const cfg = {
  entry: process.env.ENTRY_URL || '',
  headless: process.env.HEADLESS !== 'false',
  runOnStart: process.env.RUN_ON_START !== 'false',
  max: Number(process.env.MAX_MATCHES || 0),
  wait: Number(process.env.TAB_WAIT_MS || 4500),
  timeout: Number(process.env.NAV_TIMEOUT_MS || 25000),
  tz: process.env.TZ || 'Asia/Shanghai',
};
const TABS = ['概况','阵容','战绩','欧指','亚指','排名','必发'];
const ROOT = process.cwd();
let context, page, running = false;
const stamp = () => new Date().toISOString();
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
const local = () => new Intl.DateTimeFormat('sv-SE', {timeZone:cfg.tz,year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit',hourCycle:'h23'}).format(new Date());
const log = async (message, extra={}) => {
  const entry = {at:stamp(),message,...extra};
  console.log(JSON.stringify(entry));
  await fs.appendFile(path.join(ROOT,'logs','collector.log'),JSON.stringify(entry)+'\n');
};
const idOf = x => /^\d{5,12}$/.test(String(x ?? '')) ? String(x) : null;
const team = (m,home) => home ? (m.host_name_l || m.host_name_s || '') : (m.guest_name_l || m.guest_name_s || '');
function kickoffMs(match) {
  const raw=String(match?.match_time ?? '').trim();
  const m=/^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})(?::(\d{2}))?$/.exec(raw);
  if(!m) return null;
  const value=new Date(+m[1],+m[2]-1,+m[3],+m[4],+m[5],+(m[6]||0)).getTime();
  return Number.isFinite(value)?value:null;
}
function prematchEligibility(match, now=Date.now()) {
  const kickoff=kickoffMs(match);
  if(kickoff===null) return {ok:false,reason:'invalid_or_missing_match_time'};
  const minutes=(kickoff-now)/60000;
  if(minutes<=20) return {ok:false,reason:minutes<=0?'already_started':'inside_20_minute_stop_window',minutesToKickoff:Math.round(minutes)};
  return {ok:true,kickoffAt:new Date(kickoff).toISOString(),minutesToKickoff:Math.round(minutes)};
}
const pathOf = url => { try { return new URL(url).pathname; } catch { return ''; } };
const responseMatchId = (payload,url,request) => {
  const values=[];
  const add=o=>{if(o&&typeof o==='object'&&!Array.isArray(o)) for(const k of ['matchId','matchid','match_id','gameId']) if(o[k]!=null) values.push(o[k]);};
  add(payload); add(payload?.data); add(payload?.data?.data);
  try { const u=new URL(url); for(const k of ['matchId','matchid','match_id','gameId']) values.push(u.searchParams.get(k)); } catch {}
  try { const body=request?.postDataJSON?.(); add(body); add(body?.data); } catch {}
  return values.map(idOf).find(Boolean)||null;
};
function responseTeamEvidence(payload,item) {
  const d=payload?.data?.data ?? payload?.data ?? payload;
  if(!d||typeof d!=='object') return {verified:false};
  const home=d.homeTeamName??d.hName??d.host_name_l??d.host_name_s;
  const away=d.awayTeamName??d.aName??d.guest_name_l??d.guest_name_s;
  if(home==null&&away==null) return {verified:false};
  const norm=x=>String(x??'').replace(/\s/g,'').toLowerCase();
  const homeOK=home==null||norm(home)===norm(team(item.list,true));
  const awayOK=away==null||norm(away)===norm(team(item.list,false));
  return {verified:homeOK&&awayOK&&((home!=null&&away!=null)),home:home??null,away:away??null,conflict:!homeOK||!awayOK};
}
function modulePayloadValid(module,name,payload) {
  if(payload?.errcode!=null&&Number(payload.errcode)!==0) return false;
  const d=payload?.data?.data ?? payload?.data ?? payload;
  if(!d||typeof d!=='object') return false;
  if(module==='lineup') return Array.isArray(d.homeStarter)&&d.homeStarter.flat(Infinity).length>0&&Array.isArray(d.awayStarter)&&d.awayStarter.flat(Infinity).length>0;
  if(module==='history') return ['homeHistoryMatches','awayHistoryMatches','historyMatches'].some(k=>Array.isArray(d[k])&&d[k].length>0);
  if(module==='europe'&&name==='odds_list') return Array.isArray(d)&&d.length>0;
  if(module==='asia'&&name==='odds_list') return Array.isArray(d)&&d.length>0;
  if(module==='ranking') return !!(d.matchId||d.homeRanking?.length||d.awayRanking?.length||d.rankingStat||d.tournamentStats);
  if(module==='betfair'&&name==='trade') return !!(d.matchId&&(d.totalAmount!=null||d.totalLoss!=null));
  if(module==='betfair'&&name==='trend_match') return d.data_exists===true;
  if(module==='betfair'&&name==='trade_ratio') return Array.isArray(d.list)&&d.list.length>0;
  if(module==='overview'&&name==='base') return !!(d.matchId&&d.homeTeamName&&d.awayTeamName);
  if(module==='overview'&&name==='live_info') return !!(d.homeTeamName&&d.awayTeamName);
  if(module==='overview'&&name==='match_info') return Number(d.info_num)>0;
  return Object.keys(d).length>0;
}
function listFromPayload(payload) {
  const result = new Map();
  if (!payload?.data || typeof payload.data !== 'object') return [];
  for (const group of Object.values(payload.data)) {
    if (!Array.isArray(group?.match_list)) continue;
    for (const m of group.match_list) { const id=idOf(m?.match_id); if(id) result.set(id,m); }
  }
  return [...result.values()];
}
const endpointRules = [
  [/\/api\/matchjc\/MatchInfoByNum$/i,'overview','match_info'],
  [/\/api\/matchjc\/SoccerMatchLiveInfo$/i,'overview','live_info'],
  [/\/qkdata\/soccer\/match\/base\/2$/i,'overview','base'],
  [/\/analyze\/lineupDataV1$/i,'lineup','lineup'],
  [/\/itou\/soccer\/match\/detail\/history$/i,'history','history'],
  [/\/qkdata\/odds\/detail\/stats\/europe$/i,'europe','stats'],
  [/\/qkdata\/odds\/detail\/stats\/asia$/i,'asia','stats'],
  [/\/qkdata\/soccer\/match\/detail\/stats$/i,'ranking','ranking'],
  [/\/qkdata\/odds\/betfair\/trade\/info$/i,'betfair','trade'],
  [/\/api\/datatrend\/getMatchInfo$/i,'betfair','trend_match'],
  [/\/api\/datatrend\/getPlatFormTradeRatio$/i,'betfair','trade_ratio']
];
const privateKey = /token|cookie|authorization|password|secret|session|openid|unionid|mobile|phone|email|avatar|device.?id|user.?name|nick.?name|credential|access.?key|refresh.?key|uuid/i;
function redact(value, depth=0) {
  if(depth>15) return '[DEPTH_LIMIT]';
  if(Array.isArray(value)) return value.slice(0,1500).map(v=>redact(v,depth+1));
  if(value && typeof value==='object') return Object.fromEntries(Object.entries(value).map(([k,v])=>[k,privateKey.test(k)?'[REDACTED]':redact(v,depth+1)]));
  if(typeof value==='string') return value.slice(0,450000).replace(/https?:\/\/[^\s"'<>]+/gi,'[URL_REDACTED]');
  return value;
}
function classifyOdds(payload) {
  const d=payload?.data;
  const rows=Array.isArray(d)?d:Array.isArray(d?.list)?d.list:
    d&&typeof d==='object'?Object.values(d).filter(Array.isArray).flat():[];
  const grouped={europe:[],asia:[],totals:[],unclassified:[]};
  for(const row of rows) {
    if(!row||typeof row!=='object') {grouped.unclassified.push(row);continue;}
    const pt=Number(row.playType);
    const market=pt===1?'europe':pt===2?'asia':pt===3?'totals':null;
    (market?grouped[market]:grouped.unclassified).push(row);
  }
  return grouped;
}
async function waitUntil(predicate, timeout=cfg.timeout) {
  const start=Date.now();
  while(Date.now()-start<timeout) { if(await predicate()) return true; await sleep(250); }
  return false;
}
async function navigateToList(state) {
  state.list=[];
  await page.goto(cfg.entry,{waitUntil:'domcontentloaded',timeout:cfg.timeout});
  if(await waitUntil(()=>state.list.length>0,4500)) return;
  // This is a best-effort navigation, not a verified site-specific selector.
  for(const name of ['竞彩足球','竞足','竞彩足球胜平负','竞彩足球赛事']) {
    const loc=page.getByText(name,{exact:true}).first();
    if(await loc.isVisible().catch(()=>false)) {
      await log('点击竞彩足球入口',{label:name});
      await loc.click({timeout:5000}).catch(()=>{});
      if(await waitUntil(()=>state.list.length>0,8000)) return;
    }
  }
  throw new Error('未收到 SoccerMatchList：入口可能未进入竞彩足球、需要登录或页面结构已变化。');
}
async function openMatch(m,item) {
  const id=idOf(m.match_id), serial=String(m.serial_no??'').trim(), home=team(m,true), away=team(m,false);
  // Prefer exact match IDs embedded in a card, then serial and team names. No fixed yesterday's names.
  const locator=page.locator('a,article,li,section,div').filter({visible:true});
  const chosen=await page.evaluate(({id,serial,home,away})=>{
    const els=[...document.querySelectorAll('a,article,li,section,div')];
    const scored=[];
    for(const el of els){
      if(!el.getClientRects().length || el.closest('#xdh23-panel')) continue;
      const s=(el.innerText||'').trim(); if(!s || s.length>650) continue;
      const matchId=el.getAttribute('data-match-id')===id || el.getAttribute('data-matchid')===id;
      const serialOK=serial && new RegExp('(^|\\D)'+serial.replace(/[.*+?^${}()|[\]\\]/g,'\\$&')+'(\\D|$)').test(s);
      const teamOK=home && away && s.includes(home) && s.includes(away);
      if(!matchId && !serialOK && !teamOK) continue;
      scored.push({el,score:(matchId?10:0)+(teamOK?5:0)+(serialOK?3:0)-s.length/1000,len:s.length});
    }
    scored.sort((a,b)=>b.score-a.score || a.len-b.len);
    if(!scored.length) return null;
    const el=scored[0].el; el.setAttribute('data-server-collector-target','yes');
    return (el.innerText||'').slice(0,200);
  },{id,serial,home,away});
  if(!chosen) throw new Error(`找不到比赛卡片 ${id} ${home} vs ${away}`);
  await page.locator('[data-server-collector-target="yes"]').first().click({timeout:cfg.timeout});
  const found=await waitUntil(async()=>{
    const current=(await page.locator('body').innerText().catch(()=>'' )).replace(/\s/g,'');
    return current.includes(home.replace(/\s/g,''))&&current.includes(away.replace(/\s/g,''));
  },12000);
  if(!found) throw new Error(`点击比赛 ${id} 后未进入详情`);
  const body=(await page.locator('body').innerText()).replace(/\s/g,'');
  const url=new URL(page.url());
  const urlId=['matchId','matchid','match_id'].map(k=>url.searchParams.get(k)).map(idOf).find(Boolean);
  if(urlId && urlId!==id) throw new Error(`详情比赛 ID 不匹配：预期 ${id}，实际 ${urlId}`);
  if(home && away && (!body.includes(home.replace(/\s/g,'')) || !body.includes(away.replace(/\s/g,'')))) {
    throw new Error(`无法确认详情球队 ${home} vs ${away}；为防止串场停止本场；card=${chosen}; page=${body.slice(0,300)}`);
  }
  item.detailIdentityVerified=true;
  item.detailIdentityEvidence=urlId===id?'detail_url_match_id_and_team_names':'visible_home_away_team_names';
  await log('进入比赛详情',{matchId:id,card:chosen});
}
async function collectMatch(m,runDir,state) {
  const id=idOf(m.match_id);
  const item={matchId:id,list:redact(m),startedAt:stamp(),finishedAt:null,modules:{},tabs:{},unclassifiedOdds:[],errors:[]};
  state.active=item;
  try {
    await openMatch(m,item);
    for(const label of TABS){
      state.tab=label;
      const module={概况:'overview',阵容:'lineup',战绩:'history',欧指:'europe',亚指:'asia',排名:'ranking',必发:'betfair'}[label];
      item.tabs[label]={attemptedAt:stamp(),clicked:false};
      try {
        const tab=page.getByText(label,{exact:true}).first();
        await tab.waitFor({state:'visible',timeout:cfg.timeout});
        await tab.click({timeout:cfg.timeout});
        item.tabs[label].clicked=true;
        await sleep(cfg.wait);
        await Promise.allSettled([...state.pending]);
        item.tabs[label].hasData=Object.values(item.modules[module]||{}).some(r=>r.valid);
      } catch(e) { item.tabs[label].error=e.message; item.errors.push({tab:label,error:e.message}); }
      item.tabs[label].finishedAt=stamp();
      await saveJSON(path.join(runDir,`${id}.json`),item);
    }
  } catch(e){item.errors.push({stage:'navigation',error:e.message}); await log('比赛失败',{matchId:id,error:e.message});}
  finally {
    await Promise.allSettled([...state.pending]);
    item.finishedAt=stamp();
    item.missingModules=['overview','lineup','history','europe','asia','ranking','betfair'].filter(k=>!Object.values(item.modules[k]||{}).some(x=>x.valid));
    await saveJSON(path.join(runDir,`${id}.json`),item);
    state.active=null;state.tab=null;
  }
  return item;
}
async function saveJSON(file,data) {const temp=file+'.tmp';await fs.writeFile(temp,JSON.stringify(data,null,2));await fs.rename(temp,file);}
function attachListener(state){
  state.requests=new WeakMap();
  page.on('request',req=>{
    const pathname=pathOf(req.url());
    const isList=/\/api\/matchjc\/SoccerMatchList$/i.test(pathname);
    const fixed=endpointRules.find(([re])=>re.test(pathname));
    const odds=/\/qkdata\/odds\/list\/\d+$/i.test(pathname);
    if(isList){state.requests.set(req,{isList:true});return;}
    if(!fixed&&!odds) return;
    const item=state.active;
    if(!item||!item.detailIdentityVerified) return;
    state.requests.set(req,{item,matchId:item.matchId,tab:state.tab,requestMatchId:responseMatchId({},req.url(),req),fixed,odds});
  });
  page.on('response',res=>{
    const meta=state.requests.get(res.request());
    const pathname=pathOf(res.url());
    if(!meta) return;
    const task=(async()=>{
      if(meta.isList){
        if(res.status()!==200) {state.listError='SoccerMatchList HTTP '+res.status();return;}
        const payload=await res.json();
        const list=listFromPayload(payload);
        if(state.awaitingList&&list.length) state.list=list;
        return;
      }
      const item=meta.item;
      let payload;
      try{payload=await res.json();}
      catch(e){item.unverifiedResponses??=[];item.unverifiedResponses.push({at:stamp(),endpoint:pathname,status:res.status(),tabAtRequest:meta.tab,reason:'response_not_json'});return;}
      const explicit=responseMatchId(payload,res.url(),res.request());
      const teams=responseTeamEvidence(payload,item);
      const identity=explicit&&explicit!==item.matchId
        ? {verified:false,reason:'response_match_id_mismatch',explicitMatchId:explicit}
        : explicit===item.matchId
          ? {verified:true,via:'response_match_id'}
          : meta.requestMatchId===item.matchId
            ? {verified:true,via:'request_match_id',requestedMatchId:meta.requestMatchId,teamNameConflict:teams.conflict||false}
            : teams.conflict
              ? {verified:false,reason:'response_team_name_mismatch',teams}
              : teams.verified
                ? {verified:true,via:'home_away_team_names',teams}
                : item.detailIdentityVerified
                  ? {verified:true,via:'request_from_verified_detail_tab'}
                  : {verified:false,reason:'match_identity_unverified'};
      const base={at:stamp(),endpoint:new URL(res.url()).origin+pathname,tabAtRequest:meta.tab,status:res.status(),identity};
      if(!identity.verified){
        item.unverifiedResponses??=[];
        item.unverifiedResponses.push({...base,valid:false,reason:identity.reason,response:redact(payload)});
        return;
      }
      if(res.status()!==200){
        const record={...base,valid:false,validityReason:'http_'+res.status(),response:redact(payload)};
        if(meta.fixed){const [,module,name]=meta.fixed;item.modules[module]??={};item.modules[module][name]=record;}
        else {item.unclassifiedOdds.push(record);}
        return;
      }
      if(meta.fixed){
        const [,module,name]=meta.fixed;
        const record={...base,valid:modulePayloadValid(module,name,payload),response:redact(payload)};
        if(module==='lineup') {
          const lineupData=payload?.data?.data ?? payload?.data ?? payload;
          const isCurrentMatch=lineupData?.isCurrentMatch===true || ['1','true'].includes(String(lineupData?.isCurrentMatch??'').toLowerCase());
          const confirmationKeys=['startingXIConfirmed','isStartingXIConfirmed','lineupConfirmed','isLineupConfirmed','confirmedXI','isConfirmed'];
          const confirmedBySource=!!(lineupData && confirmationKeys.some(key=>lineupData[key]===true || ['1','true'].includes(String(lineupData[key]??'').toLowerCase())));
          record.isCurrentMatchFlag=lineupData?.isCurrentMatch??null;
          record.currentXIConfirmed=record.valid&&isCurrentMatch&&confirmedBySource;
          if(!record.valid) record.lineupStatus='未公布或接口无有效阵容数据';
          else if(!isCurrentMatch) record.lineupStatus=`本场首发未确认（isCurrentMatch=${String(lineupData?.isCurrentMatch??'缺失')}）`;
          else if(!confirmedBySource) record.lineupStatus='本场阵容数据已返回，但来源未标注正式首发已确认';
          else record.lineupStatus='来源已确认本场首发';
          if(!record.currentXIConfirmed) record.currentXIReason=record.lineupStatus;
        }
        if(!record.valid) record.validityReason=name==='lineup'?'lineup_missing_or_unpublished':'empty_or_invalid_module_data';
        item.modules[module]??={};item.modules[module][name]=record;
        return;
      }
      if(meta.odds){
        const grouped=classifyOdds(payload);
        for(const [market,rows] of Object.entries(grouped)){
          if(!rows.length) continue;
          const response={...payload,data:rows};
          const record={...base,valid:true,marketTypeEvidence:'response.playType',response:redact(response)};
          if(market==='europe'||market==='asia'){
            item.modules[market]??={};
            item.modules[market]['odds_list_'+market]=record;
          }else if(market==='totals'){
            item.additionalMarkets??=[];item.additionalMarkets.push({...record,market:'totals'});
          }else{
            item.unclassifiedOdds.push({...record,valid:false,reason:'missing_or_unknown_playType'});
          }
        }
        if(!Object.values(grouped).some(rows=>rows.length)){
          item.unclassifiedOdds.push({...base,valid:false,reason:'no_rows_or_unrecognized_odds_payload',response:redact(payload)});
        }
      }
    })().catch(e=>log('响应解析失败',{error:e.message,endpoint:pathname}));
    state.pending.add(task);task.finally(()=>state.pending.delete(task));
  });
}
async function readSchedulerState(){
  try{return JSON.parse(await fs.readFile(path.join(ROOT,'data','.scheduler-state.json'),'utf8'));}
  catch{return {};}
}
async function writeSchedulerState(value){
  const file=path.join(ROOT,'data','.scheduler-state.json');
  const temp=file+'.tmp';
  await fs.writeFile(temp,JSON.stringify(value,null,2),{mode:0o600});
  await fs.rename(temp,file);
}
async function round(trigger='manual',hourKey=null){
  if(running) return null;
  running=true;
  const runId=local().replace(/[^0-9]/g,'-')+'-'+Date.now();
  const runDir=path.join(ROOT,'data',runId);await fs.mkdir(runDir,{recursive:true});
  const state={list:[],awaitingList:true,active:null,tab:null,pending:new Set(),listError:null};
  const summary={runId,trigger,startedAt:stamp(),matches:[],skipped:[],errors:[]};
  if(trigger==='timer') await writeSchedulerState({last_started_hour:hourKey,last_run_id:runId,status:'running',started_at:stamp(),trigger});
  try {
    context=await chromium.launchPersistentContext(path.join(ROOT,'browser-profile'),{headless:cfg.headless,locale:'zh-CN',timezoneId:cfg.tz,viewport:{width:390,height:844}});
    page=context.pages()[0] || await context.newPage();
    page.setDefaultTimeout(cfg.timeout);attachListener(state);
    await navigateToList(state);
    const list=state.list;state.awaitingList=false;
    const eligible=[],now=Date.now();
    for(const m of list){
      const check=prematchEligibility(m,now);
      if(check.ok) eligible.push(m);
      else summary.skipped.push({matchId:idOf(m.match_id),serialNo:m.serial_no??null,home:team(m,true),away:team(m,false),kickoff:m.match_time??null,reason:check.reason,minutesToKickoff:check.minutesToKickoff??null});
    }
    summary.listCount=list.length;
    summary.eligibleCount=eligible.length;
    await log('读取当前比赛列表',{count:list.length,eligible:eligible.length,skipped:summary.skipped.length,runId});
    const queue=cfg.max>0?eligible.slice(0,cfg.max):eligible;
    for(const queued of queue){
      const matchId=idOf(queued.match_id);
      try {
        state.awaitingList=true;
        await navigateToList(state);
        state.awaitingList=false;
        const current=state.list.find(x=>idOf(x.match_id)===matchId);
        if(!current) throw new Error('当前列表已更新，目标比赛不再显示');
        const eligibility=prematchEligibility(current,Date.now());
        if(!eligibility.ok){summary.skipped.push({matchId,reason:eligibility.reason,minutesToKickoff:eligibility.minutesToKickoff??null});continue;}
        const result=await collectMatch(current,runDir,state);
        const lineupRecord=Object.values(result.modules.lineup||{}).find(x=>x?.lineupStatus);
        summary.matches.push({matchId:result.matchId,home:team(result.list,true),away:team(result.list,false),kickoff:result.list.match_time,missingModules:result.missingModules,validModules:['overview','lineup','history','europe','asia','ranking','betfair'].filter(k=>Object.values(result.modules[k]||{}).some(x=>x.valid)),currentXIConfirmed:lineupRecord?.currentXIConfirmed===true,lineupStatus:lineupRecord?.lineupStatus??'未公布或接口无有效阵容数据',errors:result.errors});
      }catch(e){summary.errors.push({matchId,error:e.message});await log('单场采集异常',{matchId,error:e.message});}
      await saveJSON(path.join(runDir,'summary.json'),summary);
    }
  }catch(e){summary.errors.push({stage:'round',error:e.message});await log('本轮采集异常',{error:e.message,listError:state.listError});}
  finally {
    summary.finishedAt=stamp();
    await saveJSON(path.join(runDir,'summary.json'),summary);
    await context?.close().catch(()=>{});
    context=null;page=null;running=false;
    if(trigger==='timer'){
      const previous=await readSchedulerState();
      await writeSchedulerState({...previous,status:summary.errors.length?'failed':'completed',finished_at:summary.finishedAt});
    }
    await log('本轮结束',{runId,processed:summary.matches.length,skipped:summary.skipped.length,errors:summary.errors.length});
  }
  return summary;
}
async function runScheduledHour(key){
  if(Number(key.slice(11,13))<10) return;
  if(running) return;
  const previous=await readSchedulerState();
  if(previous.last_started_hour===key) return;
  await round('timer',key);
}
async function main(){
  if(!cfg.entry || !/^https:\/\/xd\.xiaodianhuo\.com\//.test(cfg.entry)) throw new Error('请在 .env 中设置有效的 ENTRY_URL 店铺入口网址');
  await Promise.all(['data','logs','browser-profile'].map(d=>fs.mkdir(path.join(ROOT,d),{recursive:true})));
  if(process.argv.includes('--once')){await round('manual');return;}
  if(cfg.runOnStart) await runScheduledHour(local().slice(0,13));
  let checking=false;
  setInterval(async()=>{
    const t=local();const hour=Number(t.slice(11,13));const key=t.slice(0,13);
    if(hour<10||checking||running) return;
    checking=true;
    try{await runScheduledHour(key);}finally{checking=false;}
  },60000);
}
main().catch(e=>{console.error(e);process.exitCode=1;});
