#!/usr/bin/env python3
"""Claude Code hooks for video_creation (stdlib only). Exit 2 + stderr feeds the problem back to Claude.

bash  (PreToolUse Bash):  block commands that use an API key / paid model API (AGENTS.md: no API keys),
                          and commands that start work on a shared lab GPU without scripts/lab/gpu_free.sh.
post  (PostToolUse Edit|Write): syntax check of edited .py files; block SDK imports of paid model APIs.
usage (UserPromptSubmit / PostToolUse): one note per level and reset window when the Claude plan usage
                          (from ~/.claude/statusline.py) passes 80/90/95 % (5 h) or 90/95 % (7 d).
"""
import json
import os
import re
import sys
import time
from pathlib import Path

ROOT = Path(os.environ.get("CLAUDE_PROJECT_DIR") or Path(__file__).resolve().parents[2])
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from vg import site

# --- no API keys / paid APIs -------------------------------------------------------------
KEY_NAMES = re.compile(r"\b(OPENAI_API_KEY|GEMINI_API_KEY|GOOGLE_API_KEY|ARK_API_KEY|ANTHROPIC_API_KEY|"
                       r"ELEVENLABS_API_KEY|REPLICATE_API_TOKEN|FAL_KEY|RUNWAYML_API_SECRET|"
                       r"STABILITY_API_KEY|VOLC_ACCESSKEY|BYTEPLUS_API_KEY)\b")
PAID_HOSTS = re.compile(r"api\.openai\.com|generativelanguage\.googleapis\.com|aiplatform\.googleapis\.com|"
                        r"texttospeech\.googleapis\.com|api\.anthropic\.com|volces\.com|bytepluses\.com|"
                        r"api\.elevenlabs\.io|api\.replicate\.com|fal\.run|api\.runwayml\.com|klingai\.com|"
                        r"api\.minimax|api\.lumalabs\.ai|api\.stability\.ai")
# Removing a key from an environment (env -u NAME, env --unset=NAME, unset NAME ...) is the allowed use of its name.
SCRUB = re.compile(r"(?:(?<=\s)-u\s*|--unset[= ])\w+|\bunset\s+[\w ]+")
READ_ONLY_SEARCH = re.compile(r"^\s*(grep|rg|git\s+(grep|log|diff|show|status))\b")
PAID_SDK_IMPORT = re.compile(r"^\s*(from|import)\s+(openai|anthropic|google\.genai|google\.generativeai|"
                             r"volcenginesdkarkruntime|byteplussdkarkruntime|elevenlabs|replicate|fal_client)\b",
                             re.M)

# --- shared lab GPUs ---------------------------------------------------------------------
GPU_HOST = re.compile(site.load().get("LAB_GPU_HOST_REGEX") or r"(?!)")
GPU_SELECT = re.compile(r"CUDA_VISIBLE_DEVICES=['\"]?[\w$]")  # '' (CPU only) does not match
GPU_CHECKED = re.compile(r"gpu_free\.sh|scripts/lab/worker\.sh")

# --- plan usage ------------------------------------------------------------------------------
USAGE_STATE = Path.home() / ".claude" / "usage_state.json"  # written by ~/.claude/statusline.py
USAGE_WARNED = ROOT / ".claude" / "hooks" / ".usage_warned"
USAGE_LEVELS = {"five_hour": (80, 90, 95), "seven_day": (90, 95)}


def bash(payload):
    command = (payload.get("tool_input") or {}).get("command") or ""
    if not READ_ONLY_SEARCH.search(command):
        rest = SCRUB.sub(" ", command)
        hit = KEY_NAMES.search(rest) or PAID_HOSTS.search(rest)
        if hit:
            print(f"Blocked: this command uses '{hit.group(0)}'. AGENTS.md: no API keys and no paid APIs in this "
                  "project — use open-weights models on our GPUs. Stripping a key from an environment "
                  "(env -u NAME / unset NAME) is allowed.", file=sys.stderr)
            return 2
    if GPU_HOST.search(command) and GPU_SELECT.search(command) and not GPU_CHECKED.search(command):
        print("This starts work on a shared lab GPU without checking it. Put the check in the same ssh command, "
              "e.g. `bash scripts/lab/gpu_free.sh 2 && CUDA_VISIBLE_DEVICES=2 ...`, and pick another GPU if it "
              "reports busy (never stop other users' processes). Bulk work goes to an HPC cluster.",
              file=sys.stderr)
        return 2
    return 0


def post(payload):
    path = (payload.get("tool_input") or {}).get("file_path") or ""
    if not path.endswith(".py"):
        return 0
    try:
        rel = Path(path).resolve().relative_to(ROOT.resolve())
    except ValueError:
        return 0
    file = ROOT / rel
    if not file.is_file():
        return 0
    data = file.read_bytes()
    problems = []
    try:
        compile(data, str(rel), "exec")
    except SyntaxError as exc:
        problems.append(f"{rel}:{exc.lineno}: SyntaxError: {exc.msg}")
    if rel.parts[:2] != (".claude", "hooks"):
        match = PAID_SDK_IMPORT.search(data.decode("utf-8", "replace"))
        if match:
            problems.append(f"{rel}: imports '{match.group(2)}', a paid model API SDK (AGENTS.md: no API keys)")
    if problems:
        print("\n".join(problems), file=sys.stderr)
        return 2
    return 0


def usage(payload):
    try:
        state = json.loads(USAGE_STATE.read_text())
        warned = set(json.loads(USAGE_WARNED.read_text())) if USAGE_WARNED.is_file() else set()
    except (OSError, ValueError):
        return 0
    if time.time() - state.get("time", 0) > 1800:
        return 0
    notes, new = [], set()
    for key, levels in USAGE_LEVELS.items():
        item = state.get(key) or {}
        used = item.get("used_percentage")
        hit = [lv for lv in levels if used is not None and used >= lv]
        if not hit:
            continue
        tag = f"{key}:{item.get('resets_at')}:{hit[-1]}"
        if tag not in warned:
            new.add(tag)
            notes.append(f"{key.replace('_', ' ')} {used:.0f}% (resets {item.get('resets') or '?'})")
    if not notes:
        return 0
    USAGE_WARNED.write_text(json.dumps(sorted(warned | new)))
    text = ("Claude plan usage is high: " + "; ".join(notes) +
            ". Hand volume work to Codex (AGENTS.md); at 95% update local/HANDOFF.md (state + next task) and start nothing new.")
    event = payload.get("hook_event_name") or "UserPromptSubmit"
    print(json.dumps({"hookSpecificOutput": {"hookEventName": event, "additionalContext": text}}))
    return 0


if __name__ == "__main__":
    try:
        payload = json.load(sys.stdin)
    except ValueError:
        payload = {}
    sys.exit({"bash": bash, "post": post, "usage": usage}[sys.argv[1]](payload))
