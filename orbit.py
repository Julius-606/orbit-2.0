import os
import json
import random
import asyncio
import sys
import time
import warnings

# --- 🔇 SUPPRESS WARNINGS ---
os.environ["GRPC_VERBOSITY"] = "ERROR"
os.environ["GLOG_minloglevel"] = "2"
warnings.filterwarnings("ignore")

import google.generativeai as genai
from telegram import Bot

# --- 🔐 SECRETS MANAGEMENT ---
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
KEYS_STRING = os.environ.get("GEMINI_KEYS")

if not TELEGRAM_TOKEN or not KEYS_STRING:
    try:
        import toml
        script_dir = os.path.dirname(os.path.abspath(__file__))
        secrets_path = os.path.join(script_dir, ".streamlit", "secrets.toml")
        with open(secrets_path, "r") as f:
            local_secrets = toml.load(f)
            TELEGRAM_TOKEN = TELEGRAM_TOKEN or local_secrets.get("TELEGRAM_TOKEN")
            raw_keys = local_secrets.get("GEMINI_KEYS")
            if isinstance(raw_keys, list):
                GEMINI_API_KEYS = raw_keys
            elif isinstance(raw_keys, str):
                GEMINI_API_KEYS = raw_keys.split(",")
            else:
                GEMINI_API_KEYS = []
    except Exception:
        pass
else:
    GEMINI_API_KEYS = KEYS_STRING.split(",") if KEYS_STRING else []

GEMINI_API_KEYS = [k.strip() for k in GEMINI_API_KEYS if k.strip()]

if not TELEGRAM_TOKEN or not GEMINI_API_KEYS:
    print("❌ FATAL ERROR: Secrets not found.")
    sys.exit(1)

# --- 🎯 TARGET CONFIGURATION ---
# Add as many IDs as you want here. 
# Channel IDs usually start with -100
TARGET_IDS = [
    "6882899041",            # Your Personal ID
    "-1003540692903" # ⬅️ PASTE YOUR CHANNEL ID HERE (e.g. "-10012345678")
]

CURRENT_KEY_INDEX = 0

# --- CONFIGURATION & ROTATION ---
def configure_genai():
    global CURRENT_KEY_INDEX
    if not GEMINI_API_KEYS: return
    key = GEMINI_API_KEYS[CURRENT_KEY_INDEX]
    try:
        genai.configure(api_key=key)
    except Exception as e:
        print(f"⚠️ Config Error on Key #{CURRENT_KEY_INDEX+1}: {e}")

def rotate_key():
    global CURRENT_KEY_INDEX
    if len(GEMINI_API_KEYS) > 1:
        CURRENT_KEY_INDEX = (CURRENT_KEY_INDEX + 1) % len(GEMINI_API_KEYS)
        print(f"🔄 Rotating to Backup Key #{CURRENT_KEY_INDEX + 1}...")
        configure_genai()
        global model
        model = get_valid_model() 
        return True
    return False

# 📡 SONAR SCANNER
def get_valid_model():
    print("🔍 Sonar Scanning for valid models...")
    try:
        models = list(genai.list_models())
        valid_models = [m.name for m in models if 'generateContent' in m.supported_generation_methods]
        
        # 1. Look for standard 1.5 flash
        for m in valid_models:
            if 'gemini-1.5-flash' in m and 'latest' not in m and 'exp' not in m:
                print(f"✅ Locked on target: {m}")
                return genai.GenerativeModel(m.replace("models/", ""))
        
        # 2. Look for ANY flash
        for m in valid_models:
             if 'flash' in m and 'gemini-2' not in m and 'exp' not in m:
                print(f"⚠️ Flash Fallback: {m}")
                return genai.GenerativeModel(m.replace("models/", ""))

        if valid_models:
            return genai.GenerativeModel(valid_models[0].replace("models/", ""))
            
    except Exception as e:
        print(f"⚠️ Scan failed: {e}")
    
    print("🤞 Sonar failed. Forcing 'gemini-1.5-flash'...")
    return genai.GenerativeModel('gemini-1.5-flash')

configure_genai()
model = get_valid_model()

# 🛡️ SAFE GENERATOR
def generate_content_safe(prompt_text):
    global model
    max_retries = 3
    for attempt in range(max_retries):
        try:
            return model.generate_content(prompt_text)
        except Exception as e:
            err_msg = str(e)
            if "404" in err_msg:
                print("⚠️ Model 404. Re-scanning...")
                model = get_valid_model()
                time.sleep(1)
                continue
            elif "429" in err_msg or "403" in err_msg:
                print(f"⏳ API Issue ({err_msg}). Rotating...")
                if rotate_key():
                    time.sleep(2)
                    continue
                else:
                    time.sleep(10)
            else:
                print(f"❌ API Error: {err_msg}")
                return None
    return None

