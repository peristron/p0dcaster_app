# streamlit run p0dcaster_app.py
import streamlit as st
import os
import tempfile
import json
import requests
import io
import shutil
from pathlib import Path
from datetime import datetime, timedelta
import asyncio
import time
import traceback
from typing import Optional, Dict, List, Tuple
import logging
from dataclasses import dataclass

import PyPDF2
import docx
from pptx import Presentation
from bs4 import BeautifulSoup
import yt_dlp
import ffmpeg
from openai import OpenAI
import streamlit.components.v1 as components

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Page configuration
st.set_page_config(
    page_title="PodcastLM Studio Pro",
    page_icon="🎙️",
    layout="wide",
    initial_sidebar_state="expanded",
    menu_items={
        'Get Help': 'https://github.com/your-repo',
        'Report a bug': 'https://github.com/your-repo/issues',
        'About': "PodcastLM Studio Pro v2.0 - AI Podcast Production"
    }
)

# ==================== DATACLASSES & CONSTANTS ====================

@dataclass
class ScriptLine:
    """Dataclass for script line with validation"""
    speaker: str
    text: str
    duration: float = 0.0  # Estimated duration in seconds
    
    @property
    def word_count(self) -> int:
        return len(self.text.split())

@dataclass
class ModelConfig:
    """Centralized model configuration"""
    name: str
    provider: str
    base_url: Optional[str] = None
    cost_per_1k_tokens: float = 0.0
    
# Model configurations
MODEL_CONFIGS = {
    "gpt-4o-mini": ModelConfig("gpt-4o-mini", "OpenAI", cost_per_1k_tokens=0.15),
    "gpt-4o": ModelConfig("gpt-4o", "OpenAI", cost_per_1k_tokens=2.5),
    "grok-4-1-fast-reasoning": ModelConfig("grok-4-1-fast-reasoning", "xAI", 
                                          base_url="https://api.x.ai/v1", cost_per_1k_tokens=2.0),
    "grok-4": ModelConfig("grok-4", "xAI", base_url="https://api.x.ai/v1", cost_per_1k_tokens=30.0),
}

# Voice configurations
VOICE_PAIRS = {
    "Dynamic (Alloy & Nova)": {"male": "alloy", "female": "nova", "caller": "fable"},
    "Calm (Onyx & Shimmer)": {"male": "onyx", "female": "shimmer", "caller": "echo"},
    "Formal (Echo & Fable)": {"male": "echo", "female": "fable", "caller": "shimmer"},
    "Expressive (Nova & Echo)": {"male": "nova", "female": "echo", "caller": "onyx"},
}

# Duration targets with word counts and estimated segments
DURATION_TARGETS = {
    "Short (2 min)": {"words": 300, "segments": 6, "description": "Quick summary"},
    "Medium (5 min)": {"words": 800, "segments": 15, "description": "Detailed discussion"},
    "Long (15 min)": {"words": 2200, "segments": 40, "description": "In-depth analysis"},
    "Extra Long (30 min)": {"words": 4500, "segments": 80, "description": "Comprehensive coverage"},
}

# ==================== SESSION STATE MANAGEMENT ====================

class SessionStateManager:
    """Centralized session state management"""
    
    @staticmethod
    def init_defaults():
        defaults = {
            "authenticated": False,
            "script_data": None,
            "source_text": "",
            "source_metadata": {},  # Store file names, URLs, etc.
            "chat_history": [],
            "notebook_content": f"# Research Notebook\n**Session Started:** {datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n",
            "rehearsal_audio": None,
            "processing_progress": 0,
            "last_generation_time": None,
            "cost_estimates": {"tts": 0.0, "llm": 0.0, "total": 0.0},
            "audio_cache": {},  # Cache for generated audio segments
            "selected_language": "English (US)",
            "user_preferences": {"auto_save": True, "notify_complete": True},
        }
        
        for key, value in defaults.items():
            if key not in st.session_state:
                st.session_state[key] = value
    
    @staticmethod
    def clear_cache():
        """Clear cached items"""
        st.session_state.audio_cache = {}
    
    @staticmethod
    def reset_session():
        """Reset session to defaults"""
        for key in list(st.session_state.keys()):
            del st.session_state[key]
        SessionStateManager.init_defaults()

# Initialize session state
SessionStateManager.init_defaults()

# ==================== AUTHENTICATION ====================

def authenticate(password: str) -> bool:
    """Enhanced authentication with rate limiting"""
    max_attempts = 3
    attempt_key = "auth_attempts"
    
    if attempt_key not in st.session_state:
        st.session_state[attempt_key] = {"count": 0, "last_attempt": None}
    
    attempts = st.session_state[attempt_key]
    
    # Rate limiting: 3 attempts per minute
    if attempts["last_attempt"] and (datetime.now() - attempts["last_attempt"]) < timedelta(minutes=1):
        if attempts["count"] >= max_attempts:
            st.error("Too many attempts. Please wait 1 minute.")
            return False
    
    attempts["last_attempt"] = datetime.now()
    attempts["count"] += 1
    
    if password == st.secrets.get("APP_PASSWORD", ""):
        st.session_state.authenticated = True
        attempts["count"] = 0  # Reset on success
        return True
    
    remaining = max(0, max_attempts - attempts["count"])
    st.error(f"Invalid password. {remaining} attempts remaining.")
    return False

# ==================== LLM CLIENT MANAGEMENT ====================

