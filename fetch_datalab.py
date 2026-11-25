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
  3) 모니터링(동적 목록): 모니터링할 키워드는 고정 목록이 아니라
     매일 자동으로 바뀝니다. 수동으로 목록을 고칠 필요가 없습니다.
     - 핵심(고정): 요소 디자인 기본 단어 20개. 항상 모니터링합니다.
     - 계절(날짜): 계절·이벤트 단어는 달에 맞춰 자동 교체됩니다.
       이번 달이나 다음 달에 해당하는 단어만 포함합니다.
     - 발견(유동): 새로 발견된 단어 중 급상승 상위를 목록에 추가해
       다음 실행부터 모니터링합니다. 28일 동안 움직임이 없으면
       목록에서 빠집니다.
     발견 단어 목록 상태는 trends/keywords.json에 저장돼 다음 실행 때
     이어집니다. 처음 실행 때는 파일이 없어 빈 목록으로 시작합니다.

주의: 데이터랩 응답은 그룹 단위로 옵니다. keywords는 단어 목록(문자열)
이고, data는 그 그룹 전체의 추이입니다. 그래서 키워드별 추이를
얻으려면 키워드 하나를 하나의 그룹으로 만들어야 합니다. 한 요청에
그룹은 최대 5개까지만 넣을 수 있어, 5개씩 나눠서 조회합니다.
(모니터링 최대 80개 x 일간/주간 + 발견 단어 = 하루 40번 안팎 호출,
월 한도 5만 건에 한참 못 미칩니다.)

지수는 요청 안에서만 상대값(최댓값 100)이라, 다른 묶음끼리 절대값을
직접 비교하면 안 됩니다. 그래서 급상승 판단은 같은 키워드 안에서
최근 평균이 그 전보다 얼마나 올랐는지(delta)로 합니다.
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
KEYWORDS_STATE_FILE = "trends/keywords.json"

# 핵심: 요소 디자인 기본 단어. 항상 모니터링하고, 목록에서 빠지지 않습니다.
CORE_KEYWORDS = [
    "스티커", "일러스트", "클립아트", "손그림", "캐릭터",
    "이모티콘", "배경", "패턴", "프레임", "뱃지",
    "엽서", "카드", "포스터", "달력", "다이어리",
    "플래너", "마스킹테이프", "다꾸", "굿즈", "폰꾸미기",
]

# 계절: 단어 -> 해당 월(1~12). 이번 달이나 다음 달에 해당하면 포함합니다.
SEASONAL = {
    "크리스마스": [11, 12],
    "크리스마스 트리": [11, 12],
    "산타": [11, 12],
    "연말": [11, 12, 1],
    "새해": [12, 1],
    "신년": [12, 1],
    "설날": [1, 2],
    "발렌타인": [1, 2],
    "졸업": [2, 3],
    "입학": [2, 3],
    "어린이날": [4, 5],
    "어버이날": [4, 5],
    "스승의날": [4, 5],
    "추석": [9, 10],
    "할로윈": [10],
    "빼빼로데이": [11],
    "수능": [10, 11],
    "복날": [7, 8],
    "눈사람": [12, 1, 2],
    "눈송이": [12, 1, 2],
    "겨울": [11, 12, 1, 2],
    "눈": [11, 12, 1, 2],
    "방한": [11, 12, 1, 2],
    "크리스마스 카드": [11, 12],
    "연말 파티": [11, 12],
    "송년회": [11, 12],
    "신년회": [12, 1],
    "해돋이": [12, 1],
    "겨울방학": [12, 1, 2],
    "스키": [12, 1, 2],
    "붕어빵": [11, 12, 1, 2],
    "호빵": [11, 12, 1, 2],
    "핫팩": [11, 12, 1, 2],
    "손난로": [11, 12, 1, 2],
    "코코아": [11, 12, 1, 2],
    "귤": [11, 12, 1, 2],
    "김장": [11, 12],
    "동지": [12],
    "크리스마스 선물": [11, 12],
    "연말정산": [1, 2],
}

BATCH = 5  # 한 요청에 넣을 수 있는 그룹(키워드) 최대 개수
MAX_DISCOVERED = 20  # 구글 트렌드에서 가져올 발견 단어 최대 개수
MAX_MONITORING = 80  # 모니터링 목록 전체 상한
MAX_DISCOVERED_KEEP = 30  # 발견 단어 유지 상한
MAX_NEW_DISCOVERED = 5  # 하루에 목록에 새로 추가할 발견 단어 상한
STALE_DAYS = 28  # 움직임 없이 이 일수가 지나면 발견 단어 제외
ACTIVE_DELTA = 1.0  # 이만큼 올랐으면 "움직임 있음"
ACTIVE_AVG = 10.0  # 최근 평균이 이만큼이면 "움직임 있음"


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


def seasonal_keywords(today):
    """이번 달이나 다음 달에 해당하는 계절 단어를 돌려줍니다.
    다음 달까지 보는 건 한 달쯤 앞서 뜨는 추세를 잡기 위해서입니다."""
    m = today.month
    nxt = m % 12 + 1
    return [kw for kw, months in SEASONAL.items() if m in months or nxt in months]


