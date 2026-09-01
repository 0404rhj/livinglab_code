import xlrd
import pandas as pd
import numpy as np
import json
import os
from google.cloud import storage

# ==========================================
# 0. 설정 정보
# ==========================================
EXCEL_FILE = '0714 2026-07-14 10-40-27 (1).xls'
BUCKET_NAME = 'livinglab0707'

LOCAL_HTML_FILE = 'sync_video_safety_dashboard.html'
GCS_HTML_PATH = f'map/{LOCAL_HTML_FILE}'

# ==========================================
# 1. 엑셀 데이터 파싱 및 시작 시각 추출
# ==========================================
print("[1/3] 엑셀 데이터 파싱 중...")
book = xlrd.open_workbook(EXCEL_FILE)

# Metadata Time 파싱 (시작 기준 시각 추출)
time_sheet = book.sheet_by_name('Metadata Time')
start_unix_time = 0.0
for i in range(1, time_sheet.nrows):
    row = time_sheet.row_values(i)
    if row[0] == 'START':
        start_unix_time = float(row[2]) # 1783992533.051 (2026-07-14 10:28:53.051)
        break

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

# Accelerometer 시트 파싱 (과속방지턱 감지)
acc_sheet = book.sheet_by_name('Accelerometer')
acc_headers = acc_sheet.row_values(0)
acc_data = [acc_sheet.row_values(i) for i in range(1, acc_sheet.nrows)]
df_acc = pd.DataFrame(acc_data, columns=acc_headers)
df_acc['Time'] = df_acc['Time (s)'].astype(float)
df_acc['Z'] = df_acc['Z (m/s^2)'].astype(float)

# Z축 충격량 필터링
acc_impacts = df_acc[np.abs(df_acc['Z'] - 9.8) > 2.5]
bump_list = []
for _, row in acc_impacts.iterrows():
    t = row['Time']
    closest_loc = df_loc.iloc[(df_loc['Time'] - t).abs().argsort()[:1]].iloc[0]
    
    is_duplicate = any(abs(b['lat'] - closest_loc['Lat']) < 0.00015 and abs(b['lng'] - closest_loc['Lng']) < 0.00015 for b in bump_list)
    if not is_duplicate:
        bump_list.append({
            'time': float(closest_loc['Time']),
            'lat': float(closest_loc['Lat']),
            'lng': float(closest_loc['Lng']),
            'impact': float(row['Z'])
        })

center_lat = float(df_loc['Lat'].mean())
center_lng = float(df_loc['Lng'].mean())
gps_track_data = df_loc[['Time', 'Lat', 'Lng', 'Velocity']].to_dict(orient='records')
track_coords = df_loc[['Lat', 'Lng']].values.tolist()

# ==========================================
# 2. 실시간 싱크 조절 대시보드 HTML 생성
# ==========================================
print("[2/3] 싱크 조절 기능 탑재 HTML 대시보드 생성 중...")