class LLMClientManager:
    """Manage LLM clients with caching and fallback"""
    
    _clients = {}
    
    @classmethod
    def get_client(cls, provider: str, api_key: str, base_url: Optional[str] = None) -> Optional[OpenAI]:
        """Get or create cached client"""
        cache_key = f"{provider}_{api_key[:10]}"
        
        if cache_key not in cls._clients:
            try:
                if provider == "OpenAI":
                    cls._clients[cache_key] = OpenAI(api_key=api_key)
                elif provider == "xAI" and base_url:
                    cls._clients[cache_key] = OpenAI(api_key=api_key, base_url=base_url)
                else:
                    return None
            except Exception as e:
                logger.error(f"Failed to create client for {provider}: {e}")
                return None
        
        return cls._clients[cache_key]
    
    @classmethod
    def validate_api_key(cls, provider: str, api_key: str) -> Tuple[bool, str]:
        """Validate API key by making a simple request"""
        if not api_key:
            return False, "API key is empty"
        
        try:
            client = cls.get_client(provider, api_key)
            if not client:
                return False, "Failed to create client"
            
            # Test with a minimal request
            if provider == "OpenAI":
                client.models.list(limit=1)
            elif provider == "xAI":
                client.models.list()
            
            return True, "Valid"
        except Exception as e:
            return False, f"Invalid API key: {str(e)}"

# ==================== FILE PROCESSING ====================

class FileProcessor:
    """Process various file types with better error handling"""
    
    @staticmethod
    def extract_text_from_pdf(file_bytes: bytes) -> str:
        """Extract text from PDF with improved parsing"""
        try:
            reader = PyPDF2.PdfReader(io.BytesIO(file_bytes))
            text = ""
            for page in reader.pages:
                page_text = page.extract_text()
                if page_text:
                    text += page_text + "\n"
            return text.strip()
        except Exception as e:
            logger.error(f"PDF extraction error: {e}")
            raise
    
    @staticmethod
    def extract_text_from_docx(file_bytes: bytes) -> str:
        """Extract text from DOCX"""
        try:
            doc = docx.Document(io.BytesIO(file_bytes))
            return "\n".join([para.text for para in doc.paragraphs if para.text.strip()])
        except Exception as e:
            logger.error(f"DOCX extraction error: {e}")
            raise
    
    @staticmethod
    def extract_text_from_pptx(file_bytes: bytes) -> str:
        """Extract text from PPTX"""
        try:
            prs = Presentation(io.BytesIO(file_bytes))
            text = []
            for slide in prs.slides:
                for shape in slide.shapes:
                    if hasattr(shape, "text") and shape.text.strip():
                        text.append(shape.text)
            return "\n".join(text)
        except Exception as e:
            logger.error(f"PPTX extraction error: {e}")
            raise
    
    @staticmethod
    def process_files(files, audio_client=None) -> Tuple[str, Dict]:
        """Process multiple files and return text with metadata"""
        all_text = []
        metadata = {
            "file_count": len(files),
            "processed_files": [],
            "failed_files": []
        }
        
        for file in files:
            try:
                file_name = file.name
                file_size = len(file.getvalue()) / 1024  # KB
                
                if file_name.lower().endswith(".pdf"):
                    text = FileProcessor.extract_text_from_pdf(file.getvalue())
                elif file_name.lower().endswith(".docx"):
                    text = FileProcessor.extract_text_from_docx(file.getvalue())
                elif file_name.lower().endswith(".pptx"):
                    text = FileProcessor.extract_text_from_pptx(file.getvalue())
                elif file_name.lower().endswith(".txt"):
                    text = file.getvalue().decode("utf-8", errors="ignore")
                elif file_name.lower().endswith((".mp3", ".wav", ".m4a", ".mp4", ".webm")):
                    if audio_client:
                        with st.spinner(f"Transcribing {file_name}..."):
                            transcript = audio_client.audio.transcriptions.create(
                                model="whisper-1", 
                                file=(file_name, file.getvalue())
                            )
                            text = transcript.text
                    else:
                        raise ValueError("OpenAI key required for audio transcription")
                else:
                    raise ValueError(f"Unsupported file type: {file_name}")
                
                if text.strip():
                    all_text.append(text)
                    metadata["processed_files"].append({
                        "name": file_name,
                        "size_kb": round(file_size, 2),
                        "type": file_name.split(".")[-1].upper(),
                        "word_count": len(text.split())
                    })
                else:
                    metadata["failed_files"].append({"name": file_name, "reason": "Empty content"})
                    
            except Exception as e:
                logger.error(f"Error processing {file.name}: {e}")
                metadata["failed_files"].append({"name": file.name, "reason": str(e)})
                continue
        
        return "\n\n".join(all_text), metadata

# ==================== WEB & VIDEO PROCESSING ====================

class ContentFetcher:
    """Fetch and process web/video content"""
    
    @staticmethod
    def scrape_website(url: str, timeout: int = 15) -> Optional[str]:
        """Scrape website content with improved parsing"""
        try:
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                'Accept-Language': 'en-US,en;q=0.5',
            }
            
            response = requests.get(url, headers=headers, timeout=timeout)
            response.raise_for_status()
            
            soup = BeautifulSoup(response.content, 'html.parser')
            
            # Remove unwanted elements
            for element in soup(["script", "style", "nav", "footer", "header", "aside", "form"]):
                element.decompose()
            
            # Get text from important tags
            text_parts = []
            for tag in soup.find_all(['h1', 'h2', 'h3', 'h4', 'p', 'li']):
                if tag.text.strip():
                    text_parts.append(tag.text.strip())
            
            return "\n\n".join(text_parts) if text_parts else soup.get_text(separator='\n', strip=True)
            
        except Exception as e:
            logger.error(f"Failed to scrape {url}: {e}")
            return None
    
    @staticmethod
    async def transcribe_video(url: str, audio_client) -> Tuple[Optional[str], Optional[str]]:
        """Transcribe video content with progress tracking"""
        try:
            loop = asyncio.get_event_loop()
            
            with tempfile.TemporaryDirectory() as tmp_dir:
                ydl_opts = {
                    'format': 'bestaudio/best',
                    'outtmpl': os.path.join(tmp_dir, 'audio.%(ext)s'),
                    'postprocessors': [{'key': 'FFmpegExtractAudio', 'preferredcodec': 'mp3'}],
                    'quiet': True,
                    'no_warnings': True,
                    'http_headers': {'User-Agent': 'Mozilla/5.0'},
                }
                
                # Download audio
                def download():
                    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                        ydl.download([url])
                
                await loop.run_in_executor(None, download)
                
                # Find audio file
                audio_path = next(Path(tmp_dir).glob("audio.*"), None)
                if not audio_path:
                    return None, "No audio file found"
                
                # Transcribe
                def transcribe():
                    with open(audio_path, "rb") as f:
                        return audio_client.audio.transcriptions.create(
                            model="whisper-1", 
                            file=f,
                            response_format="text"
                        )
                
                transcript = await loop.run_in_executor(None, transcribe)
                return transcript, None
                
        except Exception as e:
            logger.error(f"Video transcription error: {e}")
            return None, str(e)

