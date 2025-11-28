# streamlit run p0dcaster_app.py
#  directory setup: cd C:\users\oakhtar\documents\pyprojs_local


import streamlit as st
import os
import tempfile
import json
import requests
import shutil
import re
from pathlib import Path
from pydub import AudioSegment
from pydub.effects import normalize, low_pass_filter
from datetime import datetime
import asyncio
import io

# --- TEXT PROCESSING ---
import PyPDF2
import docx
from pptx import Presentation
from bs4 import BeautifulSoup
import yt_dlp

# --- AI CLIENT ---
from openai import OpenAI

# ================= CONFIGURATION =================
st.set_page_config(
    page_title="PodcastLM Studio", 
    page_icon="🎧", 
    layout="wide",
    initial_sidebar_state="expanded"
)

# ================= SESSION STATE =================
if "authenticated" not in st.session_state:
    st.session_state.authenticated = False
if "script_data" not in st.session_state:
    st.session_state.script_data = None
if "source_text" not in st.session_state:
    st.session_state.source_text = ""
if "chat_history" not in st.session_state:
    st.session_state.chat_history = []
if "notebook_content" not in st.session_state:
    st.session_state.notebook_content = f"# 📓 Research Notebook\n**Session Started:** {datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n"
if "rehearsal_audio" not in st.session_state:
    st.session_state.rehearsal_audio = None

# ================= AUTHENTICATION =================
def check_password():
    user_pass = st.session_state.get("password_input", "")
    correct_pass = st.secrets.get("APP_PASSWORD")
    if correct_pass and user_pass == correct_pass:
        st.session_state.authenticated = True
    else:
        st.error("❌ Incorrect Password")

if not st.session_state.authenticated:
    st.title("🔒 Studio Login")
    st.text_input("Enter Password", type="password", key="password_input", on_change=check_password)
    st.stop()

# ================= UTILS =================

