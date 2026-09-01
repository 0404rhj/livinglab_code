import subprocess
import os
import xlrd
import pandas as pd
import numpy as np
import json
from google.cloud import storage

# ==========================================
# 0. 설정 정보
# ==========================================
BUCKET_NAME = 'livinglab0707'

# 영상 경로 설정
AVI_GCS_PATH = 'Black Box/newvideo0714/merged_video.avi'
MP4_GCS_PATH = 'Black Box/newvideo0714/merged_video.mp4'

# 지도 HTML 저장 경로
HTML_GCS_PATH = 'map/driving_safety_dashboard.html'

# 로컬 임시 파일명
LOCAL_AVI = 'temp_input.avi'
LOCAL_MP4 = 'merged_video.mp4'
LOCAL_HTML = 'driving_safety_dashboard.html'
EXCEL_FILE = '0714 2026-07-14 10-40-27 (1).xls'

client = storage.Client()
bucket = client.bucket(BUCKET_NAME)

# ==========================================
# 1. GCS에서 AVI 다운로드 및 MP4로 변환 후 업로드
# ==========================================
print("[1/5] GCS에서 AVI 영상 다운로드 중...")
blob_avi = bucket.blob(AVI_GCS_PATH)
blob_avi.download_to_filename(LOCAL_AVI)

print("[2/5] AVI -> MP4 (H.264/AAC Web-optimized) 변환 시작...")
# 웹 브라우저에서 버퍼링 없이 즉시 재생되도록 -movflags faststart 옵션 적용
ffmpeg_cmd = [
    'ffmpeg', '-y',
    '-i', LOCAL_AVI,
    '-c:v', 'libx264',
    '-preset', 'fast',
    '-crf', '22',
    '-c:a', 'aac',
    '-b:a', '128k',
    '-movflags', '+faststart',
    LOCAL_MP4
]
subprocess.run(ffmpeg_cmd, check=True)
print("MP4 변환 완료!")

print(f"[3/5] 변환된 MP4 파일을 GCS 버킷에 업로드 중 ({MP4_GCS_PATH})...")
blob_mp4 = bucket.blob(MP4_GCS_PATH)
blob_mp4.upload_from_filename(LOCAL_MP4, content_type='video/mp4')
print("MP4 업로드 완료!")

# ==========================================
# 2. 엑셀 센서 데이터 파싱 및 과속방지턱 감지
# ==========================================
print("[4/5] 엑셀 데이터 분석 및 안전지도 생성 중...")
book = xlrd.open_workbook(EXCEL_FILE)

# Location 파싱
loc_sheet = book.sheet_by_name('Location')
loc_headers = loc_sheet.row_values(0)
loc_data = [loc_sheet.row_values(i) for i in range(1, loc_sheet.nrows)]
df_loc = pd.DataFrame(loc_data, columns=loc_headers)
df_loc = df_loc.dropna(subset=['Time (s)', 'Latitude (°)', 'Longitude (°)'])
df_loc['Time'] = df_loc['Time (s)'].astype(float)
df_loc['Lat'] = df_loc['Latitude (°)'].astype(float)
df_loc['Lng'] = df_loc['Longitude (°)'].astype(float)
df_loc['Velocity'] = pd.to_numeric(df_loc['Velocity (m/s)'], errors='coerce').fillna(0.0) * 3.6

# Accelerometer 파싱 (과속방지턱 충격 감지)
acc_sheet = book.sheet_by_name('Accelerometer')
acc_headers = acc_sheet.row_values(0)
acc_data = [acc_sheet.row_values(i) for i in range(1, acc_sheet.nrows)]
df_acc = pd.DataFrame(acc_data, columns=acc_headers)
df_acc['Time'] = df_acc['Time (s)'].astype(float)
df_acc['Z'] = df_acc['Z (m/s^2)'].astype(float)

# Z축 가속도 변화량 기준 충격 감지 (|Z - 9.8| > 2.5)
acc_impacts = df_acc[np.abs(df_acc['Z'] - 9.8) > 2.5]
bump_list = []
for _, row in acc_impacts.iterrows():
    t = row['Time']
    closest_loc = df_loc.iloc[(df_loc['Time'] - t).abs().argsort()[:1]].iloc[0]
    
    # 인근 중복 지점 필터링
    is_duplicate = any(abs(b['lat'] - closest_loc['Lat']) < 0.00015 and abs(b['lng'] - closest_loc['Lng']) < 0.00015 for b in bump_list)
    if not is_duplicate:
        bump_list.append({
            'time': float(closest_loc['Time']),
            'lat': float(closest_loc['Lat']),
            'lng': float(closest_loc['Lng']),
            'label': f"⚠️ 과속방지턱 감지구간 (충격: {row['Z']:.1f} m/s²)"
        })

center_lat = float(df_loc['Lat'].mean())
center_lng = float(df_loc['Lng'].mean())
gps_track_data = df_loc[['Time', 'Lat', 'Lng', 'Velocity']].to_dict(orient='records')
track_coords = df_loc[['Lat', 'Lng']].values.tolist()

# GCS 상의 영상 공개 URL 또는 상대 경로 설정
# 버킷이 공개되어 있거나 인증이 필요할 경우 경로에 맞게 지정됩니다.
video_url = f"https://storage.googleapis.com/{BUCKET_NAME}/{MP4_GCS_PATH.replace(' ', '%20')}"

