import json, urllib.request, datetime, pathlib, time, re, math, unicodedata
from html.parser import HTMLParser

OUT=pathlib.Path("data")
OUT.mkdir(exist_ok=True)
HISTORY=OUT/"history.json"
PROFILES=OUT/"profiles.json"
ODDS_TODAY=OUT/"odds_today.json"
ODDS_HISTORY=OUT/"odds_history.json"
MAX_DETAIL_PER_RUN=280

def get_json(url,timeout=45):
    req=urllib.request.Request(url,headers={
        "User-Agent":"Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/131 Safari/537.36",
        "Accept":"application/json,text/plain,*/*",
        "Referer":"https://www.fotmob.com/",
    })
    with urllib.request.urlopen(req,timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))

def daily(date):
    ds=date.strftime("%Y%m%d")
    urls=[
        f"https://www.fotmob.com/api/matches?date={ds}",
        f"https://www.fotmob.com/api/data/matches?date={ds}&timezone=Europe%2FIstanbul&ccode3=TUR&includeNextDayLateNight=true",
    ]
    last=None
    for url in urls:
        try:
            obj=get_json(url)
            if obj.get("leagues") is not None:
                break
        except Exception as e:
            last=e;obj=None
    if not obj:raise last or RuntimeError("matches empty")
    leagues=[]
    for lg in obj.get("leagues",[]):
        leagues.append({
            "id":lg.get("id"),"primaryId":lg.get("primaryId"),
            "name":lg.get("name"),"ccode":lg.get("ccode"),
            "matches":lg.get("matches",[]),
        })
    return {"date":date.strftime("%Y-%m-%d"),"leagues":leagues}

def score_pair(status):
    s=str((status or {}).get("scoreStr") or "")
    m=re.search(r"(\d+)\s*[-–:]\s*(\d+)",s)
    return (int(m.group(1)),int(m.group(2))) if m else (None,None)

def flatten_finished(day_obj):
    out=[]
    d=day_obj["date"]
    for lg in day_obj.get("leagues",[]):
        for m in lg.get("matches",[]):
            st=m.get("status") or {}
            if not st.get("finished"):continue
            hg,ag=score_pair(st)
            if hg is None:continue
            home=m.get("home") or {};away=m.get("away") or {}
            out.append({
                "id":m.get("id"),"date":d,
                "league":lg.get("name"),"ccode":lg.get("ccode"),
                "home":home.get("name"),"away":away.get("name"),
                "home_id":home.get("id"),"away_id":away.get("id"),
                "hg":hg,"ag":ag,"scoreStr":st.get("scoreStr"),
            })
    return out

def stat_lookup(detail):
    result={}
    try:
        periods=((detail.get("content") or {}).get("stats") or {}).get("Periods") or {}
        allp=periods.get("All") or {}
        for item in allp.get("stats",[]) or []:
            title=str(item.get("title","")).strip().lower()
            vals=item.get("stats") or []
            if len(vals)>=2:
                result[title]=vals[:2]
    except:pass
    return result

def num(v):
    if isinstance(v,str):
        v=v.replace("%","").strip()
    try:return float(v)
    except:return None

def pick(stats,*names):
    for name in names:
        vals=stats.get(name.lower())
        if vals and len(vals)>=2:
            return num(vals[0]),num(vals[1])
    return None,None

def detail_enrich(match):
    mid=match.get("id")
    if mid is None:return False
    urls=[
        f"https://www.fotmob.com/api/matchDetails?matchId={mid}",
        f"https://www.fotmob.com/api/data/matchDetails?matchId={mid}",
    ]
    detail=None
    for u in urls:
        try:
            detail=get_json(u,35)
            if detail:break
        except:pass
    if not detail:return False

    stats=stat_lookup(detail)
    hc,ac=pick(stats,"Corner kicks","Corners")
    hy,ay=pick(stats,"Yellow cards","Yellow Cards")
    hr,ar=pick(stats,"Red cards","Red Cards")
    hs,as_=pick(stats,"Total shots","Shots")
    hst,ast=pick(stats,"Shots on target","Shots on Goal")
    hxg,axg=pick(stats,"Expected goals (xG)","Expected goals")

    if hc is not None and ac is not None:
        match["home_corners"]=hc;match["away_corners"]=ac
    if hy is not None or ay is not None or hr is not None or ar is not None:
        match["home_cards"]=(hy or 0)+(hr or 0)
        match["away_cards"]=(ay or 0)+(ar or 0)
    if hs is not None and as_ is not None:
        match["home_shots"]=hs;match["away_shots"]=as_
    if hst is not None and ast is not None:
        match["home_sot"]=hst;match["away_sot"]=ast
    if hxg is not None and axg is not None:
        match["home_xg"]=hxg;match["away_xg"]=axg

    # First-half score + goal presence from event minutes.
    try:
        events=((detail.get("header") or {}).get("events") or [])
        first_half_goal=False; known=False; hth=0; hta=0; side_known=False
        for ev in events:
            if str(ev.get("type","")).lower()!="goal":continue
            ts=str(ev.get("timeStr") or "")
            mm=re.search(r"(\d+)",ts)
            if not mm:continue
            known=True; minute=int(mm.group(1))
            if minute<=45:
                first_half_goal=True
                ih=ev.get("isHome")
                if isinstance(ih,bool):
                    side_known=True
                    if ih:hth+=1
                    else:hta+=1
        if known:
            match["iy05"]=1 if first_half_goal else 0
        if side_known:
            match["first_half_home"]=hth; match["first_half_away"]=hta; match["first_half_goals"]=hth+hta
    except:pass

    match["detail_ok"]=True
    return True