# ==================== AUDIO PROCESSING ====================

class AudioProcessor:
    """Handle all audio processing tasks"""
    
    @staticmethod
    def create_phone_effect(input_path: str, output_path: str) -> bool:
        """Apply phone effect to audio with fallback"""
        try:
            # Try advanced filter chain
            stream = ffmpeg.input(input_path)
            stream = ffmpeg.filter(stream, 'lowpass', f=3200)
            stream = ffmpeg.filter(stream, 'highpass', f=300)
            stream = ffmpeg.filter(stream, 'bandpass', f=800)
            
            # Add subtle compression
            stream = ffmpeg.filter(stream, 'compand', 
                                 attacks='0.1', decays='0.3',
                                 points='-70/-70 -10/-5 0/0')
            
            # Add subtle reverb for phone effect
            stream = ffmpeg.filter(stream, 'aecho', 
                                 in_gain=0.8, out_gain=0.9,
                                 delays='50', decays='0.5')
            
            ffmpeg.output(stream, output_path, 
                         acodec='mp3', audio_bitrate='192k',
                         ar=44100, ac=2).run(
                overwrite_output=True, 
                quiet=True,
                capture_stderr=True
            )
            return True
            
        except Exception as e:
            logger.warning(f"Phone effect failed, using fallback: {e}")
            try:
                # Fallback: simple filter
                stream = ffmpeg.input(input_path)
                stream = ffmpeg.filter(stream, 'lowpass', f=3000)
                stream = ffmpeg.filter(stream, 'highpass', f=400)
                ffmpeg.output(stream, output_path, 
                             acodec='mp3', audio_bitrate='192k').run(
                    overwrite_output=True, 
                    quiet=True
                )
                return True
            except:
                # Last resort: copy file
                shutil.copy(input_path, output_path)
                return False
    
    @staticmethod
    def mix_audio_segments(segments: List[str], output_path: str) -> bool:
        """Mix multiple audio segments"""
        try:
            if len(segments) == 1:
                shutil.copy(segments[0], output_path)
                return True
            
            inputs = [ffmpeg.input(path) for path in segments]
            mixed = ffmpeg.concat(*inputs, v=0, a=1, n=len(segments))
            ffmpeg.output(mixed, output_path, 
                         acodec='mp3', audio_bitrate='192k').run(
                overwrite_output=True,
                quiet=True
            )
            return True
        except Exception as e:
            logger.error(f"Audio mixing failed: {e}")
            return False

# ==================== SCRIPT GENERATION ====================

class ScriptGenerator:
    """Generate podcast scripts with improved prompting"""
    
    @staticmethod
    def build_prompt(source_text: str, config: Dict) -> str:
        """Build comprehensive prompt for script generation"""
        
        word_target = DURATION_TARGETS[config["length"]]["words"]
        segments = DURATION_TARGETS[config["length"]]["segments"]
        
        prompt_template = f"""
        Create a natural, engaging podcast script in {config['language']}.
        
        CONTEXT:
        - Source material: {source_text[:30000]}...
        - Target length: {config['length']} ({word_target} words, ~{segments} dialogue segments)
        - Language style: {config['language']}
        
        HOST PERSONALITIES:
        - Host 1: {config['host1']}
        - Host 2: {config['host2']}
        
        {'CALLER SEGMENT:' + config['caller'] if config.get('caller') else 'No caller segment'}
        
        DIRECTOR NOTES:
        {config.get('director_notes', 'No specific notes')}
        
        FORMATTING REQUIREMENTS:
        1. Return valid JSON only
        2. Structure: {{"title": "Podcast Title", "dialogue": [{{"speaker": "Host 1", "text": "..."}}]}}
        3. Include natural pauses, reactions, and conversational flow
        4. Vary sentence length and structure
        5. Add humor and personality where appropriate
        6. Ensure balanced dialogue between hosts
        7. Include transitions between topics
        
        IMPORTANT: The total word count should be approximately {word_target} words.
        The dialogue should have around {segments} segments for natural pacing.
        
        Make this podcast engaging, informative, and entertaining!
        """
        
        return prompt_template
    
    @staticmethod
    def estimate_cost(script_data: Dict, model_config: ModelConfig) -> Dict[str, float]:
        """Estimate costs more accurately"""
        
        if not script_data or "dialogue" not in script_data:
            return {"tts": 0.0, "llm": 0.0, "total": 0.0}
        
        # TTS cost (OpenAI charges per character)
        total_chars = sum(len(line.get("text", "")) for line in script_data["dialogue"])
        tts_cost = (total_chars / 1_000_000) * 15.0  # $15 per 1M characters
        
        # LLM cost (estimate based on typical token usage)
        # Rough estimate: 1 token ≈ 4 characters for English
        estimated_tokens = total_chars / 4
        llm_cost = (estimated_tokens / 1000) * model_config.cost_per_1k_tokens
        
        return {
            "tts": round(tts_cost, 3),
            "llm": round(llm_cost, 2),
            "total": round(tts_cost + llm_cost, 2)
        }

