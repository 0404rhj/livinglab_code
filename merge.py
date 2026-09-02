import os
import subprocess
from google.cloud import storage

# --- 1. 설정 ---
BUCKET_NAME = "livinglab0707"
INPUT_PREFIX = "Black Box/originalvideo/"
OUTPUT_PREFIX = "Black Box/newvideo0714/"
OUTPUT_FILENAME = "merged_video.avi"

TEMP_INPUT_DIR = "./temp_input"
TEMP_OUTPUT_DIR = "./temp_output"
os.makedirs(TEMP_INPUT_DIR, exist_ok=True)
os.makedirs(TEMP_OUTPUT_DIR, exist_ok=True)

# GCS 클라이언트 생성
storage_client = storage.Client()
bucket = storage_client.bucket(BUCKET_NAME)

print("1. Google Cloud Storage 버킷에서 파일 목록 검색 중...")

# --- 2. 버킷 파일 목록 조회 및 다운로드 ---
blobs = list(storage_client.list_blobs(BUCKET_NAME, prefix=INPUT_PREFIX))

# .avi 파일만 추출 후 이름순 정렬
avi_blobs = [b for b in blobs if b.name.endswith('.avi')]
avi_blobs.sort(key=lambda x: x.name)

print(f"총 {len(avi_blobs)}개의 영상 파일을 찾았습니다. 다운로드를 시작합니다.")

local_filepaths = []
for idx, blob in enumerate(avi_blobs):
    filename = os.path.basename(blob.name)
    local_path = os.path.join(TEMP_INPUT_DIR, f"{idx:02d}_{filename}")
    print(f"[{idx+1}/{len(avi_blobs)}] 다운로드 중: {filename}")
    blob.download_to_filename(local_path)
    local_filepaths.append(local_path)

# --- 3. ffmpeg용 파일 리스트 작성 및 영상 합치기 ---
print("\n2. 영상을 순서대로 합치는 작업 진행 중... (ffmpeg 사용)")

list_file_path = os.path.join(TEMP_INPUT_DIR, "file_list.txt")
with open(list_file_path, "w", encoding="utf-8") as f:
    for path in local_filepaths:
        # ffmpeg concating 형식 지정
        abs_path = os.path.abspath(path)
        f.write(f"file '{abs_path}'\n")

local_output_path = os.path.join(TEMP_OUTPUT_DIR, OUTPUT_FILENAME)

# ffmpeg 명령어 실행 (재인코딩 없이 빠르고 정확하게 병합)
ffmpeg_cmd = [
    "ffmpeg", "-y", "-f", "concat", "-safe", "0",
    "-i", list_file_path,
    "-c", "copy",
    local_output_path
]

try:
    subprocess.run(ffmpeg_cmd, check=True)
    
    # --- 4. 합쳐진 영상을 GCS 버킷에 업로드 ---
    print("\n3. 합쳐진 영상을 버킷에 업로드 중...")
    destination_blob_name = f"{OUTPUT_PREFIX}{OUTPUT_FILENAME}"
    output_blob = bucket.blob(destination_blob_name)
    output_blob.upload_from_filename(local_output_path)

    print(f"\n✅ 완료되었습니다!")
    print(f"저장 위치: gs://{BUCKET_NAME}/{destination_blob_name}")

except Exception as e:
    print(f"\n❌ 작업 중 에러가 발생했습니다: {e}")

finally:
    # --- 5. 임시 파일 정리 ---
    print("4. 임시 파일 정리 중...")
    if os.path.exists(list_file_path):
        os.remove(list_file_path)
    for path in local_filepaths:
        if os.path.exists(path):
            os.remove(path)
    if os.path.exists(local_output_path):
        os.remove(local_output_path)