def rate(vals):
    vals=[v for v in vals if v is not None]
    if not vals:return None
    return 100*sum(1 for v in vals if v)/len(vals)

def mean(vals):
    vals=[float(v) for v in vals if v is not None]
    return sum(vals)/len(vals) if vals else None

def build_block(rows,team_id):
    gf=[];ga=[];btts=[];o15=[];o25=[];o35=[];score=[];concede=[];iy=[]
    corner_tot=[];card_tot=[];shots_tot=[];sot_tot=[]
    for r in rows:
        home=str(r.get("home_id"))==str(team_id)
        a=r["hg"] if home else r["ag"]
        b=r["ag"] if home else r["hg"]
        gf.append(a);ga.append(b);score.append(a>=1);concede.append(b>=1)
        btts.append(a>=1 and b>=1);o15.append(a+b>=2);o25.append(a+b>=3);o35.append(a+b>=4)
        if r.get("iy05") is not None:iy.append(bool(r["iy05"]))
        if r.get("home_corners") is not None and r.get("away_corners") is not None:
            corner_tot.append(float(r["home_corners"])+float(r["away_corners"]))
        if r.get("home_cards") is not None and r.get("away_cards") is not None:
            card_tot.append(float(r["home_cards"])+float(r["away_cards"]))
        if r.get("home_shots") is not None and r.get("away_shots") is not None:
            shots_tot.append(float(r["home_shots"])+float(r["away_shots"]))
        if r.get("home_sot") is not None and r.get("away_sot") is not None:
            sot_tot.append(float(r["home_sot"])+float(r["away_sot"]))

    d={
        "n":len(rows),"gf":mean(gf),"ga":mean(ga),
        "score_rate":rate(score),"concede_rate":rate(concede),
        "btts":rate(btts),"o15":rate(o15),"o25":rate(o25),"o35":rate(o35),
        "iy05":rate(iy),"iy_n":len(iy),
        "corner_n":len(corner_tot),"corner_total_mean":mean(corner_tot),
        "card_n":len(card_tot),"card_total_mean":mean(card_tot),
        "shots_n":len(shots_tot),"shots_total_mean":mean(shots_tot),
        "sot_n":len(sot_tot),"sot_total_mean":mean(sot_tot),
    }
    for line in (7.5,8.5,9.5,10.5):
        d["corner_o"+str(line).replace(".","_")]=rate([x>line for x in corner_tot])
    for line in (2.5,3.5,4.5,5.5):
        d["card_o"+str(line).replace(".","_")]=rate([x>line for x in card_tot])
    return d


