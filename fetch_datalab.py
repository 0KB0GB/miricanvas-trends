#!/usr/bin/env python3
"""매일 트렌드를 수집해 trends/datalab.json으로 저장합니다.
표준 라이브러리만 사용하므로 별도 설치가 필요 없습니다.
GitHub Actions에서 매일 실행됩니다.

필요한 환경변수 (깃허브 Secrets에 저장):
  NAVER_HUB_KEY_ID  - NAVER API HUB Application의 Client ID
  NAVER_HUB_KEY     - NAVER API HUB Application의 Client Secret

동작 방식 (발견 중심):
  1) 발견: 구글 트렌드 일일 급상승 검색어 RSS(한국)에서 오늘 새로 뜬
     검색어를 가져옵니다. 키가 필요 없고, 우리가 정한 목록이 아니라
     오늘 사람들이 실제로 많이 검색한 단어입니다.
  2) 검증: 발견한 단어를 데이터랩으로 확인해 최근 추이 숫자를 붙입니다.
     데이터랩은 키워드를 넣어줘야 답하는 API라, 발견된 단어를 넣어
     확인하는 용도로 씁니다.
  3) 모니터링(보조): 미리 정해둔 60개 키워드도 데이터랩으로 확인합니다.
     일간(timeUnit=date, 14일)과 주간(timeUnit=week, 8주) 두 단위로
     봅니다. 일간은 갑자기 치솟는 것, 주간은 천천히 올라오는 것을
     잡습니다.

주의: 데이터랩 응답은 그룹 단위로 옵니다. keywords는 단어 목록(문자열)
이고, data는 그 그룹 전체의 추이입니다. 그래서 키워드별 추이를
얻으려면 키워드 하나를 하나의 그룹으로 만들어야 합니다. 한 요청에
그룹은 최대 5개까지만 넣을 수 있어, 5개씩 나눠서 조회합니다.
(60개 키워드 x 일간/주간 + 발견 단어 = 하루 30번 안팎 호출,
월 한도 5만 건에 한참 못 미칩니다.)

지수는 요청 안에서만 상대값(최댓값 100)이라, 다른 묶음끼리 절대값을
직접 비교하면 안 됩니다. 그래서 급상승 판단은 같은 키워드 안에서
최근 평균이 그 전보다 얼마나 올랐는지(delta)로 합니다.

모니터링할 검색어는 아래 KEYWORDS에서 바꾸면 됩니다.
"""
import json
import os
import sys
import urllib.request
import urllib.error
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone

API_URL = "https://naverapihub.apigw.ntruss.com/search-trend/v1/search"
TRENDS_RSS_URL = "https://trends.google.com/trending/rss?geo=KR"
HT_NS = "{http://www.google.com/trends/hottrends}"

KEYWORDS = [
    # 시즌이벤트
    "크리스마스", "크리스마스 트리", "산타", "연말", "새해",
    "신년", "설날", "발렌타인", "졸업", "입학",
    "어린이날", "어버이날", "스승의날", "추석", "할로윈",
    "빼빼로데이", "수능", "복날", "눈사람", "눈송이",
    # 요소디자인
    "스티커", "일러스트", "클립아트", "손그림", "캐릭터",
    "이모티콘", "배경", "패턴", "프레임", "뱃지",
    "엽서", "카드", "포스터", "달력", "다이어리",
    "플래너", "마스킹테이프", "다꾸", "굿즈", "폰꾸미기",
    # 겨울연말
    "겨울", "눈", "방한", "크리스마스 카드", "연말 파티",
    "송년회", "신년회", "해돋이", "겨울방학", "스키",
    "붕어빵", "호빵", "핫팩", "손난로", "코코아",
    "귤", "김장", "동지", "크리스마스 선물", "연말정산",
]

BATCH = 5  # 한 요청에 넣을 수 있는 그룹(키워드) 최대 개수
MAX_DISCOVERED = 20  # 구글 트렌드에서 가져올 발견 단어 최대 개수


