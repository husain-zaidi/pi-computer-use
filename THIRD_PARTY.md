# Sources and attribution

Retrieved for this implementation on 2026-09-12.

## OpenAI computer-use guide

- https://developers.openai.com/api/docs/guides/tools-computer-use
- The retrieved guide states: “For GPT-6 Astra, we recommend code execution.” It describes persistent Python/PyAutoGUI and JavaScript/Playwright environments, returning text and screenshots, and batching actions to reduce model round trips.
- We implement that **architecture**, using Python's synchronous Playwright API alongside PyAutoGUI in one persistent worker. We do not embed the sample's Responses API loop; pi supplies the agent loop and model authentication.
- This is an independent local extension, not an OpenAI-hosted sandbox or an official pi/OpenAI package.

## OpenAI CUA sample app

- Repository: https://github.com/openai/openai-cua-sample-app
- Inspected commit: `f2a3dc523ae406f9b704f9a420a05402a63b4522`
- Relevant files:
  - `python-app/app/desktop/worker.py`: persistent globals, `log`/`display`, in-memory PNGs, normalized mouse/screenshot coordinates.
  - `javascript-app/src/javascript-worker.ts`: stateful Playwright execution, bounded output and process deadlines.
  - `python-app/app/desktop/release_inputs.py`: **copied verbatim** into `python/release_inputs.py`. Fixed-code, platform-native input release, independent of generated code and PyAutoGUI failsafe.
- License: MIT. Full upstream notice retained in [licenses/openai-cua-sample-app.txt](licenses/openai-cua-sample-app.txt).
- The new worker/client/extension are original implementations of the documented pattern; screenshot normalization is adapted from the Python sample.

## Comparison package

- Gallery: https://pi.dev/packages/@injaneity/pi-computer-use?name=computer+use
- npm: `@injaneity/pi-computer-use@0.5.1`
- Repository: https://github.com/injaneity/pi-computer-use
- Reference checkout inspected: `4b8dbd7eaa13328ab1a8a4b55d0be0b077de7d62`
- **Measured code is the published npm tarball**, not that checkout; its native Windows helper is included.
- Tarball npm SHA-1: `38dbc6421e403f934b38cde66919558f4047eaed`.
- Baseline source and helper are unmodified. They remain in ignored `benchmarks/baseline/package/` solely for local comparison, retaining their MIT license.
- All 11 public tools remain available in the benchmark, including `act_ui` batching and `evaluate_browser`. No deliberate one-action-per-call handicap is imposed.

## Pi APIs

Implementation follows installed pi 0.85.0 documentation/examples:

- `docs/extensions.md`, `docs/packages.md`, `docs/json.md`
- `examples/extensions/hello.ts`
- Runtime schemas verified against the installed TypeScript declarations. Tool images use `{ type: 'image', data, mimeType }`.

Third-party Python/npm dependencies retain their respective upstream licenses. See `requirements.txt`, `package.json` and the npm lockfile for versions.