def league_tier(ccode,name):
    s=str(name or "").lower()
    cc=str(ccode or "").upper()

    if any(x in s for x in (
        "cup","copa","pokal","coupe","coppa","cupen","beker","trophy",
        "champions league","europa league","conference league","qualif","friendly",
        "club world","libertadores","sudamericana"
    )):
        return None

    # Country-specific domestic pyramids.
    rules={
      "SWE":[
        (1,("allsvenskan",)),
        (2,("superettan",)),
        (3,("ettan","division 1")),
        (4,("division 2",)),
        (5,("division 3",)),
      ],
      "ENG":[
        (1,("premier league",)),
        (2,("championship",)),
        (3,("league one",)),
        (4,("league two",)),
        (5,("national league",)),
        (6,("national league north","national league south")),
      ],
      "GER":[
        (1,("bundesliga",)),
        (2,("2. bundesliga",)),
        (3,("3. liga",)),
        (4,("regionalliga",)),
        (5,("oberliga",)),
      ],
      "ESP":[
        (1,("laliga","la liga","primera división","primera division")),
        (2,("segunda división","segunda division","laliga2")),
        (3,("primera federación","primera federacion")),
        (4,("segunda federación","segunda federacion")),
        (5,("tercera federación","tercera federacion")),
      ],
      "ITA":[
        (1,("serie a",)),
        (2,("serie b",)),
        (3,("serie c",)),
        (4,("serie d",)),
      ],
      "FRA":[
        (1,("ligue 1",)),
        (2,("ligue 2",)),
        (3,("national 1","national",)),
        (4,("national 2",)),
        (5,("national 3",)),
      ],
      "NED":[
        (1,("eredivisie",)),
        (2,("eerste divisie",)),
        (3,("tweede divisie",)),
        (4,("derde divisie",)),
      ],
      "POR":[
        (1,("primeira liga","liga portugal")),
        (2,("liga portugal 2","segunda liga")),
        (3,("liga 3",)),
      ],
      "TUR":[
        (1,("süper lig","super lig")),
        (2,("1. lig",)),
        (3,("2. lig",)),
        (4,("3. lig",)),
      ],
      "NOR":[
        (1,("eliteserien",)),
        (2,("obos-ligaen","1. divisjon")),
        (3,("2. divisjon",)),
        (4,("3. divisjon",)),
      ],
      "DEN":[
        (1,("superliga",)),
        (2,("1st division","1. division")),
        (3,("2nd division","2. division")),
        (4,("3rd division","3. division")),
      ],
      "SCO":[
        (1,("premiership",)),
        (2,("championship",)),
        (3,("league one",)),
        (4,("league two",)),
      ],
      "BEL":[
        (1,("pro league","first division a")),
        (2,("challenger pro league","first division b")),
      ],
      "AUT":[
        (1,("bundesliga",)),
        (2,("2. liga",)),
        (3,("regionalliga",)),
      ],
      "SUI":[
        (1,("super league",)),
        (2,("challenge league",)),
        (3,("promotion league",)),
      ],
      "POL":[
        (1,("ekstraklasa",)),
        (2,("i liga","1 liga")),
        (3,("ii liga","2 liga")),
      ],
      "CZE":[
        (1,("first league","1. liga")),
        (2,("national football league","2. liga")),
      ],
      "GRE":[
        (1,("super league",)),
        (2,("super league 2",)),
      ],
      "ROU":[
        (1,("liga i","liga 1")),
        (2,("liga ii","liga 2")),
      ],
      "CRO":[
        (1,("hnl","1. hnl")),
        (2,("prva nl","2. hnl")),
      ],
      "SRB":[
        (1,("super liga",)),
        (2,("prva liga",)),
      ],
      "BRA":[
        (1,("série a","serie a")),
        (2,("série b","serie b")),
        (3,("série c","serie c")),
        (4,("série d","serie d")),
      ],
      "ARG":[
        (1,("primera división","primera division","liga profesional")),
        (2,("primera nacional",)),
        (3,("primera b metropolitana",)),
      ],
      "USA":[
        (1,("mls","major league soccer")),
        (2,("usl championship",)),
        (3,("usl league one",)),
      ],
      "MEX":[
        (1,("liga mx",)),
        (2,("liga de expansión","liga de expansion")),
      ],
      "JPN":[
        (1,("j1 league",)),
        (2,("j2 league",)),
        (3,("j3 league",)),
      ],
      "KOR":[
        (1,("k league 1","k-league 1")),
        (2,("k league 2","k-league 2")),
      ],
      "AUS":[
        (1,("a-league",)),
      ],
    }

    for tier,names in rules.get(cc,[]):
        if any(x in s for x in names):
            # Bundesliga special case: don't mistake 2. Bundesliga for tier 1.
            if cc=="GER" and tier==1 and "2. bundesliga" in s:
                continue
            return tier

    # Generic fallbacks, conservative.
    generic_top=("premier division","premier league","super league","superliga","premiership")
    if any(x in s for x in generic_top):return 1
    if "division 1" in s or "first division" in s:return 2
    if "division 2" in s or "second division" in s:return 3
    if "division 3" in s or "third division" in s:return 4
    return None

def dominant_league(rows):
    # Cups/friendlies must never become the team's league identity.
    vals=[]
    for r in rows:
        lg=r.get("league")
        cc=r.get("ccode")
        tier=league_tier(cc,lg)
        if tier is None:
            continue
        vals.append((str(cc or ""),str(lg or ""),tier))
    if not vals:
        return {"ccode":None,"league":None,"tier":None,"matches":0,"share":0.0}
    counts={}
    for v in vals:counts[v]=counts.get(v,0)+1
    (cc,lg,tier),n=max(counts.items(),key=lambda kv:kv[1])
    return {"ccode":cc,"league":lg,"tier":tier,"matches":n,"share":n/max(1,len(vals))}

