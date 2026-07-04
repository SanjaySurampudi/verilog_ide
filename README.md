# Verilog IDE

A browser-based IDE for writing, simulating, and synthesizing Verilog RTL — with AI-assisted design generation built in. No local toolchain installation required; everything runs through a cloud backend.

**Live demo:** [_Live Link_](https://verilogide.web.app/)

---

## Features

- **Code Editor** — Write and edit Verilog RTL directly in the browser
- **Simulation** — Compile and simulate designs using Icarus Verilog (v12)
- **Waveform Output** — View simulation results and signal traces
- **Gate-Level Synthesis** — Generate synthesized netlists using Yosys
- **AI Design Generator** — Generate RTL from natural-language descriptions using NVIDIA NIM
- **Backend Health Detection** — Handles cold-start delays gracefully on free-tier hosting

---

## Tech Stack

| Layer      | Technology                              |
|------------|------------------------------------------|
| Frontend   | HTML, CSS, JavaScript                    |
| Backend    | FastAPI (Python)                         |
| Simulation | Icarus Verilog v12                       |
| Synthesis  | Yosys                                    |
| AI         | NVIDIA NIM API                           |
| Hosting    | Render                                   |

---

## Getting Started

### Prerequisites

- Python 3.9+
- [Icarus Verilog](http://iverilog.icarus.com/) (v12)
- [Yosys](https://yosyshq.net/yosys/)
- An NVIDIA NIM API key (for AI features)

### Installation

```bash
git clone https://github.com/SanjaySurampudi/verilog_ide.git
cd verilog_ide
```

**Backend setup:**

```bash
cd backend
pip install -r requirements.txt
```

Create a `.env` file in the backend directory:

```env
NVIDIA_NIM_API_KEY=your_api_key_here
```

Run the backend:

```bash
uvicorn main:app --reload
```

**Frontend setup:**

Open `index.html` in your browser, or serve it with a static file server. Update the backend URL in the frontend config if it differs from `http://localhost:8000`.

---

## Usage

1. Write or paste your Verilog module into the editor
2. Click **Simulate** to compile and run with Icarus Verilog
3. Click **Synthesize** to generate a gate-level netlist with Yosys
4. Use the **AI Design Generator** to describe a circuit in plain English and get RTL scaffolding
5. Review console output, waveform data, and netlist results in the results panel

---

## Project Structure

```
verilog_ide/
├── backend/
│   ├── main.py            # FastAPI app entrypoint
│   ├── routes/            # API route handlers (simulate, synthesize, AI generate)
│   ├── requirements.txt
│   └── .env
├── frontend/
│   ├── index.html
│   ├── style.css
│   └── script.js
└── README.md
```

*(Update this to match your actual folder layout.)*

---

## Deployment

The app is deployed on [Render](https://render.com/), with the FastAPI backend running as a web service. Because Render's free tier spins down idle services, the frontend includes cold-start detection to notify users while the backend wakes up.

---

## Roadmap

- [ ] Waveform viewer integration (GTKWave-style rendering in-browser)
- [ ] Testbench auto-generation via AI
- [ ] Support for multi-file projects
- [ ] SystemVerilog support
- [ ] User accounts and saved projects

---

## Contributing

Contributions, issues, and feature requests are welcome. Feel free to open an issue or submit a PR.

---

## License

_Add your license here (e.g., MIT)._

---

## Author

**Sanjay Surampudi**
GitHub: [@SanjaySurampudi](https://github.com/SanjaySurampudi)
