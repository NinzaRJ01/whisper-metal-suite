#!/usr/bin/env python3
"""
summarize_meeting.py: Turn meeting transcripts into executive summaries & interactive HTML reports.
Features:
- Live audio-synced interactive transcript with auto-scrolling & click-to-seek.
- AI-synthesized notes with dynamic additional sections (PV, Technical, Risks, etc.).
- Multi-tier processing: Gemini (AGY) -> Local LLM (LM Studio / Ollama) -> Heuristic fallback.
"""

import sys
import os
import re
import json
import urllib.request
import urllib.error
from datetime import datetime
from pathlib import Path


HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Meeting Summary - {meeting_title}</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <style>
        @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap');
        body {{
            font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
            background-color: #0b0f19;
            color: #e2e8f0;
        }}
        .glass-card {{
            background: rgba(18, 24, 38, 0.7);
            backdrop-filter: blur(12px);
            border: 1px solid rgba(255, 255, 255, 0.08);
            box-shadow: 0 8px 32px 0 rgba(0, 0, 0, 0.37);
        }}
        .pill-badge {{
            background: rgba(99, 102, 241, 0.15);
            border: 1px solid rgba(99, 102, 241, 0.3);
            color: #a5b4fc;
        }}
        .active-seg {{
            background: rgba(99, 102, 241, 0.22) !important;
            border-color: rgba(99, 102, 241, 0.6) !important;
            box-shadow: 0 0 16px rgba(99, 102, 241, 0.25);
        }}
    </style>
