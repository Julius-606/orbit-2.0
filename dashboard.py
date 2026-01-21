import os
import warnings
os.environ["GRPC_VERBOSITY"] = "ERROR"
os.environ["GLOG_minloglevel"] = "2"
warnings.filterwarnings("ignore")

import streamlit as st
import json
import time
import random
import google.generativeai as genai
import pandas as pd # Essential for Technical Analysis
from datetime import datetime

# --- ☁️ OPTIONAL IMPORTS ---
try:
    from github import Github # pip install PyGithub
except ImportError:
    Github = None # Soft fail if user hasn't installed it

# --- ⚙️ SETTINGS ---
MAX_ARCHIVED_SESSIONS = 20 # Keep main config light
MAX_VAULT_SESSIONS = 100   # Deep storage capacity

# --- 🔐 SECURE KEYCHAIN ---
GEMINI_API_KEYS = []
try:
    raw_keys = st.secrets.get("GEMINI_KEYS")
    if raw_keys:
        if isinstance(raw_keys, list):
            GEMINI_API_KEYS = raw_keys
        else:
            GEMINI_API_KEYS = [k.strip() for k in raw_keys.split(",")]
except Exception:
    pass

if not GEMINI_API_KEYS:
    try:
        keys_str = os.environ.get("GEMINI_KEYS")
        if keys_str:
            GEMINI_API_KEYS = [k.strip() for k in keys_str.split(",")]
    except Exception:
        pass

# --- 🆕 LOCAL DEV CHANGE: Manual Key Input ---
if not GEMINI_API_KEYS:
    with st.sidebar:
        st.warning("⚠️ No Secrets Found")
        manual_key = st.text_input("🔑 Enter Gemini API Key", type="password")
        if manual_key:
            GEMINI_API_KEYS = [manual_key]

if not GEMINI_API_KEYS:
    st.error("❌ NO API KEYS FOUND! Please configure secrets or enter one in the sidebar.")
    st.stop()

if "key_index" not in st.session_state: st.session_state.key_index = 0

# --- 🧠 BRAIN CONFIGURATION (OPTIMIZED) ---
def configure_genai():
    """Sets the active API key based on session state index."""
    try:
        # Wrap index safety
        idx = st.session_state.key_index % len(GEMINI_API_KEYS)
        current_key = GEMINI_API_KEYS[idx]
        genai.configure(api_key=current_key)
        return True
    except Exception: return False

def resolve_model_name():
    """Scans for the best model ONCE and caches it."""
    try:
        configure_genai()
        models = list(genai.list_models())
        valid_models = [m.name for m in models if 'generateContent' in m.supported_generation_methods]
        
        # Priority 1: Flash 1.5
        for m in valid_models:
            if 'gemini-1.5-flash' in m and 'latest' not in m and 'exp' not in m:
                return m.replace("models/", "")
        
        # Priority 2: Any Flash
        for m in valid_models:
             if 'flash' in m and 'gemini-2' not in m and 'exp' not in m:
                return m.replace("models/", "")

        # Priority 3: Anything else
        if valid_models:
            return valid_models[0].replace("models/", "")
    except Exception:
        pass
    return "gemini-1.5-flash" # Fallback

# 1. Initialize Model Name (Only once per session)
if "model_name" not in st.session_state:
    with st.spinner("🩺 Checking Vitals..."):
        st.session_state.model_name = resolve_model_name()

# 2. Configure & Instantiate (Runs on every rerun)
configure_genai()
model = genai.GenerativeModel(st.session_state.model_name)

def rotate_key():
    """Switches key index and re-instantiates model without re-scanning."""
    if len(GEMINI_API_KEYS) <= 1:
        st.toast("❌ No backup keys available.", icon="🛑")
        return False

    st.session_state.key_index = (st.session_state.key_index + 1) % len(GEMINI_API_KEYS)
    
    # Re-configure global genai with new key
    configure_genai()
    
    # Update global model object
    global model
    model = genai.GenerativeModel(st.session_state.model_name)
    
    st.toast(f"🔄 Swapped to Key #{st.session_state.key_index + 1}", icon="🔑")
    return True