html_content = f"""<!DOCTYPE html>
<html lang="ko">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>주행 영상 & 안전지도 싱크 대시보드</title>
    <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/leaflet@1.9.3/dist/leaflet.css"/>
    <script src="https://cdn.jsdelivr.net/npm/leaflet@1.9.3/dist/leaflet.js"></script>
    <style>
        * {{ box-sizing: border-box; margin: 0; padding: 0; }}
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; display: flex; height: 100vh; background-color: #141414; color: #fff; overflow: hidden; }}
        
        #video-panel {{
            width: 50%;
            height: 100%;
            display: flex;
            flex-direction: column;
            justify-content: space-between;
            padding: 16px;
            background: #1c1c1e;
            position: relative;
            box-shadow: 2px 0 10px rgba(0,0,0,0.5);
            z-index: 10;
        }}
        
        .header-bar {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding-bottom: 10px;
            border-bottom: 1px solid #333;
        }}
        .header-bar h2 {{ font-size: 16px; color: #ecf0f1; }}
        
        .file-upload-btn {{
            background: #2980b9;
            color: white;
            padding: 6px 12px;
            border-radius: 6px;
            font-size: 13px;
            font-weight: bold;
            cursor: pointer;
        }}
        #videoFileInput {{ display: none; }}

        /* 싱크 조절 바 */
        .sync-control-box {{
            background: #2c2c2e;
            padding: 8px 12px;
            border-radius: 8px;
            margin-top: 8px;
            display: flex;
            align-items: center;
            justify-content: space-between;
            font-size: 13px;
        }}
        .sync-input-group {{
            display: flex;
            align-items: center;
            gap: 6px;
        }}
        .sync-btn {{
            background: #444;
            color: white;
            border: none;
            padding: 4px 8px;
            border-radius: 4px;
            cursor: pointer;
            font-weight: bold;
        }}
        .sync-btn:hover {{ background: #555; }}
        .sync-val-input {{
            width: 60px;
            background: #111;
            border: 1px solid #555;
            color: #3498db;
            text-align: center;
            padding: 3px;
            border-radius: 4px;
            font-weight: bold;
        }}

        .video-wrapper {{
            position: relative;
            flex: 1;
            display: flex;
            justify-content: center;
            align-items: center;
            margin: 10px 0;
            background: #000;
            border-radius: 8px;
            overflow: hidden;
        }}
        video {{
            width: 100%;
            height: 100%;
            object-fit: contain;
        }}
        
        .alert-overlay {{
            position: absolute;
            top: 20px;
            background: rgba(231, 76, 60, 0.95);
            color: white;
            padding: 10px 20px;
            border-radius: 30px;
            font-weight: bold;
            font-size: 15px;
            display: none;
            box-shadow: 0 4px 15px rgba(0,0,0,0.6);
            z-index: 20;
            animation: pulse 0.8s infinite alternate;
        }}
        @keyframes pulse {{
            from {{ transform: scale(1); }}
            to {{ transform: scale(1.05); }}
        }}

        .telemetry-bar {{
            display: flex;
            justify-content: space-around;
            background: #2c2c2e;
            padding: 10px;
            border-radius: 8px;
            font-size: 13px;
        }}
        .telemetry-item span:first-child {{ color: #8e8e93; margin-right: 4px; }}
        .telemetry-val {{ font-weight: bold; }}

        #map-panel {{ width: 50%; height: 100%; position: relative; }}
        #map {{ width: 100%; height: 100%; }}
        
        .map-legend {{
            position: absolute;
            bottom: 20px;
            right: 20px;
            z-index: 1000;
            background: rgba(255,255,255,0.95);
            color: #333;
            padding: 10px 14px;
            border-radius: 8px;
            font-size: 12px;
            line-height: 1.6;
            box-shadow: 0 2px 8px rgba(0,0,0,0.2);
        }}
        .legend-dot {{
            display: inline-block;
            width: 10px;
            height: 10px;
            border-radius: 50%;
            margin-right: 5px;
            vertical-align: middle;
        }}
    </style>
</head>
<body>

    <!-- 좌측 영상 패널 -->
    <div id="video-panel">
        <div class="header-bar">
            <h2>📹 주행 영상 & GPS 동기화</h2>
            <label class="file-upload-btn" for="videoFileInput">📂 동영상 파일 선택</label>
            <input type="file" id="videoFileInput" accept="video/mp4, video/avi, video/*">
        </div>

        <!-- 싱크 오프셋 조절 바 -->
        <div class="sync-control-box">
            <span>⚙️ <b>싱크(시간 차이) 미세 조절:</b></span>
            <div class="sync-input-group">
                <button class="sync-btn" onclick="adjustOffset(-1.0)">-1s</button>
                <button class="sync-btn" onclick="adjustOffset(-0.1)">-0.1s</button>
                <input type="number" id="offsetInput" class="sync-val-input" value="0.0" step="0.1" onchange="setOffset(this.value)">
                <span>초</span>
                <button class="sync-btn" onclick="adjustOffset(0.1)">+0.1s</button>
                <button class="sync-btn" onclick="adjustOffset(1.0)">+1s</button>
            </div>
        </div>

        <div class="video-wrapper">
            <div id="alertOverlay" class="alert-overlay">⚠️ [경고] 과속방지턱 통과 중! 서행하십시오.</div>
            <video id="driveVideo" controls>
                브라우저가 비디오 태그를 지원하지 않습니다.
            </video>
        </div>

        <div class="telemetry-bar">
            <div class="telemetry-item"><span>🕒 시계 시간:</span><span id="txtRealTime" class="telemetry-val" style="color:#e67e22;">-</span></div>
            <div class="telemetry-item"><span>⏱ 센서 시간:</span><span id="txtTime" class="telemetry-val" style="color:#3498db;">0.0s</span></div>
            <div class="telemetry-item"><span>🚗 속도:</span><span id="txtSpeed" class="telemetry-val" style="color:#2ecc71;">0.0 km/h</span></div>
            <div class="telemetry-item"><span>📍 좌표:</span><span id="txtPos" class="telemetry-val" style="color:#f1c40f;">-</span></div>
        </div>
    </div>

    <!-- 우측 안전지도 패널 -->
    <div id="map-panel">
        <div id="map"></div>
        <div class="map-legend">
            <div><span class="legend-dot" style="background:#3498db;"></span> 주행 이동 경로</div>
            <div><span class="legend-dot" style="background:#e67e22;"></span> ⚠️ 과속방지턱 ({len(bump_list)}개소)</div>
            <div><span style="font-size:14px; vertical-align:middle;">🚗</span> 실시간 차량 위치</div>
        </div>
    </div>

    <script>
        var map = L.map('map').setView([{center_lat}, {center_lng}], 16);
        L.tileLayer('https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png', {{
            maxZoom: 19,
            attribution: '© OpenStreetMap'
        }}).addTo(map);

        var trackCoords = {json.dumps(track_coords)};
        var polyline = L.polyline(trackCoords, {{color: '#3498db', weight: 5, opacity: 0.85}}).addTo(map);
        map.fitBounds(polyline.getBounds(), {{ padding: [40, 40] }});

        var speedBumps = {json.dumps(bump_list)};
        speedBumps.forEach(function(bump, idx) {{
            var marker = L.circleMarker([bump.lat, bump.lng], {{
                radius: 10,
                color: '#d35400',
                fillColor: '#e67e22',
                fillOpacity: 0.9,
                weight: 2
            }}).addTo(map);
            marker.bindPopup(`<b>⚠️ 과속방지턱 #${{idx + 1}}</b><br>통과 시점: ${{bump.time.toFixed(1)}}s<br>충격량: ${{bump.impact.toFixed(1)}} m/s²`);
        }});

        var carIcon = L.divIcon({{
            className: 'car-marker-icon',
            html: '<div style="font-size: 26px; transform: translate(-5px, -5px); filter: drop-shadow(0 2px 4px rgba(0,0,0,0.5));">🚗</div>',
            iconSize: [30, 30],
            iconAnchor: [15, 15]
        }});
        var carMarker = L.marker([{track_coords[0][0]}, {track_coords[0][1]}], {{icon: carIcon}}).addTo(map);

        var fileInput = document.getElementById('videoFileInput');
        var video = document.getElementById('driveVideo');
        fileInput.addEventListener('change', function(e) {{
            var file = e.target.files[0];
            if (file) {{
                video.src = URL.createObjectURL(file);
                video.play();
            }}
        }});

        var gpsData = {json.dumps(gps_track_data)};
        var startUnix = {start_unix_time}; // 엑셀 측정 시작 타임스탬프
        var alertOverlay = document.getElementById('alertOverlay');
        var txtRealTime = document.getElementById('txtRealTime');
        var txtTime = document.getElementById('txtTime');
        var txtSpeed = document.getElementById('txtSpeed');
        var txtPos = document.getElementById('txtPos');
        var offsetInput = document.getElementById('offsetInput');

        var timeOffset = 0.0;

        function adjustOffset(delta) {{
            timeOffset = parseFloat((timeOffset + delta).toFixed(2));
            offsetInput.value = timeOffset;
            syncTelemetry();
        }}

        function setOffset(val) {{
            timeOffset = parseFloat(val) || 0.0;
            syncTelemetry();
        }}

        function formatClock(unixSeconds) {{
            var d = new Date(unixSeconds * 1000);
            var hh = String(d.getHours()).padStart(2, '0');
            var mm = String(d.getMinutes()).padStart(2, '0');
            var ss = String(d.getSeconds()).padStart(2, '0');
            return hh + ':' + mm + ':' + ss;
        }}

        function syncTelemetry() {{
            var cTime = video.currentTime + timeOffset;
            txtTime.innerText = cTime.toFixed(1) + 's';

            if (startUnix > 0) {{
                txtRealTime.innerText = formatClock(startUnix + cTime);
            }}

            var currentGps = gpsData.reduce(function(prev, curr) {{
                return (Math.abs(curr.Time - cTime) < Math.abs(prev.Time - cTime) ? curr : prev);
            }});

            if (currentGps) {{
                carMarker.setLatLng([currentGps.Lat, currentGps.Lng]);
                txtSpeed.innerText = currentGps.Velocity.toFixed(1) + ' km/h';
                txtPos.innerText = currentGps.Lat.toFixed(5) + ', ' + currentGps.Lng.toFixed(5);

                var nearBump = speedBumps.some(function(bump) {{
                    return Math.abs(bump.time - cTime) < 2.0;
                }});
                alertOverlay.style.display = nearBump ? 'block' : 'none';
            }}
        }}

        video.addEventListener('timeupdate', syncTelemetry);
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
    print(f"버킷 업로드 알림: {e}")