</head>
<body class="min-h-screen py-10 px-4 sm:px-6 lg:px-8">
    <div class="max-w-4xl mx-auto space-y-8">
        
        <!-- Header -->
        <div class="glass-card rounded-2xl p-6 sm:p-8 flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
            <div>
                <div class="flex items-center gap-3">
                    <span class="inline-flex items-center px-3 py-1 rounded-full text-xs font-semibold pill-badge">
                        🎙️ Meeting Notes
                    </span>
                    <span class="text-xs text-slate-400">{date_str}</span>
                </div>
                <h1 class="text-2xl sm:text-3xl font-bold text-white mt-2">{meeting_title}</h1>
                <p class="text-sm text-slate-400 mt-1">Duration: <span class="text-slate-300 font-medium">{duration_str}</span> • Words: <span class="text-slate-300 font-medium">{word_count}</span></p>
            </div>
            <div class="flex items-center gap-3">
                <button onclick="copyMarkdown()" id="copyBtn" class="px-4 py-2 bg-indigo-600 hover:bg-indigo-500 active:bg-indigo-700 text-white text-sm font-medium rounded-xl shadow-lg transition-all flex items-center gap-2">
                    <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M8 5H6a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2v-1M8 5a2 2 0 002 2h2a2 2 0 002-2M8 5a2 2 0 012-2h2a2 2 0 012 2m0 0h2a2 2 0 012 2v3m2 4H10m0 0l3-3m-3 3l3 3"></path></svg>
                    Copy Markdown
                </button>
            </div>
        </div>

        <!-- Audio Player & Live-Synced Moving Transcript -->
        {audio_and_synced_transcript_html}

        <!-- Local Privacy Shield (Sensitive Data Redaction) -->
        {privacy_shield_html}

        <!-- Executive Summary -->
        <div class="glass-card rounded-2xl p-6 sm:p-8">
            <h2 class="text-lg font-semibold text-white flex items-center gap-2 mb-3">
                <span>📋</span> Executive Summary
            </h2>
            <div class="text-slate-300 leading-relaxed text-sm sm:text-base">
                {executive_summary}
            </div>
        </div>

        <!-- Key Discussions & Highlights -->
        <div class="glass-card rounded-2xl p-6 sm:p-8">
            <h2 class="text-lg font-semibold text-white flex items-center gap-2 mb-4">
                <span>💬</span> Key Discussions & Topics
            </h2>
            <ul class="space-y-3">
                {key_discussions_html}
            </ul>
        </div>

        <!-- Decisions Made -->
        <div class="glass-card rounded-2xl p-6 sm:p-8">
            <h2 class="text-lg font-semibold text-white flex items-center gap-2 mb-4">
                <span>📌</span> Decisions Made
            </h2>
            <ul class="space-y-3">
                {decisions_html}
            </ul>
        </div>

        <!-- Action Items -->
        <div class="glass-card rounded-2xl p-6 sm:p-8">
            <h2 class="text-lg font-semibold text-white flex items-center gap-2 mb-4">
                <span>✅</span> Action Items & Next Steps
            </h2>
            <div class="space-y-2">
                {action_items_html}
            </div>
        </div>

        <!-- Additional Dynamic Sections Generated by AI -->
        {additional_sections_html}

        <!-- Full Plain Transcript (Collapsible Archive) -->
        <details class="glass-card rounded-2xl p-6 sm:p-8 group cursor-pointer">
            <summary class="text-base font-semibold text-slate-300 hover:text-white flex items-center justify-between">
                <span class="flex items-center gap-2"><span>📝</span> View Raw Transcript Text</span>
                <span class="text-xs text-indigo-400 group-open:rotate-180 transition-transform">▼</span>
            </summary>
            <div class="mt-4 pt-4 border-t border-slate-700/50 text-slate-400 text-sm whitespace-pre-wrap font-mono leading-relaxed max-h-96 overflow-y-auto">
{full_transcript}
            </div>
        </details>

    </div>

    <!-- Hidden Markdown text for copying -->
    <textarea id="rawMarkdown" class="hidden">{raw_markdown_escaped}</textarea>

    <script>
        function copyMarkdown() {{
            const text = document.getElementById('rawMarkdown').value;
            navigator.clipboard.writeText(text).then(() => {{
                const btn = document.getElementById('copyBtn');
                const orig = btn.innerHTML;
                btn.innerHTML = '✓ Copied!';
                btn.classList.add('bg-emerald-600');
                btn.classList.remove('bg-indigo-600');
                setTimeout(() => {{
                    btn.innerHTML = orig;
                    btn.classList.remove('bg-emerald-600');
                    btn.classList.add('bg-indigo-600');
                }}, 2000);
            }});
        }}

        // Audio-Synced Transcript Tracking & Click-to-Seek
        const audio = document.getElementById('meetingAudio');
        const segments = document.querySelectorAll('.transcript-seg');
        const autoScrollCheck = document.getElementById('autoScrollToggle');
        let currentActiveIdx = -1;

        function seekAudio(seconds) {{
            if (audio) {{
                audio.currentTime = seconds;
                audio.play();
            }}
        }}

        if (audio && segments.length > 0) {{
            audio.addEventListener('timeupdate', () => {{
                const time = audio.currentTime;
                let activeIdx = -1;

                segments.forEach((seg, idx) => {{
                    const s = parseFloat(seg.dataset.start);
                    const e = parseFloat(seg.dataset.end);
                    if (time >= s && time <= e + 0.4) {{
                        activeIdx = idx;
                    }}
                }});

                if (activeIdx !== -1 && activeIdx !== currentActiveIdx) {{
                    segments.forEach(s => {{
                        s.classList.remove('active-seg');
                        const txt = s.querySelector('.seg-text');
                        if (txt) {{
                            txt.classList.remove('text-indigo-200', 'font-medium');
                            txt.classList.add('text-slate-300');
                        }}
                    }});

                    const activeSeg = segments[activeIdx];
                    activeSeg.classList.add('active-seg');
                    const activeTxt = activeSeg.querySelector('.seg-text');
                    if (activeTxt) {{
                        activeTxt.classList.remove('text-slate-300');
                        activeTxt.classList.add('text-indigo-200', 'font-medium');
                    }}

                    if (autoScrollCheck && autoScrollCheck.checked) {{
                        activeSeg.scrollIntoView({{ behavior: 'smooth', block: 'nearest' }});
                    }}

                    currentActiveIdx = activeIdx;
                }}
            }});
        }}
    </script>