def ask_orbit(prompt):
    global model
    # Retry loop: Try all keys + 1 extra attempt
    max_retries = len(GEMINI_API_KEYS) + 1
    
    for attempt in range(max_retries):
        try:
            return model.generate_content(prompt)
        except Exception as e:
            err_msg = str(e)
            is_quota = "429" in err_msg or "quota" in err_msg.lower() or "ResourceExhausted" in err_msg
            is_auth = "403" in err_msg or "leaked" in err_msg.lower() or "API key" in err_msg
            
            if is_quota or is_auth:
                 reason = "Quota" if is_quota else "Auth"
                 # st.toast(f"⚠️ Key #{st.session_state.key_index+1} Failed ({reason}). Rotating...", icon="🔥")
                 if rotate_key():
                    time.sleep(1) # Short breather
                    continue
                 else:
                    return None
            
            # Non-critical error (Server side 500 etc)
            print(f"❌ Chat Error: {err_msg}")
            # Optional: retry once for server errors without rotating
            if attempt < max_retries - 1:
                time.sleep(1)
                continue
                
            return None
    return None

# --- PAGE SETUP ---
st.set_page_config(page_title="Orbit Command Center", page_icon="🩺", layout="wide")

# --- ☁️ GITHUB INTEGRATION ---
def get_github_session():
    # Check if library is even available first
    if Github is None:
        return None, None

    token = st.secrets.get("GITHUB_TOKEN") or st.secrets.get("GITHUB_KEYS")
    repo_name = st.secrets.get("GITHUB_REPO")
    
    if not token or not repo_name:
        # st.sidebar.error("❌ GitHub Secrets Missing!") # Muted for local dev
        return None, None
    
    try:
        g = Github(token)
        repo = g.get_repo(repo_name)
        return g, repo
    except Exception as e:
        st.sidebar.error(f"❌ GitHub Connection Failed: {e}")
        return None, None

def load_config():
    g, repo = get_github_session()
    if repo:
        try:
            contents = repo.get_contents("config.json")
            decoded = contents.decoded_content.decode()
            return json.loads(decoded)
        except Exception as e:
            st.warning(f"⚠️ Cloud load failed ({e}). Checking local...")
    
    script_dir = os.path.dirname(os.path.abspath(__file__))
    config_path = os.path.join(script_dir, 'config.json')
    try:
        with open(config_path, 'r') as f: return json.load(f)
    except FileNotFoundError:
        return {
            "user_name": "Future Doc",
            "difficulty": "Medium (Standard)",
            "current_units": [],
            "active_session": [],
            "archived_sessions": [],
            "quiz_history": [],
            "interests": [],
            "ai_persona": "Standard Orbit",
            "lock_background": False,
            "low_data_mode": False, 
            "unit_inventory": {"General": ["Math", "Science", "History", "Coding"]}
        }

def save_config(new_config):
    g, repo = get_github_session()
    if repo:
        try:
            contents = repo.get_contents("config.json")
            repo.update_file(
                path=contents.path,
                message="🤖 Orbit Session Sync",
                content=json.dumps(new_config, indent=4),
                sha=contents.sha
            )
            return True
        except Exception as e:
            st.error(f"❌ Cloud Save Failed: {e}")
            return False
    else:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        config_path = os.path.join(script_dir, 'config.json')
        with open(config_path, 'w') as f: json.dump(new_config, f, indent=4)
        return True