def base_power_from_tier(tier):
    # Wide enough separation that cross-tier cup games materially change expectation.
    return {1:90,2:76,3:64,4:54,5:47,6:42}.get(tier,68)

def tier_power_bounds(tier):
    return {
        1:(72,98),
        2:(62,87),
        3:(54,77),
        4:(46,66),
        5:(40,59),
        6:(36,53),
    }.get(tier,(35,98))

def team_result_metrics(rows,tid):
    pts=[];gd=[];gf=[];ga=[]
    for r in rows[-20:]:
        home=str(r.get("home_id"))==str(tid)
        a=int(r.get("hg",0)) if home else int(r.get("ag",0))
        b=int(r.get("ag",0)) if home else int(r.get("hg",0))
        gf.append(a);ga.append(b);gd.append(a-b)
        pts.append(3 if a>b else (1 if a==b else 0))
    if not pts:return {"ppg":None,"gd":None,"gf":None,"ga":None}
    return {
        "ppg":sum(pts)/len(pts),
        "gd":sum(gd)/len(gd),
        "gf":sum(gf)/len(gf),
        "ga":sum(ga)/len(ga),
    }

def build_strength_meta(by):
    meta={}
    for tid,rows in by.items():
        rows=sorted(rows,key=lambda x:x.get("date",""))
        dom=dominant_league(rows[-30:])
        perf=team_result_metrics(rows,tid)
        base=base_power_from_tier(dom["tier"])
        if perf["ppg"] is None:
            power=base
        else:
            power=base+(perf["ppg"]-1.35)*6.0+float(perf["gd"] or 0)*4.0
        lo,hi=tier_power_bounds(dom.get("tier"))
        bounded=max(lo,min(hi,power))
        meta[tid]={
            **dom,**perf,
            "base_power":bounded,
            "power":bounded,
        }

    # Opponent-quality adjustment. One pass is enough and avoids recursive instability.
    for tid,rows in by.items():
        vals=[]
        for r in sorted(rows,key=lambda x:x.get("date",""))[-16:]:
            home=str(r.get("home_id"))==str(tid)
            oid=str(r.get("away_id") if home else r.get("home_id"))
            opp=meta.get(oid)
            if not opp:continue
            a=int(r.get("hg",0)) if home else int(r.get("ag",0))
            b=int(r.get("ag",0)) if home else int(r.get("hg",0))
            pts=3 if a>b else (1 if a==b else 0)
            val=50+(pts-1.35)*9+(a-b)*4+(float(opp.get("base_power",68))-68)*.28
            vals.append(max(0,min(100,val)))
        if vals:
            oq=sum(vals)/len(vals)
            meta[tid]["opponent_form"]=oq
            lo,hi=tier_power_bounds(meta[tid].get("tier"))
            meta[tid]["power"]=max(lo,min(hi,.78*meta[tid]["base_power"]+.22*oq))
        else:
            meta[tid]["opponent_form"]=None

        # Context confidence: tier known + dominant-league share + enough matches.
        n=len(by.get(tid,[]))
        known=1.0 if meta[tid].get("tier") is not None else 0.0
        share=float(meta[tid].get("share") or 0)
        sample=min(1.0,n/12)
        meta[tid]["context_confidence"]=round(100*(.55*known+.25*share+.20*sample),1)

    return meta

def build_profiles(matches):
    by={}
    names={}
    for r in matches:
        for side in ("home","away"):
            tid=r.get(side+"_id")
            if tid is None:continue
            by.setdefault(str(tid),[]).append(r)
            names[str(tid)]=r.get(side)

    strength=build_strength_meta(by)
    teams={}
    for tid,rows in by.items():
        rows=sorted(rows,key=lambda x:x.get("date",""))
        home=[r for r in rows if str(r.get("home_id"))==tid][-12:]
        away=[r for r in rows if str(r.get("away_id"))==tid][-12:]
        allr=rows[-20:]
        sm=strength.get(tid,{})
        teams[tid]={
            "name":names.get(tid),
            "all":build_block(allr,tid),
            "home":build_block(home,tid),
            "away":build_block(away,tid),
            "context":{
                "ccode":sm.get("ccode"),
                "dominant_league":sm.get("league"),
                "tier":sm.get("tier"),
                "league_matches":sm.get("matches",0),
                "league_share":sm.get("share",0),
                "ppg":sm.get("ppg"),
                "goal_diff":sm.get("gd"),
                "gf":sm.get("gf"),
                "ga":sm.get("ga"),
                "base_power":sm.get("base_power",68),
                "opponent_form":sm.get("opponent_form"),
                "power":sm.get("power",68),
                "context_confidence":sm.get("context_confidence",0),
            }
        }
    return {
        "generated_at":datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "schema_version":22,
        "teams":teams
    }

