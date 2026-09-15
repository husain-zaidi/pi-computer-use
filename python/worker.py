"""Persistent code-execution worker. Process isolation, NOT a security sandbox.

Inspired by OpenAI's CUA sample (see THIRD_PARTY.md). No model/API client here:
pi owns the model conversation; this worker owns Python and browser state.
"""
import base64
import contextlib
import io
import json
import os
import sys
import tempfile
import time
import traceback

from PIL import Image

MAX_CODE = 64 * 1024
MAX_TEXT = 48 * 1024
MAX_LINES = 1900
MAX_SPOOL = 2 * 1024 * 1024
MAX_IMAGES = 8 * 1024 * 1024


class Output:
    def __init__(self):
        self.content = []
        self.text_bytes = 0
        self.lines = 0
        self.image_bytes = 0
        self.images = 0
        self.spool = None
        self.spool_bytes = 0
        self.truncated = False

    def write(self, text):
        size = len(text.encode("utf-8"))
        lines = text.count("\n")
        if self.text_bytes + size > MAX_TEXT or self.lines + lines > MAX_LINES or self.truncated:
            self.truncated = True
            if self.spool is None:
                self.spool = tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", prefix="pi-cua-output-", suffix=".txt", delete=False)
                for item in self.content:
                    if item["type"] == "text":
                        self.spool.write(item["text"])
            if self.spool_bytes + size > MAX_SPOOL:
                raise ValueError("Output exceeded the 2 MiB spill limit; execution stopped.")
            self.spool.write(text)
            self.spool_bytes += size
        elif text:
            if self.content and self.content[-1]["type"] == "text":
                self.content[-1]["text"] += text
            else:
                self.content.append({"type": "text", "text": text})
            self.text_bytes += size
            self.lines += lines
        return len(text)

    def flush(self):
        if self.spool:
            self.spool.flush()

    def log(self, *values):
        self.write(" ".join(str(v) for v in values) + "\n")

    def display(self, image):
        if isinstance(image, Image.Image):
            buf = io.BytesIO()
            image.save(buf, format="PNG")
            image = buf.getvalue()
        if not isinstance(image, bytes) or not image.startswith(b"\x89PNG\r\n\x1a\n"):
            raise TypeError("display expects a PIL image or PNG bytes (e.g. page.screenshot()).")
        with Image.open(io.BytesIO(image)) as check:
            check.verify()
        if self.images >= 4 or self.image_bytes + len(image) > MAX_IMAGES:
            raise ValueError("Image output exceeds four images / 8 MiB per call.")
        self.images += 1
        self.image_bytes += len(image)
        self.content.append({"type": "image", "data": base64.b64encode(image).decode("ascii"), "mimeType": "image/png"})

    def finish(self):
        if self.spool:
            name = self.spool.name
            self.spool.close()
            self.content.append({"type": "text", "text": f"\n[Output truncated to 48 KiB/1900 lines. Full text (up to 2 MiB spill limit) saved to {name}.]"})
        return self.content or [{"type": "text", "text": "Code completed with no output."}]