# --- 🏦 VAULT (DEEP STORAGE) MANAGEMENT ---
def load_vault():
    """Loads the heavy archive_vault.json from cloud or local."""
    g, repo = get_github_session()
    if repo:
        try:
            contents = repo.get_contents("archive_vault.json")
            decoded = contents.decoded_content.decode()
            return json.loads(decoded), contents.sha # Return SHA for updates
        except Exception:
            # File might not exist yet, that's chill
            return [], None
    
    # Local fallback
    script_dir = os.path.dirname(os.path.abspath(__file__))
    vault_path = os.path.join(script_dir, 'archive_vault.json')
    try:
        with open(vault_path, 'r') as f: return json.load(f), None
    except FileNotFoundError:
        return [], None

def save_vault(vault_data, sha=None):
    """Saves the heavy vault data."""
    g, repo = get_github_session()
    if repo:
        try:
            # If SHA exists, update. If not, create.
            if sha:
                contents = repo.get_contents("archive_vault.json") # Re-fetch to be safe
                repo.update_file(
                    path="archive_vault.json",
                    message="🧊 Deep Freeze Archive Update",
                    content=json.dumps(vault_data, indent=4),
                    sha=contents.sha
                )
            else:
                repo.create_file(
                    path="archive_vault.json",
                    message="🧊 Init Deep Freeze",
                    content=json.dumps(vault_data, indent=4)
                )
            return True
        except Exception as e:
            st.error(f"❌ Vault Save Failed: {e}")
            return False
    else:
        # Local
        script_dir = os.path.dirname(os.path.abspath(__file__))
        vault_path = os.path.join(script_dir, 'archive_vault.json')
        with open(vault_path, 'w') as f: json.dump(vault_data, f, indent=4)
        return True

def push_to_vault(old_session):
    """Moves a session from RAM to Deep Storage."""
    vault, sha = load_vault()
    
    # Insert as the 'newest' of the old stuff (index 0)
    vault.insert(0, old_session)
    
    # Cap at MAX_VAULT_SESSIONS (100)
    if len(vault) > MAX_VAULT_SESSIONS:
        vault = vault[:MAX_VAULT_SESSIONS]
        
    save_vault(vault, sha)

st.title("🩺 Orbit: Your Personal Academic Weapon")

# Load config
if 'config' not in st.session_state:
    st.session_state.config = load_config()

config = st.session_state.config

