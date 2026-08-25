import json, urllib.request, datetime, pathlib, time, re, math

OUT=pathlib.Path("data")
OUT.mkdir(exist_ok=True)
HISTORY=OUT/"history.json"
PROFILES=OUT/"profiles.json"
MAX_DETAIL_PER_RUN=220

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

    # First-half goal presence from event minutes.
    try:
        events=((detail.get("header") or {}).get("events") or [])
        first_half_goal=False
        known=False
        for ev in events:
            if str(ev.get("type","")).lower()!="goal":continue
            ts=str(ev.get("timeStr") or "")
            mm=re.search(r"(\d+)",ts)
            if not mm:continue
            known=True
            minute=int(mm.group(1))
            if minute<=45:
                first_half_goal=True
        if known:
            match["iy05"]=1 if first_half_goal else 0
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

def build_profiles(matches):
    by={}
    names={}
    for r in matches:
        for side in ("home","away"):
            tid=r.get(side+"_id")
            if tid is None:continue
            by.setdefault(str(tid),[]).append(r)
            names[str(tid)]=r.get(side)
    teams={}
    for tid,rows in by.items():
        rows=sorted(rows,key=lambda x:x.get("date",""))
        home=[r for r in rows if str(r.get("home_id"))==tid][-12:]
        away=[r for r in rows if str(r.get("away_id"))==tid][-12:]
        allr=rows[-20:]
        teams[tid]={
            "name":names.get(tid),
            "all":build_block(allr,tid),
            "home":build_block(home,tid),
            "away":build_block(away,tid),
        }
    return {"generated_at":datetime.datetime.now(datetime.timezone.utc).isoformat(),"teams":teams}

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
    for r in rows[-8:]:
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
