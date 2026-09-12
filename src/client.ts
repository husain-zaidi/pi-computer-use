import { spawn, type ChildProcessWithoutNullStreams } from "node:child_process";
import { performance } from "node:perf_hooks";
import { dirname, join } from "node:path";

export type Content = { type: "text"; text: string } | { type: "image"; data: string; mimeType: "image/png" };
export interface Reply {
  content: Content[];
  error?: string;
  fatal?: boolean;
  executionMs: number;
  roundTripMs?: number;
}
export interface Options {
  python: string;
  worker: string;
  cwd: string;
  desktop?: boolean;
  headed?: boolean;
  releaseScript?: string;
  startupMs?: number;
}

/** One serialized worker per extension session. A cancelled/dead worker requires explicit reset. */
export class ComputerRuntime {
  private child?: ChildProcessWithoutNullStreams;
  private ready?: Promise<void>;
  private tail: Promise<unknown> = Promise.resolve();
  private nextId = 0;
  private workerPid?: number;
  private stopped = false;
  private closePromise?: Promise<void>;
  private pending?: { id: number; resolve: (reply: Reply) => void; reject: (error: Error) => void };
  private stderr = "";

  constructor(private readonly options: Options) {}

  get status() { return this.stopped ? "stopped (reset required)" : this.child ? "running" : "not started"; }

  execute(code: string, timeoutMs: number, signal?: AbortSignal): Promise<Reply> {
    const run = this.tail.then(async () => {
      signal?.throwIfAborted();
      if (this.stopped) throw new Error("Computer runtime stopped. Use /computer-use reset before retrying.");
      if (!code.trim() || Buffer.byteLength(code) > 65536) throw new Error("code must be nonempty and at most 64 KiB.");
      const started = performance.now();
      let timer: NodeJS.Timeout | undefined;
      let abort: (() => void) | undefined;
      const interrupted = new Promise<never>((_, reject) => {
        const stop = (message: string) => {
          void this.close(true).then(() => reject(new Error(message)), error => reject(new Error(`${message}; cleanup failed: ${error}`)));
        };
        timer = setTimeout(() => stop(`Computer code exceeded ${timeoutMs} ms; worker stopped, state discarded.`), timeoutMs);
        abort = () => stop("Computer code cancelled; worker stopped, state discarded.");
        signal?.addEventListener("abort", abort, { once: true });
      });
      try {
        const work = async () => {
          await this.start();
          signal?.throwIfAborted();
          if (this.stopped) throw new Error("Worker stopped during startup.");
          const id = ++this.nextId;
          const reply = await new Promise<Reply>((resolve, reject) => {
            this.pending = { id, resolve, reject };
            this.child!.stdin.write(JSON.stringify({ id, operation: "execute", code }) + "\n", error => {
              if (error) this.fail(error);
            });
          });
          reply.roundTripMs = performance.now() - started;
          if (reply.fatal) await this.close(true);
          return reply;
        };
        return await Promise.race([work(), interrupted]);
      } finally {
        if (timer) clearTimeout(timer);
        if (abort) signal?.removeEventListener("abort", abort);
      }
    });
    this.tail = run.catch(() => undefined);
    return run;
  }