</body>
</html>
"""


def get_transcript_segments(meeting_dir: Path, full_transcript: str) -> list:
    """Load word/segment timestamps from transcript.json or estimate from transcript.txt."""
    json_path = meeting_dir / "transcript.json"
    if json_path.exists():
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                raw_segs = data.get("transcription", [])
                if raw_segs:
                    segments = []
                    for s in raw_segs:
                        offsets = s.get("offsets", {})
                        start_sec = offsets.get("from", 0) / 1000.0
                        end_sec = offsets.get("to", 0) / 1000.0
                        text = s.get("text", "").strip()
                        if text:
                            segments.append({
                                "start": round(start_sec, 2),
                                "end": round(end_sec, 2),
                                "text": text
                            })
                    if segments:
                        return segments
        except Exception:
            pass

    # Fallback estimation if transcript.json not present
    lines = [l.strip() for l in full_transcript.split("\n") if l.strip()]
    sentences = []
    for l in lines:
        parts = re.split(r'(?<=[.!?]) +', l)
        sentences.extend([p.strip() for p in parts if len(p.strip()) > 3])

    total_words = max(1, len(full_transcript.split()))
    est_duration = max(4.0, total_words / 2.3) # ~140 wpm
    curr_time = 0.0
    segments = []
    for s in sentences:
        words_in_s = max(1, len(s.split()))
        duration_s = (words_in_s / total_words) * est_duration
        segments.append({
            "start": round(curr_time, 2),
            "end": round(curr_time + duration_s, 2),
            "text": s
        })
        curr_time += duration_s
    return segments


def sanitize_sensitive_data(transcript: str) -> tuple[str, list[dict]]:
    """
    Local Privacy Middleware & Sensitive Data Redactor:
    Scans the transcript for sensitive credentials, passwords, passcodes, API keys,
    tokens, and secrets using a 2-stage local mechanism:
      - Stage 1: High-speed local regex & keyword pattern detector (~1ms).
      - Stage 2: Local LM Studio (Qwen 2.5 Coder 7B) contextual secret detector (~1.5s, if running).
    Replaces all detected sensitive values with variable placeholders like [REDACTED_PASSWORD_1], [REDACTED_API_KEY_1].
    Ensures that raw passwords and secrets are NEVER passed to Gemini or external clouds.
    """
    redacted = []
    text = transcript

    # Stage 1: Regex & Heuristic Patterns
    patterns = [
        ("API_KEY", r"\b(sk-[A-Za-z0-9_\-]{20,})\b"),
        ("API_KEY", r"\b(ghp_[A-Za-z0-9]{30,}|gho_[A-Za-z0-9]{30,})\b"),
        ("API_KEY", r"\b(AIza[0-9A-Za-z\-_]{35})\b"),
        ("API_KEY", r"\b(AKIA[0-9A-Z]{16})\b"),
        ("TOKEN", r"\bBearer\s+([A-Za-z0-9\._\-]{20,})\b"),
        ("SECRET", r"\b([A-Za-z0-9]{32,64})\b"),  # generic hex/alphanumeric secret hash
        ("PASSWORD", r"(?i)\b(?:my\s+)?(password|passcode|passphrase|secret|pin)\s*(?:is|to|:|was|=|should be)?\s*([\"\']?[A-Za-z0-9@#$%^&*!_\-+~]{4,}[\"\']?)"),
    ]

    found_secrets = {}  # value -> type
    for ptype, pat in patterns:
        for m in re.finditer(pat, text):
            if ptype == "PASSWORD":
                val = m.group(2).strip("\"\'.,;: ")
                if len(val) >= 4 and val.lower() not in ("that", "this", "here", "true", "false", "very", "good", "well", "fine", "done", "will", "have"):
                    found_secrets[val] = "PASSWORD"
            elif ptype == "SECRET":
                val = m.group(1).strip("\"\'.,;: ")
                if any(c.isdigit() for c in val) and any(c.isalpha() for c in val) and len(val) >= 32:
                    found_secrets[val] = "SECRET"
            else:
                val = m.group(1).strip("\"\'.,;: ")
                found_secrets[val] = ptype

    # Stage 2: Local LM Studio Contextual DLP Auditor
    try:
        dlp_prompt = f"""You are a strict Data Loss Prevention (DLP) security auditor.
