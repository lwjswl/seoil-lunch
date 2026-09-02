import os
import sys
import time
import json
import requests
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from google import genai
from google.genai import types
from google.genai.errors import APIError
from google.cloud import storage
from playwright.sync_api import sync_playwright

# ---------------------------------------------------------
# 🧪 테스트 모드 설정
# True : 인스타에 업로드하지 않고 inst_feed.png 파일만 생성/확인
# False: 실제 운영 (GCS 업로드 및 인스타그램 최종 업로드 수행)
# ---------------------------------------------------------
DRY_RUN = True  

current_dir = os.path.dirname(os.path.abspath(__file__))
env_path = os.path.join(current_dir, '.env')
load_dotenv(env_path)

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
INSTAGRAM_ACCOUNT_ID = os.getenv("INSTAGRAM_ACCOUNT_ID")
ACCESS_TOKEN = os.getenv("ACCESS_TOKEN")
DATA_GO_KR_API_KEY = os.getenv("DATA_GO_KR_API_KEY")
client = genai.Client(api_key=GEMINI_API_KEY)

# ☁️ 구글 클라우드 스토리지(GCS) 설정
GCP_KEY_PATH = "seoil-hakshikbot-826147efed61.json"
BUCKET_NAME = "seoil-haksik-bucket"

# 📅 날짜 및 요일 구하기
KST = timezone(timedelta(hours=9))
today_dt = datetime.now(KST)
weekdays = ["월", "화", "수", "목", "금", "토", "일"]
current_weekday_idx = today_dt.weekday()
today_target = weekdays[current_weekday_idx]
display_date = f"{today_dt.strftime('%Y년 %m월 %d일')} ({today_target})"

if current_weekday_idx >= 5:
    print(f"📢 오늘은 {today_target}요일(주말)이므로 인스타그램 카드뉴스를 제작하지 않습니다.")
    exit()

print(f"📅 작업 시작 - {display_date}")
if DRY_RUN:
    print("🧪 [테스트 모드 활성화] 인스타그램 업로드 없이 이미지 파일만 생성합니다.\n")

# 🛠️ 추가 기능: 한국천문연구원 특일 정보 조회 API를 통한 공휴일 체크
print("🔍 한국천문연구원 특일 API 확인 중 (법정 공휴일/선거일 등)...")
sol_year = today_dt.strftime('%Y')
sol_month = today_dt.strftime('%m')
sol_day = today_dt.strftime('%d')

holiday_url = "http://apis.data.go.kr/B090041/openapi/service/SpcdeInfoService/getHoliDeInfo"
holiday_params = {
    'serviceKey': DATA_GO_KR_API_KEY,
    'solYear': sol_year,
    'solMonth': sol_month,
    '_type': 'json' 
}

try:
    hol_res = requests.get(holiday_url, params=holiday_params, timeout=10)
    is_holiday_today = False
    
    if hol_res.status_code == 200:
        try:
            hol_data = hol_res.json()
            items = hol_data.get('response', {}).get('body', {}).get('items', {})
            
            if items:
                item_list = items.get('item', [])
                if isinstance(item_list, dict):
                    item_list = [item_list]
                    
                for item in item_list:
                    if str(item.get('locdate')) == f"{sol_year}{sol_month}{sol_day}" and item.get('isHoliday') == 'Y':
                        print(f"📢 오늘은 법정 공휴일([{item.get('dateName')}]입니다. 코드 실행을 중단합니다.")
                        is_holiday_today = True
                        break
        except json.JSONDecodeError:
            root = ET.fromstring(hol_res.text)
            for item in root.findall('.//item'):
                locdate = item.find('locdate').text if item.find('locdate') is not None else ""
                is_holiday = item.find('isHoliday').text if item.find('isHoliday') is not None else ""
                date_name = item.find('dateName').text if item.find('dateName') is not None else "공휴일"
                
                if locdate == f"{sol_year}{sol_month}{sol_day}" and is_holiday == 'Y':
                    print(f"📢 오늘은 법정 공휴일([{date_name}])입니다. 코드 실행을 중단합니다.")
                    is_holiday_today = True
                    break
                    
    if is_holiday_today:
        exit()
    print("✅ 공휴일 검증 완료 (정상 영업일)")

except Exception as e:
    print(f"⚠️ 공휴일 API 조회 중 오류 발생(무시하고 계속 진행): {e}")


# 🌐 서일대학교 학식 게시판 크롤링 시작
print("🌐 서일대학교 학식 게시판 확인 중...")
URL = "https://www.seoil.ac.kr/seoil/598/subview.do" 
headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}

response = requests.get(URL, headers=headers)
soup = BeautifulSoup(response.text, 'html.parser')
subjects = soup.find_all('td', class_='td-subject')

