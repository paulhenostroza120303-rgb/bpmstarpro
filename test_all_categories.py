import json
import requests
import sys
import time
sys.path.insert(0, ".")
from pathlib import Path
from app import get_user_key, bpmstart_create_separation, bpmstart_get_status

dl_dir = Path("downloads")
audio_files = list(dl_dir.glob("*.mp3"))
TEST_FILE = str(audio_files[0])
print(f"Using: {TEST_FILE}")

categories = [
    ("drums",     {"sep_type": 44, "add_opt1": "6",  "add_opt2": "0", "add_opt3": "0"}),
    ("bass",      {"sep_type": 41, "add_opt1": "5",  "add_opt2": "0", "add_opt3": "0"}),
    ("guitar",    {"sep_type": 31, "add_opt1": "7",  "add_opt2": None, "add_opt3": None}),
    ("synth",     {"sep_type": 88, "add_opt1": "0",  "add_opt2": None, "add_opt3": None}),
    ("drumsep",   {"sep_type": 37, "add_opt1": "7",  "add_opt2": "0", "add_opt3": None}),
]

for name, cat in categories:
    print(f"\n{'='*60}")
    print(f"Testing {name}...")
    print(f"{'='*60}")
    
    # Create
    for attempt in range(3):
        success, result = bpmstart_create_separation(
            TEST_FILE,
            sep_type=cat["sep_type"],
            output_format=1,
            add_opt1=cat["add_opt1"],
            add_opt2=cat["add_opt2"],
            add_opt3=cat.get("add_opt3"),
        )
        if success:
            break
        print(f"  Attempt {attempt+1} failed: {result}")
        print("  Waiting 30s for concurrent job to clear...")
        time.sleep(30)
    
    if not success:
        print(f"  SKIPPED {name} after 3 attempts")
        continue
    
    task_hash = result
    print(f"  Created: {task_hash}")
    
    # Poll
    for poll in range(60):
        time.sleep(15)
        try:
            status_resp = bpmstart_get_status(task_hash)
            status = status_resp.get("status", "")
            if status == "done":
                files_data = status_resp.get("data", {}).get("files", [])
                print(f"\n  === {name} RESULT ({len(files_data)} files) ===")
                for i, f in enumerate(files_data):
                    print(f"    [{i}] type={f.get('type')!r}  download={f.get('download','')[:60]}")
                break
            elif status in ("error", "cancelled"):
                print(f"  FAILED: {status}")
                break
            else:
                print(f"  Status: {status}...")
        except Exception as e:
            print(f"  Poll error: {e}")
    
    # Wait before next to avoid concurrent job limit
    print("  Waiting 20s before next category...")
    time.sleep(20)

print("\n" + "="*60)
print("ALL DONE")
print("="*60)