# --- 🎨 UI THEME & BACKGROUND ---
def set_ui_theme(current_config):
    low_data = current_config.get('low_data_mode', False)
    accents = ["#00f2ff", "#ff0055", "#00ff9d", "#bd00ff", "#ffae00"]

    if low_data:
        bg_css = "background-color: #0e1117;"
        accent_color = random.choice(accents)
    else:
        base_urls = [
            "https://images.unsplash.com/photo-1576091160399-112ba8d25d1d", # Tech Blue
            "https://images.unsplash.com/photo-1532187863486-abf9dbad1b69", # Lab Fluids
            "https://images.unsplash.com/photo-1581093458791-9f302e683057", # Dark Dentist/Tech
            "https://images.unsplash.com/photo-1516549655169-df83a0674f66", # Stethoscope Dark
            "https://images.unsplash.com/photo-1505751172876-fa1923c5c528", # Doctor coat abstract
            "https://images.unsplash.com/photo-1584036561566-b93a901e3bae", # Molecular
            "https://images.unsplash.com/photo-1579684385261-d030917ac686", # Lab samples
            "https://images.unsplash.com/photo-1559757609-f31090331def",   # Blue petri dish
            "https://images.unsplash.com/photo-1582719478250-c89cae4dc85b", # Microscope dark
            "https://images.unsplash.com/photo-1530210124550-912dc1381cb8", # Heart rate monitor
            "https://images.unsplash.com/photo-1551076805-e1869033e561",   # Robotic surgery arm
            "https://images.unsplash.com/photo-1576086213369-97a306d36557", # MRI Scan
            "https://images.unsplash.com/photo-1581595221898-9d493d43c5e7", # Nurse working
            "https://images.unsplash.com/photo-1583324113626-70df0f4deaab", # Virus/Science
            "https://images.unsplash.com/photo-1518152006812-edab29b06cc4", # Dark EKG
            "https://images.unsplash.com/photo-1583912267652-3c82ea98c763", # Viral cells render
            "https://images.unsplash.com/photo-1581090464777-f3220bbe1b8b", # Industrial/Medical Light
            "https://images.unsplash.com/photo-1580481072645-022f9a6dbf27", # DNA Strand
            "https://images.unsplash.com/photo-1579154204601-01588f351e67", # Modern Lab
            "https://images.unsplash.com/photo-1530497610204-3c4286d41b78", # Skeleton/Anatomy
            "https://images.unsplash.com/photo-1530026405186-ed1f139313f8", # Digital DNA
            "https://images.unsplash.com/photo-1576091358783-a212ec293ff3", # Medical Gloves
            "https://images.unsplash.com/photo-1551884170-09fb70a3a2ed",   # Pharmacy Art
            "https://images.unsplash.com/photo-1584362917165-526a968579e8", # Test Tubes
            "https://images.unsplash.com/photo-1582719508461-905c673771fd", # Virus Abstract
            "https://images.unsplash.com/photo-1581091226825-a6a2a5aee158", # Laptop/Data/Medical
            "https://images.unsplash.com/photo-1519494006885-e2420c4ac12e", # Hospital Room
            "https://images.unsplash.com/photo-1532094349884-543bc11b234d", # Pipette
            "https://images.unsplash.com/photo-1576670156567-c3679806543b", # Nurse/Doctor writing
            "https://images.unsplash.com/photo-1584036518754-80c32808e114", # Medical Research
        ]
        backgrounds = [f"{url}?auto=format&fit=crop&w=1920&q=80" for url in base_urls]
        
        current_time = time.time()
        if "theme_cache" not in st.session_state:
            st.session_state.theme_cache = {
                "bg_url": random.choice(backgrounds),
                "accent": random.choice(accents),
                "next_update": current_time + random.randint(300, 1800)
            }
        
        is_locked = current_config.get('lock_background', False)
        
        if not is_locked and current_time > st.session_state.theme_cache["next_update"]:
            st.session_state.theme_cache["bg_url"] = random.choice(backgrounds)
            st.session_state.theme_cache["accent"] = random.choice(accents)
            st.session_state.theme_cache["next_update"] = current_time + random.randint(300, 1800)
        
        bg_url = st.session_state.theme_cache["bg_url"]
        accent_color = st.session_state.theme_cache["accent"]
        
        bg_css = f"""
            background-color: #0e1117;
            background-image: linear-gradient(rgba(0, 0, 0, 0.5), rgba(0, 0, 0, 0.7)), url("{bg_url}");
            background-size: cover;
            background-position: center;
            background-attachment: fixed;
        """

    st.markdown(
        f"""
        <style>
        @import url('https://fonts.googleapis.com/css2?family=Orbitron:wght@400;700&family=Roboto:wght@300;400&display=swap');
        .stApp {{ {bg_css} font-family: 'Roboto', sans-serif; }}
        [data-testid="stSidebar"] {{ background-color: rgba(0, 0, 0, 0.6) !important; backdrop-filter: blur(12px); border-right: 1px solid rgba(255, 255, 255, 0.1); }}
        h1, h2, h3 {{ font-family: 'Orbitron', sans-serif !important; color: {accent_color} !important; text-shadow: 0 0 10px rgba(0,0,0,0.5); letter-spacing: 1.5px; }}
        div.stButton > button {{ background: rgba(255, 255, 255, 0.1); color: white; border: 1px solid {accent_color}; border-radius: 8px; transition: all 0.3s ease; backdrop-filter: blur(5px); }}
        div.stButton > button:hover {{ background: {accent_color}; color: black; box-shadow: 0 0 20px {accent_color}; transform: translateY(-2px); font-weight: bold; }}
        header[data-testid="stHeader"] {{ background-color: transparent !important; }}
        .stChatInputContainer {{ background-color: rgba(0, 0, 0, 0.6) !important; backdrop-filter: blur(10px); border-top: 1px solid rgba(255,255,255,0.1); }}
        </style>
        """,
        unsafe_allow_html=True
    )