tz=datetime.timezone(datetime.timedelta(hours=3))
today=datetime.datetime.now(datetime.timezone.utc).astimezone(tz).date()

today_obj=daily(today)
tomorrow_obj=daily(today+datetime.timedelta(days=1))
(OUT/"today.json").write_text(json.dumps(today_obj,ensure_ascii=False),encoding="utf-8")
(OUT/"tomorrow.json").write_text(json.dumps(tomorrow_obj,ensure_ascii=False),encoding="utf-8")
print("today",sum(len(x["matches"]) for x in today_obj["leagues"]),"matches")

existing=[]
if HISTORY.exists():
    try:existing=json.loads(HISTORY.read_text(encoding="utf-8")).get("matches",[])
    except:existing=[]

# First run: 300 days. Later runs: yesterday only.
if len(existing)<1000:
    dates=[today-datetime.timedelta(days=i) for i in range(1,301)]
    print("Bootstrapping 300-day result history...")
else:
    dates=[today-datetime.timedelta(days=1)]

all_matches={str(x.get("id")):x for x in existing if x.get("id") is not None}
for idx,d in enumerate(reversed(dates),1):
    try:
        obj=daily(d)
        for x in flatten_finished(obj):all_matches[str(x["id"])]=x
        if idx%10==0 or idx==len(dates):
            print("history",idx,"/",len(dates),d)
        time.sleep(.12)
    except Exception as e:
        print("history error",d,e)

# Keep rolling 400 days.
cutoff=(today-datetime.timedelta(days=400)).isoformat()
hist=[x for x in all_matches.values() if str(x.get("date",""))>=cutoff]
hist.sort(key=lambda x:(x.get("date",""),str(x.get("id",""))))

# Detail enrichment: prioritize recent matches of today's teams.
today_team_ids=set()
for lg in today_obj.get("leagues",[]):
    for m in lg.get("matches",[]):
        h=m.get("home") or {};a=m.get("away") or {}
        if h.get("id") is not None:today_team_ids.add(str(h["id"]))
        if a.get("id") is not None:today_team_ids.add(str(a["id"]))

team_hist={tid:[] for tid in today_team_ids}
for r in hist:
    h=str(r.get("home_id"));a=str(r.get("away_id"))
    if h in team_hist:team_hist[h].append(r)
    if a in team_hist:team_hist[a].append(r)

targets={}
for tid,rows in team_hist.items():
    for r in rows[-14:]:
        if not r.get("detail_ok"):
            targets[str(r["id"])]=r

print("detail candidates",len(targets))
count=0
for mid,r in list(targets.items())[:MAX_DETAIL_PER_RUN]:
    try:
        if detail_enrich(r):
            count+=1
        if count%20==0 and count:
            print("details",count)
        time.sleep(.22)
    except Exception as e:
        pass
print("detail enriched this run",count)

HISTORY.write_text(
    json.dumps({"generated_at":datetime.datetime.now(tz).isoformat(),"matches":hist},ensure_ascii=False),
    encoding="utf-8"
)
PROFILES.write_text(
    json.dumps(build_profiles(hist),ensure_ascii=False),
    encoding="utf-8"
)
print("history.json",len(hist),"matches")
print("profiles.json written")




# ---------------- V25 TRUE-OPENING TR-MARKET ODDS SNAPSHOTS ----------------
# Public pages expose current Iddaa-style prices. We store the FIRST hourly observation
# as opening and the latest observation as current. Site prediction percentages are ignored.
from html.parser import HTMLParser
import unicodedata

ODDS_PAGES={
    "MS1":"https://www.iddaaorantahmin.com/iddaa-oran-analiz/mac-sonucu-ev-sahibi-kazanir",
    "MSX":"https://www.iddaaorantahmin.com/iddaa-oran-analiz/mac-sonucu-berabere-biter",
    "KG Var":"https://www.iddaaorantahmin.com/iddaa-oran-analiz/kg-var-analizi",
    "KG Yok":"https://www.iddaaorantahmin.com/iddaa-oran-analiz/kg-yok-analizi",
    "1.5 Üst":"https://www.iddaaorantahmin.com/iddaa-oran-analiz/mac-1-5-ust-biter",
    "2.5 Üst":"https://www.iddaaorantahmin.com/iddaa-oran-analiz/mac-2-5-ust-biter",
    "2.5 Alt":"https://www.iddaaorantahmin.com/iddaa-oran-analiz/mac-2-5-alt-biter",
    "İY 0.5 Üst":"https://www.iddaaorantahmin.com/iddaa-oran-analiz/ilk-yari-ust-0-5",
}

