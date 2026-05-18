import streamlit as st
import google.generativeai as genai
import gspread
from google.oauth2.service_account import Credentials
from datetime import datetime
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import qrcode
from io import BytesIO

# ============================================================
# CONFIGURATION
# ============================================================
SHEET_ID = "1S9Q87yl_HyFoqS0GeI8350hsjCT7i6ehF-1Uxxx56C0"
APP_NAME = "UnitAssist"
APP_TAGLINE = "Your 24/7 Self-Storage Assistant"

st.set_page_config(
    page_title=APP_NAME,
    page_icon="📦",
    layout="centered",
    initial_sidebar_state="collapsed"
)

# ============================================================
# GOOGLE SHEETS CONNECTION
# ============================================================
@st.cache_resource
def get_gspread_client():
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive"
    ]
    creds = Credentials.from_service_account_info(
        st.secrets["gcp_service_account"],
        scopes=scopes
    )
    return gspread.authorize(creds)

@st.cache_data(ttl=300)
def load_knowledge_base():
    """Load KB from Google Sheet. Cached 5 minutes."""
    client = get_gspread_client()
    sheet = client.open_by_key(SHEET_ID).worksheet("knowledge_base")
    rows = sheet.get_all_records()
    return {row["topic"].strip().lower(): row["answer"] for row in rows if row.get("topic")}

@st.cache_data(ttl=300)
def load_config():
    """Load config tab as key/value dict."""
    client = get_gspread_client()
    sheet = client.open_by_key(SHEET_ID).worksheet("config")
    rows = sheet.get_all_records()
    return {row["key"].strip().lower(): str(row["value"]).strip() for row in rows if row.get("key")}

def log_unanswered(question):
    """Log an unanswered question to the sheet."""
    try:
        client = get_gspread_client()
        sheet = client.open_by_key(SHEET_ID).worksheet("unanswered_log")
        sheet.append_row([
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            question
        ])
    except Exception as e:
        st.error(f"Logging failed: {e}")

# ============================================================
# EMAIL NOTIFICATIONS
# ============================================================
def send_unanswered_email(question, host_email):
    try:
        msg = MIMEMultipart()
        msg["From"] = f"UnitAssist <{st.secrets['GMAIL_USER']}>"
        msg["To"] = host_email
        msg["Subject"] = f"UnitAssist: Unanswered Tenant Question"

        body = f"""A tenant asked a question your AI couldn't answer.

Question: {question}

Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

Recommendation: Add an answer for this topic to your knowledge base so UnitAssist can handle it next time.

— UnitAssist
"""
        msg.attach(MIMEText(body, "plain"))

        with smtplib.SMTP("smtp.gmail.com", 587) as server:
            server.starttls()
            server.login(st.secrets["GMAIL_USER"], st.secrets["GMAIL_APP_PASSWORD"])
            server.send_message(msg)
        return True
    except Exception as e:
        st.error(f"Email failed: {e}")
        return False

# ============================================================
# GEMINI AI
# ============================================================
def build_system_prompt(kb, facility_name):
    kb_text = "\n\n".join([f"TOPIC: {topic}\nANSWER: {answer}" for topic, answer in kb.items()])

    return f"""You are UnitAssist, the official AI assistant for {facility_name}, a self-storage facility.

Your job is to answer tenant questions accurately and warmly using ONLY the knowledge base below. You are speaking with current tenants, prospective renters, and visitors.

KNOWLEDGE BASE:
{kb_text}

CRITICAL RULES:
1. ONLY use information from the knowledge base above. Never invent facts, prices, hours, policies, or details.
2. If the answer is not in the knowledge base, respond EXACTLY with: "I don't have that information on hand, but I've flagged your question for the office team — they'll follow up with you directly. In the meantime, you can reach the office at (515) 555-0142 during business hours."
3. Be friendly, concise, and professional. 2-4 sentences when possible.
4. If the tenant asks something unrelated to self-storage (e.g., weather, jokes, news), politely redirect: "I'm here to help with questions about {facility_name}. Is there something about your unit or our facility I can help with?"
5. Do NOT make up phone numbers, emails, prices, or policies not listed above.
6. If asked about something dangerous, illegal, or violating prohibited items rules, refer them to the prohibited items policy.
7. Never give legal, financial, or insurance advice beyond what's stated in the KB.

Remember: When in doubt, use the fallback response in rule #2."""

def get_ai_response(user_question, chat_history, kb, facility_name):
    genai.configure(api_key=st.secrets["GEMINI_API_KEY"])
    model = genai.GenerativeModel(
        model_name="gemini-2.5-flash",
        system_instruction=build_system_prompt(kb, facility_name)
    )

    # Build conversation context
    gemini_history = []
    for msg in chat_history[:-1]:  # exclude the current question
        gemini_history.append({
            "role": "user" if msg["role"] == "user" else "model",
            "parts": [msg["content"]]
        })

    chat = model.start_chat(history=gemini_history)
    response = chat.send_message(user_question)
    return response.text

