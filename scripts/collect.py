"""공개 API(FRED, 한국은행 ECOS)에서 지표를 받아 data/ 아래에 저장.

- data/series/<id>.csv : date,value (일간 지표는 주간 금요일 기준 마지막 값으로 변환)
- data/meta.json       : 지표 메타정보와 최신값, 수집 결과
- data/_discover/      : ECOS 통계표 항목 목록(코드 확인용)

API 키는 환경변수 FRED_API_KEY, ECOS_API_KEY 에서만 읽으며 어떤 파일에도 기록하지 않음.
"""
import csv
import datetime as dt
import json
import os
import sys
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CFG = json.loads((ROOT / "scripts" / "indicators.json").read_text(encoding="utf-8"))
OUT = ROOT / "data"
SERIES = OUT / "series"
SERIES.mkdir(parents=True, exist_ok=True)

FRED_KEY = os.environ.get("FRED_API_KEY", "").strip()
ECOS_KEY = os.environ.get("ECOS_API_KEY", "").strip()
START = CFG["start"]
TODAY = dt.date.today()


def get_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": "steel-weekly-collector"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode("utf-8"))


def redact(msg):
    for k in (FRED_KEY, ECOS_KEY):
        if k:
            msg = msg.replace(k, "***")
    return msg


def fred(code):
    if not FRED_KEY:
        raise RuntimeError("FRED_API_KEY 없음")
    q = urllib.parse.urlencode({"series_id": code, "api_key": FRED_KEY, "file_type": "json", "observation_start": START})
    data = get_json(f"https://api.stlouisfed.org/fred/series/observations?{q}")
    rows = []
    for o in data.get("observations", []):
        if o["value"] in (".", ""):
            continue
        rows.append((dt.date.fromisoformat(o["date"]), float(o["value"])))
    return rows


def ecos_period(cycle, d):
    return {"D": d.strftime("%Y%m%d"), "M": d.strftime("%Y%m"), "Q": f"{d.year}Q{(d.month-1)//3+1}", "A": str(d.year)}[cycle]


def ecos(stat, cycle, item):
    if not ECOS_KEY:
        raise RuntimeError("ECOS_API_KEY 없음")
    s = ecos_period(cycle, dt.date.fromisoformat(START))
    e = ecos_period(cycle, TODAY)
    url = f"https://ecos.bok.or.kr/api/StatisticSearch/{ECOS_KEY}/json/kr/1/100000/{stat}/{cycle}/{s}/{e}/{item}"
    data = get_json(url)
    if "StatisticSearch" not in data:
        raise RuntimeError(f"ECOS 응답 오류: {json.dumps(data, ensure_ascii=False)[:200]}")
    rows = []
    for r in data["StatisticSearch"]["row"]:
        t = r["TIME"]
        if cycle == "D":
            d = dt.date(int(t[:4]), int(t[4:6]), int(t[6:8]))
        elif cycle == "M":
            y, m = int(t[:4]), int(t[4:6])
            d = (dt.date(y + (m == 12), m % 12 + 1, 1) - dt.timedelta(days=1))
        else:
            continue
        try:
            rows.append((d, float(r["DATA_VALUE"])))
        except ValueError:
            pass
    return rows


def ecos_calc(ind):
    """여러 ECOS 항목을 같은 날짜끼리 묶어 산식으로 계산 (모든 항목 값이 있는 날만)."""
    cols = {k: dict(ecos(ind["stat"], ind["cycle"], code)) for k, code in ind["items"].items()}
    dates = set.intersection(*(set(v) for v in cols.values()))
    expr = compile(ind["formula"], "<formula>", "eval")
    rows = []
    for d in sorted(dates):
        env = {k: cols[k][d] for k in cols}
        try:
            rows.append((d, float(eval(expr, {"__builtins__": {}}, env))))
        except (ZeroDivisionError, ValueError):
            pass
    return rows


def to_weekly(rows):
    """주(금요일 마감) 기준 마지막 관측값."""
    wk = {}
    for d, v in sorted(rows):
        fri = d + dt.timedelta(days=(4 - d.weekday()) % 7)
        wk[fri] = v
    return sorted(wk.items())