# ==================== UI COMPONENTS ====================

def render_sidebar():
    """Render the sidebar with all controls"""
    with st.sidebar:
        st.title("🎙️ Studio Control")
        
        # API Keys
        st.subheader("API Configuration")
        openai_key = st.secrets.get("OPENAI_API_KEY") or st.text_input(
            "OpenAI API Key", 
            type="password",
            help="Required for audio generation and transcription"
        )
        
        xai_key = st.secrets.get("XAI_API_KEY") or st.text_input(
            "xAI API Key", 
            type="password",
            help="Optional for Grok models"
        )
        
        # Model Selection
        st.subheader("AI Engine")
        model_tab1, model_tab2 = st.tabs(["OpenAI", "xAI"])
        
        with model_tab1:
            openai_model = st.selectbox(
                "Model",
                ["gpt-4o-mini", "gpt-4o"],
                index=0,
                help="GPT-4o-mini is cost-effective, GPT-4o is more capable"
            )
            
        with model_tab2:
            if xai_key:
                xai_model = st.selectbox(
                    "Grok Model",
                    ["grok-4-1-fast-reasoning", "grok-4"],
                    index=0
                )
            else:
                st.info("Enter xAI key to use Grok models")
                xai_model = "grok-4-1-fast-reasoning"
        
        # Budget Mode
        budget_mode = st.checkbox(
            "💰 Budget Mode", 
            value=True,
            help="Use GPT-4o-mini regardless of selection"
        )
        
        # Privacy
        privacy_mode = st.toggle(
            "🔒 Privacy Mode", 
            value=False,
            help="Clear source text after processing"
        )
        
        st.divider()
        
        # Podcast Configuration
        st.subheader("Podcast Settings")
        
        language = st.selectbox(
            "Language",
            ["English (US)", "English (UK)", "Spanish", "French", "German", 
             "Italian", "Portuguese", "Hindi", "Arabic", "Japanese", "Chinese"],
            index=0
        )
        
        length = st.select_slider(
            "Duration",
            options=list(DURATION_TARGETS.keys()),
            value="Medium (5 min)"
        )
        
        # Show duration details
        duration_info = DURATION_TARGETS[length]
        st.caption(f"Target: {duration_info['words']} words, ~{duration_info['segments']} segments")
        
        # Host Configuration
        st.subheader("Host Personalities")
        with st.expander("Configure Hosts", expanded=True):
            host1 = st.text_input(
                "Host 1", 
                value="Male, curious, slightly skeptical, well-informed",
                help="Describe personality and style"
            )
            host2 = st.text_input(
                "Host 2", 
                value="Female, enthusiastic expert, articulate",
                help="Describe personality and style"
            )
        
        # Voice Selection
        voice_style = st.selectbox(
            "Voice Pair",
            list(VOICE_PAIRS.keys()),
            index=0
        )
        
        st.divider()
        
        # Audio Enhancements
        st.subheader("Audio Production")
        
        bg_source = st.radio(
            "Background Music",
            ["None", "Presets", "Upload Custom"],
            horizontal=True
        )
        
        if bg_source == "Presets":
            music_choice = st.selectbox(
                "Track",
                ["Lo-Fi (Study)", "Upbeat (Morning)", "Ambient (News)", "Cinematic (Deep)"]
            )
            music_urls = {
                "Lo-Fi (Study)": "https://cdn.pixabay.com/download/audio/2022/05/27/audio_1808fbf07a.mp3",
                "Upbeat (Morning)": "https://cdn.pixabay.com/download/audio/2024/05/24/audio_95e3f5f471.mp3",
                "Ambient (News)": "https://cdn.pixabay.com/download/audio/2022/03/10/audio_c8c8a73467.mp3",
                "Cinematic (Deep)": "https://cdn.pixabay.com/download/audio/2022/03/22/audio_c2b86c77ce.mp3"
            }
            selected_bg_url = music_urls[music_choice]
        else:
            selected_bg_url = None
        
        # Intro/Outro
        with st.expander("Intro/Outro Settings"):
            col1, col2 = st.columns(2)
            with col1:
                uploaded_intro = st.file_uploader(
                    "Intro Audio", 
                    type=["mp3", "wav"],
                    help="Optional opening clip"
                )
            with col2:
                uploaded_outro = st.file_uploader(
                    "Outro Audio", 
                    type=["mp3", "wav"],
                    help="Optional closing clip"
                )
        
        st.divider()
        
        # Session Management
        st.subheader("Session")
        
        col1, col2 = st.columns(2)
        with col1:
            if st.button("🔄 New Session", use_container_width=True):
                SessionStateManager.reset_session()
                st.rerun()
        
        with col2:
            if st.button("🧹 Clear Cache", use_container_width=True):
                SessionStateManager.clear_cache()
                st.success("Cache cleared!")
        
        # Cost Estimation
        if st.session_state.script_data:
            st.divider()
            st.subheader("Cost Estimate")
            
            costs = st.session_state.cost_estimates
            col1, col2, col3 = st.columns(3)
            with col1:
                st.metric("TTS", f"${costs['tts']:.3f}")
            with col2:
                st.metric("LLM", f"${costs['llm']:.2f}")
            with col3:
                st.metric("Total", f"${costs['total']:.2f}")
            
            st.caption("Based on OpenAI pricing")
    
    return {
        "openai_key": openai_key,
        "xai_key": xai_key,
        "openai_model": openai_model,
        "xai_model": xai_model,
        "budget_mode": budget_mode,
        "privacy_mode": privacy_mode,
        "language": language,
        "length": length,
        "host1": host1,
        "host2": host2,
        "voice_style": voice_style,
        "bg_source": bg_source,
        "selected_bg_url": selected_bg_url,
        "uploaded_intro": uploaded_intro,
        "uploaded_outro": uploaded_outro,
    }

