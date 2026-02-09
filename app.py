import os
import mimetypes
import random
import time
import json
from typing import Any, Dict, List, Optional

import streamlit as st
import google.genai as genai
from google.genai import types


MODEL_ID = "gemini-3-pro-preview"


def build_system_prompt() -> str:
    return (
        "You are 'The Scrutinizer', a seasoned forensic scam investigator and "
        "fraud analyst. Your job is to:\n"
        "- Deconstruct content (audio, video, links, or text) for deception, manipulation, "
        "and social engineering.\n"
        "- Identify red flags in tone, pacing, visuals, claims, credentials, payment flows, "
        "and technical signals.\n"
        "- Distinguish between honest mistakes and deliberate fraud.\n"
        "- Provide a clear numeric Deception Score from 0–100 where 0 is clean/benign and "
        "100 is highly likely to be a scam, fraud, or deepfake.\n"
        "- Produce a concise, chronological red‑flag timeline that a non‑technical person "
        "could understand.\n"
        "- Whenever there are financial promises, investments, or numeric claims, use code "
        "execution to compute realistic returns or probabilities (for example: required daily "
        "interest rate vs typical S&P 500 returns) and summarize that as a 'Math Reality Check'.\n"
        "- When explaining math, use clear plain English and standard numbers (for example: "
        "'A $1,000 investment growing to $3.3 billion in one year is economically impossible'). "
        "Avoid duplicated digits, broken words like '1,000investmentturninginto', weird spacing, "
        "or LaTeX-style formulas. Write one clean sentence instead. Prefer a short markdown table "
        "over complex notation.\n"
        "- When multiple media files, links, or text snippets are provided, ALWAYS consider "
        "and reference **all** of them in your analysis. Do not focus only on the first item; "
        "your deception score and summary must reflect the entire batch.\n"
        "Output format requirements:\n"
        "- Always respond as JSON only (no free‑form text outside JSON).\n"
        "- Always include an integer field 'deception_score' (0–100).\n"
        "- Always include an integer field 'scam_score' (0–100) that mirrors 'deception_score'.\n"
        "- When you perform numeric analysis, include a short markdown narrative in "
        "'math_reality_check_markdown' and, if helpful, a simple markdown table in "
        "'math_table_markdown'.\n"
        "Be skeptical, methodical, and evidence‑driven. Always justify why you assign a "
        "particular score."
    )


FORENSIC_MESSAGES = [
    "🔍 Scanning metadata for deepfake artifacts...",
    "🧠 Analyzing logical consistency of financial claims...",
    "🌐 Cross‑referencing entities with global scam databases...",
    "💻 Executing Python‑based math verification...",
    "⚖️ Evaluating social engineering tactics...",
    "🔎 Identifying suspicious domain registrations...",
    "📡 Checking historical context via Google Search...",
]

FORENSIC_TIPS = [
    "Most scams use false urgency to stop you from thinking logically.",
    "If a crypto return is > 1% daily, it is almost certainly a Ponzi scheme.",
    "Legitimate CEOs almost never use a free webmail address for wire instructions.",
    "Scammers often ask you to move conversations off‑platform to avoid moderation.",
]


def get_client(api_key: str) -> genai.Client:
    # Prefer explicit API key from sidebar, but allow env fallback if needed.
    if not api_key:
        api_key = os.getenv("GEMINI_API_KEY", "") or os.getenv("GOOGLE_API_KEY", "")
    return genai.Client(api_key=api_key)