# 🛡️ ROBUST MESSAGE SENDER (Splits Long Texts)
async def send_safe_message(bot, chat_id, text):
    # Telegram hard limit is 4096. We use 4000 to be safe.
    MAX_LENGTH = 4000 

    # Helper to send a single chunk safely
    async def send_chunk(chunk):
        try:
            await bot.send_message(chat_id=chat_id, text=chunk, parse_mode='HTML')
        except Exception as e:
            # If HTML fails (e.g. we sliced a <b> tag in half), send raw text
            print(f"⚠️ HTML formatting failed for chunk, sending raw: {e}")
            await bot.send_message(chat_id=chat_id, text=chunk)

    if len(text) <= MAX_LENGTH:
        await send_chunk(text)
    else:
        # ✂️ It's too big. Split it.
        lines = text.split('\n')
        current_chunk = ""
        
        for line in lines:
            if len(current_chunk) + len(line) + 1 > MAX_LENGTH:
                # Send what we have so far
                await send_chunk(current_chunk)
                current_chunk = ""
            
            current_chunk += line + "\n"
        
        # Send the leftovers
        if current_chunk:
            await send_chunk(current_chunk)

# 📡 BROADCAST HELPER
async def broadcast_message(bot, text):
    """Sends a text message to all targets in TARGET_IDS"""
    for chat_id in TARGET_IDS:
        # Skip placeholder text if user forgot to remove it
        if "REPLACE" in chat_id: 
            print("⚠️ Skipping placeholder ID")
            continue
            
        try:
            print(f"📤 Sending to {chat_id}...")
            await send_safe_message(bot, chat_id, text)
        except Exception as e:
            print(f"⚠️ Broadcast failed for {chat_id}: {e}")

def load_config():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    config_path = os.path.join(script_dir, 'config.json')
    try:
        with open(config_path, 'r') as f: return json.load(f)
    except FileNotFoundError: return None