# ============================================================
# QR CODE
# ============================================================
def generate_qr_code(url):
    qr = qrcode.QRCode(version=1, box_size=8, border=2)
    qr.add_data(url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf

# ============================================================
# SESSION STATE
# ============================================================
if "authenticated" not in st.session_state:
    st.session_state.authenticated = False
if "messages" not in st.session_state:
    st.session_state.messages = []
if "total_chats" not in st.session_state:
    st.session_state.total_chats = 0
if "unanswered_count" not in st.session_state:
    st.session_state.unanswered_count = 0

# ============================================================
# LOAD DATA
# ============================================================
try:
    config = load_config()
    kb = load_knowledge_base()
    facility_name = config.get("facility_name", "Cyclone Self Storage")
    correct_pin = config.get("pin", "4321")
    host_email = config.get("host_email", st.secrets.get("HOST_EMAIL", ""))
except Exception as e:
    st.error(f"⚠️ Setup error: {e}")
    st.stop()

# ============================================================
# PIN AUTHENTICATION SCREEN
# ============================================================
if not st.session_state.authenticated:
    st.title(f"📦 {APP_NAME}")
    st.caption(APP_TAGLINE)
    st.markdown(f"### Welcome to **{facility_name}**")
    st.write("Please enter your access PIN to chat with our AI assistant.")

    pin_input = st.text_input("Access PIN", type="password", max_chars=10)
    if st.button("Enter", type="primary", use_container_width=True):
        if pin_input == correct_pin:
            st.session_state.authenticated = True
            st.rerun()
        else:
            st.error("Incorrect PIN. Please try again or contact the office.")
    st.stop()

# ============================================================
# MAIN CHAT UI
# ============================================================
st.title(f"📦 {APP_NAME}")
st.caption(f"{facility_name} — {APP_TAGLINE}")

# Sidebar
with st.sidebar:
    st.header("Facility Dashboard")
    col1, col2 = st.columns(2)
    col1.metric("Chats", st.session_state.total_chats)
    col2.metric("Unanswered", st.session_state.unanswered_count)

    st.divider()
    st.subheader("Quick Actions")

    if st.button("🔄 Refresh KB", use_container_width=True):
        st.cache_data.clear()
        st.success("Knowledge base refreshed.")
        st.rerun()

    if st.button("🗑️ Clear Chat", use_container_width=True):
        st.session_state.messages = []
        st.rerun()

    if st.button("🔒 Lock App", use_container_width=True):
        st.session_state.authenticated = False
        st.session_state.messages = []
        st.rerun()

    st.divider()
    st.subheader("Tenant QR Code")
    try:
        current_url = st.context.url if hasattr(st.context, "url") else "https://unitassist-demo.streamlit.app"
    except Exception:
        current_url = "https://unitassist-demo.streamlit.app"
    qr_buf = generate_qr_code(current_url)
    st.image(qr_buf, caption="Tenants scan to chat")
    st.download_button(
        label="⬇️ Download QR (PNG)",
        data=qr_buf,
        file_name="unitassist_qr.png",
        mime="image/png",
        use_container_width=True
    )

    st.divider()
    st.caption(f"✅ System operational  \n📚 {len(kb)} KB topics loaded")

# Welcome message on first load
if not st.session_state.messages:
    welcome = f"👋 Hi! I'm UnitAssist, your AI helper for {facility_name}. Ask me anything — gate hours, unit sizes, payment questions, move-out process, you name it. How can I help today?"
    st.session_state.messages.append({"role": "assistant", "content": welcome})

# Display chat history
for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

# Chat input
if user_input := st.chat_input("Ask a question about your unit or facility..."):
    st.session_state.messages.append({"role": "user", "content": user_input})
    with st.chat_message("user"):
        st.markdown(user_input)

    with st.chat_message("assistant"):
        with st.spinner("Thinking..."):
            try:
                response = get_ai_response(user_input, st.session_state.messages, kb, facility_name)
                st.markdown(response)
                st.session_state.messages.append({"role": "assistant", "content": response})
                st.session_state.total_chats += 1

                # Detect fallback response → log + email
                fallback_phrase = "I don't have that information on hand"
                if fallback_phrase.lower() in response.lower():
                    log_unanswered(user_input)
                    send_unanswered_email(user_input, host_email)
                    st.session_state.unanswered_count += 1

            except Exception as e:
                st.error(f"Sorry, something went wrong: {e}")
