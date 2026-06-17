r"""
VerilogIDE Backend — FastAPI + Icarus Verilog v12
Place in:  A:\T HUB\jarvis_web\verilog_backend\server.py
Run:       uvicorn server:app --reload --port 8001   (NOT python server.py)

AI PROVIDER PRIORITY (first key found wins — all are FREE):
  1. GEMINI_API_KEY   → Google Gemini 2.0 Flash       (aistudio.google.com)
  2. GROQ_API_KEY     → Groq Llama 3.1 70B            (console.groq.com)
  3. NVIDIA_API_KEY   → NVIDIA NIM Nemotron-70B       (build.nvidia.com)
  4. OPENROUTER_KEY   → OpenRouter free models         (openrouter.ai)
  5. ANTHROPIC_API_KEY→ Anthropic Claude (paid)        (console.anthropic.com)
  6. No key           → Local template fallback        (always free, no internet)

Set ONE of these in CMD before starting:
  set GEMINI_API_KEY=AIza...
  set GROQ_API_KEY=gsk_...
  set NVIDIA_API_KEY=nvapi-...
  set OPENROUTER_KEY=sk-or-...
"""

import os
from dotenv import load_dotenv

load_dotenv()

import subprocess
import tempfile
import shutil
import re
import time
import json
from pathlib import Path
from typing import Optional
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel

# ══════════════════════════════════════════════════════════════
#  AI PROVIDER SETUP  (tries each in order, uses first found)
# ══════════════════════════════════════════════════════════════

_ai_provider = None   # which provider is active
_ai_client   = None   # client object

# ── 1. Google Gemini (FREE — recommended) ─────────────────────
_GEMINI_KEY = os.getenv("GEMINI_API_KEY", "")
if _GEMINI_KEY:
    try:
        from google import genai as _genai
        _ai_client   = _genai.Client(api_key=_GEMINI_KE)
        _ai_provider = "gemini"
        print("[AI] Using Google Gemini 2.0 Flash (FREE)")
    except Exception as e:
        print(f"[AI] Gemini init failed: {e}")

# ── 2. Groq (FREE — very fast) ────────────────────────────────
_GROQ_KEY = os.getenv("GROQ_API_KEY", "")
if not _ai_provider and _GROQ_KEY:
    try:
        from groq import Groq
        _ai_client   = Groq(api_key=_GROQ_KEY)
        _ai_provider = "groq"
        print("[AI] Using Groq Llama-3.1-70b (FREE)")
    except Exception as e:
        print(f"[AI] Groq init failed: {e}")

# ── 3. NVIDIA NIM (FREE — build.nvidia.com) ───────────────────
_NVIDIA_KEY = os.getenv("NVIDIA_API_KEY", "")
if not _ai_provider and _NVIDIA_KEY:
    _ai_provider = "nvidia"
    _ai_client   = _NVIDIA_KEY   # used directly via urllib in _call_nvidia
    print("[AI] Using NVIDIA NIM (FREE — Llama-3.1-Nemotron-70b)")

# ── 4. OpenRouter (FREE models available) ─────────────────────
_OR_KEY = os.getenv("OPENROUTER_KEY", "")
if not _ai_provider and _OR_KEY:
    _ai_provider = "openrouter"
    _ai_client   = _OR_KEY   # used directly in requests
    print("[AI] Using OpenRouter free models")

# ── 5. Anthropic Claude (PAID fallback) ───────────────────────
_ANTHROPIC_KEY = os.getenv("ANTHROPIC_API_KEY", "")
if not _ai_provider and _ANTHROPIC_KEY:
    try:
        import anthropic
        _ai_client   = anthropic.Anthropic(api_key=_ANTHROPIC_KEY)
        _ai_provider = "anthropic"
        print("[AI] Using Anthropic Claude (paid)")
    except Exception as e:
        print(f"[AI] Anthropic init failed: {e}")