# 🚀 MAIN CHAOS ENGINE
async def send_chaos():
    bot = Bot(token=TELEGRAM_TOKEN)
    config = load_config()
    
    if not config: return 

    # DEBUG OVERRIDES
    if "--quiz" in sys.argv: roll = 90
    elif "--brain_teaser" in sys.argv: roll = 100
    elif "--fact" in sys.argv: roll = 60
    else: roll = random.randint(1, 100)
    
    print(f"🎲 Rolled a {roll}")

    if roll <= 50:
        print("Silence is golden.")
        return

    # --- FACT MODE (51-85) ---
    elif 51 <= roll <= 85:
        topic = random.choice(config['interests'])
        prompt = f"Tell me a mind-blowing, short random fact about {topic}. Keep it under 2 sentences."
        response = generate_content_safe(prompt)
        if response and response.text:
            msg = f"🎱 <b>Magic-∞ Fact:</b>\n\n{response.text}"
            await broadcast_message(bot, msg)
        else:
            print("⚠️ No response for Fact")

    # --- MULTI-QUIZ MODE (86-98) ---
    elif 86 <= roll <= 98:
        quotes = [
            "Your stop loss is tighter than your work ethic right now. 🛑💀",
            "Green candles wait for no one. Neither does your rent. 🕯️💸",
            "Market's volatile. Your focus? Non-existent. 📉🥴",
            "Stop staring at the 1-minute chart and start grinding. ⏳😤",
            "Do it for the plot. (And the paycheck). 🎬💰",
            "Standing on business? More like sleeping on business. 🛌📉",
            "Delulu is not the solulu if you don't do the work. 🦄🚫",
            "Academic comeback season starts in 3... 2... never mind, just start. 🎓🏁",
            "Not the academic downfall arc... fix it immediately. 📉🚧",
            "Brain rot is real, and you are patient zero. 🧟📉",
            "Locked in? Or locked out of reality? Focus. 🔒🌍"
        ]
        
        unit = random.choice(config['current_units'])
        quote = random.choice(quotes)
        
        # 🎲 Determine number of questions (1 to 5)
        num_q = random.randint(1, 5) 
        
        # Broadcast Intro
        await broadcast_message(bot, f"🚨 <b>{quote}</b>\n\nIncoming Rapid Fire: <b>{num_q} Questions on {unit}</b>")
        
        # BATCH REQUEST
        prompt = f"""
        Generate {num_q} multiple-choice questions about {unit} for a 4th Year Student.
        
        Strict JSON format: Return a LIST of objects.
        [
            {{"question": "...", "options": ["A","B","C","D"], "correct_id": 0, "explanation": "..."}},
            ...
        ]
        
        Limits: Question < 250 chars, Options < 100 chars.
        """.replace("{num_questions}", str(num_q))

        response = generate_content_safe(prompt)
        
        if response and response.text:
            try:
                text = response.text.replace('```json', '').replace('```', '').strip()
                data = json.loads(text)
                
                if isinstance(data, dict):
                    data = [data]
                
                for i, q in enumerate(data):
                    try:
                        # Broadcast Poll to all targets
                        for chat_id in TARGET_IDS:
                            if "REPLACE" in chat_id: continue
                            
                            try:
                                await bot.send_poll(
                                    chat_id=chat_id,
                                    question=f"[{i+1}/{len(data)}] {q['question'][:290]}",
                                    options=[o[:97] for o in q['options']],
                                    type="quiz",
                                    correct_option_id=q['correct_id'],
                                    explanation=q['explanation'][:190]
                                )
                            except Exception as e:
                                print(f"⚠️ Poll failed for {chat_id}: {e}")
                                
                        time.sleep(2) 
                    except Exception as e:
                        print(f"⚠️ Poll loop error {i+1}: {e}")
                        
            except Exception as e:
                print(f"Quiz Parse Error: {e}")
        else:
             print("⚠️ No response for Quiz")
             
    # --- 👑 GOD MODE: THE DIAGNOSTIC NIGHTMARE (99-100) ---
    else:
        await broadcast_message(bot, "👑 <b>GOD MODE ACTIVATED: THE HOUSE M.D. PROTOCOL</b> 👑\n\n<i>Searching global medical archives for anomalies...</i>")
        
        god_prompt = """
        ACT AS: A Senior Consultant at a top-tier research hospital.
        TASK: Present a "Medical Mystery" case study for a final year student.
        TOPIC: A rare, baffling, or catastrophic condition (Any field: Toxicology, Neuro, ID, Genetics).
        
        STRICT FORMATTING RULES:
        1. Do NOT use Markdown (no ##, no **, no __).
        2. Use only these HTML tags: <b>bold</b>, <i>italic</i>, <u>underline</u>, <span class="tg-spoiler">hidden</span>.
        3. Split the response into two distinct parts separated by the text "||REVEAL||".
        
        PART 1 (The Presentation):
        - Start with <b>PATIENT DEMOGRAPHICS:</b> (Make it weird).
        - <b>VITALS & LABS:</b> Use <u>underline</u> tags to highlight abnormal values or key findings.
        - <b>THE DETERIORATION:</b> (Patient gets worse).
        - End with: <i>"WHAT IS YOUR DIAGNOSIS?"</i>
        
        PART 2 (The Solution):
        - <b>DIAGNOSIS:</b> Wrap the name of the diagnosis in <span class="tg-spoiler">TAGS</span> so it is hidden.
        - <b>THE SMOKING GUN:</b> Wrap the key clue in <span class="tg-spoiler">TAGS</span> so it is hidden.
        - <b>PATHOPHYSIOLOGY:</b> Explain why this happened.
        - <b>SURVIVAL STATUS:</b> Did they make it?
        
        TONE: Intense, professional but baffled ("Doctors were stumped"), academic.
        """
        
        response = generate_content_safe(god_prompt)
        
        if response and response.text:
            # Split the Case from the Answer
            parts = response.text.split("||REVEAL||")
            
            # Helper to scrub markdown AND illegal HTML
            def scrub(t):
                # 1. Strip Markdown
                t = t.replace("## ", "").replace("### ", "").replace("**", "").replace("__", "")
                # 2. Strip Illegal HTML for Telegram
                t = t.replace("<p>", "").replace("</p>", "\n\n") 
                t = t.replace("<ul>", "").replace("</ul>", "")
                t = t.replace("<li>", "• ").replace("</li>", "\n") 
                t = t.replace("<h1>", "<b>").replace("</h1>", "</b>\n") 
                t = t.replace("<h2>", "<b>").replace("</h2>", "</b>\n")
                # 3. Ensure spoilers and underlines are kept (Safety check)
                # No action needed as replace only targets illegal tags
                return t.strip()

            part1_clean = scrub(parts[0])
            
            # Send The Case (Part 1)
            case_text = f"📋 <b>CASE FILE #{random.randint(1000,9999)}: THE UNEXPLAINED</b>\n\n{part1_clean}"
            await broadcast_message(bot, case_text)
            
            # Build Suspense
            await broadcast_message(bot, "<i>⏳ Analyzing differentials... (You have 10 seconds to guess)</i>")
            time.sleep(10) 
            
            # The Prestige (Part 2)
            if len(parts) > 1:
                part2_clean = scrub(parts[1])
                reveal_text = f"🧬 <b>DIAGNOSIS REVEALED</b>\n\n{part2_clean}"
                await broadcast_message(bot, reveal_text)
            else:
                await broadcast_message(bot, "⚠️ <b>Data Corruption:</b> AI forgot the spoiler tag. Diagnosis is in the text above.")
        else:
            await broadcast_message(bot, "⚠️ <b>System Failure:</b> The case files are encrypted. (API Error).")

if __name__ == "__main__":
    asyncio.run(send_chaos())