def fetch_datalab(key_id, key, keywords, start, end, time_unit):
    body = json.dumps(
        {
            "startDate": start,
            "endDate": end,
            "timeUnit": time_unit,
            "keywordGroups": [
                {"groupName": kw, "keywords": [kw]} for kw in keywords
            ],
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        API_URL,
        data=body,
        method="POST",
        headers={
            "X-NCP-APIGW-API-KEY-ID": key_id,
            "X-NCP-APIGW-API-KEY": key,
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as res:
            return json.loads(res.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        print(
            "API 오류 %s: %s"
            % (e.code, e.read().decode("utf-8", "replace")[:500]),
            file=sys.stderr,
        )
        print(
            "HUB Application에 '검색어 트렌드' API가 선택됐는지 확인하세요.",
            file=sys.stderr,
        )
        sys.exit(1)


def summarize(resp, recent_n, prev_n):
    """응답(results) 한 건이 키워드 하나. 그 키워드의 추이를 요약합니다.
    recent_n: 최근 구간 개수, prev_n: 그 전 구간 개수."""
    items = []
    for g in resp.get("results", []):
        kw = g.get("title")
        series = sorted(g.get("data", []), key=lambda d: d["period"])
        ratios = [float(d["ratio"]) for d in series]
        recent = ratios[-recent_n:] if recent_n else ratios
        prev = ratios[:-recent_n] if recent_n else []
        if prev_n:
            prev = prev[-prev_n:]
        prev_avg = sum(prev) / len(prev) if prev else 0.0
        recent_avg = sum(recent) / len(recent) if recent else 0.0
        delta = recent_avg - prev_avg
        ratio = (
            (recent_avg / prev_avg)
            if prev_avg > 0
            else (float("inf") if recent_avg > 0 else 0.0)
        )
        items.append(
            {
                "keyword": kw,
                "recentAvg": round(recent_avg, 2),
                "prevAvg": round(prev_avg, 2),
                "delta": round(delta, 2),
                "ratio": round(ratio, 2) if ratio != float("inf") else None,
            }
        )
    return items


def fetch_discovered():
    """구글 트렌드 일일 급상승 검색어 RSS(한국)에서 오늘 뜬 단어를 가져옵니다.
    실패하면 빈 목록을 돌려주고, 데이터랩 모니터링은 계속 진행합니다."""
    try:
        req = urllib.request.Request(
            TRENDS_RSS_URL, headers={"User-Agent": "miricanvas-trends/1.0"}
        )
        with urllib.request.urlopen(req, timeout=30) as res:
            xml_bytes = res.read()
    except Exception as e:
        print("구글 트렌드 RSS 가져오기 실패: %s" % e, file=sys.stderr)
        return []
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as e:
        print("구글 트렌드 RSS 파싱 실패: %s" % e, file=sys.stderr)
        return []
    terms = []
    seen = set()
    for item in root.iter("item"):
        title = item.findtext("title")
        if not title:
            continue
        title = title.strip()
        if not title or title in seen:
            continue
        seen.add(title)
        traffic = item.findtext(HT_NS + "approx_traffic")
        terms.append(
            {"keyword": title, "traffic": (traffic or "").strip() or None}
        )
        if len(terms) >= MAX_DISCOVERED:
            break
    return terms


def query_keywords(key_id, key, keywords, start, end, time_unit,
                   recent_n, prev_n):
    """키워드 목록을 5개씩 나눠 데이터랩으로 조회해 요약 목록을 돌려줍니다."""
    all_items = []
    for i in range(0, len(keywords), BATCH):
        batch = keywords[i : i + BATCH]
        resp = fetch_datalab(key_id, key, batch, start, end, time_unit)
        all_items.extend(summarize(resp, recent_n, prev_n))
    return all_items


def main():
    key_id = os.environ.get("NAVER_HUB_KEY_ID", "").strip()
    key = os.environ.get("NAVER_HUB_KEY", "").strip()
    if not key_id or not key:
        print(
            "NAVER_HUB_KEY_ID / NAVER_HUB_KEY 환경변수가 없습니다. "
            "깃허브 Secrets를 확인하세요.",
            file=sys.stderr,
        )
        sys.exit(1)
    # 데이터랩은 1~2일 지연이 있어 종료일을 이틀 전으로 잡습니다.
    end = date.today() - timedelta(days=2)
    daily_start = end - timedelta(days=13)
    weekly_start = end - timedelta(days=55)

    # 1) 발견: 구글 트렌드 오늘 급상승 검색어
    discovered_terms = fetch_discovered()
    print(
        "구글 트렌드 발견: %d개 (%s)"
        % (
            len(discovered_terms),
            ", ".join(t["keyword"] for t in discovered_terms[:5]),
        )
    )

    # 2) 검증: 발견한 단어를 데이터랩 일간 추이로 확인
    discovered = []
    if discovered_terms:
        words = [t["keyword"] for t in discovered_terms]
        traffic = {t["keyword"]: t["traffic"] for t in discovered_terms}
        items = query_keywords(
            key_id, key, words,
            daily_start.isoformat(), end.isoformat(), "date", 7, 7,
        )
        for it in items:
            it["traffic"] = traffic.get(it["keyword"])
        discovered = sorted(items, key=lambda x: x["delta"], reverse=True)
        print("발견 단어 데이터랩 확인 완료: %d개" % len(discovered))

    # 3) 모니터링(보조): 정해둔 60개 키워드, 일간 + 주간
    daily = query_keywords(
        key_id, key, KEYWORDS,
        daily_start.isoformat(), end.isoformat(), "date", 7, 7,
    )
    print("일간 모니터링 완료: %d개" % len(daily))
    weekly = query_keywords(
        key_id, key, KEYWORDS,
        weekly_start.isoformat(), end.isoformat(), "week", 4, 4,
    )
    print("주간 모니터링 완료: %d개" % len(weekly))

    risers = sorted(daily, key=lambda x: x["delta"], reverse=True)[:10]
    hottest = sorted(daily, key=lambda x: x["recentAvg"], reverse=True)[:10]
    weekly_risers = sorted(weekly, key=lambda x: x["delta"], reverse=True)[:10]

    data = {
        "updatedAt": datetime.now(timezone.utc).isoformat(),
        "source": "naver-datalab-hub+google-trends",
        "period": {"start": daily_start.isoformat(), "end": end.isoformat()},
        "weeklyPeriod": {
            "start": weekly_start.isoformat(),
            "end": end.isoformat(),
        },
        "keywords": daily,
        "risers": risers,
        "hottest": hottest,
        "weeklyKeywords": weekly,
        "weeklyRisers": weekly_risers,
        "discovered": discovered,
    }
    os.makedirs("trends", exist_ok=True)
    with open("trends/datalab.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    top_disc = discovered[0]["keyword"] if discovered else "-"
    top_riser = risers[0]["keyword"] if risers else "-"
    top_weekly = weekly_risers[0]["keyword"] if weekly_risers else "-"
    print(
        "저장 완료: trends/datalab.json (%s ~ %s), "
        "발견 1위: %s, 일간 급상승 1위: %s, 주간 급상승 1위: %s"
        % (daily_start, end, top_disc, top_riser, top_weekly)
    )


if __name__ == "__main__":
    main()