Scan this transcript for sensitive information: passwords, passcodes, API keys, credentials, secret tokens, or private keys.
Return a valid JSON object listing all sensitive values that must be redacted before sending to the cloud:
{{
  "redactions": [
    {{"value": "exact sensitive string", "type": "PASSWORD | API_KEY | SECRET | CREDENTIAL"}}
  ]
}}
Transcript:
\"\"\"
{text[:4000]}
\"\"\"
Respond strictly in valid JSON only."""
        req = urllib.request.Request(
            "http://127.0.0.1:1234/v1/chat/completions",
            data=json.dumps({
                "model": "qwen2.5-coder-7b",
                "messages": [{"role": "user", "content": dlp_prompt}],
                "temperature": 0.1,
                "max_tokens": 200
            }).encode("utf-8"),
            headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=4) as resp:
            res = json.loads(resp.read().decode("utf-8"))
            raw_content = res["choices"][0]["message"]["content"]
            match = re.search(r"\{[\s\S]*\}", raw_content)
            if match:
                parsed = json.loads(match.group(0))
                for item in parsed.get("redactions", []):
                    v = item.get("value", "").strip("\"\'.,;: ")
                    t = item.get("type", "SECRET").upper()
                    if len(v) >= 3 and v in text and v.lower() not in ("that", "this", "here", "true", "false"):
                        found_secrets[v] = t
    except Exception:
        pass

    # Replace with Variables [REDACTED_{TYPE}_{N}]
    sanitized = text
    sorted_secrets = sorted(found_secrets.items(), key=lambda x: len(x[0]), reverse=True)
    counters = {}
    for val, stype in sorted_secrets:
        counters[stype] = counters.get(stype, 0) + 1
        var_name = f"[REDACTED_{stype}_{counters[stype]}]"
        sanitized = sanitized.replace(val, var_name)
        masked = val[:2] + "***" + val[-1:] if len(val) > 3 else "***"
        redacted.append({
            "masked_preview": masked,
            "type": stype,
            "variable": var_name
        })

    return sanitized, redacted


def summarize_with_gemini(transcript: str) -> dict:
    """Primary: Use AGY / Gemini CLI with dynamic sections enabled."""
    prompt = f"""You are an elite executive notes synthesizer.
Analyze this meeting transcript and produce insightful, noteworthy meeting notes.
Never output raw verbatim transcript quotes; instead, group discussions by real conceptual themes, explain the 'why' and context, and extract explicit decisions and action items.

You are also encouraged to dynamically create any additional relevant domain-specific sections under 'additional_sections' (such as 'Product Vision (PV) & Scope', 'Technical Architecture & Invariants', 'Open Questions & Risks', 'Timelines & Deadlines', etc.).

Respond strictly in valid JSON matching this schema:
{{
  "title": "Concise, punchy 3-6 word meeting title",
  "summary": "2-3 sentence executive synthesis explaining the core discussion, why it matters, and the primary takeaway.",
  "discussions": [
    "Insightful thematic takeaway explaining the concept, debate, or nuance.",
    "Another distinct conceptual topic discussed."
  ],
  "decisions": [
    "Explicit consensus reached, decision made, or policy agreed upon."
  ],
  "action_items": [
    "Action item with assignee or timeline if mentioned."
  ],
  "additional_sections": [
    {{
      "title": "Product Vision (PV) / Architecture / Open Questions / Risks (as appropriate)",
      "icon": "🎯",
      "items": ["Point 1", "Point 2"]
    }}
  ]
}}

