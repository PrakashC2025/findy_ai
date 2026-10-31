# Adding a language

DevMind's routing and prompt guidance are language-aware but not language-specific: nothing in
the core assumes Python. Supporting a new language takes three small edits.

## 1. Detection

`devmind/router.py` has two tables:

```python
_EXT_TO_LANG = {".py": "python", ".ex": "elixir", ...}   # file extensions
_LANG_HINTS = {"elixir": ("elixir", ".ex", "phoenix"), ...}  # keywords and idioms
```

Add an extension and a handful of distinctive hints (framework names, common keywords). Hints are
weighted by length, so `.cs` beats `.c` and `typescript` beats `.ts`.

Test it:

```python
from devmind.router import detect_language
detect_language("please fix lib/app.ex")   # 'elixir'
```

Add a case to `tests/test_router.py::test_language_detection`.

## 2. Task vocabulary

If the language has its own vocabulary for tasks (e.g. "mix task", "GenServer", "ecto migration"),
add it to the relevant regex in `router.py` (`_TEST_RE`, `_SHELL_RE`, `_BUG_FIX`/`_ERROR_RE`, …).
Keep it specific: a false positive misroutes the prompt.

## 3. In step 2+ (indexer and verification)

When the code indexer lands, each language needs a chunker and a verifier:

| Work | Where it will live |
|---|---|
| AST-aware chunking | `devmind/index/chunkers/<lang>.py` (Python `ast` first, tree-sitter for others) |
| Import/symbol extraction | same module; feeds existence checks |
| Compile / lint / test command | `devmind/verify/profiles.py` — e.g. Elixir: `mix compile`, `mix test`; Rust: `cargo check`, `cargo test`; Go: `go vet`, `go test ./...` |
| Format | optional; only run what the project already configures |

Until then, DevMind still answers in that language — it just cannot execute the result.

## Checklist

- [ ] extension and hints added
- [ ] one detection test
- [ ] one classification test if new vocabulary was added
- [ ] docs updated (`docs/adding-a-language.md`)
- [ ] `devmind ask` in that language returns the right task type and model tier