# ==========================================
# 3. 분할 화면 대시보드 HTML 템플릿 생성
# ==========================================
html_content = f"""<!DOCTYPE html>
<html lang="ko">
<head>
    <meta charset="UTF-8">
    <title>주행 영상 & 실시간 안전지도 대시보드</title>
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/leaflet@1.9.3/dist/leaflet.css"/>
    <script src="https://cdn.jsdelivr.net/npm/leaflet@1.9.3/dist/leaflet.js"></script>
    <style>
        * {{ box-sizing: border-box; margin: 0; padding: 0; }}
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; display: flex; height: 100vh; background-color: #1a1a1a; color: #fff; }}
        #video-container {{
            width: 50%;
            height: 100%;
            display: flex;
            flex-direction: column;
            justify-content: center;
            align-items: center;
            padding: 20px;
            background: #111;
            position: relative;
        }}
        video {{
            width: 100%;
            max-height: 80%;
            border-radius: 8px;
            background: #000;
            outline: none;
        }}
        .info-panel {{
            margin-top: 15px;
            width: 100%;
            display: flex;
            justify-content: space-around;
            background: #222;
            padding: 12px;
            border-radius: 8px;
            font-size: 15px;
        }}
        .alert-banner {{
            position: absolute;
            top: 30px;
            background: rgba(231, 76, 60, 0.95);
            color: white;
            padding: 10px 20px;
            border-radius: 30px;
            font-weight: bold;
            font-size: 16px;
            display: none;
            box-shadow: 0 4px 10px rgba(0,0,0,0.5);
            z-index: 10;
        }}
        #map-container {{ width: 50%; height: 100%; }}
        #map {{ width: 100%; height: 100%; }}
    </style>
</head>
<body>
    <div id="video-container">
        <div id="alertBanner" class="alert-banner">⚠️ [경고] 과속방지턱 접근 중! 서행하십시오.</div>
        <video id="driveVideo" controls crossorigin="anonymous">
            <source src="{video_url}" type="video/mp4">
            브라우저가 해당 비디오 형식을 지원하지 않습니다.
        </video>
        <div class="info-panel">
            <div>⏱ 재생 시간: <span id="curTime" style="color:#3498db; font-weight:bold;">0.0s</span></div>
            <div>🚗 현재 속도: <span id="curSpeed" style="color:#2ecc71; font-weight:bold;">0.0 km/h</span></div>
            <div>📍 위치: <span id="curPos" style="color:#f1c40f;">-</span></div>
        </div>
    </div>
    <div id="map-container">
        <div id="map"></div>
    </div>

    <script>
        var map = L.map('map').setView([{center_lat}, {center_lng}], 16);
        L.tileLayer('https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png', {{
            maxZoom: 19,
            attribution: '© OpenStreetMap'
        }}).addTo(map);

        var trackCoords = {json.dumps(track_coords)};
        L.polyline(trackCoords, {{color: '#3498db', weight: 5, opacity: 0.8}}).addTo(map);

        var speedBumps = {json.dumps(bump_list)};
        speedBumps.forEach(function(bump) {{
            L.circleMarker([bump.lat, bump.lng], {{
                radius: 9,
                color: '#e74c3c',
                fillColor: '#f39c12',
                fillOpacity: 0.9,
                weight: 2
            }}).addTo(map).bindPopup("<b>" + bump.label + "</b>");
        }});

        var carIcon = L.divIcon({{
            className: 'car-marker',
            html: '<div style="font-size: 24px;">🚗</div>',
            iconSize: [30, 30],
            iconAnchor: [15, 15]
        }});
        var currentCarMarker = L.marker([{track_coords[0][0]}, {track_coords[0][1]}], {{icon: carIcon}}).addTo(map);

        var gpsData = {json.dumps(gps_track_data)};
        var video = document.getElementById('driveVideo');
        var alertBanner = document.getElementById('alertBanner');
        var curTimeTxt = document.getElementById('curTime');
        var curSpeedTxt = document.getElementById('curSpeed');
        var curPosTxt = document.getElementById('curPos');

        video.addEventListener('timeupdate', function() {{
            var cTime = video.currentTime;
            curTimeTxt.innerText = cTime.toFixed(1) + 's';

            var currentGps = gpsData.reduce(function(prev, curr) {{
                return (Math.abs(curr.Time - cTime) < Math.abs(prev.Time - cTime) ? curr : prev);
            }});

            if (currentGps) {{
                currentCarMarker.setLatLng([currentGps.Lat, currentGps.Lng]);
                curSpeedTxt.innerText = currentGps.Velocity.toFixed(1) + ' km/h';
                curPosTxt.innerText = currentGps.Lat.toFixed(5) + ', ' + currentGps.Lng.toFixed(5);

                var nearBump = speedBumps.some(function(bump) {{
                    return Math.abs(bump.time - cTime) < 2.0;
                }});
                alertBanner.style.display = nearBump ? 'block' : 'none';
            }}
        }});
    </script>
</body>
</html>
"""

with open(LOCAL_HTML, 'w', encoding='utf-8') as f:
    f.write(html_content)

# ==========================================
# 4. 생성된 HTML 파일을 GCS 버킷의 map 경로에 업로드
# ==========================================
print(f"[5/5] HTML 대시보드를 GCS 버킷에 업로드 중 ({HTML_GCS_PATH})...")
blob_html = bucket.blob(HTML_GCS_PATH)
blob_html.upload_from_filename(LOCAL_HTML, content_type='text/html')
print(f"모든 작업이 성공적으로 완료되었습니다!")
print(f"👉 지도 대시보드 저장 위치: gs://{BUCKET_NAME}/{HTML_GCS_PATH}")
print(f"👉 변환된 비디오 저장 위치: gs://{BUCKET_NAME}/{MP4_GCS_PATH}")

# 임시 파일 정리
if os.path.exists(LOCAL_AVI): os.remove(LOCAL_AVI)
if os.path.exists(LOCAL_MP4): os.remove(LOCAL_MP4)
if os.path.exists(LOCAL_HTML): os.remove(LOCAL_HTML)