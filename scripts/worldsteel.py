"""worldsteel 월간 조강 생산 보도자료 → 지역별·국가별 월간 시계열.

출처 표기 조건(worldsteel 저작권 안내): 인용 시마다 "Source: World Steel Association (worldsteel)" 표기,
데이터 변경·왜곡 금지. 따라서 보도자료에 공표된 수치(백만 톤, 소수 1자리)를 그대로 저장.

- data/worldsteel/YYYY-MM.json : 해당 월 보도자료의 표 셀(수치)만 저장 (본문 텍스트는 저장하지 않음)
- data/series/ws_*.csv          : 지역·국가별 월간 조강 생산 시계열
- data/worldsteel/meta_ws.json  : DB센터용 지표 메타
보도자료는 해당 월 최초 공표치이며, 이후 worldsteel이 수정한 값은 반영되지 않을 수 있음.
"""
import csv
import datetime as dt
import json
import re
import sys
import time
import urllib.error
import urllib.request
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WS = ROOT / "data" / "worldsteel"
SERIES = ROOT / "data" / "series"
WS.mkdir(parents=True, exist_ok=True)
SERIES.mkdir(parents=True, exist_ok=True)
BASE = "https://worldsteel.org"
UA = {"User-Agent": "Mozilla/5.0 (steel-weekly data collector; github.com/take5000k/steel-weekly)"}
SRC = "Source: World Steel Association (worldsteel)"
START = (2021, 1)
MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august",
          "september", "october", "november", "december"]

REGION_KO = {
    "total": "세계(보도자료 집계국 합계)", "africa": "아프리카", "asia and oceania": "아시아·오세아니아",
    "eu (27)": "EU(27)", "eu (28)": "EU(28)", "europe, other": "기타 유럽", "middle east": "중동",
    "north america": "북미", "russia & other cis + ukraine": "러시아·CIS·우크라이나", "cis": "CIS",
    "south america": "남미",
}
COUNTRY_KO = {
    "china": "중국", "india": "인도", "united states": "미국", "japan": "일본", "russia": "러시아",
    "south korea": "한국", "korea": "한국", "türkiye": "튀르키예", "turkey": "튀르키예", "germany": "독일",
    "brazil": "브라질", "viet nam": "베트남", "vietnam": "베트남", "iran": "이란", "taiwan, china": "대만",
    "taiwan": "대만", "italy": "이탈리아", "mexico": "멕시코", "ukraine": "우크라이나", "france": "프랑스",
    "spain": "스페인", "canada": "캐나다", "indonesia": "인도네시아", "saudi arabia": "사우디아라비아",
}


def slug(s):
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")


def get(url):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read().decode("utf-8", "replace")


class TableParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tables, self.row, self.cell, self.in_cell = [], None, "", False

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self.tables.append([])
        elif tag == "tr" and self.tables:
            self.row = []
        elif tag in ("td", "th") and self.row is not None:
            self.in_cell, self.cell = True, ""

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self.in_cell:
            self.row.append(re.sub(r"\s+", " ", self.cell).strip())
            self.in_cell = False
        elif tag == "tr" and self.row is not None:
            if any(self.row):
                self.tables[-1].append(self.row)
            self.row = None

    def handle_data(self, data):
        if self.in_cell:
            self.cell += data


def num(x):
    try:
        return float(x.replace(",", ""))
    except (ValueError, AttributeError):
        return None


def fetch_month(y, m):
    """해당 월 보도자료 표를 받아 {'regions':{}, 'countries':{}} 반환. 없으면 None."""
    url = f"{BASE}/media/press-releases/{y}/{MONTHS[m-1]}-{y}-crude-steel-production/"
    try:
        html = get(url)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise
    p = TableParser()
    p.feed(html)
    out = {"url": url, "regions": {}, "countries": {}}
    for t in p.tables:
        if not t or len(t[0]) < 3 or "(Mt)" not in " ".join(t[0]):
            continue
        is_region = any(r[0].lower().startswith(("total", "africa", "asia")) for r in t[1:])
        for r in t[1:]:
            if len(r) < 3:
                continue
            name = re.sub(r"\s*\(e\)\s*", "", r[0]).strip()
            v, yoy = num(r[1]), num(r[2])
            if v is None:
                continue
            key = "total" if name.lower().startswith("total") else name.lower()
            (out["regions"] if is_region else out["countries"])[key] = {"label": name, "mt": v, "yoy": yoy}
    if not out["regions"] and not out["countries"]:
        return {"url": url, "error": "표를 찾지 못함"}
    return out


def months_to_check():
    t = dt.date.today()
    y, m = START
    res = []
    while (y, m) < (t.year, t.month):
        res.append((y, m))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return res


def main():
    log = []
    todo = months_to_check()
    recent = set(todo[-2:])  # 최근 2개월은 매번 다시 확인
    for y, m in todo:
        f = WS / f"{y}-{m:02d}.json"
        if f.exists() and (y, m) not in recent:
            old = json.loads(f.read_text(encoding="utf-8"))
            if "error" not in old:
                continue
        try:
            data = fetch_month(y, m)
            time.sleep(1.0)
        except Exception as e:
            log.append(f"FAIL {y}-{m:02d} {str(e)[:120]}")
            continue
        if data is None:
            log.append(f"SKIP {y}-{m:02d} 보도자료 없음(미발표 또는 주소 다름)")
            continue
        f.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        log.append(f"{'FAIL' if 'error' in data else 'OK  '} {y}-{m:02d} 지역 {len(data.get('regions', {}))} · 국가 {len(data.get('countries', {}))}")

    # 시계열 구성
    series = {}
    for f in sorted(WS.glob("20??-??.json")):
        d = json.loads(f.read_text(encoding="utf-8"))
        if "error" in d:
            continue
        y, m = map(int, f.stem.split("-"))
        date = dt.date(y, m, 1).isoformat()
        for kind, table, ko in (("r", d["regions"], REGION_KO), ("c", d["countries"], COUNTRY_KO)):
            for key, row in table.items():
                sid = "ws_total" if key == "total" else f"ws_{kind}_{slug(key)}"
                name_ko = ko.get(key, row["label"])
                s = series.setdefault(sid, {"name": name_ko, "kind": kind, "rows": {}})
                s["rows"][date] = row["mt"]
    meta = []
    for sid, s in sorted(series.items()):
        rows = sorted(s["rows"].items())
        with open(SERIES / f"{sid}.csv", "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["date", "value"])
            w.writerows(rows)
        label = "조강 생산 · " + s["name"]
        meta.append({"id": sid, "cat": "steel", "name": label, "unit": "백만 톤", "freq": "M",
                     "src_label": SRC, "status": "ok", "rows": len(rows),
                     "first": rows[0][0], "last": rows[-1][0], "latest": rows[-1][1]})
    order = {"ws_total": 0}
    meta.sort(key=lambda x: (order.get(x["id"], 1 if x["id"].startswith("ws_r_") else 2), -(x["latest"] or 0)))
    (WS / "meta_ws.json").write_text(json.dumps({"source": SRC, "indicators": meta, "log": log},
                                                 ensure_ascii=False, indent=1), encoding="utf-8")
    print("\n".join(log[-30:]))
    print(f"시계열 {len(meta)}개")

    sys.path.insert(0, str(ROOT / "scripts"))
    import collect  # noqa: E402  사이트용 통합 파일 갱신
    collect.build_db()


if __name__ == "__main__":
    main()