def ecos_discover(stat):
    url = f"https://ecos.bok.or.kr/api/StatisticItemList/{ECOS_KEY}/json/kr/1/2000/{stat}"
    data = get_json(url)
    items = [{k: r.get(k) for k in ("ITEM_CODE", "ITEM_NAME", "CYCLE", "START_TIME", "END_TIME", "UNIT_NAME")}
             for r in data.get("StatisticItemList", {}).get("row", [])]
    d = OUT / "_discover"
    d.mkdir(exist_ok=True)
    (d / f"ecos_{stat}.json").write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")
    return len(items)


def build_db():
    """사이트용 통합 파일 data/db.json 생성 (메타 + 전체 시계열)."""
    meta = json.loads((OUT / "meta.json").read_text(encoding="utf-8"))
    inds = []
    extra = OUT / "worldsteel" / "meta_ws.json"
    allm = meta["indicators"] + (json.loads(extra.read_text(encoding="utf-8"))["indicators"] if extra.exists() else [])
    for m in allm:
        f = SERIES / f"{m['id']}.csv"
        if not f.exists():
            continue
        with open(f, encoding="utf-8") as fh:
            rows = [[r["date"], float(r["value"])] for r in csv.DictReader(fh)]
        inds.append({k: m.get(k) for k in ("id", "cat", "name", "unit", "freq", "src_label", "status")} | {"series": rows})
    db = {"updated_at": meta["updated_at"], "indicators": inds}
    (OUT / "db.json").write_text(json.dumps(db, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def main():
    meta_path = OUT / "meta.json"
    old = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
    old_ind = {m["id"]: m for m in old.get("indicators", [])}
    out, log = [], []
    for ind in CFG["indicators"]:
        m = {k: ind[k] for k in ("id", "cat", "name", "unit", "freq", "src_label")}
        try:
            if ind["source"] == "fred":
                rows = fred(ind["code"])
            elif ind["source"] == "ecos_calc":
                rows = ecos_calc(ind)
            else:
                rows = ecos(ind["stat"], ind["cycle"], ind["item"])
            if ind["freq"] == "W":
                rows = to_weekly(rows)
            if not rows:
                raise RuntimeError("데이터 0건")
            with open(SERIES / f"{ind['id']}.csv", "w", newline="", encoding="utf-8") as f:
                w = csv.writer(f)
                w.writerow(["date", "value"])
                for d, v in rows:
                    w.writerow([d.isoformat(), round(v, 4)])
            m.update(status="ok", rows=len(rows), first=rows[0][0].isoformat(), last=rows[-1][0].isoformat(),
                     latest=rows[-1][1], prev=rows[-2][1] if len(rows) > 1 else None)
            log.append(f"OK   {ind['id']:<14} {len(rows):>5}건  최신 {rows[-1][0]} = {rows[-1][1]}")
        except Exception as e:  # 실패해도 기존 데이터는 유지
            msg = redact(str(e))[:200]
            prev = old_ind.get(ind["id"], {})
            m.update({k: prev[k] for k in ("rows", "first", "last", "latest", "prev") if k in prev})
            m.update(status="error", error=msg)
            log.append(f"FAIL {ind['id']:<14} {msg}")
        out.append(m)

    if ECOS_KEY:
        for stat in CFG.get("ecos_discover", []):
            try:
                log.append(f"INFO ECOS {stat} 항목 {ecos_discover(stat)}개 목록 저장")
            except Exception as e:
                log.append(f"FAIL ECOS 목록 {stat} {redact(str(e))[:150]}")

    meta = {"updated_at": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"), "indicators": out, "log": log}
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    build_db()
    print("\n".join(log))
    ok = sum(1 for m in out if m["status"] == "ok")
    print(f"\n성공 {ok}/{len(out)}")
    if ok == 0:
        sys.exit(1)


if __name__ == "__main__":
    if "--db-only" in sys.argv:
        build_db()
    else:
        main()