def build_config(include_code_execution: bool = True) -> types.GenerateContentConfig:
    tools = [
        # Grounding with Google Search (enabled for all requests)
        types.Tool(google_search=types.GoogleSearch()),
    ]
    # Code execution is powerful for text/link analysis but can conflict with some
    # media MIME types, so we enable it selectively.
    if include_code_execution:
        tools.append(types.Tool(code_execution=types.ToolCodeExecution()))

    return types.GenerateContentConfig(
        system_instruction=build_system_prompt(),
        tools=tools,
        thinking_config=types.ThinkingConfig(
            include_thoughts=True,
            thinking_level=types.ThinkingLevel.HIGH,
        ),
        # Ask the model to return a structured JSON payload we can render.
        response_mime_type="application/json",
        response_schema={
            "type": "object",
            "properties": {
                "deception_score": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": 100,
                    "description": "Overall deception / scam likelihood from 0 (benign) to 100 (highly deceptive).",
                },
                "scam_score": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": 100,
                    "description": "Alias for deception_score, used specifically for the deception meter UI.",
                },
                "risk_level": {
                    "type": "string",
                    "description": "Short label like LOW, MEDIUM, HIGH summarizing the risk.",
                },
                "summary": {
                    "type": "string",
                    "description": "Short natural‑language summary of why this score was assigned.",
                },
                "red_flag_timeline_markdown": {
                    "type": "string",
                    "description": "Chronological bullet list in markdown with timestamps or sections and red flags.",
                },
                "advice_markdown": {
                    "type": "string",
                    "description": "Optional markdown with practical safety advice for the viewer/reader.",
                },
                "math_reality_check_markdown": {
                    "type": "string",
                    "description": "Optional markdown narrative explaining numeric reasoning that exposes scams.",
                },
                "math_table_markdown": {
                    "type": "string",
                    "description": "Optional markdown table showing key math comparisons (e.g., promised vs realistic returns).",
                },
            },
            "required": ["deception_score", "risk_level", "summary", "red_flag_timeline_markdown"],
        },
        temperature=1.0,
        top_p=0.9,
    )


def run_gemini_analysis(
    api_key: str,
    contents: List[Any],
    *,
    include_code_execution: bool = True,
) -> Optional[Dict[str, Any]]:
    try:
        client = get_client(api_key)
        response = client.models.generate_content(
            model=MODEL_ID,
            contents=contents,
            config=build_config(include_code_execution=include_code_execution),
        )
    except Exception as e:  # noqa: BLE001
        st.error(f"Gemini request failed: {e}")
        return None

    # Collect thought summaries (internal monologue) if present.
    thoughts: List[str] = []
    try:
        for part in response.candidates[0].content.parts:
            if getattr(part, "thought", False) and getattr(part, "text", None):
                thoughts.append(part.text)
    except Exception:
        # Non‑critical: if thought extraction fails, we still try to parse the main payload.
        thoughts = []

    # Primary path: use structured JSON from .parsed
    analysis = getattr(response, "parsed", None)

    # If the SDK returned a Pydantic model or similar, normalize it.
    if hasattr(analysis, "model_dump"):
        try:
            analysis = analysis.model_dump()  # type: ignore[assignment]
        except Exception:
            pass

    # Sometimes a list is returned when using JSON schema; take the first dict.
    if isinstance(analysis, list) and analysis and isinstance(analysis[0], dict):
        analysis = analysis[0]

    if isinstance(analysis, dict):
        if thoughts:
            analysis["_thoughts_markdown"] = "".join(thoughts)
        return analysis

    # Fallback 1: try to parse raw text as JSON.
    raw_text = getattr(response, "text", None)
    if raw_text:
        try:
            parsed = json.loads(raw_text)
            if isinstance(parsed, dict):
                if thoughts:
                    parsed["_thoughts_markdown"] = "".join(thoughts)
                return parsed
        except Exception:
            # Not valid JSON – continue to final fallback.
            pass

    # Fallback 2: fabricate a minimal analysis so the UI always shows something.
    summary_text = raw_text or "Model returned an unexpected format; showing raw output."
    fallback: Dict[str, Any] = {
        "deception_score": 0,
        "risk_level": "UNKNOWN",
        "summary": summary_text,
        "red_flag_timeline_markdown": "",
    }
    if thoughts:
        fallback["_thoughts_markdown"] = "".join(thoughts)
    st.warning("Could not parse structured response from Gemini; showing a best‑effort analysis.")
    return fallback


