import json, urllib.request, datetime, pathlib

OUT=pathlib.Path("data")
OUT.mkdir(exist_ok=True)

def get_json(url):
    req=urllib.request.Request(url,headers={
        "User-Agent":"Mozilla/5.0",
        "Accept":"application/json,text/plain,*/*"
    })
    with urllib.request.urlopen(req,timeout=40) as r:
        return json.loads(r.read().decode("utf-8"))

def daily(date):
    ds=date.strftime("%Y%m%d")
    url=(
        "https://www.fotmob.com/api/data/matches"
        f"?date={ds}&timezone=Europe%2FIstanbul&ccode3=TUR"
        "&includeNextDayLateNight=true"
    )
    obj=get_json(url)
    # Store original useful shape, trimmed only to leagues/matches.
    leagues=[]
    for lg in obj.get("leagues",[]):
        leagues.append({
            "id":lg.get("id"),
            "primaryId":lg.get("primaryId"),
            "name":lg.get("name"),
            "ccode":lg.get("ccode"),
            "matches":lg.get("matches",[]),
        })
    return {"date":date.strftime("%Y-%m-%d"),"leagues":leagues}

today=datetime.datetime.now(datetime.timezone.utc).astimezone(
    datetime.timezone(datetime.timedelta(hours=3))
).date()

for offset in (0,1):
    d=today+datetime.timedelta(days=offset)
    obj=daily(d)
    name="today.json" if offset==0 else "tomorrow.json"
    (OUT/name).write_text(json.dumps(obj,ensure_ascii=False),encoding="utf-8")
    print(name, sum(len(x["matches"]) for x in obj["leagues"]), "matches")