# ==================== MAIN APP ====================

# Authentication check
if not st.session_state.authenticated:
    st.title("PodcastLM Studio Pro")
    st.markdown("---")
    
    col1, col2, col3 = st.columns([1, 2, 1])
    with col2:
        st.markdown("### 🔐 Studio Login")
        
        password = st.text_input(
            "Enter Password",
            type="password",
            key="password_input",
            label_visibility="collapsed",
            placeholder="Enter studio password..."
        )
        
        if st.button("Authenticate", type="primary", use_container_width=True):
            if authenticate(password):
                st.success("Authentication successful!")
                time.sleep(1)
                st.rerun()
    
    st.stop()

# Main app layout
st.title("PodcastLM Studio Pro")
st.markdown("---")

# Render sidebar and get config
config = render_sidebar()

# Create tabs
tab1, tab2, tab3, tab4 = st.tabs([
    "📁 Source Input", 
    "💬 Research Chat", 
    "📝 Script Studio", 
    "🎧 Produce Podcast"
])

# Initialize audio client
audio_client = OpenAI(api_key=config["openai_key"]) if config["openai_key"] else None

# === TAB 1: SOURCE INPUT ===
with tab1:
    st.header("Source Material")
    st.info("Upload or input content to drive podcast generation")
    
    input_method = st.radio(
        "Input Method",
        ["📄 Files", "🌐 Web URL", "🎥 Video URL", "📝 Direct Text"],
        horizontal=True
    )
    
    new_text = ""
    metadata = {}
    
    if input_method == "📄 Files":
        files = st.file_uploader(
            "Upload documents or audio files",
            type=["pdf", "docx", "pptx", "txt", "mp3", "wav", "m4a", "mp4"],
            accept_multiple_files=True,
            help="Supported: PDF, DOCX, PPTX, TXT, MP3, WAV, M4A, MP4"
        )
        
        if files and st.button("Process Files", type="primary"):
            with st.spinner("Processing files..."):
                try:
                    new_text, metadata = FileProcessor.process_files(files, audio_client)
                    
                    # Display processing results
                    if metadata["processed_files"]:
                        st.success(f"Processed {len(metadata['processed_files'])} files successfully!")
                        
                        with st.expander("📊 Processing Details"):
                            for file_info in metadata["processed_files"]:
                                st.write(f"✅ {file_info['name']} ({file_info['size_kb']}KB, {file_info['word_count']} words)")
                    
                    if metadata["failed_files"]:
                        st.warning(f"{len(metadata['failed_files'])} files failed to process")
                        
                        with st.expander("⚠️ Failed Files"):
                            for file_info in metadata["failed_files"]:
                                st.write(f"❌ {file_info['name']}: {file_info['reason']}")
                
                except Exception as e:
                    st.error(f"Processing failed: {str(e)}")
    
    elif input_method == "🌐 Web URL":
        url = st.text_input("Enter article URL", placeholder="https://example.com/article")
        if url and st.button("Scrape & Process", type="primary"):
            with st.spinner("Fetching content..."):
                new_text = ContentFetcher.scrape_website(url)
                if new_text:
                    st.success(f"Successfully scraped {len(new_text.split())} words")
                    metadata = {"source_type": "web", "url": url}
                else:
                    st.error("Failed to scrape website")
    
    elif input_method == "🎥 Video URL":
        video_url = st.text_input("Enter video URL", placeholder="https://youtube.com/watch?v=...")
        if video_url and st.button("Transcribe Video", type="primary"):
            if not audio_client:
                st.error("OpenAI API key required for video transcription")
            else:
                with st.spinner("Downloading and transcribing video..."):
                    try:
                        loop = asyncio.new_event_loop()
                        asyncio.set_event_loop(loop)
                        transcript, error = loop.run_until_complete(
                            ContentFetcher.transcribe_video(video_url, audio_client)
                        )
                        
                        if transcript:
                            new_text = transcript
                            st.success(f"Transcribed {len(transcript.split())} words")
                            metadata = {"source_type": "video", "url": video_url}
                        else:
                            st.error(f"Transcription failed: {error}")
                    except Exception as e:
                        st.error(f"Video processing error: {str(e)}")
    
    elif input_method == "📝 Direct Text":
        new_text = st.text_area(
            "Paste or type your content",
            height=300,
            placeholder="Enter your text here..."
        )
        if new_text:
            metadata = {"source_type": "direct_text"}
    
    # Update session state if we have new content
    if new_text and new_text != st.session_state.source_text:
        st.session_state.source_text = new_text
        st.session_state.source_metadata = metadata
        st.session_state.chat_history = []
        
        # Add to notebook
        timestamp = datetime.now().strftime("%H:%M")
        source_type = metadata.get("source_type", "unknown")
        st.session_state.notebook_content += f"\n---\n### New Source ({timestamp})\n"
        st.session_state.notebook_content += f"**Type:** {source_type}\n"
        st.session_state.notebook_content += f"**Preview:** {new_text[:500]}...\n\n"
        
        st.success("✅ Source loaded successfully!")
        
        # Show word count
        word_count = len(new_text.split())
        st.info(f"📊 **Word Count:** {word_count:,} words")
        
        # Estimate podcast duration
        reading_speed = 150  # words per minute
        estimated_minutes = word_count / reading_speed
        st.info(f"⏱️ **Estimated Podcast Duration:** ~{estimated_minutes:.1f} minutes")

