#!/usr/bin/env python3
"""Offline test of check.py decisions: python3 .claude/hooks/test_check.py (exit 1 on any mismatch)."""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HOOK = Path(__file__).with_name("check.py")
ROOT = HOOK.parents[2]
BASH_CASES = [  # (expected exit code, command)
    (2, "echo $OPENAI_API_KEY"),
    (2, "curl -s https://generativelanguage.googleapis.com/v1/models"),
    (2, 'python3 -c "import os; print(os.environ[\'ARK_API_KEY\'])"'),
    (2, "curl https://ark.ap-southeast.bytepluses.com/api/v3/contents/generations/tasks"),
    (0, "env -u OPENAI_API_KEY -u GEMINI_API_KEY -u ARK_API_KEY python3 x.py"),
    (0, "unset OPENAI_API_KEY GEMINI_API_KEY ARK_API_KEY; python3 x.py"),
    (0, "grep -rn OPENAI_API_KEY vg/"),
    (2, "ssh gpu-test 'CUDA_VISIBLE_DEVICES=0 python x.py'"),
    (0, "ssh gpu-test 'bash scripts/lab/gpu_free.sh 0 && CUDA_VISIBLE_DEVICES=0 python x.py'"),
    (0, "ssh gpu-test nvidia-smi"),
    (0, "ssh gpu-test \"CUDA_VISIBLE_DEVICES='' python x.py\""),
]
POST_CASES = [  # (expected exit code, file content)
    (2, "import os\nfrom openai import OpenAI\n"),
    (2, "import google.genai as genai\n"),
    (2, "def f(:\n"),
    (0, "import os\nprint(1)\n"),
]


def run(mode, payload, gpu_pattern=r"\bgpu-test\b"):
    env = dict(os.environ, CLAUDE_PROJECT_DIR=str(ROOT), LAB_GPU_HOST_REGEX=gpu_pattern)
    return subprocess.run([sys.executable, str(HOOK), mode], input=json.dumps(payload), text=True,
                          capture_output=True, env=env).returncode


def main():
    bad = 0
    for want, command in BASH_CASES:
        got = run("bash", {"tool_input": {"command": command}})
        bad += got != want
        print(f"{'ok ' if got == want else 'BAD'} bash want={want} got={got}  {command}")
    command = "ssh gpu-test 'CUDA_VISIBLE_DEVICES=0 python x.py'"
    got = run("bash", {"tool_input": {"command": command}}, gpu_pattern="")
    bad += got != 0
    print(f"{'ok ' if got == 0 else 'BAD'} bash unset site regex matches nothing")
    with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
        target = Path(tmp) / "case.py"
        for want, content in POST_CASES:
            target.write_text(content)
            got = run("post", {"tool_input": {"file_path": str(target)}})
            bad += got != want
            print(f"{'ok ' if got == want else 'BAD'} post want={want} got={got}  {content.splitlines()[-1]}")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