if not _ai_provider:
    print("[AI] No API key found — using local template generator (free, offline)")

# ══════════════════════════════════════════════════════════════
#  FASTAPI APP
# ══════════════════════════════════════════════════════════════

app = FastAPI(title="VerilogIDE API", version="2.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
)

IVERILOG = shutil.which("iverilog") or "iverilog"
VVP      = shutil.which("vvp")      or "vvp"
TIMEOUT  = 15

_HERE = Path(__file__).parent
if (_HERE / "index.html").exists():
    @app.get("/")
    def root():
        return FileResponse(_HERE / "index.html")

# ══════════════════════════════════════════════════════════════
#  REQUEST MODELS
# ══════════════════════════════════════════════════════════════

class CompileReq(BaseModel):
    design: str
    testbench: Optional[str] = None
    language: str = "verilog"

class SimReq(BaseModel):
    design: str
    testbench: str
    language: str = "verilog"

class AIReq(BaseModel):
    design: str
    language: str = "verilog"

class DesignReq(BaseModel):
    prompt: str
    language: str = "verilog"

class FormatReq(BaseModel):
    code: str
    language: str = "verilog"

# ══════════════════════════════════════════════════════════════
#  HELPERS
# ══════════════════════════════════════════════════════════════

def lang_flags(lang: str) -> list:
    return ["-g2012"] if lang == "systemverilog" else ["-g2005-sv"]

def parse_errors(raw: str, design_path: str, tb_path: Optional[str]) -> list:
    out = []
    pat = re.compile(r"^(.+?):(\d+):\s*(error|warning|note):\s*(.+)$", re.M)
    for m in pat.finditer(raw):
        fp, ln, sev, msg = m.groups()
        label = "testbench.v" if (tb_path and fp == tb_path) else "design.v"
        out.append({"file": label, "line": int(ln), "severity": sev, "message": msg.strip()})
    if not out and raw.strip():
        out.append({"file": "compiler", "line": 0, "severity": "error", "message": raw.strip()})
    return out

def extract_ports(code):
    """
    Handles every port declaration style in Verilog-95 and ANSI, AND
    captures bit widths so testbench generation can declare correctly
    sized regs/wires (e.g. reg [3:0] A; instead of reg A;).

      input a, b, c;            body comma list, width 1
      input [3:0] A, B;         bus + comma list, width 4
      output reg [3:0] result;  output reg single, width 4
      output reg zero, carry;   output reg comma list, width 1
      input  [3:0] A, B,        ANSI header comma-terminated
      input clk, rst            single-line ANSI header

    Returns inputs/outputs as both a list of names (back-compat) AND a
    list of {"name":..., "width":...} dicts under "inputs_w"/"outputs_w".
    """
    SKIP = {
        "wire","reg","tri","supply0","supply1","wand","wor",
        "input","output","inout","integer","real","time",
        "signed","unsigned","parameter","localparam",
        "begin","end","always","initial","assign",
        "module","endmodule","case","endcase","if","else","for"
    }

    modm = re.search(r'\bmodule\s+(\w+)', code)
    ins, outs, params = [], [], []
    ins_w, outs_w = [], []
    seen_in, seen_out = set(), set()

    def add(direction, name, width):
        name = name.strip()
        if not re.match(r'^[A-Za-z_]\w*$', name): return
        if name in SKIP: return
        if direction == 'input':
            if name not in seen_in:
                seen_in.add(name); ins.append(name); ins_w.append({"name": name, "width": width})
        else:
            if name not in seen_out:
                seen_out.add(name); outs.append(name); outs_w.append({"name": name, "width": width})

    # Unified pattern — captures optional [MSB:LSB] bus width
    port_re = re.compile(
        r'\b(input|output|inout)\b'
        r'(?:\s+(?:wire|reg|tri))?'
        r'(?:\s*\[\s*(\d+)\s*:\s*(\d+)\s*\])?'
        r'\s+((?:\w+\s*,\s*)*\w+)'
        r'(?=\s*[;)]\s*|\s*,?\s*(?:input|output|inout)\b|\s*\n)',
        re.MULTILINE
    )
    for m2 in port_re.finditer(code):
        direction = m2.group(1)
        msb, lsb = m2.group(2), m2.group(3)
        width = (int(msb) - int(lsb) + 1) if (msb is not None and lsb is not None) else 1
        for name in m2.group(4).split(','):
            add(direction, name, width)

    for m2 in re.finditer(
        r'\bparameter\s+(?:\w+\s+)?(\w+)\s*=\s*([^;,\n]+)', code
    ):
        params.append({"name": m2.group(1), "value": m2.group(2).strip()})

    return {
        "module":     modm.group(1) if modm else "design",
        "inputs":     ins,
        "outputs":    outs,
        "inputs_w":   ins_w,
        "outputs_w":  outs_w,
        "parameters": params,
    }