if config:
    set_ui_theme(config)

    with st.sidebar:
        st.header("👤 Commander Profile")
        st.text_input("Username", value=config.get('user_name', 'Future Doc'), disabled=True)
        st.divider()
        diffs = ["Easy (Review)", "Medium (Standard)", "Hard (Exam Prep)", "Asian Parent Expectations (Extreme)"]
        curr_diff = config.get('difficulty', "Asian Parent Expectations (Extreme)")
        idx = diffs.index(curr_diff) if curr_diff in diffs else 3
        new_diff = st.selectbox("Difficulty Level", diffs, index=idx)
        if new_diff != curr_diff:
            config['difficulty'] = new_diff
            if save_config(config):
                st.session_state.config = config
        st.divider()
        st.header("🎯 Active Loadout")
        for unit in config.get('current_units', []): st.caption(f"• {unit}")

    tab1, tab2, tab3, tab4, tab5, tab6 = st.tabs(["💬 Orbit Chat", "📜 History", "📝 Chaos Quiz", "📈 Progress", "📚 Manager", "⚙️ Settings"])

    # --- TAB 1: ACTIVE CHAT SESSION ---
    with tab1:
        c1, c2 = st.columns([5, 1])
        with c1:
            st.subheader("🧠 Neural Link")
        with c2:
            if st.button("➕ New Chat", use_container_width=True, help="Archive current session and start fresh"):
                current_msgs = st.session_state.messages
                if current_msgs:
                    if 'archived_sessions' not in config: config['archived_sessions'] = []
                    
                    first_user_msg = next((m['content'] for m in current_msgs if m['role'] == 'user'), "Empty Session")
                    summary = (first_user_msg[:40] + '...') if len(first_user_msg) > 40 else first_user_msg
                    
                    session_archive = {
                        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M"),
                        "summary": summary,
                        "messages": current_msgs
                    }
                    
                    # 1. Add to local recent history
                    config['archived_sessions'].insert(0, session_archive)
                    
                    # 2. OVERFLOW LOGIC: Yeet old sessions to the Vault
                    while len(config['archived_sessions']) > MAX_ARCHIVED_SESSIONS:
                        # Pop the oldest session from recent history (last item)
                        overflow_session = config['archived_sessions'].pop()
                        with st.spinner("📦 Migrating old chats to Deep Storage..."):
                            push_to_vault(overflow_session)
                        st.toast("Oldest chat moved to Vault 🏦", icon="🧊")
                    
                    config['active_session'] = []
                    
                    save_config(config)
                    st.session_state.config = config
                    st.session_state.messages = []
                    st.rerun()

        if "messages" not in st.session_state:
            st.session_state.messages = config.get('active_session', [])

        for msg in st.session_state.messages:
            with st.chat_message(msg["role"]): st.markdown(msg["content"])

        if prompt := st.chat_input("Ask Orbit..."):
            st.session_state.messages.append({"role": "user", "content": prompt})
            with st.chat_message("user"): st.markdown(prompt)
            
            with st.chat_message("assistant"):
                with st.spinner("Thinking..."):
                    p_map = {
                        "Standard Orbit": "You are Orbit, a helpful and precise academic assistant.",
                        "Socratic Tutor": "You are a Socratic tutor. Never give the answer directly. Ask guiding questions to lead the user to the answer.",
                        "Dr. House": "You are Dr. Gregory House. You are brilliant but sarcastic, grumpy, and slightly condescending. Use medical metaphors. Roast the user if they ask something obvious.",
                        "ELI5": "Explain like I'm 5 years old. Use simple analogies and easy language."
                    }
                    selected_p = config.get('ai_persona', "Standard Orbit")
                    persona_prompt = p_map.get(selected_p, p_map["Standard Orbit"])

                    ctx = f"""
                    {persona_prompt}
   