target_url = None
for sub in subjects:
    link_tag = sub.find('a')
    if not link_tag:
        continue
    title_text = link_tag.find('strong').get_text().strip() if link_tag.find('strong') else link_tag.get_text().strip()

    if any(keyword in title_text for keyword in ['학생식당', '메뉴', '식단']):
        sub_url = link_tag.get('href')
        target_url = f"https://www.seoil.ac.kr{sub_url}"
        break

if not target_url:
    print("🚨 오늘자 식단표 게시글을 찾지 못했습니다. 종료합니다.")
    exit()

# JS 렌더링
print(f"🌐 Playwright로 게시글 스크린샷 캡처 중: {target_url}")
with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page(viewport={"width": 1280, "height": 900})
    page.goto(target_url, wait_until="networkidle")

    content_el = page.query_selector('.artclView') or page.query_selector('.board-view-content') or page.query_selector('.view-con') or page.query_selector('.bbsV-cont')
    if content_el:
        print("  ✅ 본문 영역 찾음 → 본문만 캡처")
        content_el.screenshot(path="menu.png")
    else:
        print("  ⚠️ 본문 영역 못 찾음 → 전체 페이지 캡처")
        page.screenshot(path="menu.png", full_page=True)

    browser.close()

print("✅ 식단표 스크린샷 캡처 완료")

with open("menu.png", "rb") as f:
    image_bytes = f.read()

image_part = types.Part.from_bytes(data=image_bytes, mime_type="image/png")

prompt = f"""
이 이미지는 대학 학식 식단표입니다. [{today_target}요일]에 해당하는 메뉴만 추출하세요.
각 메뉴는 <br>로 구분해야합니다.

[매핑 규칙]
- 왼쪽의 '누들 코너' 영역에 세로로 나열된 모든 메뉴(예: 메인 메뉴, 요거트, 디저트류 등)를 하나의 문자열로 합쳐서 "corner": "누들"에 매핑하세요. (예: "냉메밀소바&왕새우튀김 / 블루베리견과류그릭요거트 / 스콘/에그타르트")
- 왼쪽의 '한식 코너' 영역에 세로로 나열된 모든 메뉴를 하나의 문자열로 합쳐서 "corner": "한식"에 매핑하세요. (예: "제육김치덮밥... / 장어덮밥...")
- 만약 이미지에 '튀김' 코너가 따로 없다면, 메뉴내용을 공백으로 두세요.

[제외 사항]
- 괄호 등에 들어있는 원산지 표기는 제외하고 출력해야 합니다. (예: (돈육:미국산) 제외)
- `는 텍스트 추출제서 제외하세요. (예: 치킨버거(O) `치킨버거(X) `치킨버거`(X))
- '라이트한 한끼'는 텍스트 추출에서 제외하세요.

한국어 음식명 추출 시 주의사항:
- 쌍자음(ㄲ,ㄸ,ㅃ,ㅆ,ㅉ)을 정확히 인식하세요. 예) 깍두기(O) 각두기(X), 뽕따(O) 뿜따(X)
- 불명확한 글자는 한국 음식명 맥락으로 추론하세요.

추가로, 오늘 날짜({display_date})가 식단표 상에 휴무(재량휴업일, 방역, 공사 등)로 명시되어 있다면 is_holiday를 true로 설정하세요.

출력은 반드시 다른 설명 없이 아래 구조의 JSON 데이터 형식으로만 응답해야 합니다:
{{
  "is_holiday": true 또는 false,
  "menus": [
    {{"corner": "누들", "menu_name": "메뉴내용"}},
    {{"corner": "한식", "menu_name": "메뉴내용"}},
    {{"corner": "튀김", "menu_name": "메뉴내용"}}
  ]
}}
"""

print("🔍 제미나이 데이터 분석 및 코너 맵핑 중...")

max_retries = 5
base_delay = 2
response = None

for attempt in range(max_retries):
    try:
        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=[image_part, prompt],
            config=types.GenerateContentConfig(response_mime_type="application/json"),
        )
        break
    except APIError as e:
        if e.code == 503 or "503" in str(e):
            delay = base_delay * (2 ** attempt)
            print(f"⚠️ Gemini API 503 에러 발생 (시도 {attempt + 1}/{max_retries}). {delay}초 후 재시도합니다...")
            time.sleep(delay)
        else:
            raise e
    except Exception as e:
        raise e

if not response:
    print("❌ Gemini API 재시도 횟수를 초과하여 실행을 중단합니다.")
    exit()