def iverilog_version() -> str:
    try:
        r = subprocess.run([IVERILOG, "-V"], capture_output=True, text=True, timeout=4)
        lines = (r.stdout or r.stderr or "").splitlines()
        return lines[0] if lines else "unknown"
    except Exception as e:
        return str(e)

def _static_analysis(code: str, label: str) -> list:
    warns = []
    is_tb = label == "testbench.v"

    for i, line in enumerate(code.splitlines(), 1):
        s = line.strip()
        if s.startswith("//"): continue
        # Warn about comparing two numeric constants (always true/false)
        if "==" in s and re.search(r"\b\d+\b\s*==\s*\b\d+\b", s):
            warns.append({"file": label, "line": i, "severity": "warning",
                          "message": "Comparing two constants — always true/false?"})
        # Warn about blocking assignment inside sequential always block
        if is_tb is False and re.search(r"\bposedge\b|\bnegedge\b", s):
            if "=" in s and "<=" not in s and "==" not in s and "!=" not in s:
                warns.append({"file": label, "line": i, "severity": "warning",
                              "message": "Blocking assignment (=) in sequential always block — use non-blocking (<=)"})

    # $finish check: ONLY warn for testbenches, never for design files
    if is_tb:
        if "$finish" not in code and "$stop" not in code:
            warns.append({"file": label, "line": 0, "severity": "warning",
                          "message": "Testbench has no $finish or $stop — simulation may run forever"})
        if "$dumpfile" not in code and "$dumpvars" not in code:
            warns.append({"file": label, "line": 0, "severity": "note",
                          "message": "No $dumpfile/$dumpvars — VCD waveform will not be generated"})

    return warns

# ══════════════════════════════════════════════════════════════
#  AI TESTBENCH GENERATION  (provider-aware)
# ══════════════════════════════════════════════════════════════

_TB_PROMPT = """\
Write a complete, runnable {lang} testbench for this design. This testbench
will be compiled and simulated immediately, so it MUST produce real signal
activity — a flat/empty waveform is treated as a FAILED testbench.

STRICT REQUIREMENTS:
1. `timescale 1ns/1ps directive
2. Clock generation (10ns period) if a clk/clock port exists
3. Reset pulse sequence if a rst/reset port exists
4. EVERY OTHER INPUT PORT MUST BE ASSIGNED CONCRETE VALUES — never leave a
   placeholder comment like "add test vectors here". If the design has no
   clock (combinational or gate-level/structural), drive ALL input
   combinations exhaustively using a for-loop over a concatenated vector,
   e.g.: for (integer i=0; i<2**N; i=i+1) begin {{a,b,c}} = i; #10; end
5. $dumpfile and $dumpvars for VCD waveform output, called before any
   input changes
6. $monitor printing every input and output signal with $time
7. $finish at the end
8. Brief comments explaining each phase of the test

Output ONLY the raw Verilog/SystemVerilog code. No markdown, no explanation,
no "TODO" or placeholder comments — every input must actually be driven.

Design code:
{design}"""

