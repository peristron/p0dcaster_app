PodcastLM Studio
Turn any article, PDF, YouTube video, or text into a fully voiced, professional-sounding podcast in under 2 minutes — for less than $1.

Live demo: TBD

Features
Upload PDFs, DOCX, PPTX, web articles, YouTube videos, or just paste text  
AI research assistant that answers questions using only your source material  
Generate a natural, two-host podcast script in 20+ languages (including Urdu, Arabic, Hindi, Hebrew, etc.)  
Interactive rehearsal mode with per-line preview and AI-powered rewrites  
Full studio-quality audio production with background music, intro/outro, and phone-effect caller  
Live cost calculator (most podcasts cost $0.40–$1.10)  
Budget Mode toggle (90% cheaper using GPT-4o-mini)  
Zero installation — runs completely in-browser

Quick Start (Deploy in 60 seconds)
Option 1: One-click deploy (recommended)![Deploy to Streamlit](https://static.streamlit.io/badges/streamlit_badge_black_white.svg)
Option 2: Fork & deploy manuallyClick “Use this template” or fork this repo  
Go to https://share.streamlit.io  
New app → Connect your forked repo  
In Settings → Secrets, paste:

toml

# .streamlit/secrets.toml
APP_PASSWORD = "your-chosen-password"
OPENAI_API_KEY = "sk-..."
XAI_API_KEY = "xai-..."        # optional – only needed for Grok-4 models

Deploy!

That’s it. No servers, no ffmpeg, no headaches. Pricing (as of Nov 2025)Podcast Length
Intelligence Engine
Total Cost
5–8 minutes
GPT-4o-mini (Budget Mode)
~$0.35–$0.55
10–15 minutes
Grok-4.1-fast
~$0.70–$1.10
30 minutes
Grok-4.1-fast + extras
~$2.00–$2.50

Still cheaper than a coffee — and infinitely better than reading aloud yourself. 

Supported LanguagesEnglish (US/UK) • Spanish • French • German • Italian • Portuguese  
Hindi • Urdu • Arabic • Hebrew • Russian • Turkish • Polish • Dutch  
Swedish • Japanese • Korean • Chinese (Mandarin) • Indonesian • Thai

TTS and script generation work natively in all of these. 

Privacy

Your documents never leave the browser session  
“Privacy Mode” instantly wipes source text after script generation  
All API keys are stored securely in Streamlit Secrets (never in the repo)

Tech Stack
Streamlit – frontend & deployment  
OpenAI (GPT-4o-mini / TTS-1 / Whisper)  
xAI Grok-4.1 (optional, best reasoning)  
yt-dlp + pydub – YouTube & audio processing  
Pure Python – no Docker, no ffmpeg binary needed


open an issue or PR if you want to:
Add new background music presets  
Support ElevenLabs / Azure TTS voices  
Add export to video (with subtitles)  
Improve non-Latin script rendering

Credits
Built and maintained by...me
Inspired by the indie podcasting revolution of 2025.
Star this repo if it saved you hours of editing
https://github.com/your-username/podcastlm-studioMade with  and late-night AI-tinkering

