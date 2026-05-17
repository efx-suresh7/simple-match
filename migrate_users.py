import os
import json
from datetime import datetime

# Load Firebase
import firebase_admin
from firebase_admin import credentials, firestore
from dotenv import load_dotenv

load_dotenv()

print("Initializing Firebase...")
key_json = os.environ.get("FIREBASE_KEY_JSON", "")
if key_json:
    import tempfile
    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    tmp.write(key_json)
    tmp.flush()
    cred = credentials.Certificate(tmp.name)
else:
    cred = credentials.Certificate("serviceAccountKey.json")

if not firebase_admin._apps:
    firebase_admin.initialize_app(cred)

db = firestore.client()

INPUT_FILE = "user_data.json" # Change this if your old json file is named differently

def run_migration():
    if not os.path.exists(INPUT_FILE):
        print(f"Error: {INPUT_FILE} not found. Place your old JSON file here!")
        return

    print(f"Loading {INPUT_FILE}...")
    with open(INPUT_FILE, "r") as f:
        try:
            users_dict = json.load(f)
        except Exception as e:
            print(f"Failed to parse JSON: {e}")
            return

    count = 0
    print(f"Found {len(users_dict)} users. Migrating to Firebase...")
    
    # Check if the json is a list or a dictionary. Assuming dictionary: {"1234": {"name": "Test", ...}}
    if isinstance(users_dict, list):
        # If it's a list, assume each item has an "id" field
        items = users_dict
    else:
        # If dict, map items to include 'id'
        items = [{"id": k, **v} for k, v in users_dict.items()]

    for user in items:
        uid = str(user.get("id"))
        if not uid or uid == "None":
            continue
            
        # Structure payload correctly for the new Firebase Logic
        profile = {
            "name": user.get("name", "Unknown"),
            "age": user.get("age", 18),
            "gender": user.get("gender", "Other"),
            "interested_gender": user.get("interested_gender", "Other"), # Added newly
            "place": user.get("place", "Unknown"),
            "bio": user.get("description", user.get("bio", "")), # Covers old format
            "interest": user.get("interest", "Just Chat 💬"),
            "chatting_with": None,
            "waiting_for_acceptance": False,
            "joined_at": user.get("joined_at", datetime.utcnow().isoformat()),
            "chat_count": user.get("chat_count", 0),
            
            # Critical: Set 10 points for all legacy users
            "points": 10
        }
        
        # Save to Firebase
        db.collection("users").document(uid).set(profile, merge=True)
        count += 1
        if count % 50 == 0:
            print(f"Uploaded {count} users...")

    print(f"✅ Migration Complete! {count} legacy users uploaded to Firebase successfully with 10 default points.")

if __name__ == "__main__":
    run_migration()