  private start(): Promise<void> {
    if (this.ready) return this.ready;
    this.ready = new Promise<void>((resolve, reject) => {
      const args = ["-u", this.options.worker];
      if (this.options.desktop) args.push("--desktop");
      if (this.options.headed) args.push("--headed");
      const child = spawn(this.options.python, args, {
        cwd: this.options.cwd, windowsHide: true, detached: process.platform !== "win32",
        env: { ...process.env, PYTHONIOENCODING: "utf-8", PI_CUA_PARENT_PID: String(process.pid) }, stdio: "pipe",
      });
      this.child = child;
      let buffer = "";
      let initialized = false;
      const startup = setTimeout(() => {
        reject(new Error(`Python startup timed out. ${this.stderr}`));
        void this.close(true).catch(() => undefined);
      }, this.options.startupMs ?? 15000);
      child.stdout.setEncoding("utf8");
      child.stderr.setEncoding("utf8");
      child.stderr.on("data", (chunk: string) => { this.stderr = (this.stderr + chunk).slice(-4000); });
      child.stdin.on("error", error => this.fail(error));
      child.on("error", error => {
        clearTimeout(startup);
        reject(error);
        this.fail(error);
      });
      child.on("exit", (code, signal) => {
        clearTimeout(startup);
        const error = new Error(`Python worker exited (${code ?? signal}). ${this.stderr}`);
        reject(error);
        this.fail(error);
        // Includes unexpected exits while a desktop key could still be held.
        void this.close(true).catch(() => undefined);
      });
      child.stdout.on("data", (chunk: string) => {
        buffer += chunk;
        if (Buffer.byteLength(buffer) > 16 * 1024 * 1024) {
          reject(new Error("Worker protocol output exceeded 16 MiB."));
          void this.close(true).catch(() => undefined);
          return;
        }
        let newline: number;
        while ((newline = buffer.indexOf("\n")) >= 0) {
          const line = buffer.slice(0, newline);
          buffer = buffer.slice(newline + 1);
          try {
            const reply = JSON.parse(line);
            if (!initialized) {
              if (!reply.ready) throw new Error(reply.error ?? "Invalid worker handshake.");
              this.workerPid = Number.isSafeInteger(reply.pid) ? reply.pid : undefined;
              initialized = true;
              clearTimeout(startup);
              resolve();
            } else if (this.pending && reply.id === this.pending.id) {
              const pending = this.pending;
              this.pending = undefined;
              if (!Array.isArray(reply.content)) pending.reject(new Error(reply.error ?? "Invalid worker response."));
              else pending.resolve(reply);
            } else {
              throw new Error("Unexpected worker response ID.");
            }
          } catch (error) {
            const failure = error instanceof Error ? error : new Error(String(error));
            clearTimeout(startup);
            reject(failure);
            this.fail(failure);
            void this.close(true).catch(() => undefined);
          }
        }
      });
    });
    return this.ready;
  }

  private fail(error: Error) {
    this.stopped = true;
    this.pending?.reject(error);
    this.pending = undefined;
  }

  close(force = false): Promise<void> {
    if (this.closePromise) return this.closePromise;
    this.stopped = true;
    this.closePromise = this.cleanup(force);
    return this.closePromise;
  }

  private async cleanup(force: boolean): Promise<void> {
    const child = this.child;
    if (!child) return;
    if (!force && !this.pending && child.exitCode === null && child.signalCode === null) {
      child.stdin.end(JSON.stringify({ operation: "close" }) + "\n");
      await new Promise<void>(resolve => {
        const timer = setTimeout(resolve, 2000);
        child.once("exit", () => { clearTimeout(timer); resolve(); });
      });
    }
    if (child.pid && child.exitCode === null && child.signalCode === null) {
      if (process.platform === "win32") {
        await runProcess("taskkill", ["/PID", String(child.pid), "/T", "/F"], 5000);
      } else {
        try { process.kill(-child.pid, "SIGKILL"); } catch (error) {
          if ((error as NodeJS.ErrnoException).code !== "ESRCH") throw error;
        }
      }
    }
    this.fail(new Error("Computer runtime stopped; state discarded."));
    if (process.platform === "win32" && child.pid) {
      const roots = [...new Set([child.pid, this.workerPid].filter((pid): pid is number => pid !== undefined))];
      const code = await runProcess(this.options.python, [join(dirname(this.options.worker), "cleanup.py"), ...roots.map(String)], 5000);
      if (code !== 0) throw new Error("Owned-process cleanup failed; check leftover test browsers before resetting.");
    }
    if (this.options.desktop && this.options.releaseScript) {
      const code = await runProcess(this.options.python, [this.options.releaseScript], 5000);
      if (code !== 0) throw new Error("Desktop input release failed. Check held keys/buttons before restarting.");
    }
  }
}

function runProcess(command: string, args: string[], timeout: number): Promise<number | null> {
  return new Promise((resolve, reject) => {
    const child = spawn(command, args, { windowsHide: true, stdio: "ignore" });
    const timer = setTimeout(() => { child.kill(); reject(new Error(`${command} cleanup timed out`)); }, timeout);
    child.once("error", error => { clearTimeout(timer); reject(error); });
    child.once("exit", code => { clearTimeout(timer); resolve(code); });
  });
}