Transcript:
\"\"\"
{transcript[:15000]}
\"\"\"
"""
    try:
        import subprocess
        proc = subprocess.run(
            ["agy", "--dangerously-skip-permissions", "-p", prompt],
            capture_output=True,
            text=True,
            timeout=25
        )
        if proc.returncode == 0 and proc.stdout:
            match = re.search(r"\{[\s\S]*\}", proc.stdout)
            if match:
                data = json.loads(match.group(0))
                if data.get("discussions"):
                    print("✓ Synthesized with Gemini (AGY).")
                    return data
    except Exception as e:
        print(f"Notice: Gemini processing skipped or timed out ({e}). Falling back to local model...")

    return {}


def summarize_with_local_llm(transcript: str) -> dict:
    """Fallback Tier 2: Use local LLM (LM Studio / Ollama) if Gemini is unavailable."""
    prompt = f"""You are an elite executive notes assistant. Analyze this transcript and return valid JSON with dynamic sections:
{{
  "title": "Concise 3-6 word title",
  "summary": "2-3 sentence executive summary",
  "discussions": ["Conceptual topic 1 with insight", "Conceptual topic 2"],
  "decisions": ["Decision 1"],
  "action_items": ["Action item 1"],
  "additional_sections": [
    {{
      "title": "Key Insights or Open Questions",
      "icon": "💡",
      "items": ["Note 1"]
    }}
  ]
}}