def render_deception_score(score: int, risk_level: str) -> None:
    score = max(0, min(100, int(score)))

    if score < 30:
        color = "#16a34a"  # green
    elif score < 60:
        color = "#f97316"  # orange
    else:
        color = "#dc2626"  # red

    st.subheader("Deception Score")
    # Simple meter using Streamlit built‑ins for immediate visual impact.
    st.progress(score / 100.0)

    st.markdown(
        f"""
<div class="score-container">
  <div class="score-label">Deception Score: <span>{score}/100</span> — {risk_level.upper()}</div>
  <div class="score-bar-outer">
    <div class="score-bar-inner" style="width: {score}%; background: {color};"></div>
  </div>
</div>
        """,
        unsafe_allow_html=True,
    )


def render_red_flag_timeline(markdown_timeline: str, advice_markdown: Optional[str]) -> None:
    st.subheader("Red Flag Timeline")
    st.markdown(markdown_timeline or "_No clear red flags identified._")

    if advice_markdown:
        st.markdown("---")
        st.subheader("Forensic Safety Briefing")
        st.markdown(advice_markdown)


def render_analysis_output(result: Dict[str, Any]) -> None:
    # Prefer scam_score if present, otherwise fall back to deception_score.
    base_score = int(result.get("deception_score", 0))
    scam_score = int(result.get("scam_score", base_score))
    deception_score = scam_score
    risk_level = str(result.get("risk_level", "UNKNOWN"))
    summary = str(result.get("summary", "")).strip()
    timeline_md = str(result.get("red_flag_timeline_markdown", "")).strip()
    advice_md = str(result.get("advice_markdown", "")).strip() or None
    thoughts_md = str(result.get("_thoughts_markdown", "")).strip() or None
    math_md = str(result.get("math_reality_check_markdown", "")).strip() or None
    math_table_md = str(result.get("math_table_markdown", "")).strip() or None

    render_deception_score(deception_score, risk_level)

    if thoughts_md:
        with st.expander("🕵️ Forensic Reasoning (Thinking Process)", expanded=True):
            st.markdown(thoughts_md)

    if summary:
        st.markdown("### Forensic Summary")
        st.markdown(summary)

    if math_md or math_table_md:
        st.markdown("---")
        st.markdown("### Math Reality Check")
        if math_md:
            st.markdown(math_md)
        if math_table_md:
            st.markdown(math_table_md)

    render_red_flag_timeline(timeline_md, advice_md)


