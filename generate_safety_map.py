import xlrd
import pandas as pd
import numpy as np
import json
import os
from google.cloud import storage

# ==========================================
# 0. 파일 및 버킷 설정 (새로운 파일명 지정)
# ==========================================
EXCEL_FILE = '0714 2026-07-14 10-40-27 (1).xls'
BUCKET_NAME = 'livinglab0707'

# 새로 생성될 지도 HTML 파일 이름
LOCAL_HTML_FILE = 'speed_bump_safety_map.html'
GCS_HTML_PATH = f'map/{LOCAL_HTML_FILE}'

# ==========================================
# 1. 엑셀 데이터 파싱 (Location & Accelerometer)
# ==========================================
print("[1/3] 엑셀 데이터 분석 중...")
book = xlrd.open_workbook(EXCEL_FILE)

# Location 시트 파싱
loc_sheet = book.sheet_by_name('Location')
loc_headers = loc_sheet.row_values(0)
loc_data = [loc_sheet.row_values(i) for i in range(1, loc_sheet.nrows)]
df_loc = pd.DataFrame(loc_data, columns=loc_headers)
df_loc = df_loc.dropna(subset=['Time (s)', 'Latitude (°)', 'Longitude (°)'])
df_loc['Time'] = df_loc['Time (s)'].astype(float)
df_loc['Lat'] = df_loc['Latitude (°)'].astype(float)
df_loc['Lng'] = df_loc['Longitude (°)'].astype(float)
df_loc['Velocity'] = pd.to_numeric(df_loc['Velocity (m/s)'], errors='coerce').fillna(0.0) * 3.6

# Accelerometer 시트 파싱 및 과속방지턱 충격 감지
acc_sheet = book.sheet_by_name('Accelerometer')
acc_headers = acc_sheet.row_values(0)
acc_data = [acc_sheet.row_values(i) for i in range(1, acc_sheet.nrows)]
df_acc = pd.DataFrame(acc_data, columns=acc_headers)
df_acc['Time'] = df_acc['Time (s)'].astype(float)
df_acc['Z'] = df_acc['Z (m/s^2)'].astype(float)

# Z축 충격량 기준 방지턱 필터링 (|Z - 9.8| > 2.5)
acc_impacts = df_acc[np.abs(df_acc['Z'] - 9.8) > 2.5]
bump_list = []
for _, row in acc_impacts.iterrows():
    t = row['Time']
    closest_loc = df_loc.iloc[(df_loc['Time'] - t).abs().argsort()[:1]].iloc[0]
    
    # 반경 중복 제거
    is_duplicate = any(abs(b['lat'] - closest_loc['Lat']) < 0.00015 and abs(b['lng'] - closest_loc['Lng']) < 0.00015 for b in bump_list)
    if not is_duplicate:
        bump_list.append({
            'time': float(closest_loc['Time']),
            'lat': float(closest_loc['Lat']),
            'lng': float(closest_loc['Lng']),
            'impact': float(row['Z'])
        })

# 지도 초기 중심 좌표 및 경로 리스트
center_lat = float(df_loc['Lat'].mean())
center_lng = float(df_loc['Lng'].mean())
track_coords = df_loc[['Lat', 'Lng']].values.tolist()
start_point = track_coords[0]
end_point = track_coords[-1]

# ==========================================
# 2. 풀스크린 대화형 안전지도 HTML 생성
# ==========================================
print("[2/3] 안전지도 HTML 문서 렌더링 중...")