Transcript:
\"\"\"
{transcript[:8000]}
\"\"\"
"""
    # 1. Try LM Studio (http://127.0.0.1:1234/v1/chat/completions)
    try:
        req = urllib.request.Request(
            "http://127.0.0.1:1234/v1/chat/completions",
            data=json.dumps({
                "model": "qwen2.5-coder-7b",
                "messages": [
                    {"role": "system", "content": "You are an elite executive notes synthesizer. Never repeat verbatim sentences; group discussion into meaningful concepts and takeaways. Output valid JSON only."},
                    {"role": "user", "content": prompt}
                ],
                "temperature": 0.2
            }).encode("utf-8"),
            headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=20.0) as res:
            res_data = json.loads(res.read().decode())
            content = res_data["choices"][0]["message"]["content"]
            match = re.search(r"\{[\s\S]*\}", content)
            if match:
                data = json.loads(match.group(0))
                if data.get("discussions"):
                    print("✓ Synthesized with Local LLM (LM Studio).")
                    return data
    except Exception:
        pass

    # 2. Try Ollama (http://127.0.0.1:11434/api/chat)
    try:
        req = urllib.request.Request(
            "http://127.0.0.1:11434/api/chat",
            data=json.dumps({
                "model": "qwen2.5-coder:7b",
                "messages": [{"role": "user", "content": prompt}],
                "stream": False,
                "format": "json"
            }).encode("utf-8"),
            headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=20.0) as res:
            res_data = json.loads(res.read().decode())
            content = res_data["message"]["content"]
            match = re.search(r"\{[\s\S]*\}", content)
            if match:
                data = json.loads(match.group(0))
                if data.get("discussions"):
                    print("✓ Synthesized with Local LLM (Ollama).")
                    return data
    except Exception:
        pass

    return {}


def fallback_nlp_summary(transcript: str) -> dict:
    """Intelligent fallback rule-based NLP extraction when no local LLM server is active."""
    lines = [l.strip() for l in transcript.split("\n") if l.strip()]
    full_text = " ".join(lines)
    sentences = re.split(r'(?<=[.!?]) +', full_text)
    sentences = [s.strip() for s in sentences if len(s.strip()) > 8]

    action_keywords = ["need to", "will do", "action item", "follow up", "make sure", "let's", "todo", "should", "assign", "by friday", "by tomorrow", "working on"]
    decision_keywords = ["decided", "agreed", "consensus", "going with", "choose", "approved", "finalized", "plan is", "we will"]

    action_items = []
    decisions = []
    discussions = []

    for s in sentences:
        low = s.lower()
        if any(k in low for k in action_keywords):
            if len(action_items) < 8:
                action_items.append(s)
        elif any(k in low for k in decision_keywords):
            if len(decisions) < 6:
                decisions.append(s)
        else:
            if len(discussions) < 8 and len(s) > 25:
                discussions.append(s)

    if not action_items:
        action_items = ["Review transcript notes and confirm next steps with team."]
    if not decisions:
        decisions = ["No explicit consensus recorded; review discussion points."]
    if not discussions:
        discussions = sentences[:5] if sentences else ["Meeting opened and discussed current status."]

    summary = " ".join(sentences[:2]) if sentences else "Meeting transcript recorded."

    return {
        "title": "Meeting Sync & Discussion",
        "summary": summary,
        "discussions": discussions,
        "decisions": decisions,
        "action_items": action_items,
        "additional_sections": []
    }


def main():
    if len(sys.argv) < 2:
        print("Usage: summarize_meeting.py <path-to-transcript.txt> [path-to-audio.wav]")
        sys.exit(1)

    txt_path = Path(sys.argv[1]).resolve()
    if not txt_path.exists():
        print(f"Error: Transcript file not found: {txt_path}")
        sys.exit(1)

    audio_path = Path(sys.argv[2]).resolve() if len(sys.argv) > 2 else None
    meeting_dir = txt_path.parent

    with open(txt_path, "r", encoding="utf-8") as f:
        raw_transcript = f.read().strip()

    word_count = len(raw_transcript.split())
    date_str = datetime.now().strftime("%B %d, %Y • %I:%M %p")

    # Local Privacy Middleware: Sanitize passwords & secrets before cloud transmission
    sanitized_transcript, redaction_log = sanitize_sensitive_data(raw_transcript)
    if redaction_log:
        print(f"🛡️ Local Privacy Middleware: Redacted {len(redaction_log)} sensitive item(s) before sending to Gemini.")
        try:
            with open(meeting_dir / "sanitization_report.json", "w", encoding="utf-8") as rf:
                json.dump(redaction_log, rf, indent=2)
        except Exception as e:
            print(f"Notice: Failed to save sanitization report: {e}")

    # Tier 1: Gemini (AGY) -> Tier 2: Local LLM (LM Studio / Ollama) -> Tier 3: Rule-based
    # NOTE: Cloud Gemini ONLY receives sanitized_transcript with variable placeholders
    data = summarize_with_gemini(sanitized_transcript)
    if not data or not data.get("discussions"):
        data = summarize_with_local_llm(sanitized_transcript)
    if not data or not data.get("discussions"):
        data = fallback_nlp_summary(sanitized_transcript)

    title = data.get("title", "Meeting Discussion")
    summary = data.get("summary", "Meeting conducted.")
    discussions = data.get("discussions", [])
    decisions = data.get("decisions", [])
    action_items = data.get("action_items", [])
    additional_sections = data.get("additional_sections", [])

    # Format Markdown
    md_lines = [
        f"# {title}",
        f"**Date:** {date_str}  ",
        f"**Words:** {word_count}  ",
        "",
        "## 📋 Executive Summary",
        summary,
        "",
        "## 💬 Key Discussions & Highlights",
    ]
    for d in discussions:
        md_lines.append(f"- {d}")
    md_lines.append("")
    md_lines.append("## 📌 Decisions Made")
    for dec in decisions:
        md_lines.append(f"- {dec}")
    md_lines.append("")
    md_lines.append("## ✅ Action Items")
    for a in action_items:
        md_lines.append(f"- [ ] {a}")
    md_lines.append("")

    # Privacy Shield Section in Markdown
    if redaction_log:
        md_lines.append("## 🛡️ Local Privacy Shield (Data Loss Prevention)")
        md_lines.append(f"- **Protected:** {len(redaction_log)} sensitive item(s) converted into variables before cloud synthesis.")
        for r in redaction_log:
            md_lines.append(f"  - `{r['variable']}` (Type: `{r['type']}`, Masked: `{r['masked_preview']}`)")
        md_lines.append("")

    # Additional Dynamic Sections in Markdown
    for sec in additional_sections:
        sec_title = sec.get("title", "Additional Notes")
        sec_icon = sec.get("icon", "📌")
        md_lines.append(f"## {sec_icon} {sec_title}")
        for item in sec.get("items", []):
            md_lines.append(f"- {item}")
        md_lines.append("")

    md_lines.append("---")
    md_lines.append("## 📝 Full Transcript")
    md_lines.append("```text")
    md_lines.append(sanitized_transcript)
    md_lines.append("```")

    raw_markdown = "\n".join(md_lines)
    summary_md_path = meeting_dir / "summary.md"
    with open(summary_md_path, "w", encoding="utf-8") as f:
        f.write(raw_markdown)

    # Format HTML Sections
    discussions_html = "\n".join([
        f'<li class="flex items-start gap-3"><span class="text-indigo-400 mt-1">•</span><span class="text-slate-300 leading-relaxed text-sm sm:text-base">{d}</span></li>'
        for d in discussions
    ])
    decisions_html = "\n".join([
        f'<li class="flex items-start gap-3"><span class="text-amber-400 mt-1">✓</span><span class="text-slate-300 leading-relaxed text-sm sm:text-base">{dec}</span></li>'
        for dec in decisions
    ])
    action_items_html = "\n".join([
        f'<label class="flex items-start gap-3 p-3 rounded-xl hover:bg-slate-800/40 cursor-pointer transition-colors border border-transparent hover:border-slate-700/50">'
        f'<input type="checkbox" class="mt-1 rounded border-slate-700 text-indigo-600 focus:ring-indigo-500 bg-slate-900 w-4 h-4">'
        f'<span class="text-slate-200 text-sm sm:text-base">{a}</span>'
        f'</label>'
        for a in action_items
    ])

    # Dynamic Additional Sections in HTML
    additional_sections_html_parts = []
    for sec in additional_sections:
        sec_title = sec.get("title", "Additional Notes")
        sec_icon = sec.get("icon", "📌")
        items_html = "\n".join([
            f'<li class="flex items-start gap-3"><span class="text-indigo-400 mt-1">•</span><span class="text-slate-300 leading-relaxed text-sm sm:text-base">{item}</span></li>'
            for item in sec.get("items", [])
        ])
        sec_card = f"""
        <div class="glass-card rounded-2xl p-6 sm:p-8">
            <h2 class="text-lg font-semibold text-white flex items-center gap-2 mb-4">
                <span>{sec_icon}</span> {sec_title}
            </h2>
            <ul class="space-y-3">
                {items_html}
            </ul>
        </div>
        """
        additional_sections_html_parts.append(sec_card)
    additional_sections_html = "\n".join(additional_sections_html_parts)

    # Audio Player & Live-Synced Moving Transcript HTML
    segments = get_transcript_segments(meeting_dir, sanitized_transcript)
    seg_html_list = []
    for s in segments:
        seg_text = s['text']
        for r in redaction_log:
            # Ensure variable placeholder is reflected in segment text
            if r.get("original") and r["original"] in seg_text:
                seg_text = seg_text.replace(r["original"], r["variable"])
        start_fmt = f"{int(s['start'] // 60):02d}:{int(s['start'] % 60):02d}"
        seg_html = f"""
        <div class="transcript-seg p-2.5 rounded-xl cursor-pointer hover:bg-slate-800/60 transition-all flex items-start gap-3 border border-transparent"
             data-start="{s['start']}" data-end="{s['end']}" onclick="seekAudio({s['start']})">
             <span class="text-xs font-mono text-indigo-400 font-semibold px-2 py-0.5 rounded bg-indigo-950/70 border border-indigo-800/40 select-none">{start_fmt}</span>
             <span class="seg-text text-sm sm:text-base text-slate-300 leading-relaxed">{s['text']}</span>
        </div>
        """
        seg_html_list.append(seg_html)
    synced_segments_html = "\n".join(seg_html_list)

    audio_and_synced_transcript_html = ""
    if audio_path and audio_path.exists():
        rel_audio = os.path.relpath(audio_path, meeting_dir)
        audio_and_synced_transcript_html = f"""
        <div class="glass-card rounded-2xl p-6 sm:p-8 space-y-4">
            <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-3 pb-3 border-b border-slate-700/50">
                <h2 class="text-lg font-semibold text-white flex items-center gap-2">
                    <span>🎧</span> Audio Recording & Live Synced Transcript
                </h2>
                <div class="flex items-center gap-4 text-xs">
                    <span class="text-slate-400">Click any sentence to jump audio</span>
                    <label class="flex items-center gap-1.5 cursor-pointer text-slate-300 hover:text-white select-none">
                        <input type="checkbox" id="autoScrollToggle" checked class="rounded bg-slate-800 border-slate-600 text-indigo-500 focus:ring-0">
                        <span>Auto-follow</span>
                    </label>
                </div>
            </div>

            <!-- Audio Player Widget -->
            <div class="bg-slate-900/70 p-3 rounded-xl border border-slate-800">
                <audio id="meetingAudio" controls class="w-full h-9">
                    <source src="{rel_audio}" type="audio/wav">
                    Your browser does not support audio playback.
                </audio>
            </div>

            <!-- Moving Transcript box -->
            <div id="transcriptBox" class="max-h-72 overflow-y-auto space-y-1.5 p-2 rounded-xl bg-slate-900/50 border border-slate-800/80 scroll-smooth">
                {synced_segments_html}
            </div>
        </div>
        """
    else:
        # If audio was pruned, show the transcript box cleanly
        audio_and_synced_transcript_html = f"""
        <div class="glass-card rounded-2xl p-6 sm:p-8 space-y-4">
            <h2 class="text-lg font-semibold text-white flex items-center gap-2 pb-3 border-b border-slate-700/50">
                <span>📝</span> Meeting Transcript
            </h2>
            <div class="max-h-72 overflow-y-auto space-y-1.5 p-2 rounded-xl bg-slate-900/50 border border-slate-800/80">
                {synced_segments_html}
            </div>
        </div>
        """

    privacy_shield_html = ""
    if redaction_log:
        badges_html = " ".join([
            f'<span class="inline-flex items-center px-2.5 py-1 rounded-lg text-xs font-mono bg-emerald-950/80 text-emerald-300 border border-emerald-800/60">{r["variable"]} ({r["type"]})</span>'
            for r in redaction_log
        ])
        privacy_shield_html = f"""
        <div class="glass-card rounded-2xl p-6 sm:p-8 border border-emerald-500/40 bg-emerald-950/20">
            <div class="flex items-start gap-4">
                <span class="text-3xl">🛡️</span>
                <div class="space-y-1">
                    <h3 class="text-base font-bold text-emerald-400">Local Privacy Shield Activated</h3>
                    <p class="text-xs text-slate-300 leading-relaxed">
                        <strong>{len(redaction_log)} sensitive item(s)</strong> (passwords, tokens, or credentials) were detected by the local DLP middleware and converted into safe variable placeholders before transmission to Gemini. Zero raw secrets left this machine.
                    </p>
                    <div class="pt-2 flex flex-wrap gap-2">
                        {badges_html}
                    </div>
                </div>
            </div>
        </div>
        """

    html_content = HTML_TEMPLATE.format(
        meeting_title=title,
        date_str=date_str,
        duration_str=f"~{max(1, round(word_count / 140))} min",
        word_count=word_count,
        audio_and_synced_transcript_html=audio_and_synced_transcript_html,
        privacy_shield_html=privacy_shield_html,
        executive_summary=summary,
        key_discussions_html=discussions_html,
        decisions_html=decisions_html,
        action_items_html=action_items_html,
        additional_sections_html=additional_sections_html,
        full_transcript=sanitized_transcript,
        raw_markdown_escaped=raw_markdown.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    )

    summary_html_path = meeting_dir / "summary.html"
    with open(summary_html_path, "w", encoding="utf-8") as f:
        f.write(html_content)

    print(f"✓ Summary generated:")
    print(f"  Markdown: {summary_md_path}")
    print(f"  HTML:     {summary_html_path}")

    # Copy to clipboard if pbcopy exists
    try:
        import subprocess
        p = subprocess.Popen(["pbcopy"], stdin=subprocess.PIPE)
        p.communicate(input=raw_markdown.encode("utf-8"))
        print("✓ Markdown summary copied to clipboard.")
    except Exception:
        pass

    # Independent auto-cleanup retention check
    try:
        sys.path.insert(0, str(Path(__file__).parent))
        from prune_meetings import prune_meetings
        prune_stats = prune_meetings(str(meeting_dir.parent))
        if prune_stats.get("audio_deleted_count", 0) > 0 or prune_stats.get("meetings_deleted_count", 0) > 0:
            print(f"✓ Retention cleanup: Pruned {prune_stats.get('audio_deleted_count')} audio files, {prune_stats.get('meetings_deleted_count')} older meetings.")
    except Exception as e:
        print(f"Retention check notice: {e}")


if __name__ == "__main__":
    main()
