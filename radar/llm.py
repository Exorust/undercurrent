"""One helper: ask an LLM through the `claude -p` CLI. Model is Sonnet by default
(project rule: delegated work runs on Opus or lower)."""
import subprocess


def ask(prompt, model="sonnet", timeout=300):
    r = subprocess.run(["claude", "-p", "--model", model, "--output-format", "text"], input=prompt,
                       capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        raise RuntimeError(r.stderr[:500])
    return r.stdout.strip()