class _Table(HTMLParser):
    def __init__(self):super().__init__();self.rows=[];self.row=None;self.cell=None
    def handle_starttag(self,tag,attrs):
        if tag=='tr':self.row=[]
        elif tag in ('td','th') and self.row is not None:self.cell=[]
    def handle_data(self,data):
        if self.cell is not None:self.cell.append(data)
    def handle_endtag(self,tag):
        if tag in ('td','th') and self.cell is not None and self.row is not None:
            self.row.append(' '.join(' '.join(self.cell).split()));self.cell=None
        elif tag=='tr' and self.row is not None:
            if self.row:self.rows.append(self.row)
            self.row=None

def _norm(s):
    s=unicodedata.normalize('NFKD',str(s or '')).encode('ascii','ignore').decode().lower()
    return re.sub(r'[^a-z0-9]+',' ',s).strip()

def _sim(a,b):
    A=set(_norm(a).split());B=set(_norm(b).split())
    return len(A&B)/max(1,len(A|B))

def scrape_market(url,target_date):
    req=urllib.request.Request(url,headers={'User-Agent':'Mozilla/5.0','Accept':'text/html,*/*'})
    with urllib.request.urlopen(req,timeout=40) as r:html=r.read().decode('utf-8','ignore')
    p=_Table();p.feed(html);out=[]
    for cells in p.rows:
        if len(cells)<5:continue
        datecell=cells[0];match=cells[2];odd=cells[-1]
        if target_date.isoformat() not in datecell or ' - ' not in match:continue
        try:o=float(str(odd).replace(',','.'))
        except:continue
        home,away=match.split(' - ',1)
        if 1.01<=o<=50:out.append({'home':home.strip(),'away':away.strip(),'odd':o})
    return out

def _odd_num(cell):
    s=str(cell or '').replace(',','.')
    nums=re.findall(r'(?<!\d)(\d+(?:\.\d+)?)(?!\d)',s)
    if not nums:return None
    try:
        vals=[float(x) for x in nums]
        vals=[x for x in vals if 1.01<=x<=50]
        return vals[-1] if vals else None
    except:return None

def scrape_bulletin_url(url):
    """Parse a bulletin URL. Historical dated pages are treated as archived opening prices."""
    req=urllib.request.Request(url,headers={'User-Agent':'Mozilla/5.0','Accept':'text/html,*/*'})
    with urllib.request.urlopen(req,timeout=45) as r:html=r.read().decode('utf-8','ignore')
    p=_Table();p.feed(html);out=[]
    for cells in p.rows:
        if len(cells)<15:continue
        # Typical columns: Lig,Saat,Kod,MBS,Maç,1,0,2,2.5A,2.5U,3.5A,3.5U,1X,12,X2,VAR,YOK,...
        match_i=None
        for i,cell in enumerate(cells):
            if ' - ' in cell and i>=2:
                match_i=i;break
        if match_i is None or match_i+8>=len(cells):continue
        match=cells[match_i]
        try:home,away=match.split(' - ',1)
        except:continue
        vals=cells[match_i+1:]
        mapping={}
        names=['MS1','MSX','MS2','2.5 Alt','2.5 Üst','3.5 Alt','3.5 Üst','1X','12','X2','KG Var','KG Yok']
        for name,cell in zip(names,vals[:len(names)]):
            o=_odd_num(cell)
            if o is not None:mapping[name]=o
        if mapping:
            out.append({'home':home.strip(),'away':away.strip(),'markets':mapping})
    return out


def scrape_bulletin():
    return scrape_bulletin_url('https://www.iddaaorantahmin.com/iddaa-bulteni')

def scrape_archive_day(d):
    url=f"https://www.iddaaorantahmin.com/iddaa-bulteni/{d.strftime('%d/%m/%Y')}"
    return scrape_bulletin_url(url)

def _match_hist_for_archive(hist_by_date,d,home,away):
    best=None;bestscore=0.0
    for fr in hist_by_date.get(d.isoformat(),[]):
        score=(_sim(home,fr.get('home'))+_sim(away,fr.get('away')))/2
        if score>bestscore:bestscore=score;best=fr
    return best if bestscore>=.50 else None