try:
    cleaned_data = json.loads(response.text)
    
    if cleaned_data.get("is_holiday") is True:
        print(f"📢 제미나이 분석 결과 오늘({display_date})은 학교 자체 휴무일입니다. 실행을 중단합니다.")
        exit()

    menu_html_blocks = ""
    for item in cleaned_data.get("menus", []):
        corner = item.get("corner")
        menu_name = item.get("menu_name", "").strip().replace('\n', '<br>')
        
        if menu_name and "미운영" not in menu_name and "품절" not in menu_name:
            menu_html_blocks += f"""
            <div class="corner-column">
                <div class="corner-title">{corner} 코너</div>
                <div class="menu-content">{menu_name}</div>
            </div>
            """

    template_path = os.path.join(current_dir, "card_template.html")
    with open(template_path, "r", encoding="utf-8") as f:
        template_html = f.read()

    rendered_html = template_html.replace("{{CURRENT_DATE}}", display_date)\
                                 .replace("{{MENU_CONTAINERS}}", menu_html_blocks)

    result_path = os.path.join(current_dir, "temp_result.html")
    with open(result_path, "w", encoding="utf-8") as f:
        f.write(rendered_html)

    print("📸 고화질 인스타 단일 이미지 생성 시작...")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(f"file://{result_path}")
        page.set_viewport_size({"width": 1080, "height": 1440})
        
        # 카드 1장(#main-card) 캡처
        output_image_path = os.path.join(current_dir, "inst_feed.png")
        page.locator("#main-card").screenshot(path=output_image_path)
        browser.close()
        
    print(f"🎉 카드뉴스 이미지 생성 완료! 파일 경로: {output_image_path}")

    # =========================================================
    # 🧪 DRY_RUN (테스트 모드) 분기 처리
    # =========================================================
    if DRY_RUN:
        print("\n" + "="*50)
        print("🧪 [DRY_RUN 완료] 실제 인스타그램에 업로드되지 않았습니다.")
        print(f"🖼️ 생성된 이미지 위치: {output_image_path}")
        print(f"🌐 생성된 HTML 위치: {result_path}")
        print("="*50 + "\n")
        
        # (선택 사항) 로컬 환경(Windows/Mac)인 경우 완성된 이미지 자동 실행
        try:
            if sys.platform == "win32":
                os.startfile(output_image_path)
            elif sys.platform == "darwin": # macOS
                os.system(f"open '{output_image_path}'")
        except Exception:
            pass

        # 테스트 모드이므로 여기서 스크립트 종료
        sys.exit(0)

    # =========================================================
    # 🚀 실제 운영 모드 (DRY_RUN = False 인 경우만 아래 실행)
    # =========================================================

    # ☁️ 구글 클라우드 스토리지(GCS) 단일 이미지 업로드
    print("☁️ 구글 클라우드 스토리지(GCS)에 임시 이미지 업로드 중...")
    storage_client = storage.Client.from_service_account_json(os.path.join(current_dir, GCP_KEY_PATH))
    bucket = storage_client.bucket(BUCKET_NAME)

    today_str = today_dt.strftime('%Y%m%d')
    blob = bucket.blob(f"feeds/{today_str}_feed.png")
    blob.upload_from_filename("inst_feed.png")
    IMAGE_URL = blob.public_url

    # 🚀 인스타그램 Graph API 단일 이미지 업로드
    print("🚀 인스타그램 업로드 프로세스 시작...")
    caption = f"🍱 {display_date} 오늘의 서일대 학식 안내\n\n오늘의 맛있는 학식 메뉴를 확인해보세요! #서일대 #서일대학교 #학식"
    base_url = f"https://graph.facebook.com/v19.0/{INSTAGRAM_ACCOUNT_ID}"
    
    res = requests.post(f"{base_url}/media", data={
        'image_url': IMAGE_URL, 
        'caption': caption,
        'access_token': ACCESS_TOKEN
    }).json()
    
    container_id = res.get('id')
    
    if container_id:
        time.sleep(5) 
        publish_res = requests.post(f"{base_url}/media_publish", data={
            'creation_id': container_id, 
            'access_token': ACCESS_TOKEN
        }).json()
        
        if "id" in publish_res:
            print(f"✨ 인스타그램 단일 이미지 업로드 완료! (Post ID: {publish_res['id']})")
            
            print("🗑️ 클라우드 용량 확보를 위해 임시 이미지를 삭제합니다...")
            blob.delete()
            print("✨ GCS 용량 초기화 완료! (언제나 0MB 유지)")
        else:
            print(f"❌ 최종 발행 실패: {publish_res}")
    else:
        print(f"❌ 미디어 컨테이너 생성 실패: {res}")

except Exception as e:
    print(f"❌ 처리 중 에러 발생: {e}")
