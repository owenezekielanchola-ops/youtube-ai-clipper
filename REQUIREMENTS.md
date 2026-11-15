# Setup & Requirements Guide (Windows / macOS / Linux)

Follow this guide after **downloading the ZIP** (or cloning the repo) to get **ClipForge AI** running on your computer.

---

## Option 1: Windows Step-by-Step (Download ZIP — No Git Required)

### 1. Download & Unzip
1. On GitHub, click **Code → Download ZIP**.
2. Right-click the `.zip` file → **Extract All...**
3. Open the extracted folder (`youtube-ai-clipper-main`), right-click an empty space inside the folder, and click **"Open in Terminal"** (or type `powershell` in the File Explorer address bar and press **Enter**).

*(Or if you prefer `git clone`:)*
```powershell
git clone https://github.com/owenezekielanchola-ops/youtube-ai-clipper.git
cd youtube-ai-clipper
```

---

### 2. Install Prerequisites (Only if Python, Node.js, or FFmpeg are not installed)
Paste these commands into PowerShell to install everything automatically:
```powershell
winget install --id Python.Python.3.12 -e
winget install --id OpenJS.NodeJS.LTS -e
winget install --id Gyan.FFmpeg -e
```
> **Important:** After installing, **close and reopen your terminal** inside the project folder so Windows updates your `PATH`.

---

### 3. Install App Dependencies & Add Your API Keys
Run these commands inside the root `youtube-ai-clipper` folder:
```powershell
pip install -r backend\requirements.txt imageio-ffmpeg
cd frontend
npm install
cd ..
copy backend\.env.example backend\.env
notepad backend\.env
```

When Notepad opens `backend\.env`, paste your free API keys:
```env
GROQ_API_KEY=their_groq_api_key_here
GEMINI_API_KEY=their_gemini_api_key_here
```
Press **Ctrl + S** to save and close Notepad.

- Get a free **Groq API Key** at: https://console.groq.com/
- Get a free **Gemini API Key** at: https://aistudio.google.com/

---

### 4. Start the App
Run in PowerShell (or double-click `start.bat` in the folder):
```powershell
.\start.bat
```
Wait for both server windows (**Backend on 8000** and **Frontend on 5173**) to start, then open:
- **App UI:** http://localhost:5173

---

### Optional: Fix `"Sign in to confirm you're not a bot"` on YouTube
If YouTube blocks downloads on your network with `"Sign in to confirm you're not a bot"`:
1. Sign into `youtube.com` in **Brave** or **Chrome**.
2. Install **Get cookies.txt LOCALLY** from the Chrome Web Store.
3. On `youtube.com`, click the extension → **Export** and save the file as **`backend\cookies.txt`**.
4. Retry your YouTube link (no restart needed). *(Never share `cookies.txt`—it is ignored by `.gitignore`.)*

---

### 5. Reopening the App Later
You do **not** need to install packages or set `.env` again. Whenever you want to use it again:
- Double-click **`start.bat`** (or run `.\start.bat` in the project folder), then open http://localhost:5173.

Or launch manually in two separate terminals:
```powershell
# Terminal 1 (Backend)
cd backend
python -m uvicorn main:app --reload --port 8000
```
```powershell
# Terminal 2 (Frontend)
cd frontend
npm run dev
```

---

## Option 2: macOS / Linux Setup

1. Install Python 3.10+, Node.js 22+ LTS, and FFmpeg (`brew install ffmpeg` on macOS, `sudo apt install -y ffmpeg` on Ubuntu/Debian).
2. Download/clone the repo, create `backend/.env`, and run:
   ```bash
   cp backend/.env.example backend/.env
   # Add GROQ_API_KEY and GEMINI_API_KEY inside backend/.env
   chmod +x start.sh
   ./start.sh
   ```
3. Open http://localhost:5173 in your browser.