def backfill_archive_odds(hist,hmap,target_date,days=120):
    """Backfill exact archived opening odds for the previous 120 days. Persistent and restart-safe."""
    hist_by_date={}
    for fr in hist:
        hist_by_date.setdefault(str(fr.get('date',''))[:10],[]).append(fr)
    existing_dates=set(str(x.get('date',''))[:10] for x in hmap.values() if x.get('date') and x.get('opening_kind')=='archive_opening')
    wanted=[target_date-datetime.timedelta(days=i) for i in range(1,days+1)]
    missing=[d for d in wanted if d.isoformat() not in existing_dates]
    # First V23 run fills the whole 120-day window; later runs normally fetch only the new missing day(s).
    print('archive backfill missing days',len(missing))
    for j,d in enumerate(reversed(missing),1):
        try:
            rows=scrape_archive_day(d)
            added=0
            for n,row in enumerate(rows):
                markets=row.get('markets') or {}
                if markets.get('KG Var') is None or markets.get('MS1') is None: continue
                fr=_match_hist_for_archive(hist_by_date,d,row.get('home'),row.get('away'))
                if not fr: continue
                mid=str(fr.get('id') or f"archive:{d.isoformat()}:{_norm(row.get('home'))}:{_norm(row.get('away'))}")
                rec={
                    'match_id':fr.get('id') or mid,'home':fr.get('home') or row.get('home'),'away':fr.get('away') or row.get('away'),
                    'league':fr.get('league'),'opening':markets,'current':markets,'opening_kind':'archive_opening',
                    'first_seen':d.isoformat(),'updated_at':d.isoformat(),'date':d.isoformat(),
                    'hg':fr.get('hg'),'ag':fr.get('ag'),'first_half_goals':fr.get('first_half_goals'),'first_half_home':fr.get('first_half_home'),'first_half_away':fr.get('first_half_away'),
                }
                hmap[mid]=rec;added+=1
            print('archive',j,'/',len(missing),d,'rows',len(rows),'matched',added)
            time.sleep(.10)
        except Exception as e:
            print('archive error',d,e)
    return hmap

def collect_tr_odds(today_obj,target_date):
    fixtures=[]
    for lg in today_obj.get('leagues',[]):
        for m in lg.get('matches',[]):
            h=(m.get('home') or {}).get('name');a=(m.get('away') or {}).get('name')
            if h and a:fixtures.append({'match_id':m.get('id'),'home':h,'away':a,'league':lg.get('name')})
    collected={str(x['match_id']):dict(x,markets={}) for x in fixtures if x.get('match_id') is not None}

    # Preferred source: full bulletin in one request.
    used_bulletin=False
    try:
        rows=scrape_bulletin();used_bulletin=bool(rows)
        for row in rows:
            best=None;bestscore=0
            for fx in fixtures:
                score=(_sim(row['home'],fx['home'])+_sim(row['away'],fx['away']))/2
                if score>bestscore:bestscore=score;best=fx
            if best and bestscore>=.48 and best.get('match_id') is not None:
                collected[str(best['match_id'])]['markets'].update(row.get('markets') or {})
        print('bulletin odds rows',len(rows))
    except Exception as e:
        print('bulletin odds error',e)

    # Fallback / extra market source. Never use the site's prediction percentage, only price.
    if not used_bulletin:
        for market,url in ODDS_PAGES.items():
            try:rows=scrape_market(url,target_date)
            except Exception as e:
                print('odds scrape error',market,e);continue
            for row in rows:
                best=None;bestscore=0
                for fx in fixtures:
                    score=(_sim(row['home'],fx['home'])+_sim(row['away'],fx['away']))/2
                    if score>bestscore:bestscore=score;best=fx
                if best and bestscore>=.48 and best.get('match_id') is not None:
                    collected[str(best['match_id'])]['markets'][market]=row['odd']
            time.sleep(.15)
    return [x for x in collected.values() if x['markets']]

def collect_archive_openings_for_fixtures(today_obj,target_date):
    """Use the dated bulletin as true/archived opening when the site exposes it; never invent a price."""
    fixtures=[]
    for lg in today_obj.get('leagues',[]):
        for m in lg.get('matches',[]):
            h=(m.get('home') or {}).get('name');a=(m.get('away') or {}).get('name')
            if h and a and m.get('id') is not None:fixtures.append({'match_id':m.get('id'),'home':h,'away':a})
    out={}
    try: rows=scrape_archive_day(target_date)
    except Exception as e:
        print('today archive opening error',e); return out
    for row in rows:
        best=None;bestscore=0
        for fx in fixtures:
            score=(_sim(row.get('home'),fx['home'])+_sim(row.get('away'),fx['away']))/2
            if score>bestscore:bestscore=score;best=fx
        if best and bestscore>=.48:
            out[str(best['match_id'])]=dict(row.get('markets') or {})
    print('today archive opening matches',len(out))
    return out

