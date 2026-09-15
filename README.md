# pi-computer-use

A code-first [pi](https://pi.dev) extension with **persistent Python + Playwright + opt-in PyAutoGUI**. Inspired by the [OpenAI CUA sample](https://github.com/openai/openai-cua-sample-app) and the [computer-use guide](https://developers.openai.com/api/docs/guides/tools-computer-use).

Instead of a separate model call for every click, `exec_py` lets the model inspect the UI, run a short sequence/loop, and return text or screenshots. Pi owns the model conversation and authentication; the Python worker owns the browser and REPL state. There is no separate OpenAI API client or extra API key.

### Why this is the best computer-use interface

The measured comparison tells the story. On an identical local form-filling task, `pi-computer-use` finished with **2 tool calls and 3 model turns** where the baseline [`@injaneity/pi-computer-use`](https://www.npmjs.com/package/@injaneity/pi-computer-use) needed **13 calls and 14 turns**, at a median wall time of **19.89 s vs 64.93 s** — a **3.26× speedup** 

| | **pi-computer-use** | @injaneity/pi-computer-use |
|---|---|---|
| Tool calls per task (measured) | **2** | 13 |
| Model turns (measured) | **3** | 14 |
| Extra API key or model loop | None — Pi owns conversation + auth | OpenAI key + separate client | 
| Desktop input | Opt-in PyAutoGUI, verified focus refusal, failsafe intact | Raw mouse/keyboard hooks |
| Frontier-model fit | Tuned for GPT-6-class long-horizon UI reasoning | Generic |

Tested for DMV appointment task found in [Astra's blog](https://openai.com/index/gpt-6-astra/?video=1223356203)

| | **pi-computer-use** | @injaneity/pi-computer-use |
|---|---|---|
|Time|7m 46s| 13m 59s |
|Tool Calls|60|119|
|Tool errors| 1| 10|

The interface is shaped for the frontier: one grounded sequence of actions, verified by screenshots, instead of click-by-click tool chatter. That is what agentic computer use should look like — and it is the direction every model provider's own samples are already converging on.

## Setup

```powershell
# Install the published package into pi (extension loads directly from TypeScript).
pi install npm:@husain-zaidi/pi-computer-use
```

Or install straight from the public git repo:

```
pi install git:github.com/husain-zaidi/pi-computer-use
```

`pi install` runs the package's `postinstall`, which handles the Python side automatically: it creates `.venv`, installs `requirements.txt` into it, and downloads Playwright's Chromium. If you'd rather do that by hand (or `PI_CUA_SKIP_POSTINSTALL=1` was set), run once in the package directory:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m playwright install chromium
```

The extension loads directly from TypeScript; `npm install` is only needed for local development/tests. Core extension imports are provided by pi. To register it permanently for one project, run `pi install -l C:\Dev\thirdparty\pi-computer-use` from that project. This repository does **not** install itself globally.

In an interactive session, switch modes with the command:

```
/computer-use browser   # dedicated headed Playwright browser only
/computer-use desktop   # + PyAutoGUI screenshots, mouse and keyboard
/computer-use           # status
/computer-use reset     # discard Python/browser state
```

Set PI_CUA_PROFILE_DIR for persistent profile location. It defaults to ~/.pi-cua/persistent

The dedicated Playwright browser is always headed; there is no headless mode. The first `exec_py` call asks for code-execution consent in the interactive session.

On Linux/macOS, create `.venv` the same way and use `.venv/bin/python`. PyAutoGUI requires a graphical desktop (X11 on Linux; Accessibility and Screen Recording permissions on macOS). Native focus assistance and the measured benchmark were tested on Windows only.

## Tool and runtime

`exec_py({ code, timeout_ms? })` executes synchronous Python; no `await` is needed. The default deadline is 60 seconds, including lazy startup; maximum 120 seconds.

Available globals:

| Global | Purpose |
|---|---|
| `start_browser(url='about:blank')` | Launch/reuse dedicated Chromium; return and export `page`, `context`, `browser` |
| `page`, `context`, `browser` | Python Playwright **sync** API, available after `start_browser()` |
| `log(*values)` / `print(...)` | Return text to the model |
| `display(image)` | Return a Pillow image or PNG bytes to pi as an image block |
| `time` | Python time module |
| `pyautogui` | Desktop screenshots, mouse and keyboard; preloaded in desktop mode (`/computer-use desktop`) |
| `focus_browser()` | Windows desktop helper: identify a unique native browser title, request foreground focus, then verify it. Refuses ambiguity/denied focus. |

Example first call:

```python
start_browser('http://127.0.0.1:8080')
log(page.locator('body').aria_snapshot())
display(page.screenshot())
```

After observing that these controls exist:

```python
page.get_by_label('Full name', exact=True).fill('Ada Lovelace')
page.get_by_label('Email', exact=True).fill('ada@example.test')
# Fill the other observed fields, then submit only if user-authorized.
page.get_by_role('button', name='Register', exact=True).click()
page.get_by_role('status').filter(has_text='Registration complete').wait_for()
log(page.get_by_role('status').inner_text())
display(page.screenshot())
```

Desktop example, after inspecting the screen and selecting the intended field:

```python
focus_browser()  # Windows: do not assume page.bring_to_front() grants native keyboard focus
page.get_by_label('Full name', exact=True).click()
pyautogui.write('Ada Lovelace', interval=0.01)
log(page.get_by_label('Full name', exact=True).input_value())
```

Use `display(pyautogui.screenshot())` to observe the desktop. Capture is normalized to the mouse coordinate space; displayed images are not arbitrarily resized. PyAutoGUI's `write()` is not a general Unicode text API; prefer Playwright `fill()` for Unicode browser input.

### Configuration

- `/computer-use browser|desktop`: switch modes; switching restarts the worker on the next call.
- `/computer-use reset`: wait for idle, close the owned browser/worker, discard REPL variables.
- `PI_CUA_PYTHON`: override interpreter executable. Default: repository `.venv`, otherwise `python`.
- `PI_CUA_BROWSER_EXECUTABLE`: optional Chromium-family executable override.

The dedicated Playwright browser is always headed; there is no headless mode.

One worker per extension session, started only on the first authorized tool call. Tool calls are serialized, including when pi requests them concurrently. Normal Python exceptions preserve state for correction; timeouts, cancellation and PyAutoGUI failsafe stop the worker. Reset before retrying. Reload/session replacement shuts it down; tree navigation discards runtime state. Resuming a conversation does **not** restore a browser or Python variables.

Text is limited to 48 KiB/1,900 lines per result, with a temporary spill file for overflow (2 MiB cap). Images are limited to four/8 MiB per call. Screenshots stay in memory unless the caller explicitly writes them; pi can persist returned images in its session logs. Timing details include worker execution and parent round-trip milliseconds.

## Safety and limitations

**This is unrestricted local Python, not a security sandbox.** A separate process, explicit consent and a separate browser profile are not OS isolation. The model can import modules, read files, make network requests and spawn processes with your user permissions. Desktop mode (`/computer-use desktop`) controls convenience globals, not an adversarial security boundary: unrestricted code could import PyAutoGUI itself.

Use a disposable VM/desktop and accounts you control. Do not browse sensitive accounts or leave unrelated windows visible. Page text and screenshots are untrusted task data, not instructions; the tool guidance forbids following page-borne instructions to access unrelated files or credentials. That guidance is **not** an enforceable per-action permission system. Require user approval before sensitive submissions, external communications, purchases or destructive actions.

PyAutoGUI can affect the wrong app if focus changes. The Windows `focus_browser()` guard checks focus at call time, not throughout a later script. Use a dedicated desktop and do not interact with it concurrently. Windows may deny foreground activation; stop or manually focus the dedicated browser, rather than typing blindly. Moving the mouse to a failsafe corner interrupts the next PyAutoGUI action; keep `FAILSAFE` enabled.

The parent enforces timeouts and kills the owned process tree. Windows cleanup tracks both the venv launcher and actual interpreter PID and releases held inputs through a separate, fixed-code helper. A parent-death watchdog also attempts cleanup. Cleanup is best-effort, not a sandbox or a guarantee against arbitrary generated processes escaping/reparenting. If the host crashes, verify no keys/buttons remain held and close leftover test browsers. Manual emergency release:

```powershell
.\.venv\Scripts\python.exe python\release_inputs.py
```

No existing browser profiles are attached; no CDP listener is exposed by this extension. Code can still navigate to any origin. No external action was needed for the included local form test.


