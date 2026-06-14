import requests
from bs4 import BeautifulSoup
import time
import json
from datetime import datetime, timezone, timedelta
import os
from google import genai
from google.genai import types
from google.genai.errors import APIError
from google.cloud import storage
import xml.etree.ElementTree as ET  # 특일 API(XML) 파싱용

from dotenv import load_dotenv
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
display_date = f"{today_dt.strftime('%Y년 %m월 %d일')} {today_target}요일"

if current_weekday_idx >= 5:
    print(f"📢 오늘은 {today_target}요일(주말)이므로 인스타그램 카드뉴스를 제작하지 않습니다.")
    exit()

print(f"📅 작업 시작 - {display_date}")

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
    '_type': 'json' # JSON 타입 요청 (간혹 지원 안 될 때를 대비해 하단에 예외처리 추가)
}

try:
    hol_res = requests.get(holiday_url, params=holiday_params, timeout=10)
    is_holiday_today = False
    
    # API가 JSON 응답을 정상적으로 준 경우
    if hol_res.status_code == 200:
        try:
            hol_data = hol_res.json()
            items = hol_data.get('response', {}).get('body', {}).get('items', {})
            
            if items: # 공휴일 정보가 존재할 때
                item_list = items.get('item', [])
                if isinstance(item_list, dict): # 공휴일이 한 개만 있으면 dict 형태로 옴
                    item_list = [item_list]
                    
                for item in item_list:
                    # locdate 형식: 20260505
                    if str(item.get('locdate')) == f"{sol_year}{sol_month}{sol_day}" and item.get('isHoliday') == 'Y':
                        print(f"📢 오늘은 법정 공휴일([{item.get('dateName')}]입니다. 코드 실행을 중단합니다.")
                        is_holiday_today = True
                        break
        except json.JSONDecodeError:
            # API가 JSON을 요청했으나 XML로 강제 응답을 주는 경우 파싱 처리
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

img_url = None
print("=" * 50)
print("🐛 [DEBUG] 게시판 글 목록:")
for sub in subjects:
    link_tag = sub.find('a')
    if not link_tag:
        continue
    title_text = link_tag.find('strong').get_text().strip() if link_tag.find('strong') else link_tag.get_text().strip()
    print(f"  → title_text: '{title_text}'")
    
    if any(keyword in title_text for keyword in ['학생식당', '메뉴', '식단']):
        print(f"  ✅ 매칭됨: '{title_text}'")
        print("=" * 50)
        sub_url = link_tag.get('href')
        full_url = f"https://www.seoil.ac.kr{sub_url}"
        
        time.sleep(1)
        detail_response = requests.get(full_url, headers=headers)
        detail_soup = BeautifulSoup(detail_response.text, 'html.parser')
        
        img_tags = detail_soup.find_all('img')
        for img in img_tags:
            # ✅ [수정] data-src(지연 로딩) 우선, 없으면 src 사용
            src = img.get('data-src') or img.get('src', '')

            # ✅ [수정] base64 데이터 URI 및 불필요한 이미지 건너뜀
            if not src or src.startswith('data:'):
                continue
            if any(x in src.lower() for x in ['logo', 'icon', 'main', 'head', 'foot']):
                continue

            img_url = src if src.startswith('http') else f"https://www.seoil.ac.kr{src}"
            break
        if img_url:
            break

if not img_url:
    print("🚨 오늘자 식단표 이미지가 게시판에 아직 업로드되지 않았습니다. 종료합니다.")
    exit()

print(f"✅ 식단표 이미지 URL 확인: {img_url}")

# 식단표 다운로드
img_data = requests.get(img_url, headers=headers).content
filename = "menu.jpg"
with open(filename, "wb") as handler:
    handler.write(img_data)

with open(filename, "rb") as f:
    image_bytes = f.read()

image_part = types.Part.from_bytes(data=image_bytes, mime_type="image/jpeg")

# 프롬프트 구성 (학교 자체 재량휴업 등 2차 필터링용으로 구조 유지)
prompt = f"""
이 이미지는 대학 학식 식단표입니다. [{today_target}요일]에 해당하는 메뉴만 추출하세요.
반드시 코너명을 '누들', '한식', '튀김', '라면' 4가지 중 하나로 정확히 매핑해야 합니다.
괄호 등에 들어있는 원산지 표기는 제외하고 출력해야 합니다.
'라이트한 한끼'는 텍스트 추출에서 제외하세요.

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
    {{"corner": "튀김", "menu_name": "메뉴내용"}},
    {{"corner": "라면", "menu_name": "메뉴내용"}}
  ]
}}
"""

print("🔍 제미나이 데이터 분석 및 코너 맵핑 중...")