def update_odds_memory(today_obj,hist,target_date):
    try:old=json.loads(ODDS_TODAY.read_text(encoding='utf-8')) if ODDS_TODAY.exists() else {}
    except:old={}
    oldmap={str(x.get('match_id')):x for x in old.get('matches',[]) if x.get('match_id') is not None}
    fresh=collect_tr_odds(today_obj,target_date)
    archive_openings=collect_archive_openings_for_fixtures(today_obj,target_date)
    now=datetime.datetime.now(tz).isoformat();rows=[]
    for x in fresh:
        k=str(x['match_id']);prev=oldmap.get(k,{})
        archived=dict(archive_openings.get(k) or {})
        # STRICT separation: opening contains ONLY the dated-bulletin archived opening.
        # First-observed/current prices are never copied into opening.
        opening=dict(archived)
        current=dict(x.get('markets') or {})
        first_observed=dict(prev.get('first_observed') or {})
        if not first_observed:
            first_observed=dict(current)
        kind='archive_opening' if archived else 'opening_unverified'
        rows.append({**{z:x.get(z) for z in ('match_id','home','away','league')},
                     'opening':opening,'current':current,'first_observed':first_observed,
                     'opening_kind':kind,'opening_verified':bool(archived),
                     'first_seen':prev.get('first_seen') or now,'updated_at':now})
    ODDS_TODAY.write_text(json.dumps({'schema_version':25,'generated_at':now,'market':'TR','source':'public TR iddaa bulletin / odds pages','matches':rows},ensure_ascii=False),encoding='utf-8')

    # Move finished snapshots into durable pattern history, attaching actual scores.
    try:oh=json.loads(ODDS_HISTORY.read_text(encoding='utf-8')) if ODDS_HISTORY.exists() else {}
    except:oh={}
    hmap={str(x.get('match_id')):x for x in oh.get('matches',[]) if x.get('match_id') is not None}
    finished={str(x.get('id')):x for x in hist if x.get('id') is not None}
    for k,x in oldmap.items():
        fr=finished.get(k)
        if not fr or x.get('opening_kind')!='archive_opening' or not (x.get('opening') or {}):continue
        rec=dict(x);rec.update({'date':fr.get('date'),'hg':fr.get('hg'),'ag':fr.get('ag'),'first_half_goals':fr.get('first_half_goals'),'first_half_home':fr.get('first_half_home'),'first_half_away':fr.get('first_half_away')})
        hmap[k]=rec
    # Historical archive: last 120 days, exact opening odds + actual final scores.
    hmap=backfill_archive_odds(hist,hmap,target_date,120)
    cutoff=(target_date-datetime.timedelta(days=125)).isoformat()
    vals=[x for x in hmap.values() if x.get('opening_kind')=='archive_opening' and str(x.get('date') or x.get('first_seen') or '')[:10]>=cutoff]
    vals.sort(key=lambda x:(str(x.get('date','')),str(x.get('match_id',''))))
    ODDS_HISTORY.write_text(json.dumps({'schema_version':25,'generated_at':now,'market':'TR','window_days':120,'signature':'TRUE_OPENING_EXACT_KG_VAR_PLUS_MS1','matches':vals},ensure_ascii=False),encoding='utf-8')
    print('TR odds snapshots',len(rows),'TRUE-opening exact 120d pattern history',len(vals))

# V22 odds-memory storage. This updater never invents prices.
# When a TR-market odds collector writes matches here, verified archived prices stay opening; first-observed is stored separately,
# later snapshots become current, and finished rows can be retained in odds_history.json.
def ensure_odds_store(path,kind):
    if path.exists():
        try:
            obj=json.loads(path.read_text(encoding="utf-8"))
            if isinstance(obj,dict):
                obj["schema_version"]=25;obj["generated_at"]=datetime.datetime.now(tz).isoformat()
                path.write_text(json.dumps(obj,ensure_ascii=False),encoding="utf-8");return
        except:pass
    path.write_text(json.dumps({"schema_version":25,"generated_at":datetime.datetime.now(tz).isoformat(),"market":"TR","kind":kind,"matches":[]},ensure_ascii=False),encoding="utf-8")

ensure_odds_store(ODDS_TODAY,"opening_current")
ensure_odds_store(ODDS_HISTORY,"pattern_history")
try:
    update_odds_memory(today_obj,hist,today)
except Exception as e:
    print("odds memory update error",e)
print("odds memory store ready (real prices only; empty if no TR odds feed)")
