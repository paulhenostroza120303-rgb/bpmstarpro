import json
import requests
import sys
sys.path.insert(0, ".")
from app import get_user_key, bpmstart_create_separation, bpmstart_get_status, SEPARATIONS_DIR

from pathlib import Path
dl_dir = Path("downloads")
audio_files = list(dl_dir.glob("*.mp3"))
if not audio_files:
    print("No MP3 files found in downloads/")
    sys.exit(1)
TEST_FILE = str(audio_files[0])
print(f"Using: {TEST_FILE}")

categories = {
    "vocal_2":   {"sep_type": 40, "add_opt1": "81", "add_opt2": "2", "add_opt3": None},
    "vocal_7":   {"sep_type": 63, "add_opt1": None, "add_opt2": None, "add_opt3": None},
    "drums":     {"sep_type": 44, "add_opt1": "6",  "add_opt2": "0", "add_opt3": "0"},
    "bass":      {"sep_type": 41, "add_opt1": "5",  "add_opt2": "0", "add_opt3": "0"},
    "drumsep":   {"sep_type": 37, "add_opt1": "7",  "add_opt2": "0", "add_opt3": None},
}

cat_name = sys.argv[1] if len(sys.argv) > 1 else "vocal_2"
cat = categories.get(cat_name)
if not cat:
    print(f"Unknown: {cat_name}")
    sys.exit(1)

print(f"Testing {cat_name}...")
success, result = bpmstart_create_separation(
    TEST_FILE,
    sep_type=cat["sep_type"],
    output_format=1,
    add_opt1=cat["add_opt1"],
    add_opt2=cat["add_opt2"],
    add_opt3=cat.get("add_opt3"),
)
print(f"Create: success={success}, result={result}")

if not success:
    sys.exit(1)

import time
task_hash = result
for _ in range(60):
    time.sleep(10)
    status_resp = bpmstart_get_status(task_hash)
    status = status_resp.get("status", "")
    print(f"  Status: {status}")
    if status == "done":
        files_data = status_resp.get("data", {}).get("files", [])
        print(f"\n=== FILES ({len(files_data)}) ===")
        for i, f in enumerate(files_data):
            url = f.get("url", "")
            name = f.get("name")
            orig_name = f.get("original_name", f.get("orig_name"))
            stem = f.get("stem", f.get("stem_type"))
            print(f"  [{i}] name={name!r}  orig={orig_name!r}  stem={stem!r}  url={url}")
        print("\n=== RAW JSON ===")
        print(json.dumps(files_data, indent=2)[:3000])
        break
    elif status in ("error", "cancelled"):
        print(f"FAILED: {status_resp}")
        break
