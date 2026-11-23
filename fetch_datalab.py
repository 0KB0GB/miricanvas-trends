#!/usr/bin/env python3
"""NAVER API HUB 검색어 트렌드를 매일 가져와 trends/datalab.json으로 저장합니다.
표준 라이브러리만 사용하므로 별도 설치가 필요 없습니다.
GitHub Actions에서 매일 실행됩니다.

필요한 환경변수 (깃허브 Secrets에 저장):
  NAVER_HUB_KEY_ID  - NAVER API HUB Application의 Client ID
  NAVER_HUB_KEY     - NAVER API HUB Application의 Client Secret

주의: 데이터랩 응답은 그룹 단위로 옵니다. keywords는 단어 목록(문자열)이고,
data는 그 그룹 전체의 추이입니다. 그래서 키워드별 추이를 얻으려면
키워드 하나를 하나의 그룹으로 만들어야 합니다. 한 요청에 그룹은 최대
5개까지만 넣을 수 있어, 키워드를 5개씩 나눠서 여러 번 조회합니다.
(60개 키워드 = 12번 호출, 월 한도 5만 건에 한참 못 미칩니다.)

지수는 요청 안에서만 상대값(최댓값 100)이라, 다른 묶음끼리 절대값을
직접 비교하면 안 됩니다. 그래서 급상승 판단은 같은 키워드 안에서
최근 7일 평균이 그 전 7일보다 얼마나 올랐는지(delta)로 합니다.
hottest는 참고용입니다.

추적할 검색어는 아래 KEYWORDS에서 바꾸면 됩니다.
"""
import json
import os
import sys
import urllib.request
import urllib.error
from datetime import date, datetime, timedelta, timezone

API_URL = "https://naverapihub.apigw.ntruss.com/search-trend/v1/search"

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


def fetch_batch(key_id, key, keywords, start, end):
    body = json.dumps(
        {
            "startDate": start,
            "endDate": end,
            "timeUnit": "date",
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


def summarize(resp):
    """응답(results) 한 건이 키워드 하나. 그 키워드의 추이를 요약합니다."""
    items = []
    for g in resp.get("results", []):
        kw = g.get("title")
        series = sorted(g.get("data", []), key=lambda d: d["period"])
        ratios = [float(d["ratio"]) for d in series]
        recent = ratios[-7:]
        prev = ratios[:-7]
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
    start = end - timedelta(days=13)
    all_kw = []
    for i in range(0, len(KEYWORDS), BATCH):
        batch = KEYWORDS[i : i + BATCH]
        resp = fetch_batch(
            key_id, key, batch, start.isoformat(), end.isoformat()
        )
        all_kw.extend(summarize(resp))
        print("조회 완료: %s" % ", ".join(batch))
    risers = sorted(all_kw, key=lambda x: x["delta"], reverse=True)[:10]
    hottest = sorted(all_kw, key=lambda x: x["recentAvg"], reverse=True)[:10]
    data = {
        "updatedAt": datetime.now(timezone.utc).isoformat(),
        "source": "naver-datalab-hub",
        "period": {"start": start.isoformat(), "end": end.isoformat()},
        "keywords": all_kw,
        "risers": risers,
        "hottest": hottest,
    }
    os.makedirs("trends", exist_ok=True)
    with open("trends/datalab.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    top = risers[0]["keyword"] if risers else "-"
    print(
        "저장 완료: trends/datalab.json (%s ~ %s), 급상승 1위: %s"
        % (start, end, top)
    )


if __name__ == "__main__":
    main()