# Gemini 503 에러 대비 백오프 재시도 로직
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
    from playwright.sync_api import sync_playwright
    cleaned_data = json.loads(response.text)
    
    # 제미나이 2차 검증: 이미지 내에 명시된 학교 휴무일 체크
    if cleaned_data.get("is_holiday") is True:
        print(f"📢 제미나이 분석 결과 오늘({display_date})은 학교 자체 휴무일입니다. 실행을 중단합니다.")
        exit()

    menu_dict = {item['corner']: item['menu_name'].replace('\n', '<br>') for item in cleaned_data.get("menus", [])}
    
    noodle_val = menu_dict.get("누들", "미운영 또는 품절")
    hansik_val = menu_dict.get("한식", "미운영 또는 품절")
    twigim_val = menu_dict.get("튀김", "미운영 또는 품절")
    ramen_val  = menu_dict.get("라면", "미운영 또는 품절")

    with open("card_template.html", "r", encoding="utf-8") as f:
        template_html = f.read()

    rendered_html = template_html.replace("{{CURRENT_DATE}}", display_date)\
                                 .replace("{{NOODLE_MENU}}", noodle_val)\
                                 .replace("{{HANSIK_MENU}}", hansik_val)\
                                 .replace("{{TWIGIM_MENU}}", twigim_val)\
                                 .replace("{{RAMEN_MENU}}", ramen_val)

    with open("temp_result.html", "w", encoding="utf-8") as f:
        f.write(rendered_html)

    print("📸 고화질 인스타 피드 이미지 생성 시작...")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(f"file://{os.path.abspath('temp_result.html')}")
        page.set_viewport_size({"width": 1080, "height": 1440})
        
        page.locator("#card1").screenshot(path="inst_feed_1.png")
        page.locator("#card2").screenshot(path="inst_feed_2.png")
        browser.close()
        
    print("🎉 카드뉴스 제작 완료! 인스타에 올리러 갑시다!")

    # ⛔ [DEBUG] 인스타그램 업로드 임시 비활성화
    print("⛔ [DEBUG] 인스타그램 업로드 건너뜀 (디버깅 중)")

    # ☁️ 구글 클라우드 스토리지(GCS) 자동 업로드
    # print("☁️ 구글 클라우드 스토리지(GCS)에 임시 이미지 업로드 중...")
    # storage_client = storage.Client.from_service_account_json(GCP_KEY_PATH)
    # bucket = storage_client.bucket(BUCKET_NAME)
    #     # today_str = today_dt.strftime('%Y%m%d')
    # blob1 = bucket.blob(f"feeds/{today_str}_feed_1.png")
    # blob1.upload_from_filename("inst_feed_1.png")
    # IMAGE_URL_1 = blob1.public_url
    #     # blob2 = bucket.blob(f"feeds/{today_str}_feed_2.png")
    # blob2.upload_from_filename("inst_feed_2.png")
    # IMAGE_URL_2 = blob2.public_url
    #     # 🚀 인스타그램 Graph API 멀티 이미지 업로드
    # print("🚀 인스타그램 업로드 프로세스 시작...")
    # caption = f"🍱 {display_date}\n오늘의 학식입니다!\n#서일 #서일대 #학식"
    # base_url = f"https://graph.facebook.com/v19.0/{INSTAGRAM_ACCOUNT_ID}"
    #     # res1 = requests.post(f"{base_url}/media", data={'image_url': IMAGE_URL_1, 'is_carousel_item': 'true', 'access_token': ACCESS_TOKEN}).json()
    # container_id1 = res1.get('id')
    #     # res2 = requests.post(f"{base_url}/media", data={'image_url': IMAGE_URL_2, 'is_carousel_item': 'true', 'access_token': ACCESS_TOKEN}).json()
    # container_id2 = res2.get('id')
    #     # if container_id1 and container_id2:
    # carousel_res = requests.post(f"{base_url}/media", data={
    # 'media_type': 'CAROUSEL',
    # 'children': f"[{container_id1},{container_id2}]",
    # 'caption': caption,
    # 'access_token': ACCESS_TOKEN
    # }).json()
    # parent_container_id = carousel_res.get('id')
    #     # if parent_container_id:
    # time.sleep(5) 
    # publish_res = requests.post(f"{base_url}/media_publish", data={'creation_id': parent_container_id, 'access_token': ACCESS_TOKEN}).json()
    #     # if "id" in publish_res:
    # print(f"✨ 인스타그램 업로드 완료! (Post ID: {publish_res['id']})")
    #                 # 🗑️ 용량 제로화: 업로드 성공 즉시 GCS에 올린 파일 삭제
    # print("🗑️ 클라우드 용량 확보를 위해 임시 이미지를 삭제합니다...")
    # blob1.delete()
    # blob2.delete()
    # print("✨ GCS 용량 초기화 완료! (언제나 0MB 유지)")
    # else:
    # print(f"❌ 최종 발행 실패: {publish_res}")
    # else:
    # print(f"❌ 캐러셀 생성 실패: {carousel_res}")
    # else:
    # print(f"❌ 미디어 컨테이너 생성 실패: {res1}, {res2}")

except Exception as e:
    print(f"❌ 오류 발생: {e}")