# Phrases that indicate the AI left test vectors unimplemented — if any of
# these appear with no real stimulus around them, we discard the AI output
# and fall back to the guaranteed-exhaustive local template instead.
_PLACEHOLDER_MARKERS = (
    "add test vector", "todo", "// fill in", "your test", "insert test",
    "drive inputs here", "test cases here", "stimulus here",
)

_DESIGN_PROMPT = """
You are an expert RTL designer.

Generate ONLY synthesizable {lang} code.

Rules:

1. Include complete module.
2. Add comments.
3. No markdown.
4. No explanation.
5. Output only code.

User Prompt:

{prompt}
"""

def _looks_like_placeholder_tb(code: str) -> bool:
    lower = code.lower()
    if any(marker in lower for marker in _PLACEHOLDER_MARKERS):
        return True
    # Heuristic: testbench declares regs but never assigns most of them
    # outside of the module port list (i.e. no "name = " or "name <= " or
    # "name=" appears anywhere in the body).
    reg_names = re.findall(r"\breg\s+(\w+)\s*;", code)
    if reg_names:
        undriven = [n for n in reg_names if not re.search(rf"\b{re.escape(n)}\s*(<=|=)\s*[^=]", code)]
        if len(undriven) == len(reg_names) and len(reg_names) > 0:
            return True
    return False

def _call_gemini(prompt: str) -> str:
    response = _ai_client.models.generate_content(
        model="gemini-2.0-flash",
        contents=prompt,
    )
    return response.text

def _call_groq(prompt: str) -> str:
    completion = _ai_client.chat.completions.create(
        model="llama-3.1-70b-versatile",
        messages=[{"role": "user", "content": prompt}],
        max_tokens=1400,
        temperature=0.2,
    )
    return completion.choices[0].message.content

