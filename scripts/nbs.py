"""중국 국가통계국 「유통영역 중요 생산재 시장가격 변동 상황」(순별, 매월 상·중·하순) 수집.

공식 공표 통계(国家统计局 流通领域重要生产资料市场价格变动情况)의 표 수치를 그대로 저장.
- data/nbs/YYYY-MM-[1|2|3].json : 해당 순 공표표 전 품목(품목명·단위·가격·전기 대비)
- data/series/nbs_*.csv          : 철강·석탄 주요 품목 시계열
- data/nbs/meta_nbs.json         : DB센터용 메타
"""
import calendar
import csv
import datetime as dt
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NB = ROOT / "data" / "nbs"
SERIES = ROOT / "data" / "series"
NB.mkdir(parents=True, exist_ok=True)
SERIES.mkdir(parents=True, exist_ok=True)
LIST = "https://www.stats.gov.cn/sj/zxfb/"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"}
SRC = "중국 국가통계국 유통영역 중요 생산재 시장가격(순별)"
START_YEAR = 2021
KEY = "流通领域重要生产资料市场价格变动情况"

# 시계열로 만들 품목: (품목명 접두어, id, 분류, 한글명, 설명)
PICK = [
    ("螺纹钢", "nbs_rebar", "steel", "중국 철근 유통가(Φ20mm, HRB400E)", "전국 유통시장 철근(직경 20mm, HRB400E) 평균 거래가격, 위안/톤, 부가세 포함 유통가"),
    ("热轧普通板卷", "nbs_hrc", "steel", "중국 열연코일 유통가(4.75~11.5mm, Q235)", "전국 유통시장 열연 보통판권(두께 4.75~11.5mm, Q235, 폭 1,500mm) 평균 거래가격"),
    ("普通中板", "nbs_plate", "steel", "중국 중판 유통가(20mm, Q235)", "전국 유통시장 보통 중판(두께 20mm, Q235) 평균 거래가격"),
    ("线材", "nbs_wire", "steel", "중국 선재 유통가(Φ8~10mm, HPB300)", "전국 유통시장 선재(직경 8~10mm, HPB300) 평균 거래가격"),
    ("焦煤", "nbs_cokingcoal", "coal", "중국 강점결탄 유통가(主焦煤)", "국가통계국 유통가격 품목 '焦煤(主焦煤)': 제철용 강점결탄(황 1% 미만), 중국 내 유통시장 평균 거래가격, 위안/톤. 호주산 해상 FOB 가격(PLV 등)과는 다른 중국 내수 가격"),
    ("焦炭", "nbs_coke", "coal", "중국 야금용 코크스 유통가(준1급)", "국가통계국 유통가격 품목 '焦炭(准一级冶金焦)': 회분 12.01~13.50% 야금용 코크스 평균 거래가격"),
    ("山西优混", "nbs_thermal", "coal", "중국 발전용탄 유통가(산시 우량혼탄 5,500kcal)", "국가통계국 유통가격 품목 '山西优混(5500大卡)': 발열량 5,500kcal 발전용 혼탄 평균 거래가격(참고 지표)"),
    ("无烟煤", "nbs_anthracite", "coal", "중국 무연탄 유통가(세척 중괴)", "국가통계국 유통가격 품목 '无烟煤(洗中块)': 휘발분 8% 이하 무연탄 평균 거래가격(참고 지표)"),
]


def get(url):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read().decode("utf-8", "replace")


class TableParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.rows, self.row, self.cell, self.in_cell = [], None, "", False

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self.row = []
        elif tag in ("td", "th") and self.row is not None:
            self.in_cell, self.cell = True, ""

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self.in_cell:
            self.row.append(re.sub(r"\s+", "", self.cell))
            self.in_cell = False
        elif tag == "tr" and self.row is not None:
            if self.row:
                self.rows.append(self.row)
            self.row = None

    def handle_data(self, data):
        if self.in_cell:
            self.cell += data