def main() -> None:
    st.set_page_config(
        page_title="The Scrutinizer",
        layout="wide",
        page_icon="🔍",
    )

    # Custom dark forensic theme
    st.markdown(
        """
<style>
/* Global dark background and typography */
body, .stApp {
  background: radial-gradient(circle at top, #020617 0, #020617 40%, #020617 60%, #000000 100%) !important;
  color: #e5e7eb !important;
}

section.main > div {
  padding-top: 1.5rem;
}

/* Sidebar styling */
div[data-testid="stSidebar"] {
  background: linear-gradient(180deg, #020617 0%, #020617 40%, #020617 100%) !important;
  border-right: 1px solid #1f2937;
}

.sidebar-title {
  font-size: 1.4rem;
  font-weight: 700;
  letter-spacing: 0.06em;
  text-transform: uppercase;
  color: #f97316;
}

.sidebar-subtitle {
  font-size: 0.8rem;
  color: #9ca3af;
  text-transform: uppercase;
  letter-spacing: 0.14em;
}

/* Cards / containers */
.scrutinizer-card {
  background: rgba(15, 23, 42, 0.96);
  border-radius: 0.8rem;
  border: 1px solid rgba(75, 85, 99, 0.7);
  padding: 1.2rem 1.4rem;
  box-shadow: 0 18px 45px rgba(0, 0, 0, 0.65);
}

/* Score meter */
.score-container {
  margin: 0.5rem 0 1rem;
}

.score-label {
  font-size: 0.86rem;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: #9ca3af;
  margin-bottom: 0.4rem;
}

.score-label span {
  color: #e5e7eb;
  font-weight: 600;
}

.score-bar-outer {
  width: 100%;
  height: 0.65rem;
  border-radius: 999px;
  background: #020617;
  overflow: hidden;
  box-shadow: 0 0 0 1px rgba(31, 41, 55, 0.9), 0 0 18px rgba(148, 27, 38, 0.2);
}

.score-bar-inner {
  height: 100%;
  border-radius: 999px;
  background: linear-gradient(90deg, #22c55e, #facc15, #ef4444);
  transition: width 0.7s ease-out;
}

/* Tabs styling */
button[role="tab"] {
  border-radius: 999px !important;
  padding: 0.35rem 1.2rem !important;
}

/* Headings tweak */
h1, h2, h3 {
  color: #e5e7eb !important;
}
</style>
        """,
        unsafe_allow_html=True,
    )

    # Sidebar
    with st.sidebar:
        st.markdown('<div class="sidebar-title">The Scrutinizer</div>', unsafe_allow_html=True)
        st.markdown('<div class="sidebar-subtitle">Forensic deception lab</div>', unsafe_allow_html=True)
        st.markdown("---")

        api_key = st.text_input("Gemini API Key", type="password", help="Paste a valid Gemini or Google API key.")
        st.caption(
            "The key is used only from your browser session to call the Gemini API via `google-genai`."
        )
        st.markdown("---")
        st.subheader("💡 Forensic Tip")
        st.info(random.choice(FORENSIC_TIPS))

    st.title("Digital Forensic Deception Analysis")
    st.write(
        "Upload suspect media or paste links/text to get a **forensic‑style breakdown** of potential scams, "
        "deepfakes, and manipulation patterns."
    )

    tab_media, tab_text = st.tabs(["🎥 Video / Audio Analysis", "🔗 Link / Text Analysis"])

    # Video / Image / Audio Analysis
    with tab_media:
        st.markdown('<div class="scrutinizer-card">', unsafe_allow_html=True)
        uploads = st.file_uploader(
            "Upload one or more media files (video, image, or audio)",
            type=[
                "mp4",
                "mov",
                "mkv",
                "webm",
                "mp3",
                "wav",
                "m4a",
                "jpg",
                "jpeg",
                "png",
            ],
            accept_multiple_files=True,
        )

        # Inline media previews so users can see what is being analyzed.
        if uploads:
            st.markdown("#### Media Preview")
            cols = st.columns(2)
            for idx, uploaded in enumerate(uploads):
                filetype = (uploaded.type or "").lower()
                filename = uploaded.name
                col = cols[idx % 2]
                with col:
                    st.caption(f"**{filename}**")
                    if filetype.startswith("video"):
                        st.video(uploaded)
                    elif filetype.startswith("image"):
                        st.image(uploaded, use_column_width=True)
                    elif filetype.startswith("audio"):
                        st.audio(uploaded)
                    else:
                        st.write("Unsupported preview type; will still be analyzed.")

        context_notes = st.text_area(
            "Context (optional)",
            placeholder="Describe where this clip came from, how it was sent to you, and why it feels suspicious.",
        )
        analyze_btn = st.button("Run Forensic Scan", type="primary")

        if analyze_btn:
            if not api_key:
                st.error("Please add your Gemini API key in the sidebar first.")
            elif not uploads:
                st.error("Please upload at least one media file to analyze.")
            else:
                status_box = st.container()
                with status_box:
                    st.markdown("🕵️ **Investigation in Progress...**")
                    msg_placeholder = st.empty()
                    for _ in range(3):
                        msg_placeholder.write(random.choice(FORENSIC_MESSAGES))
                        time.sleep(1.5)

                client = get_client(api_key)

                media_inputs: List[Any] = []
                media_labels: List[str] = []
                for idx, uploaded in enumerate(uploads):
                    uploaded.seek(0)
                    guessed_mime, _ = mimetypes.guess_type(uploaded.name)
                    media_labels.append(f"Media {idx + 1}: {uploaded.name}")
                    # If we can guess a sane media MIME type from the filename, use the Files API.
                    if guessed_mime:
                        try:
                            file_ref = client.files.upload(file=uploaded)
                            media_inputs.append(file_ref)
                            continue
                        except Exception:
                            # Non‑fatal: fall back to direct bytes ingestion.
                            uploaded.seek(0)
                            data = uploaded.read()
                            media_inputs.append(
                                types.Part.from_bytes(data=data, mime_type=guessed_mime)
                            )
                    else:
                        # If we really can't determine a MIME type, bypass Files API entirely.
                        uploaded.seek(0)
                        data = uploaded.read()
                        mime_type = uploaded.type or "application/octet-stream"
                        media_inputs.append(
                            types.Part.from_bytes(data=data, mime_type=mime_type)
                        )

                prompt = (
                    "You are analyzing potentially deceptive media (video, images, and/or audio files).\n"
                    "Below is a numbered list of the media assets provided in this batch:\n"
                    f"{chr(10).join(media_labels)}\n\n"
                    "Use **all** of these media assets together with the optional context notes to detect scams, "
                    "fraud, or deepfake‑like behavior. If different assets show different risk levels, explain that "
                    "clearly and base the overall deception score on the **worst** (most dangerous) case.\n"
                    "Pay close attention to:\n"
                    "- voice consistency, unnatural edits, and lip‑sync issues\n"
                    "- visual or stylistic artifacts that suggest manipulation\n"
                    "- pressure tactics, urgency, or emotional manipulation\n"
                    "- financial promises, crypto or investment schemes\n"
                    "- identity claims, credentials, or impersonation cues\n"
                    "Return a single structured JSON analysis that reflects the entire batch of media."
                )

                contents: List[Any] = [prompt]
                if context_notes.strip():
                    contents.append(
                        f"Additional human context from the victim:\n{context_notes.strip()}"
                    )
                contents.extend(media_inputs)

                # For media analysis we keep Google Search + thinking, but
                # skip code_execution to avoid MIME restrictions in that tool.
                result = run_gemini_analysis(
                    api_key,
                    contents,
                    include_code_execution=False,
                )

                st.success("✅ Investigation Complete!")

                if isinstance(result, dict):
                    render_analysis_output(result)
                else:
                    st.error("Unexpected response format from Gemini.")

        st.markdown("</div>", unsafe_allow_html=True)

    # Link / Text Analysis
    with tab_text:
        st.markdown('<div class="scrutinizer-card">', unsafe_allow_html=True)
        suspicious_link = st.text_input(
            "Suspicious link (optional)",
            placeholder="https://example.com/claim-your-prize",
        )
        suspicious_text = st.text_area(
            "Suspicious message, email, script, or transcript",
            placeholder=(
                "Paste phishing emails, DMs, script from a call, sales page copy, or any text you want investigated."
            ),
        )
        text_analyze_btn = st.button("Run Forensic Scan on Text / Link", type="primary")

        if text_analyze_btn:
            if not api_key:
                st.error("Please add your Gemini API key in the sidebar first.")
            elif not suspicious_link.strip() and not suspicious_text.strip():
                st.error("Please provide at least a link or some text to analyze.")
            else:
                status_box = st.container()
                with status_box:
                    st.markdown("🕵️ **Investigation in Progress...**")
                    msg_placeholder = st.empty()
                    for _ in range(3):
                        msg_placeholder.write(random.choice(FORENSIC_MESSAGES))
                        time.sleep(1.5)

                description_lines = [
                    "You are analyzing potentially deceptive online content (link and/or text).",
                    "Investigate for:",
                    "- phishing, account takeover, credential harvesting",
                    "- fake technical support or refund scams",
                    "- crypto / investment fraud and Ponzi patterns",
                    "- romance scams, giveaway scams, and deepfake‑assisted grifts",
                    "Cross‑check key claims with web search where useful.",
                    "Return a structured JSON analysis that matches the configured schema.",
                ]
                base_prompt = "\n".join(description_lines)

                text_block = suspicious_text.strip() or ""
                link_block = suspicious_link.strip() or ""

                user_payload = "User‑provided artifacts:\n"
                if link_block:
                    user_payload += f"- Link: {link_block}\n"
                if text_block:
                    user_payload += f"- Text snippet:\n{text_block}\n"

                contents = [base_prompt, user_payload]
                # For text / link analysis we enable both Google Search and code_execution.
                result = run_gemini_analysis(
                    api_key,
                    contents,
                    include_code_execution=True,
                )

                st.success("✅ Investigation Complete!")

                if isinstance(result, dict):
                    render_analysis_output(result)
                else:
                    st.error("Unexpected response format from Gemini.")

        st.markdown("</div>", unsafe_allow_html=True)


if __name__ == "__main__":
    main()
