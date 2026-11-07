"""One helper: ask an LLM through the `claude -p` CLI. Model is Sonnet by default
(project rule: delegated work runs on Opus or lower).

Tools off, no MCP servers, no session file, and run from an empty temp directory,
so text pulled from papers can never make the model read or run anything (arch D16)."""
import subprocess, tempfile


def argv(model="sonnet"):
    return ["claude", "-p", "--model", model, "--output-format", "text",
            "--tools", "", "--strict-mcp-config", "--no-session-persistence"]


_a = argv()
assert _a[_a.index("--tools") + 1] == "" and "--strict-mcp-config" in _a and "--no-session-persistence" in _a


def ask(prompt, model="sonnet", timeout=300):
    with tempfile.TemporaryDirectory(prefix="radar-llm-") as tmp:
        r = subprocess.run(argv(model), input=prompt, capture_output=True, text=True, timeout=timeout, cwd=tmp)
    if r.returncode != 0:
        raise RuntimeError(f"claude -p exited {r.returncode}: {r.stderr[:500]}")
    return r.stdout.strip()