# === TAB 2: RESEARCH CHAT ===
with tab2:
    st.header("Research Assistant")
    
    if not st.session_state.source_text:
        st.info("Load source material first to start chatting")
    else:
        col1, col2 = st.columns([3, 1])
        
        with col1:
            st.markdown("### 💬 Chat with your content")
            user_query = st.text_input(
                "Ask about the source material",
                placeholder="Summarize the key points...",
                label_visibility="collapsed"
            )
        
        with col2:
            st.markdown("### ")
            if st.button("Clear Chat", type="secondary"):
                st.session_state.chat_history = []
                st.rerun()
        
        if user_query:
            # Implement chat functionality here
            st.info("Chat functionality would be implemented here")
            # This would involve creating a chat completion with the source as context
        
        # Display chat history
        if st.session_state.chat_history:
            st.markdown("---")
            st.subheader("Chat History")
            for msg in st.session_state.chat_history[-10:]:  # Show last 10 messages
                with st.chat_message(msg["role"]):
                    st.write(msg["content"])

# === TAB 3: SCRIPT STUDIO ===
with tab3:
    st.header("Script Generation & Editing")
    
    if not st.session_state.source_text:
        st.warning("Please load source material in Tab 1 first")
        st.stop()
    
    # Configuration columns
    col_config, col_notes = st.columns([1, 1])
    
    with col_config:
        st.subheader("Script Configuration")
        
        # Caller segment
        include_caller = st.checkbox("Include Caller Segment", value=False)
        if include_caller:
            caller_prompt = st.text_area(
                "Caller Question",
                placeholder="What would the caller ask?",
                height=100
            )
        else:
            caller_prompt = ""
    
    with col_notes:
        st.subheader("Director's Notes")
        director_notes = st.text_area(
            "Additional instructions",
            placeholder="Make it humorous / Focus on technical details / Keep it casual...",
            height=150
        )
    
    # Generate button
    if st.button("🎬 Generate Script", type="primary", use_container_width=True):
        with st.spinner("Crafting your podcast script..."):
            try:
                # Determine which model to use
                if config["budget_mode"]:
                    model_name = "gpt-4o-mini"
                    provider = "OpenAI"
                    api_key = config["openai_key"]
                elif config["openai_model"].startswith("gpt-"):
                    model_name = config["openai_model"]
                    provider = "OpenAI"
                    api_key = config["openai_key"]
                else:
                    model_name = config["xai_model"]
                    provider = "xAI"
                    api_key = config["xai_key"]
                
                # Get model config
                model_config = MODEL_CONFIGS.get(model_name, MODEL_CONFIGS["gpt-4o-mini"])
                
                # Get client
                client = LLMClientManager.get_client(
                    provider, 
                    api_key, 
                    model_config.base_url
                )
                
                if not client:
                    st.error(f"Failed to initialize {provider} client")
                    st.stop()
                
                # Build prompt
                generation_config = {
                    "language": config["language"],
                    "length": config["length"],
                    "host1": config["host1"],
                    "host2": config["host2"],
                    "caller": caller_prompt if include_caller else "",
                    "director_notes": director_notes,
                }
                
                prompt = ScriptGenerator.build_prompt(
                    st.session_state.source_text, 
                    generation_config
                )
                
                # Generate script
                response = client.chat.completions.create(
                    model=model_name,
                    messages=[{"role": "user", "content": prompt}],
                    response_format={"type": "json_object"},
                    temperature=0.7,
                    max_tokens=4000
                )
                
                # Parse response
                script_data = json.loads(response.choices[0].message.content)
                
                # Validate structure
                if "dialogue" not in script_data:
                    raise ValueError("Invalid script format: missing 'dialogue' key")
                
                # Add metadata
                script_data["metadata"] = {
                    "generated_at": datetime.now().isoformat(),
                    "model": model_name,
                    "word_count": sum(len(line["text"].split()) for line in script_data["dialogue"]),
                    "segment_count": len(script_data["dialogue"]),
                }
                
                # Store in session
                st.session_state.script_data = script_data
                st.session_state.last_generation_time = datetime.now()
                
                # Calculate costs
                st.session_state.cost_estimates = ScriptGenerator.estimate_cost(
                    script_data, 
                    model_config
                )
                
                # Clear source if privacy mode
                if config["privacy_mode"]:
                    st.session_state.source_text = ""
                
                st.success("✅ Script generated successfully!")
                
                # Show statistics
                with st.expander("📊 Script Statistics", expanded=True):
                    col1, col2, col3 = st.columns(3)
                    with col1:
                        st.metric("Segments", script_data["metadata"]["segment_count"])
                    with col2:
                        st.metric("Words", script_data["metadata"]["word_count"])
                    with col3:
                        est_minutes = script_data["metadata"]["word_count"] / 150
                        st.metric("Est. Duration", f"{est_minutes:.1f} min")
            
            except Exception as e:
                st.error(f"Script generation failed: {str(e)}")
                logger.error(f"Generation error: {traceback.format_exc()}")
    
    # Display and edit script if available
    if st.session_state.script_data:
        st.markdown("---")
        
        script = st.session_state.script_data
        
        # Title
        st.subheader(script.get("title", "Untitled Podcast"))
        
        # Edit interface
        with st.expander("✏️ Edit Script", expanded=False):
            with st.form("script_editor"):
                edited_lines = []
                
                for i, line in enumerate(script["dialogue"]):
                    col_speaker, col_text = st.columns([1, 4])
                    
                    with col_speaker:
                        speaker_options = ["Host 1", "Host 2"]
                        if any(l.get("speaker") == "Caller" for l in script["dialogue"]):
                            speaker_options.append("Caller")
                        
                        speaker = st.selectbox(
                            "Speaker",
                            speaker_options,
                            index=speaker_options.index(line["speaker"]) if line["speaker"] in speaker_options else 0,
                            key=f"speaker_{i}",
                            label_visibility="collapsed"
                        )
                    
                    with col_text:
                        text = st.text_area(
                            "Line",
                            value=line["text"],
                            height=100,
                            key=f"text_{i}",
                            label_visibility="collapsed",
                            placeholder="Enter dialogue..."
                        )
                    
                    edited_lines.append({"speaker": speaker, "text": text})
                
                # Form actions
                col_save, col_auto, col_dl = st.columns(3)
                
                with col_save:
                    save_clicked = st.form_submit_button("💾 Save Changes", use_container_width=True)
                
                with col_auto:
                    auto_format = st.form_submit_button("✨ Auto-Format", use_container_width=True)
                
                with col_dl:
                    download_json = st.form_submit_button("📥 Download JSON", use_container_width=True)
                
                if save_clicked:
                    st.session_state.script_data["dialogue"] = edited_lines
                    st.success("Changes saved!")
                    st.rerun()
                
                if download_json:
                    json_str = json.dumps(st.session_state.script_data, indent=2)
                    st.download_button(
                        "Download Script",
                        json_str,
                        "podcast_script.json",
                        "application/json"
                    )
        
        # Live preview
        st.markdown("### 🎭 Script Preview")
        
        preview_container = st.container(height=400, border=True)
        with preview_container:
            for i, line in enumerate(script["dialogue"]):
                speaker_color = {
                    "Host 1": "#4A90E2",
                    "Host 2": "#E24A4A", 
                    "Caller": "#4AE2A0"
                }.get(line["speaker"], "#666666")
                
                st.markdown(f"""
                <div style="margin-bottom: 10px; padding: 10px; border-left: 4px solid {speaker_color}; background: #f8f9fa;">
                    <strong style="color: {speaker_color};">{line['speaker']}</strong><br>
                    {line['text']}
                </div>
                """, unsafe_allow_html=True)
        
        # Rehearsal mode
        st.markdown("### 🔊 Live Rehearsal")
        
        if not audio_client:
            st.warning("Add OpenAI API key to enable voice preview")
        else:
            col_line, col_voice = st.columns([3, 1])
            
            with col_line:
                line_options = [
                    f"{i+1}. {line['speaker']}: {line['text'][:80]}..." 
                    for i, line in enumerate(script["dialogue"])
                ]
                selected_idx = st.selectbox(
                    "Select line to preview",
                    range(len(script["dialogue"])),
                    format_func=lambda i: line_options[i]
                )
            
            with col_voice:
                selected_line = script["dialogue"][selected_idx]
                voice_map = VOICE_PAIRS[config["voice_style"]]
                
                if selected_line["speaker"] == "Host 1":
                    voice = voice_map["male"]
                elif selected_line["speaker"] == "Host 2":
                    voice = voice_map["female"]
                else:
                    voice = voice_map["caller"]
                
                speed = st.slider("Speed", 0.8, 1.5, 1.0, 0.1, key="preview_speed")
            
            if st.button("▶️ Preview Line", type="secondary"):
                with st.spinner("Generating audio..."):
                    try:
                        # Check cache first
                        cache_key = f"{selected_line['text'][:50]}_{voice}_{speed}"
                        
                        if cache_key in st.session_state.audio_cache:
                            audio_bytes = st.session_state.audio_cache[cache_key]
                            st.audio(audio_bytes, format="audio/mp3")
                        else:
                            # Generate new audio
                            with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
                                response = audio_client.audio.speech.create(
                                    model="tts-1",
                                    voice=voice,
                                    input=selected_line["text"],
                                    speed=speed
                                )
                                response.stream_to_file(tmp.name)
                                
                                # Read and cache
                                with open(tmp.name, "rb") as f:
                                    audio_bytes = f.read()
                                    st.session_state.audio_cache[cache_key] = audio_bytes
                                
                                st.audio(audio_bytes, format="audio/mp3")
                        
                        # Clean up temp file
                        os.unlink(tmp.name)
                        
                    except Exception as e:
                        st.error(f"Audio generation failed: {str(e)}")