class Runtime:
    def __init__(self, desktop=False, headed=False):
        self.desktop = desktop
        self.headed = headed or desktop
        self.playwright = None
        self.browser = None
        self.context = None
        self.page = None
        self.pyautogui = None
        self.namespace = {"__name__": "__computer_use__", "time": time, "start_browser": self.start_browser}
        if desktop:
            import pyautogui
            self.pyautogui = pyautogui
            pyautogui.FAILSAFE = True
            pyautogui.PAUSE = 0.1
            # Normalize screenshots to the mouse coordinate space (not a model resize).
            capture = pyautogui.screenshot

            def screenshot(imageFilename=None, region=None, **kwargs):
                image = capture(**kwargs)
                size = tuple(pyautogui.size())
                if image.size != size:
                    image = image.resize(size, Image.Resampling.LANCZOS)
                if region:
                    x, y, w, h = region
                    image = image.crop((x, y, x + w, y + h))
                if imageFilename:
                    image.save(imageFilename)
                return image

            pyautogui.screenshot = screenshot
            self.namespace.update(pyautogui=pyautogui, focus_browser=self.focus_browser)

    def focus_browser(self):
        """Windows-only physical-input guard. Refuse ambiguous titles or denied OS focus."""
        if not self.pyautogui or self.page is None:
            raise RuntimeError("focus_browser requires desktop mode and start_browser().")
        if sys.platform != "win32":
            raise RuntimeError("Automatic native focus is Windows-only. Manually focus the dedicated browser before desktop input.")
        import ctypes
        from ctypes import wintypes
        self.page.bring_to_front()
        title = self.page.title()
        if not title:
            raise RuntimeError("Cannot safely identify a native browser window with an empty page title.")
        matches = [w for w in self.pyautogui.getWindowsWithTitle(title)
                   if w.title in {f"{title} - Chromium", f"{title} - Google Chrome", f"{title} - Google Chrome for Testing", f"{title} - Microsoft Edge"}]
        if len(matches) != 1:
            raise RuntimeError("Native browser title is missing or ambiguous. Close duplicate test windows or focus manually; no physical input sent.")
        window = matches[0]
        get_foreground = ctypes.windll.user32.GetForegroundWindow
        get_foreground.restype = wintypes.HWND
        if window.isMinimized:
            window.restore()
        if get_foreground() != window._hWnd:
            try:
                window.activate()
            except Exception as exc:
                if get_foreground() != window._hWnd:
                    raise RuntimeError("Windows denied foreground focus. Manually focus the dedicated browser; no physical input sent.") from exc
        if get_foreground() != window._hWnd:
            raise RuntimeError("Windows denied foreground focus. Manually focus the dedicated browser; no physical input sent.")
        return {"title": window.title, "focused": True}

    def start_browser(self, url="about:blank"):
        """Open/reuse a dedicated Chromium browser backed by a persistent on-disk profile. Returns page; also exports page/context/browser."""
        if self.context is None:
            from playwright.sync_api import sync_playwright
            self.playwright = sync_playwright().start()
            executable = os.environ.get("PI_CUA_BROWSER_EXECUTABLE")
            # Persistent storage lives at this fixed location across runs (override via PI_CUA_PROFILE_DIR).
            user_data_dir = os.environ.get("PI_CUA_PROFILE_DIR") or os.path.expanduser("~/.pi-cua/persistent")
            kwargs = {
                "headless": not self.headed,
                "viewport": {"width": 1285, "height": 900},
                "device_scale_factor": 1,
            }
            if executable:
                kwargs["executable_path"] = executable
            # launch_persistent_context keeps cookies/storage on disk at user_data_dir.
            self.context = self.playwright.chromium.launch_persistent_context(user_data_dir=user_data_dir, **kwargs)
            self.context.set_default_timeout(10000)
            self.context.set_default_navigation_timeout(20000)
            self.page = self.context.pages[0] if self.context.pages else self.context.new_page()
        self.namespace.update(page=self.page, context=self.context, browser=self.browser)
        if url != "about:blank" or self.page.url == "about:blank":
            self.page.goto(url, wait_until="domcontentloaded")
        return self.page

    def execute(self, code):
        output = Output()
        self.namespace.update(log=output.log, display=output.display)
        error = None
        fatal = False
        started = time.perf_counter()
        try:
            if not isinstance(code, str) or not code.strip() or len(code.encode("utf-8")) > MAX_CODE:
                raise ValueError("code must be nonempty and at most 64 KiB.")
            if self.pyautogui:
                self.pyautogui.FAILSAFE = True
            with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
                exec(compile(code, "<exec_py>", "exec"), self.namespace)
            if self.pyautogui and not self.pyautogui.FAILSAFE:
                raise RuntimeError("Do not disable PyAutoGUI's failsafe.")
        except BaseException as exc:
            error = traceback.format_exc()[-4000:]
            if self.pyautogui and isinstance(exc, self.pyautogui.FailSafeException):
                fatal = True
        finally:
            if self.pyautogui:
                self.pyautogui.FAILSAFE = True
        content = output.finish()
        if error:
            content.append({"type": "text", "text": error})
        return {"content": content, "error": error, "fatal": fatal, "executionMs": round((time.perf_counter() - started) * 1000, 3)}

    def close(self):
        try:
            if self.context is not None:
                self.context.close()
            elif self.browser:
                self.browser.close()
        finally:
            if self.playwright:
                self.playwright.stop()


def emit(value):
    sys.__stdout__.write(json.dumps(value, ensure_ascii=True) + "\n")
    sys.__stdout__.flush()


def main():
    from lifecycle import install_parent_guard
    install_parent_guard()
    runtime = Runtime(desktop="--desktop" in sys.argv, headed="--headed" in sys.argv)
    emit({"ready": True, "pid": os.getpid(), "desktop": runtime.desktop, "headed": runtime.headed})
    try:
        while True:
            line = sys.stdin.buffer.readline(MAX_CODE * 8 + 1)
            if not line:
                break
            if len(line) > MAX_CODE * 8:
                raise ValueError("Oversized protocol request.")
            request = json.loads(line)
            if request.get("operation") == "close":
                break
            if request.get("operation") != "execute":
                emit({"id": request.get("id"), "error": "Unknown operation"})
                continue
            result = runtime.execute(request.get("code"))
            emit({"id": request.get("id"), **result})
            if result["fatal"]:
                break
    finally:
        runtime.close()


if __name__ == "__main__":
    try:
        main()
    except BaseException:
        emit({"error": traceback.format_exc()[-4000:]})
        sys.exit(1)
