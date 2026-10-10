"""worldsteel 월간 조강 생산 보도자료에서 표 데이터를 추출.

출처 표기 조건: "Source: World Steel Association (worldsteel)" — 데이터 변경·왜곡 금지.
1단계(현재): 최신 보도자료 3건의 제목·URL·표 셀만 data/_discover/worldsteel_parse.json 에 저장해 구조를 확인.
보도자료 본문 전체는 저장하지 않음.
"""
import json
import re
import urllib.request
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "_discover"
OUT.mkdir(parents=True, exist_ok=True)
BASE = "https://worldsteel.org"
UA = {"User-Agent": "Mozilla/5.0 (steel-weekly data collector; contact via github.com/take5000k/steel-weekly)"}


def get(url):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read().decode("utf-8", "replace")


class TableParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tables, self.row, self.cell, self.in_cell, self.depth = [], None, "", False, 0
        self.title = ""
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self.depth += 1
            self.tables.append([])
        elif tag == "tr" and self.depth:
            self.row = []
        elif tag in ("td", "th") and self.row is not None:
            self.in_cell, self.cell = True, ""
        elif tag == "h1":
            self._in_title = True

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self.in_cell:
            self.row.append(re.sub(r"\s+", " ", self.cell).strip())
            self.in_cell = False
        elif tag == "tr" and self.row is not None:
            if any(self.row):
                self.tables[-1].append(self.row)
            self.row = None
        elif tag == "table":
            self.depth -= 1
        elif tag == "h1":
            self._in_title = False

    def handle_data(self, data):
        if self.in_cell:
            self.cell += data
        if self._in_title:
            self.title += data


def main():
    result = {"source": "Source: World Steel Association (worldsteel)", "releases": [], "errors": []}
    try:
        listing = get(f"{BASE}/media/press-releases/")
    except Exception as e:
        result["errors"].append(f"목록 페이지 실패: {e}")
        listing = ""
    links = []
    for href in re.findall(r'href="([^"]+)"', listing):
        if "crude-steel-production" in href and href not in links:
            links.append(href)
    result["found_links"] = links[:10]
    for href in links[:3]:
        url = href if href.startswith("http") else BASE + href
        try:
            p = TableParser()
            p.feed(get(url))
            result["releases"].append({"url": url, "title": p.title.strip(), "tables": p.tables})
        except Exception as e:
            result["errors"].append(f"{url}: {e}")
    (OUT / "worldsteel_parse.json").write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"링크 {len(links)}건, 추출 {len(result['releases'])}건, 오류 {len(result['errors'])}건")


if __name__ == "__main__":
    main()