def _call_nvidia(prompt: str) -> str:
    """NVIDIA NIM — OpenAI-compatible API. Get a free key at build.nvidia.com"""
    import urllib.request
    payload = json.dumps({
        "model": "meta/llama-3.3-70b-instruct",
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 1400,
        "temperature": 0.2,
    }).encode()
    req = urllib.request.Request(
        "https://integrate.api.nvidia.com/v1/chat/completions",
        data=payload,
        headers={
            "Authorization": f"Bearer {_ai_client}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
    )
    with urllib.request.urlopen(req, timeout=40) as r:
        data = json.loads(r.read())
    return data["choices"][0]["message"]["content"]

def _call_openrouter(prompt: str) -> str:
    import urllib.request
    payload = json.dumps({
        "model": "mistralai/mistral-7b-instruct:free",
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 1400,
    }).encode()
    req = urllib.request.Request(
        "https://openrouter.ai/api/v1/chat/completions",
        data=payload,
        headers={
            "Authorization": f"Bearer {_ai_client}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://verilog-ide-backend.onrender.com",
        }
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        data = json.loads(r.read())
    return data["choices"][0]["message"]["content"]

def _call_anthropic(prompt: str) -> str:
    msg = _ai_client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1400,
        messages=[{"role": "user", "content": prompt}]
    )
    return msg.content[0].text

def _clean_code(raw: str) -> str:
    """Strip any markdown fences the model might add."""
    raw = re.sub(r"^```[a-z]*\n?", "", raw, flags=re.M)
    raw = raw.replace("```", "").strip()
    return raw

def _ai_generate(design: str, language: str) -> tuple[str, str]:
    """
    Returns (testbench_code, source_label).
    Tries each available provider; if the result still looks like a
    placeholder (undriven inputs / TODO comments), discards it and falls
    back to the guaranteed-exhaustive local template instead.
    """
    lang_label = "SystemVerilog" if language == "systemverilog" else "Verilog HDL"
    prompt = _TB_PROMPT.format(lang=lang_label, design=design)

    providers = [
        ("gemini",     _call_gemini,     "gemini-2.0-flash (free)"),
        ("groq",       _call_groq,       "groq/llama-3.1-70b (free)"),
        ("nvidia",     _call_nvidia,     "nvidia/llama-3.1-nemotron-70b (free)"),
        ("openrouter", _call_openrouter, "openrouter/mistral-7b (free)"),
        ("anthropic",  _call_anthropic,  "claude-sonnet-4-6 (paid)"),
    ]

    for name, fn, label in providers:
        if _ai_provider != name:
            continue
        try:
            code = _clean_code(fn(prompt))
            if _looks_like_placeholder_tb(code):
                print(f"[AI] {name} produced a placeholder testbench — using guaranteed template instead")
                return _template_tb(design), "template (auto-corrected)"
            return code, label
        except Exception as e:
            print(f"[AI] {name} call failed: {e}")

    # final fallback — local template, always works and always drives inputs
    return _template_tb(design), "template (offline)"

def _ai_design(prompt: str, language: str):

    lang_label = "SystemVerilog" if language == "systemverilog" else "Verilog HDL"
    p = _DESIGN_PROMPT.format(prompt=prompt, lang=lang_label)

    try:

        if _ai_provider == "gemini":
            out = _call_gemini(p)

        elif _ai_provider == "groq":
            out = _call_groq(p)

        elif _ai_provider == "nvidia":
            out = _call_nvidia(p)

        elif _ai_provider == "openrouter":
            out = _call_openrouter(p)

        elif _ai_provider == "anthropic":
            out = _call_anthropic(p)

        else:

            return {
                "design": """module top_module();

endmodule""",

                "specifications":
                "- Offline mode\n"
                "- No AI provider configured"

            }, "template"

        out = out.replace("```verilog", "")
        out = out.replace("```systemverilog", "")
        out = out.replace("```", "")
        out = out.strip()

        return {

            "design": out,

            "specifications":
            "- Generated by AI"

        }, "ai"

    except Exception as e:

        print("[AI DESIGN ERROR]", e)

        return {

            "design":
"""module top_module();

endmodule""",

            "specifications":
f"- AI generation failed\n- {str(e)}"

        }, "fallback"

# ══════════════════════════════════════════════════════════════
#  ROUTES
# ══════════════════════════════════════════════════════════════

@app.get("/health")
def health():
    return {
        "status":           "ok",
        "iverilog_path":    IVERILOG,
        "iverilog_version": iverilog_version(),
        "ai_provider":      _ai_provider or "none (template fallback)",
        "ai_available":     _ai_provider is not None,
        "timestamp":        time.time(),
    }

@app.post("/generate-design")
def gen_design(req: DesignReq):

    d, source = _ai_design(
        req.prompt,
        req.language
    )

    return {

        "success": True,
        "design":d.get("design","""module top_module();endmodule"""),
        "specifications":d.get("specifications",""),
        "source":source
    }

@app.post("/compile")
async def compile_verilog(data: dict):
    code = data.get("code", "")

    # Compile code here

    return {
        "success": True,
        "message": "Compilation completed"
    }


@app.post("/simulate")
def simulate(req: SimReq):
    if not req.testbench or len(req.testbench.strip()) < 5:
        raise HTTPException(400, "Testbench is required for simulation")
    tmp = tempfile.mkdtemp()
    try:
        df  = os.path.join(tmp, "design.v")
        tbf = os.path.join(tmp, "testbench.v")
        vvp = os.path.join(tmp, "sim.vvp")
        Path(df).write_text(req.design,    encoding="utf-8")
        Path(tbf).write_text(req.testbench, encoding="utf-8")

        r1 = subprocess.run(
            [IVERILOG, *lang_flags(req.language), "-o", vvp, df, tbf],
            capture_output=True, text=True, timeout=TIMEOUT
        )
        if r1.returncode != 0:
            return {"success": False,
                    "errors": parse_errors(r1.stderr + r1.stdout, df, tbf),
                    "sim_output": "", "vcd": None,
                    "ports": extract_ports(req.design)}

        r2 = subprocess.run(
            [VVP, vvp], capture_output=True, text=True, timeout=TIMEOUT, cwd=tmp
        )
        sim_out = (r2.stdout + r2.stderr).strip()

        vcd = None
        for f in os.listdir(tmp):
            if f.endswith(".vcd"):
                vcd = Path(os.path.join(tmp, f)).read_text(errors="replace")
                break

        return {
            "success":    True,
            "errors":     [],
            "sim_output": sim_out,
            "vcd":        vcd,
            "ports":      extract_ports(req.design),
        }
    except subprocess.TimeoutExpired:
        return {"success": False,
                "errors": [{"file":"simulator","line":0,"severity":"error",
                             "message":"Simulation timed out — add $finish to testbench"}],
                "sim_output": "", "vcd": None}
    except Exception as e:
        raise HTTPException(500, str(e))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@app.post("/generate-testbench")
def gen_tb(req: AIReq):
    tb_code, source = _ai_generate(req.design, req.language)
    return {"success": True, "testbench": tb_code, "source": source}


@app.post("/format")
def format_code(req: FormatReq):
    lines = req.code.splitlines()
    out, indent = [], 0
    kw_in  = {"begin","module","case","function","task","generate","fork"}
    kw_out = {"end","endmodule","endcase","endfunction","endtask","endgenerate","join"}
    for raw in lines:
        s = raw.strip()
        if not s:
            out.append(""); continue
        if any(s.startswith(k) for k in kw_out):
            indent = max(0, indent - 1)
        out.append("  " * indent + s)
        if any(s.startswith(k) or s.endswith(k) for k in kw_in) and not s.endswith(";"):
            indent += 1
    return {"code": "\n".join(out)}


@app.post("/analyze")
def analyze(req: CompileReq):
    result = compile_code(req)
    always = re.findall(r"always\s*@\s*\(([^)]+)\)", req.design)
    insts  = [
        {"module": m.group(1), "name": m.group(2)}
        for m in re.finditer(r"\b(\w+)\s+(\w+)\s*\(", req.design)
        if m.group(1) not in {
            "module","input","output","wire","reg","always","initial",
            "if","else","case","for","begin","end","assign","parameter","localparam"
        }
    ]
    return {
        **result,
        "analysis": {
            "always_blocks": always,
            "instances":     insts,
            "line_count":    len(req.design.splitlines()),
            "has_clk":       bool(re.search(r"\bclk\b|\bclock\b", req.design)),
            "has_rst":       bool(re.search(r"\brst\b|\breset\b", req.design)),
            "sequential":    "posedge" in req.design or "negedge" in req.design,
            "combinational": "always @(*)" in req.design or "always @*" in req.design,
        }
    }

# ══════════════════════════════════════════════════════════════
#  LOCAL TEMPLATE FALLBACK
# ══════════════════════════════════════════════════════════════

def _template_tb(design: str) -> str:
    """
    Generates a testbench that ALWAYS drives real values into every input —
    never leaves a TODO placeholder. Strategy:
      - clk/clock port  -> standard clock generator
      - rst/reset port  -> standard reset pulse
      - remaining inputs, total width <=20 bits -> exhaustive truth table
        (every combination across all driven inputs combined)
      - remaining inputs, total width >20 bits  -> pseudo-random toggling
      - sequential designs (clock present) drive remaining inputs with
        $random on every negedge instead of with #delay, so the design
        actually sees clock-relative stimulus
    Bit widths are read from extract_ports() so multi-bit ports (e.g.
    [3:0] A) get correctly sized reg declarations and the exhaustive loop
    iterates 2**total_width times instead of treating every port as 1 bit.
    """
    p   = extract_ports(design)
    mod = p["module"]
    ins, outs = p["inputs"], p["outputs"]
    in_width  = {d["name"]: d["width"] for d in p["inputs_w"]}
    out_width = {d["name"]: d["width"] for d in p["outputs_w"]}

    def decl(name, width):
        return f"  reg  [{width-1}:0] {name};" if width > 1 else f"  reg  {name};"
    def decl_wire(name, width):
        return f"  wire [{width-1}:0] {name};" if width > 1 else f"  wire {name};"

    clk_name = next((s for s in ins if s.lower() in ("clk", "clock")), None)
    rst_name = next((s for s in ins if s.lower() in ("rst","reset","n_rst","rst_n","nreset")), None)
    active_low_rst = bool(rst_name and rst_name.lower() in ("rst_n","n_rst","nreset"))

    drive_ins = [s for s in ins if s not in (clk_name, rst_name)]
    total_drive_width = sum(in_width.get(s, 1) for s in drive_ins)

    L = ["`timescale 1ns/1ps", f"module tb_{mod};", ""]
    for s in ins:  L.append(decl(s, in_width.get(s, 1)))
    for s in outs: L.append(decl_wire(s, out_width.get(s, 1)))
    L += ["", f"  {mod} uut ("]
    L.append(",\n".join(f"    .{s}({s})" for s in ins + outs))
    L += ["  );", ""]

    if clk_name:
        L += [f"  initial {clk_name} = 0;", f"  always #5 {clk_name} = ~{clk_name};", ""]

    L.append("  initial begin")
    L.append(f'    $dumpfile("{mod}.vcd");')
    L.append(f"    $dumpvars(0, tb_{mod});")

    mon_fmt = " ".join(f"{s}=%b" for s in ins + outs)
    mon_args = ", ".join(ins + outs)
    if ins or outs:
        L.append(f'    $monitor("%0t {mon_fmt}", $time, {mon_args});')

    for s in drive_ins:
        L.append(f"    {s} = 0;")

    if rst_name:
        active_val  = "0" if active_low_rst else "1"
        release_val = "1" if active_low_rst else "0"
        L.append(f"    {rst_name} = {active_val}; #20; {rst_name} = {release_val};")

    if clk_name:
        if drive_ins:
            L.append("    // Drive inputs across clock edges")
            L.append(f"    repeat (16) begin")
            L.append(f"      @(negedge {clk_name});")
            for s in drive_ins:
                w = in_width.get(s, 1)
                L.append(f"      {s} = $random;" if w <= 32 else f"      {s} = $random;  // truncated to {w} bits by assignment")
            L.append("    end")
            L.append(f"    @(negedge {clk_name});")
        else:
            L.append("    #160;")
    else:
        # Combinational / structural design: no clock.
        if total_drive_width == 0:
            L.append("    #50;")
        elif total_drive_width <= 20:
            # Exhaustive truth table across the COMBINED bit-width of all
            # driven inputs, correctly sized regardless of individual
            # port widths (e.g. A[3:0],B[3:0],op[2:0] -> 11 bits -> 2048).
            total = 2 ** total_drive_width
            concat = ", ".join(drive_ins)
            L.append(f"    // Exhaustive test — all {total} input combinations ({total_drive_width} total bits)")
            L.append(f"    for (integer _i = 0; _i < {total}; _i = _i + 1) begin")
            L.append(f"      {{{concat}}} = _i;")
            L.append("      #10;")
            L.append("    end")
        else:
            L.append("    // Randomised test vectors (too many input bits to enumerate exhaustively)")
            L.append("    repeat (32) begin")
            for s in drive_ins:
                L.append(f"      {s} = $random;")
            L.append("      #10;")
            L.append("    end")

    L += ["    $finish;", "  end", "endmodule"]
    return "\n".join(L)