# === TAB 4: PRODUCE PODCAST ===
with tab4:
    st.header("Produce Final Podcast")
    
    if not st.session_state.script_data:
        st.warning("Generate a script first in Tab 3")
        st.stop()
    
    if not audio_client:
        st.error("OpenAI API key required for production")
        st.stop()
    
    # Production settings
    with st.expander("⚙️ Production Settings", expanded=True):
        col1, col2, col3 = st.columns(3)
        
        with col1:
            normalize_audio = st.checkbox("Normalize Audio", value=True)
            add_fade = st.checkbox("Add Fade Out", value=True)
        
        with col2:
            phone_effect = st.checkbox("Phone Effect (Caller)", value=True)
            bg_volume = st.slider("BG Volume %", 0, 20, 12)
        
        with col3:
            sample_rate = st.selectbox("Sample Rate", ["44100", "48000", "22050"])
            bitrate = st.selectbox("Bitrate", ["192k", "256k", "320k", "128k"])
    
    # Cost warning
    costs = st.session_state.cost_estimates
    if costs["total"] > 1.0:
        st.warning(f"⚠️ Estimated cost: ${costs['total']:.2f}. Proceed with production?")
    
    # Production button
    if st.button("🚀 Produce Podcast", type="primary", use_container_width=True):
        script = st.session_state.script_data["dialogue"]
        
        # Setup progress tracking
        progress_bar = st.progress(0)
        status_text = st.empty()
        results_container = st.container()
        
        try:
            with tempfile.TemporaryDirectory() as tmp_dir:
                tmp = Path(tmp_dir)
                audio_segments = []
                
                # Step 1: Generate all audio segments
                status_text.text("Step 1/3: Generating audio segments...")
                total_segments = len(script)
                
                for i, line in enumerate(script):
                    progress = (i / total_segments) * 0.4  # 40% of progress
                    progress_bar.progress(progress)
                    
                    status_text.text(f"Generating line {i+1}/{total_segments}: {line['speaker']}")
                    
                    # Determine voice
                    voice_map = VOICE_PAIRS[config["voice_style"]]
                    if line["speaker"] == "Host 1":
                        voice = voice_map["male"]
                    elif line["speaker"] == "Host 2":
                        voice = voice_map["female"]
                    else:
                        voice = voice_map["caller"]
                    
                    # Generate audio
                    segment_path = tmp / f"segment_{i:03d}.mp3"
                    response = audio_client.audio.speech.create(
                        model="tts-1",
                        voice=voice,
                        input=line["text"],
                        speed=1.0
                    )
                    response.stream_to_file(str(segment_path))
                    
                    # Apply phone effect if needed
                    if phone_effect and line["speaker"] == "Caller":
                        phone_path = tmp / f"phone_{i:03d}.mp3"
                        AudioProcessor.create_phone_effect(str(segment_path), str(phone_path))
                        audio_segments.append(str(phone_path))
                    else:
                        audio_segments.append(str(segment_path))
                
                # Step 2: Mix segments
                status_text.text("Step 2/3: Mixing audio segments...")
                progress_bar.progress(0.6)
                
                mixed_path = tmp / "mixed.mp3"
                AudioProcessor.mix_audio_segments(audio_segments, str(mixed_path))
                
                # Step 3: Add background music and effects
                status_text.text("Step 3/3: Adding final touches...")
                progress_bar.progress(0.8)
                
                # This is where you'd add background music, intro/outro, etc.
                # For now, we'll just use the mixed audio
                final_path = tmp / "final_podcast.mp3"
                
                # Apply final processing if needed
                if normalize_audio:
                    # Simple normalization
                    stream = ffmpeg.input(str(mixed_path))
                    stream = ffmpeg.filter(stream, 'loudnorm', I=-16, TP=-1.5)
                    
                    if add_fade:
                        stream = ffmpeg.filter(stream, 'afade', t='out', d=5.0)
                    
                    ffmpeg.output(
                        stream, 
                        str(final_path),
                        acodec='mp3',
                        audio_bitrate=bitrate,
                        ar=sample_rate
                    ).run(overwrite_output=True, quiet=True)
                else:
                    shutil.copy(mixed_path, final_path)
                
                # Complete
                progress_bar.progress(1.0)
                status_text.text("✅ Production complete!")
                
                # Display results
                with results_container:
                    st.balloons()
                    
                    # Load and display audio
                    with open(final_path, "rb") as f:
                        audio_bytes = f.read()
                    
                    st.audio(audio_bytes, format="audio/mp3")
                    
                    # Download button
                    file_name = f"podcast_{datetime.now().strftime('%Y%m%d_%H%M')}.mp3"
                    st.download_button(
                        "📥 Download Podcast",
                        audio_bytes,
                        file_name,
                        "audio/mp3",
                        type="primary",
                        use_container_width=True
                    )
                    
                    # Production summary
                    with st.expander("📊 Production Summary"):
                        col1, col2 = st.columns(2)
                        
                        with col1:
                            st.metric("Total Segments", len(script))
                            st.metric("File Size", f"{len(audio_bytes) / 1024 / 1024:.2f} MB")
                        
                        with col2:
                            st.metric("Sample Rate", f"{sample_rate} Hz")
                            st.metric("Bitrate", bitrate)
                    
                    # Add to notebook
                    timestamp = datetime.now().strftime("%H:%M")
                    st.session_state.notebook_content += f"\n---\n### Podcast Produced ({timestamp})\n"
                    st.session_state.notebook_content += f"**File:** {file_name}\n"
                    st.session_state.notebook_content += f"**Segments:** {len(script)}\n"
                    st.session_state.notebook_content += f"**Cost:** ${costs['total']:.2f}\n\n"
        
        except Exception as e:
            st.error(f"Production failed: {str(e)}")
            logger.error(f"Production error: {traceback.format_exc()}")
            progress_bar.empty()
            status_text.error("Production failed!")

# === RESEARCH NOTEBOOK (BOTTOM OF PAGE) ===
st.markdown("---")
st.subheader("📓 Research Notebook")

notebook_col1, notebook_col2 = st.columns([4, 1])
with notebook_col1:
    notebook_content = st.text_area(
        "Session Notes",
        value=st.session_state.notebook_content,
        height=200,
        label_visibility="collapsed"
    )

with notebook_col2:
    st.markdown("### ")
    if st.button("Save Notes", use_container_width=True):
        st.session_state.notebook_content = notebook_content
        st.success("Notes saved!")
    
    if st.button("Clear Notes", type="secondary", use_container_width=True):
        st.session_state.notebook_content = f"# Research Notebook\n**Session Started:** {datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n"
        st.rerun()
    
    if st.button("Download Notes", use_container_width=True):
        st.download_button(
            "📥 Download",
            st.session_state.notebook_content,
            "research_notes.md",
            "text/markdown"
        )

# Footer
st.markdown("---")
st.caption("PodcastLM Studio Pro v2.0 | Powered by OpenAI & xAI | For internal use only")