def load_discovered_state():
    """trends/keywords.json에서 발견 단어 목록을 읽습니다.
    파일이 없거나 깨졌으면 빈 목록(처음 실행)으로 봅니다."""
    try:
        with open(KEYWORDS_STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        items = []
        for it in data.get("keywords", []):
            kw = (it.get("keyword") or "").strip()
            if not kw:
                continue
            items.append({
                "keyword": kw,
                "addedAt": it.get("addedAt") or "",
                "lastActive": it.get("lastActive") or it.get("addedAt") or "",
            })
        return items
    except (OSError, ValueError) as e:
        print("키워드 상태 파일 없음/읽기 실패(%s): 처음 실행으로 봅니다." % e)
        return []


def save_discovered_state(items):
    """발견 단어 목록 상태를 trends/keywords.json에 저장합니다."""
    os.makedirs("trends", exist_ok=True)
    data = {
        "updatedAt": datetime.now(timezone.utc).isoformat(),
        "keywords": items,
    }
    with open(KEYWORDS_STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def is_active(item):
    """일간 요약에서 움직임이 있었는지 봅니다.
    최근 평균이 올랐거나, 최근 평균 자체가 높으면 움직임 있음으로 봅니다."""
    return item["delta"] >= ACTIVE_DELTA or item["recentAvg"] >= ACTIVE_AVG


def parse_day(value, fallback):
    try:
        return date.fromisoformat(value or "")
    except ValueError:
        return fallback


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
    today = date.today()
    # 데이터랩은 1~2일 지연이 있어 종료일을 이틀 전으로 잡습니다.
    end = today - timedelta(days=2)
    daily_start = end - timedelta(days=13)
    weekly_start = end - timedelta(days=55)

    # --- 모니터링 목록 만들기 (핵심 + 계절 + 발견 이어받기) ---
    seasonal = seasonal_keywords(today)
    carried = load_discovered_state()
    # 이미 28일 넘게 움직임이 없는 발견 단어는 제외합니다 (오늘 모니터링 안 함).
    dropped = []
    kept = []
    for c in carried:
        last = parse_day(c.get("lastActive") or c.get("addedAt"), date(2000, 1, 1))
        if (today - last).days > STALE_DAYS:
            dropped.append(c["keyword"])
        else:
            kept.append(c)
    carried = kept
    # 목록: 핵심 + 계절 + 발견 이어받기 (중복 제거, 순서 유지).
    monitoring = []
    seen = set()
    for kw in CORE_KEYWORDS + seasonal + [c["keyword"] for c in carried]:
        if kw not in seen:
            seen.add(kw)
            monitoring.append(kw)
    # 상한을 넘으면 가장 오래 안 움직인 발견 단어부터 줄입니다.
    # 핵심/계절 단어는 줄이지 않습니다.
    if len(monitoring) > MAX_MONITORING:
        base = set(CORE_KEYWORDS) | set(seasonal)
        disc = [c for c in carried if c["keyword"] not in base]
        disc.sort(key=lambda c: c.get("lastActive") or "")
        excess = len(monitoring) - MAX_MONITORING
        drop = disc[:excess]
        drop_set = {c["keyword"] for c in drop}
        dropped.extend(sorted(drop_set))
        carried = [c for c in carried if c["keyword"] not in drop_set]
        monitoring = [kw for kw in monitoring if kw not in drop_set]
    n_disc = len(monitoring) - len(CORE_KEYWORDS) - len(seasonal)
    print(
        "모니터링 목록: %d개 (핵심 %d, 계절 %d, 발견 %d)"
        % (len(monitoring), len(CORE_KEYWORDS), len(seasonal), n_disc)
    )
    if dropped:
        print("제외된 발견 단어: %s" % ", ".join(dropped))

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

    # 3) 모니터링: 동적 목록, 일간 + 주간
    daily = query_keywords(
        key_id, key, monitoring,
        daily_start.isoformat(), end.isoformat(), "date", 7, 7,
    )
    print("일간 모니터링 완료: %d개" % len(daily))
    weekly = query_keywords(
        key_id, key, monitoring,
        weekly_start.isoformat(), end.isoformat(), "week", 4, 4,
    )
    print("주간 모니터링 완료: %d개" % len(weekly))

    # --- 발견 단어 상태 갱신 ---
    # 오늘 일간 결과에서 움직임이 있었던 발견 단어는 lastActive를 오늘로.
    daily_by_kw = {it["keyword"]: it for it in daily}
    for c in carried:
        it = daily_by_kw.get(c["keyword"])
        if it is not None and is_active(it):
            c["lastActive"] = today.isoformat()
    # 새로 발견된 단어 중 급상승 상위를 목록에 추가합니다.
    # 다음 실행부터 모니터링합니다 (오늘은 이미 검증 쿼리로 확인했습니다).
    in_monitoring = set(monitoring)
    have = {c["keyword"] for c in carried}
    added = []
    for it in discovered:
        kw = it["keyword"]
        if len(added) >= MAX_NEW_DISCOVERED:
            break
        if it["delta"] <= 0:
            continue
        if kw in in_monitoring or kw in have:
            continue
        if kw in CORE_KEYWORDS or kw in seasonal:
            continue
        carried.append({
            "keyword": kw,
            "addedAt": today.isoformat(),
            "lastActive": today.isoformat(),
        })
        have.add(kw)
        added.append(kw)
    # 발견 단어 유지 상한을 넘으면 가장 오래 안 움직인 것부터 제외합니다.
    if len(carried) > MAX_DISCOVERED_KEEP:
        carried.sort(key=lambda c: c.get("lastActive") or "")
        excess = carried[:-MAX_DISCOVERED_KEEP]
        dropped.extend(c["keyword"] for c in excess)
        carried = carried[-MAX_DISCOVERED_KEEP:]
    save_discovered_state(carried)
    print(
        "발견 단어 목록: 유지 %d개, 새로 추가 %d개 (%s), 제외 %d개"
        % (len(carried), len(added), ", ".join(added), len(dropped))
    )

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
        "monitoring": {
            "total": len(monitoring),
            "core": len(CORE_KEYWORDS),
            "seasonal": len(seasonal),
            "discovered": n_disc,
            "added": added,
            "dropped": dropped,
        },
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