html_content = f"""<!DOCTYPE html>
<html lang="ko">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>GPS 이동경로 및 과속방지턱 안전지도</title>
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/leaflet@1.9.3/dist/leaflet.css"/>
    <script src="https://cdn.jsdelivr.net/npm/leaflet@1.9.3/dist/leaflet.js"></script>
    <style>
        * {{ box-sizing: border-box; margin: 0; padding: 0; }}
        body, html {{ width: 100%; height: 100%; overflow: hidden; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; }}
        #map {{ width: 100%; height: 100%; }}
        
        /* 상단 정보 패널 */
        .dashboard-header {{
            position: absolute;
            top: 20px;
            left: 20px;
            z-index: 1000;
            background: rgba(255, 255, 255, 0.95);
            padding: 16px 20px;
            border-radius: 12px;
            box-shadow: 0 4px 15px rgba(0,0,0,0.15);
            max-width: 320px;
        }}
        .dashboard-header h2 {{
            font-size: 17px;
            color: #2c3e50;
            margin-bottom: 8px;
            display: flex;
            align-items: center;
            gap: 6px;
        }}
        .stat-item {{
            font-size: 13px;
            color: #555;
            margin-top: 4px;
            display: flex;
            justify-content: space-between;
        }}
        .stat-val {{ font-weight: bold; color: #2980b9; }}
        
        /* 범례 패널 */
        .legend {{
            position: absolute;
            bottom: 25px;
            right: 20px;
            z-index: 1000;
            background: white;
            padding: 12px 16px;
            border-radius: 8px;
            box-shadow: 0 2px 10px rgba(0,0,0,0.15);
            font-size: 13px;
            line-height: 1.8;
        }}
        .legend-icon {{
            display: inline-block;
            width: 12px;
            height: 12px;
            margin-right: 6px;
            border-radius: 50%;
            vertical-align: middle;
        }}
    </style>
</head>
<body>
    <div class="dashboard-header">
        <h2>🛡️ 주행 안전 분석 지도</h2>
        <div class="stat-item"><span>총 기록 좌표:</span> <span class="stat-val">{len(df_loc)} 개</span></div>
        <div class="stat-item"><span>감지된 과속방지턱:</span> <span class="stat-val" style="color:#e74c3c;">{len(bump_list)} 개소</span></div>
        <div class="stat-item"><span>평균 속도:</span> <span class="stat-val">{df_loc['Velocity'].mean():.1f} km/h</span></div>
    </div>

    <div class="legend">
        <div><span class="legend-icon" style="background:#27ae60;"></span> 출발 지점</div>
        <div><span class="legend-icon" style="background:#c0392b;"></span> 도착 지점</div>
        <div><span class="legend-icon" style="background:#e67e22;"></span> ⚠️ 과속방지턱 구간</div>
        <div><span style="display:inline-block; width:16px; height:4px; background:#3498db; margin-right:4px; vertical-align:middle;"></span> 주행 이동 경로</div>
    </div>

    <div id="map"></div>

    <script>
        // 1. 지도 초기화
        var map = L.map('map').setView([{center_lat}, {center_lng}], 16);
        
        L.tileLayer('https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png', {{
            maxZoom: 19,
            attribution: '© OpenStreetMap'
        }}).addTo(map);

        // 2. 주행 경로선 그리기
        var trackCoords = {json.dumps(track_coords)};
        var polyline = L.polyline(trackCoords, {{
            color: '#3498db',
            weight: 5,
            opacity: 0.85
        }}).addTo(map);
        map.fitBounds(polyline.getBounds(), {{ padding: [50, 50] }});

        // 3. 출발 / 도착 마커
        L.circleMarker([{start_point[0]}, {start_point[1]}], {{
            radius: 8,
            color: '#27ae60',
            fillColor: '#2ecc71',
            fillOpacity: 1,
            weight: 3
        }}).addTo(map).bindPopup("<b>🚩 출발 지점</b>");

        L.circleMarker([{end_point[0]}, {end_point[1]}], {{
            radius: 8,
            color: '#c0392b',
            fillColor: '#e74c3c',
            fillOpacity: 1,
            weight: 3
        }}).addTo(map).bindPopup("<b>🏁 도착 지점</b>");

        // 4. 과속방지턱 마커 & 상세 팝업
        var speedBumps = {json.dumps(bump_list)};
        speedBumps.forEach(function(bump, idx) {{
            var marker = L.circleMarker([bump.lat, bump.lng], {{
                radius: 10,
                color: '#d35400',
                fillColor: '#e67e22',
                fillOpacity: 0.9,
                weight: 2
            }}).addTo(map);

            var popupContent = `
                <div style="font-size:13px; line-height:1.5;">
                    <b style="color:#d35400;">⚠️ 과속방지턱 #${{idx + 1}}</b><br>
                    • 통과 시간: ${{bump.time.toFixed(1)}}초<br>
                    • 수직 충격량(Z): ${{bump.impact.toFixed(2)}} m/s²<br>
                    • 위치: ${{bump.lat.toFixed(5)}}, ${{bump.lng.toFixed(5)}}
                </div>
            `;
            marker.bindPopup(popupContent);
            marker.bindTooltip("⚠️ 방지턱 #" + (idx + 1), {{ direction: 'top', offset: [0, -8] }});
        }});
    </script>
</body>
</html>
"""

with open(LOCAL_HTML_FILE, 'w', encoding='utf-8') as f:
    f.write(html_content)
print(f"로컬 파일 생성 완료: {LOCAL_HTML_FILE}")

# ==========================================
# 3. GCS 버킷 업로드
# ==========================================
print(f"[3/3] GCS 버킷 업로드 중 ({GCS_HTML_PATH})...")
try:
    client = storage.Client()
    bucket = client.bucket(BUCKET_NAME)
    blob = bucket.blob(GCS_HTML_PATH)
    blob.upload_from_filename(LOCAL_HTML_FILE, content_type='text/html')
    print("성공적으로 버킷에 업로드되었습니다!")
    print(f"👉 버킷 저장 위치: gs://{BUCKET_NAME}/{GCS_HTML_PATH}")
except Exception as e:
    print(f"버킷 업로드 중 알림 (로컬 파일은 정상 생성됨): {e}")