def num(x):
    try:
        return float(x.replace(",", ""))
    except ValueError:
        return None


def period_of(title):
    m = re.search(r"(\d{4})年(\d{1,2})月(上|中|下)旬", title)
    if not m:
        return None
    y, mo, t = int(m.group(1)), int(m.group(2)), "上中下".index(m.group(3)) + 1
    day = {1: 10, 2: 20, 3: calendar.monthrange(y, mo)[1]}[t]
    return y, mo, t, dt.date(y, mo, day)


def list_releases(max_pages):
    found, log = {}, []
    for k in range(max_pages):
        url = LIST + ("index.html" if k == 0 else f"index_{k}.html")
        try:
            html = get(url)
        except Exception as e:
            log.append(f"FAIL 목록 {k}: {str(e)[:100]}")
            break
        n0 = len(found)
        for m in re.finditer(r'href="([^"]*t\d{8}_\d+\.html)"[^>]*>(.*?)</a>', html, re.S):
            title = re.sub(r"<[^>]+>|\s+", "", m.group(2))
            if KEY in title:
                p = period_of(title)
                if p:
                    found[p[:3]] = (urllib.parse.urljoin(url, m.group(1)), title, p[3])
        oldest = min((v[2] for v in found.values()), default=dt.date.today())
        log.append(f"목록 {k}: 누적 {len(found)}건")
        if oldest.year < START_YEAR or (k > 0 and len(found) == n0 and k > 60):
            break
        time.sleep(0.5)
    return found, log


def main():
    backfill = not any(NB.glob("20??-??-?.json"))
    found, log = list_releases(150 if backfill else 3)
    for (y, mo, t), (url, title, d) in sorted(found.items()):
        if y < START_YEAR:
            continue
        f = NB / f"{y}-{mo:02d}-{t}.json"
        if f.exists():
            continue
        try:
            p = TableParser()
            p.feed(get(url))
            time.sleep(0.5)
        except Exception as e:
            log.append(f"FAIL {y}-{mo:02d}-{t} {str(e)[:100]}")
            continue
        rows = []
        for r in p.rows:
            if len(r) >= 5 and num(r[2]) is not None:
                rows.append([r[0], r[1], num(r[2]), num(r[3]), num(r[4])])
        f.write_text(json.dumps({"url": url, "title": title, "date": d.isoformat(), "rows": rows},
                                ensure_ascii=False, indent=0), encoding="utf-8")
        log.append(f"OK   {y}-{mo:02d}-{t} 품목 {len(rows)}개")

    series = {sid: [] for _, sid, *_ in PICK}
    for f in sorted(NB.glob("20??-??-?.json")):
        d = json.loads(f.read_text(encoding="utf-8"))
        for prefix, sid, *_ in PICK:
            row = next((r for r in d["rows"] if r[0].startswith(prefix)), None)
            if row and row[2] is not None:
                series[sid].append((d["date"], row[2]))
    meta = []
    for prefix, sid, cat, name, desc in PICK:
        rows = sorted(series[sid])
        if len(rows) < 3:
            continue
        with open(SERIES / f"{sid}.csv", "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["date", "value"])
            w.writerows(rows)
        meta.append({"id": sid, "cat": cat, "name": name, "unit": "위안/톤", "freq": "T", "src_label": SRC,
                     "desc": desc, "status": "ok", "rows": len(rows), "first": rows[0][0], "last": rows[-1][0],
                     "latest": rows[-1][1]})
    (NB / "meta_nbs.json").write_text(json.dumps({"source": SRC, "indicators": meta, "log": log[-60:]},
                                                 ensure_ascii=False, indent=1), encoding="utf-8")
    print("\n".join(log[-40:]))
    print(f"시계열 {len(meta)}개")
    sys.path.insert(0, str(ROOT / "scripts"))
    import collect  # noqa: E402
    collect.build_db()


if __name__ == "__main__":
    main()