def chunk_text(text, max_tokens=25000):
    """Chunk text to fit token limits, summarize if needed."""
    if len(text) <= max_tokens:
        return [text]
    # Simple word-based chunking; could use tiktoken for precision
    words = text.split()
    chunks = [' '.join(words[i:i+ (max_tokens//4)] ) for i in range(0, len(words), max_tokens//4)]  # Rough token estimate
    return chunks

def summarize_chunk(client, chunk, prompt="Summarize this concisely: "):
    """Summarize a chunk if too long."""
    try:
        res = client.chat.completions.create(model="gpt-4o-mini", messages=[{"role": "user", "content": prompt + chunk}])
        return res.choices[0].message.content
    except:
        return chunk[:max_tokens]

def get_llm_client(model_selection, specific_model_name, openai_key, xai_key):
    """
    Returns client and model string. Updated for current xAI models (Nov 2025).
    """
    if model_selection == "Model A (OpenAI)":
        if not openai_key: return None, None, "Missing OpenAI API Key"
        return OpenAI(api_key=openai_key), "gpt-4o-mini", None
    
    elif model_selection == "Model B (xAI Grok)":
        if not xai_key: return None, None, "Missing xAI API Key"
        # Updated to real models: Prioritize Grok-4.1 for advanced reasoning
        model_map = {
            "Grok 4.1 Fast (Recommended)": "grok-4-1-fast-reasoning",
            "Grok 4 Full": "grok-4",
            "Grok 4 Fast": "grok-4-fast-reasoning",
            "Grok Code Fast": "grok-code-fast-1"
        }
        actual_model = model_map.get(specific_model_name, "grok-4-1-fast-reasoning")
        return OpenAI(api_key=xai_key, base_url="https://api.x.ai/v1"), actual_model, None
        
    return None, None, "Invalid Selection"

def download_file_with_headers(url, save_path):
    try:
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
        response = requests.get(url, headers=headers, stream=True, timeout=15)
        if response.status_code == 200:
            with open(save_path, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    f.write(chunk)
            return True
        return False
    except: return False

def scrape_website(url):
    try:
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
        response = requests.get(url, headers=headers, timeout=10)
        soup = BeautifulSoup(response.content, 'html.parser')
        for script in soup(["script", "style", "header", "footer", "nav"]):
            script.decompose()
        return soup.get_text()
    except: return None

def extract_text_from_files(files, audio_client=None):
    text = ""
    for file in files:
        try:
            name = file.name.lower()
            if name.endswith(".pdf"):
                reader = PyPDF2.PdfReader(io.BytesIO(file.getvalue()))
                for page in reader.pages: text += page.extract_text() + "\n"
            elif name.endswith(".docx"):
                doc = docx.Document(io.BytesIO(file.getvalue()))
                for para in doc.paragraphs: text += para.text + "\n"
            elif name.endswith(".pptx"):
                prs = Presentation(io.BytesIO(file.getvalue()))
                for slide in prs.slides:
                    for shape in slide.shapes:
                        if hasattr(shape, "text"): text += shape.text + "\n"
            elif name.endswith(".txt"):
                text += file.getvalue().decode("utf-8") + "\n"
            elif name.endswith((".mp3", ".mp4", ".wav", ".m4a", ".mpeg", ".webm")):
                if audio_client:
                    with st.spinner(f"Transcribing {name}..."):
                        transcript = audio_client.audio.transcriptions.create(model="whisper-1", file=(name, file.getvalue()))
                        text += transcript.text + "\n"
                else:
                    st.warning(f"Skipped {file.name}: OpenAI Key required for Audio transcription.")
            else:
                st.warning(f"Skipped {file.name}: Unsupported format.")
        except Exception as e:
            st.error(f"Error reading {file.name}: {e}")
    return text

async def download_and_transcribe_video(url, audio_client):
    """Async version for non-blocking UI."""
    loop = asyncio.get_event_loop()
    try:
        with tempfile.TemporaryDirectory() as tmp_dir:
            ydl_opts = {
                'format': 'bestaudio/best',
                'outtmpl': os.path.join(tmp_dir, 'audio.%(ext)s'),
                'postprocessors': [{'key': 'FFmpegExtractAudio','preferredcodec': 'mp3','preferredquality': '128'}],
                'quiet': True, 'no_warnings': True, 'nocheckcertificate': True,
                'http_headers': {'User-Agent': 'Mozilla/5.0'}
            }
            await loop.run_in_executor(None, lambda: yt_dlp.YoutubeDL(ydl_opts).download([url]))
            audio_path = os.path.join(tmp_dir, "audio.mp3")
            if not os.path.exists(audio_path): return None, "Download failed."
            if os.path.getsize(audio_path) / (1024*1024) > 24: return None, "Video too long (>25MB)."
            with open(audio_path, "rb") as f:
                transcript = audio_client.audio.transcriptions.create(model="whisper-1", file=f)
            return transcript.text, None
    except Exception as e: return None, str(e)

def generate_audio_openai(client, text, voice, filename, speed=1.0):
    try:
        response = client.audio.speech.create(model="tts-1", voice=voice, input=text, speed=speed)
        response.stream_to_file(filename)
        return True
    except: return False

def create_phone_effect(seg):
    """Better phone filter: low-pass + echo."""
    seg = low_pass_filter(seg, 3000)
    # Simple echo: overlay delayed version
    echo = seg - 10  # Quieter
    echo = echo + AudioSegment.silent(duration=100)  # Delay
    return seg.overlay(echo[:len(seg)])

# ================= SIDEBAR =================

with st.sidebar:
    st.title("🎛️ Studio Settings")
    
    # KEYS (hide text_input if secrets set)
    if "OPENAI_API_KEY" not in st.secrets:
        openai_key = st.text_input("OpenAI API Key", type="password")
    else:
        openai_key = st.secrets["OPENAI_API_KEY"]
    if "XAI_API_KEY" not in st.secrets:
        xai_key = st.text_input("xAI API Key (Optional)", type="password")
    else:
        xai_key = st.secrets["XAI_API_KEY"]
    
    # MODEL SELECTOR
    model_choice = st.radio("Intelligence Engine", ["Model A (OpenAI)", "Model B (xAI Grok)"])
    
    # DYNAMIC SUB-SELECTOR FOR XAI (updated models)
    xai_version = "Grok 4.1 Fast (Recommended)"
    if model_choice == "Model B (xAI Grok)":
        xai_version = st.selectbox(
            "Grok Model", 
            ["Grok 4.1 Fast (Recommended)", "Grok 4 Full", "Grok 4 Fast", "Grok Code Fast"],
            index=0,
            help="Grok-4.1+ for advanced reasoning; check https://x.ai/api for access."
        )
    
    privacy_mode = st.toggle("🛡️ Privacy Mode", value=False)
    
    if st.button("🗑️ New Session (Clear All)"):
        for key in ["chat_history", "notebook_content", "source_text", "script_data", "rehearsal_audio"]:
            st.session_state[key] = "" if key in ["source_text", "notebook_content"] else []
        st.session_state.notebook_content = f"# 📓 Research Notebook\n**Session Started:** {datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n"
        st.rerun()
        
    st.divider()
    
    st.subheader("🌍 Localization")
    language = st.selectbox("Output Language", [
        "English (US)", "English (UK)", "Spanish (Spain)", "Spanish (LatAm)", 
        "French", "German", "Italian", "Portuguese", "Portuguese (Brazil)",
        "Japanese", "Chinese (Mandarin)", "Korean", "Hindi", "Urdu", "Arabic", "Russian",
        "Turkish", "Dutch", "Polish", "Swedish", "Danish", "Norwegian", "Finnish",
        "Greek", "Czech", "Romanian", "Indonesian", "Vietnamese", "Thai", "Hebrew"
    ])
    
    length_option = st.select_slider("Duration", ["Short (2 min)", "Medium (5 min)", "Long (15 min)", "Extra Long (30 min)"])

    st.subheader("🎭 Hosts")
    host1_persona = st.text_input("Host 1 Persona", "Male, curious, slightly skeptical")
    host2_persona = st.text_input("Host 2 Persona", "Female, enthusiastic expert, fast talker")
    
    voice_style = st.selectbox("Voice Pair", ["Dynamic (Alloy & Nova)", "Calm (Onyx & Shimmer)", "Formal (Echo & Fable)"])
    voice_map = {"Dynamic (Alloy & Nova)": ("alloy", "nova"), "Calm (Onyx & Shimmer)": ("onyx", "shimmer"), "Formal (Echo & Fable)": ("echo", "fable")}

    st.divider()
    st.subheader("🎵 Music & Branding")
    
    bg_source = st.radio("Background Music", ["Presets", "Upload Custom", "None"], horizontal=True)
    
    music_ramp_up = st.checkbox("🎵 Start Music 5s Before Dialogue", value=False, help="Creates a 'Cold Open' effect using the background music.")

    selected_bg_url = None
    uploaded_bg_file = None
    if bg_source == "Presets":
        music_choice = st.selectbox("Track", ["Lo-Fi (Study)", "Upbeat (Morning)", "Ambient (News)", "Cinematic (Deep)"])
        music_urls = {
            "Lo-Fi (Study)": "https://cdn.pixabay.com/download/audio/2022/05/27/audio_1808fbf07a.mp3?filename=lofi-study-112191.mp3",
            "Upbeat (Morning)": "https://cdn.pixabay.com/download/audio/2024/05/24/audio_95e3f5f471.mp3?filename=good-morning-206098.mp3",
            "Ambient (News)": "https://cdn.pixabay.com/download/audio/2022/03/10/audio_c8c8a73467.mp3?filename=ambient-piano-10226.mp3",
            "Cinematic (Deep)": "https://cdn.pixabay.com/download/audio/2022/03/22/audio_c2b86c77ce.mp3?filename=cinematic-atmosphere-score-2-21266.mp3"
        }
        selected_bg_url = music_urls[music_choice]
    elif bg_source == "Upload Custom":
        uploaded_bg_file = st.file_uploader("Upload Loop (MP3/WAV)", type=["mp3", "wav"])

    with st.expander("Intro/Outro Clips"):
        uploaded_intro = st.file_uploader("Intro (Plays Once)", type=["mp3", "wav"])
        uploaded_outro = st.file_uploader("Outro (Plays Once)", type=["mp3", "wav"])

# ================= MAIN APP =================
st.title("🎧 PodcastLM Studio")

tab1, tab2, tab3, tab4 = st.tabs(["1. Source Material", "2. 🤖 AI Research Assistant", "3. Script Editor", "4. Audio Production"])

# --- TAB 1: INPUT ---
with tab1:
    st.info("Upload content here. This drives both the **Podcast** and the **Chatbot**.")
    input_type = st.radio("Input Type", ["📂 Files", "🔗 Web URL", "📺 Video URL", "📝 Text"], horizontal=True)
    new_text = ""
    
    # Audio client is ALWAYS OpenAI (xAI has no audio support yet)
    audio_client = OpenAI(api_key=openai_key) if openai_key else None

    if input_type == "📂 Files":
        files = st.file_uploader("Upload", accept_multiple_files=True)
        if files and st.button("Process Files"):
            with st.spinner("Processing uploaded files..."):
                new_text = extract_text_from_files(files, audio_client)
            
    elif input_type == "🔗 Web URL":
        url = st.text_input("Enter Article URL")
        if url and st.button("Scrape Website"): 
            with st.spinner("Scraping..."):
                scraped = scrape_website(url)
                if scraped: new_text = scraped
                else: st.error("Blocked by website.")
                
    elif input_type == "📺 Video URL":
        vid_url = st.text_input("Enter Video URL")
        if vid_url and st.button("Transcribe"):
            if audio_client:
                with st.spinner("Downloading and Transcribing Video..."):
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    text, err = loop.run_until_complete(download_and_transcribe_video(vid_url, audio_client))
                    if text: new_text = text
                    else: st.error(err)
            else: st.error("OpenAI API Key Required for Transcription (even if using Model B).")
    
    elif input_type == "📝 Text":
        new_text = st.text_area("Paste Text", height=300)

    # Update State
    if new_text and new_text != st.session_state.source_text:
        st.session_state.source_text = new_text
        st.session_state.chat_history = [] 
        timestamp = datetime.now().strftime("%H:%M:%S")
        st.session_state.notebook_content += f"\n---\n### 📥 New Source Loaded ({timestamp})\n*Source Type: {input_type}*\n\n"
        st.success("✅ Source text loaded!")

    if st.session_state.source_text:
        with st.expander("View Source Text"):
            st.text_area("Content", st.session_state.source_text, height=150, disabled=True)

# --- TAB 2: CHAT & NOTEBOOK ---
with tab2:
    col_chat, col_notes = st.columns([1, 1])
    
    with col_chat:
        st.subheader("💬 Active Chat")
        if not st.session_state.source_text:
            st.warning("Load source text first.")
        else:
            for message in st.session_state.chat_history:
                with st.chat_message(message["role"]): st.markdown(message["content"])
            
            if prompt := st.chat_input("Ask a question..."):
                # Get LLM Client (OpenAI or xAI)
                llm_client, llm_model, err = get_llm_client(model_choice, xai_version, openai_key, xai_key)
                
                if err:
                    st.error(err)
                else:
                    st.session_state.chat_history.append({"role": "user", "content": prompt})
                    st.session_state.notebook_content += f"**Q:** {prompt}\n\n"
                    with st.chat_message("user"): st.markdown(prompt)
                    
                    with st.chat_message("assistant"):
                        chunks = chunk_text(st.session_state.source_text)
                        full_context = ""
                        for chunk in chunks:
                            summarized = summarize_chunk(llm_client, chunk) if len(chunk) > 25000 else chunk
                            full_context += summarized + "\n"
                        stream = llm_client.chat.completions.create(
                            model=llm_model,
                            messages=[
                                {"role": "system", "content": "Answer based ONLY on source text."},
                                {"role": "user", "content": f"Source: {full_context}"},
                                {"role": "user", "content": prompt}
                            ], stream=True)
                        response = st.write_stream(stream)
                    
                    st.session_state.chat_history.append({"role": "assistant", "content": response})
                    st.session_state.notebook_content += f"**A:** {response}\n\n"
                    st.rerun()

    with col_notes:
        st.subheader("📓 Research Notebook")
        st.caption("Auto-saves Q&A. Editable. Preview below.")
        updated_notebook = st.text_area("Notebook Content", value=st.session_state.notebook_content, height=400, key="notebook_area")
        if updated_notebook != st.session_state.notebook_content: 
            st.session_state.notebook_content = updated_notebook
        st.markdown(st.session_state.notebook_content)  # Render preview
        st.download_button("💾 Save Notebook (.md)", st.session_state.notebook_content, f"notebook_{datetime.now().strftime('%Y%m%d_%H%M')}.md")

# --- TAB 3: SCRIPT & INTERACTIVE REHEARSAL ---
with tab3:
    st.markdown("### 🎬 Director Mode")
    col_dir, col_call = st.columns([1, 1])
    
    with col_dir:
        user_instructions = st.text_area("📢 Custom Instructions", placeholder="e.g., 'Focus on financials', 'Make it funny'")
        if st.session_state.script_data:
            word_count = sum(len(line['text'].split()) for line in st.session_state.script_data['dialogue'])
            st.metric("Script Word Count", word_count)
    with col_call:
        st.markdown("#### 📞 Call-in Segment")
        caller_prompt = st.text_area("Listener Question", placeholder="Type a question for a 'Caller' to ask...")

    if st.button("Generate Podcast Script", type="primary"):
        if not st.session_state.source_text: st.error("No source text loaded.")
        else:
            # Get LLM Client (OpenAI or xAI)
            llm_client, llm_model, err = get_llm_client(model_choice, xai_version, openai_key, xai_key)
            
            if err:
                st.error(err)
            else:
                try:
                    length_instr = "12-15 exchanges"
                    if "Medium" in length_option: length_instr = "30 exchanges. Deep dive."
                    elif "Long" in length_option: length_instr = "50 exchanges. Very detailed."
                    elif "Extra Long" in length_option: length_instr = "80 exchanges. Comprehensive."

                    call_in_instr = ""
                    if caller_prompt:
                        call_in_instr = f"MANDATORY: Include a 'Caller' speaker who asks: '{caller_prompt}'. Hosts must discuss this."

                    # Translate instructions if non-English
                    lang_code = {"English (US)": "en", "Spanish (Spain)": "es"}.get(language, "en")  # Expand as needed
                    trans_prompt = f"Translate to {lang_code}: " if lang_code != "en" else ""

                    chunks = chunk_text(st.session_state.source_text)
                    full_source = ""
                    for chunk in chunks:
                        full_source += chunk + "\n"  # Use chunking directly

                    prompt = f"{trans_prompt}\nCreate a podcast script.\nLanguage: {language}\nLength: {length_instr}\nHost 1: {host1_persona}\nHost 2: {host2_persona}\nDIRECTOR NOTES: {user_instructions}\n{call_in_instr}\nFormat: JSON {{ \"title\": \"...\", \"dialogue\": [ {{\"speaker\": \"Host 1\", \"text\": \"...\"}}, {{\"speaker\": \"Caller\", \"text\": \"...\"}} ] }}\nText: {full_source}"
                    
                    with st.spinner(f"Drafting Script using {llm_model}..."):
                        res = llm_client.chat.completions.create(
                            model=llm_model,
                            messages=[{"role": "user", "content": prompt}],
                            response_format={"type": "json_object"}
                        )
                        st.session_state.script_data = json.loads(res.choices[0].message.content)
                        st.success("Ready!")
                        if privacy_mode: st.session_state.source_text = ""
                except Exception as e: st.error(f"Error: {e}")

    # Interactive Script Editor & Rehearsal
    if st.session_state.script_data:
        data = st.session_state.script_data
        st.subheader(data.get('title', 'Podcast'))
        
        # Editable Form
        with st.form("edit"):
            new_d = []
            for i, l in enumerate(data['dialogue']):
                c1, c2 = st.columns([1, 5])
                roles = ["Host 1", "Host 2", "Caller"] if any(s == "Caller" for s in [d['speaker'] for d in data['dialogue']]) else ["Host 1", "Host 2"]
                idx = roles.index(l['speaker']) if l['speaker'] in roles else 0
                spk = c1.selectbox("Role", roles, index=idx, key=f"s{i}")
                txt = c2.text_area("Line", l['text'], height=70, key=f"t{i}")
                new_d.append({"speaker": spk, "text": txt})
            if st.form_submit_button("Save Edits"):
                st.session_state.script_data['dialogue'] = new_d
                st.success("Edits Saved!")

        # New: Interactive Rehearsal
        st.subheader("🎤 Live Rehearsal Mode")
        st.info("Preview audio segments interactively. Edit lines and regenerate on-the-fly.")
        rehearsal_col1, rehearsal_col2 = st.columns([2, 1])
        
        with rehearsal_col1:
            segment_idx = st.selectbox("Select Segment to Preview", range(len(data['dialogue'])), format_func=lambda i: f"{data['dialogue'][i]['speaker']}: {data['dialogue'][i]['text'][:50]}...")
            if st.button("🔊 Generate Preview Audio"):
                if openai_key:
                    with tempfile.TemporaryDirectory() as tmp:
                        audio_client = OpenAI(api_key=openai_key)
                        line = data['dialogue'][segment_idx]
                        voice = voice_map[voice_style][0 if line['speaker'] == "Host 1" else 1]
                        if line['speaker'] == "Caller": voice = "fable"
                        f_path = Path(tmp) / "preview.mp3"
                        if generate_audio_openai(audio_client, line['text'], voice, str(f_path)):
                            with open(f_path, "rb") as f:
                                preview_bytes = f.read()
                            st.session_state.rehearsal_audio = preview_bytes
                            st.audio(preview_bytes, format="audio/mp3")
                        else:
                            st.error("Audio generation failed.")
                else:
                    st.error("OpenAI Key required for audio preview.")
        
        with rehearsal_col2:
            st.markdown("### 💡 AI Edit Suggestions")
            edit_prompt = st.text_input("Suggest edit for segment (e.g., 'Make funnier')")
            if edit_prompt and st.button("Apply Suggestion"):
                llm_client, llm_model, err = get_llm_client(model_choice, xai_version, openai_key, xai_key)
                if not err:
                    orig_line = data['dialogue'][segment_idx]['text']
                    sug_prompt = f"Rewrite this line based on: {edit_prompt}. Original: {orig_line}. Keep style: {host1_persona if segment_idx % 2 == 0 else host2_persona}."
                    res = llm_client.chat.completions.create(model=llm_model, messages=[{"role": "user", "content": sug_prompt}])
                    new_text = res.choices[0].message.content
                    data['dialogue'][segment_idx]['text'] = new_text
                    st.success("Updated! Preview again.")
                    st.rerun()

# --- TAB 4: AUDIO ---
with tab4:
    if st.session_state.script_data and st.button("🎙️ Start Production", type="primary"):
        # Audio generation ALWAYS uses OpenAI
        if not openai_key: 
            st.error("OpenAI API Key is REQUIRED for Audio Generation (even if you used Model B for the script).")
            st.stop()
        
        progress = st.progress(0)
        status = st.empty()
        # Force OpenAI for Audio
        audio_client = OpenAI(api_key=openai_key)
        m_voice, f_voice = voice_map[voice_style]
        
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            segs = []
            script = st.session_state.script_data['dialogue']
            
            for i, line in enumerate(script):
                status.text(f"Recording {i+1}/{len(script)}...")
                voice = m_voice if line['speaker'] == "Host 1" else f_voice
                if line['speaker'] == "Caller": voice = "fable"
                
                f_path = tmp_path / f"line_{i}.mp3"
                if generate_audio_openai(audio_client, line['text'], voice, str(f_path)):
                    seg = AudioSegment.from_file(f_path)
                    if line['speaker'] == "Caller":
                        seg = create_phone_effect(seg)
                    seg = normalize(seg)  # Add normalization
                    segs.append(seg)
                    if i < len(script) - 1:  # Crossfade between lines
                        next_seg = AudioSegment.from_file(tmp_path / f"line_{i+1}.mp3")
                        seg = seg.append(next_seg, crossfade=500)  # 500ms crossfade
                        segs[-1] = seg  # Update last
                progress.progress((i+1)/len(script))
            
            if segs:
                status.text("Mixing...")
                final = sum(segs[:-1]) + segs[-1] if len(segs) > 1 else segs[0]  # Avoid double-append from crossfade
                
                bg_seg = None
                try:
                    if bg_source == "Presets" and selected_bg_url:
                        if download_file_with_headers(selected_bg_url, tmp_path/"bg.mp3"): bg_seg = AudioSegment.from_file(tmp_path/"bg.mp3")
                    elif bg_source == "Upload Custom" and uploaded_bg_file:
                        with open(tmp_path/"bg.mp3", "wb") as f: f.write(uploaded_bg_file.getvalue())
                        bg_seg = AudioSegment.from_file(tmp_path/"bg.mp3")
                    
                    if bg_seg:
                        bg_seg = bg_seg - 22  # Lower volume
                        if music_ramp_up: final = AudioSegment.silent(duration=5000) + final
                        while len(bg_seg) < len(final) + 5000: bg_seg += bg_seg
                        bg_seg = bg_seg[:len(final)+2000].fade_out(3000)
                        final = bg_seg.overlay(final)
                except: pass

                try:
                    if uploaded_intro:
                        intro_seg = AudioSegment.from_file(io.BytesIO(uploaded_intro.getvalue()))
                        final = intro_seg.fade_out(1000) + final  # Fade intro
                    if uploaded_outro:
                        outro_seg = AudioSegment.from_file(io.BytesIO(uploaded_outro.getvalue()))
                        final = final.fade_out(1000) + outro_seg  # Fade outro
                except: pass

                final = normalize(final)  # Final normalization
                final.export(tmp_path/"master.mp3", format="mp3", bitrate="192k")
                with open(tmp_path/"master.mp3", "rb") as f: ab = f.read()
                
                status.success("Done!")
                st.audio(ab, format="audio/mp3")
                st.download_button("Download MP3", ab, "podcast.mp3", "audio/mp3")