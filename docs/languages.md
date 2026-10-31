# Language packs

DevMind ships a pack per ecosystem so "correct" means the same thing in Python and in Rust: the
commands the project actually uses, the style notes worth injecting into a prompt, and the pitfalls
assistants repeat most often.

List what applies to a project:

```bash
devmind languages                 # current directory
devmind languages --root ~/code/web
devmind languages --root . --json
```

```
language    files  test          lint            typecheck       docs
----------  -----  ------------  --------------  --------------  --------------------------------
TypeScript  42     npm test      npx eslint .    npx tsc --noEmit https://www.typescriptlang.org/docs/
```

## Included packs

| id | Ecosystem | Test | Lint | Type-check | Format |
|---|---|---|---|---|---|
| `python` | Python | `pytest -q` | `ruff check .` | `mypy .` | `ruff format .` |
| `javascript` | JS/JSX | `npm test` | `npx eslint .` | — | `npx prettier --write .` |
| `typescript` | TS/TSX | `npm test` | `npx eslint .` | `npx tsc --noEmit` | `npx prettier --write .` |
| `java` | Java | `mvn test` | `checkstyle:check` | compiled | `spotless:apply` |
| `kotlin` | Kotlin | `./gradlew test` | `detekt` | compiled | `ktlintFormat` |
| `c` | C | `make test` | `cppcheck .` | compiled | `clang-format` |
| `cpp` | C++ | `ctest` | `clang-tidy` | compiled | `clang-format` |
| `csharp` | C# | `dotnet test` | `dotnet format` | compiled | `dotnet format` |
| `go` | Go | `go test ./...` | `go vet ./...` | `go build ./...` | `gofmt -w .` |
| `rust` | Rust | `cargo test` | `cargo clippy` | `cargo check` | `cargo fmt` |
| `php` | PHP | `vendor/bin/phpunit` | `phpstan` | — | `php-cs-fixer` |
| `ruby` | Ruby | `bundle exec rspec` | `rubocop` | — | `rubocop -a` |
| `swift` | Swift | `swift test` | `swiftlint` | compiled | `swiftformat` |
| `dart` | Dart/Flutter | `flutter test` | `dart analyze` | — | `dart format` |
| `sql` | SQL | *(none — say so)* | — | — | — |
| `bash` | Bash | `bats tests/` | `shellcheck` | — | `shfmt` |
| `powershell` | PowerShell | `Invoke-Pester` | `ScriptAnalyzer` | — | — |
| `html` | HTML/CSS | *(none)* | `stylelint` | — | `prettier` |

Two deliberate decisions:

* **A pack never installs anything.** The commands listed are what the project would use; DevMind only
  executes what `devmind.verify.detect_checks` recognises in *this* project.
* **SQL says it has no runner.** Presenting one would be the exact kind of invention this project bans.

## How detection works

1. File extensions by count (`detect_languages`) — evidence, not a guess.
2. Config files that identify an ecosystem (`pyproject.toml`, `tsconfig.json`, `go.mod`, `Cargo.toml`,
   `pom.xml`, `composer.json`, `Gemfile`, `Package.swift`, `pubspec.yaml`, `CMakeLists.txt`).
3. TypeScript wins over JavaScript when both appear (a `.tsx` project is a TS project).

`devmind.languages.project_packs(root)` returns the packs; `style_block(root)` turns the top three into
a compact block for prompts ("PROJECT LANGUAGE PACKS…" plus pitfalls). That block is what stops
generic advice like "use `dict[key]`" when the project is TypeScript, or `pip install` when the repo
uses `uv`.

## Adding a language

Packs are data in `devmind/languages.py`. Add one entry to the `LANGUAGE_PACKS` tuple:

```python
_pack(
    id="elixir",
    name="Elixir",
    extensions=(".ex", ".exs"),
    config_files=("mix.exs",),
    test="mix test",
    lint="mix credo",
    format="mix format",
    docs="https://hexdocs.pm/elixir/",
    notes=("Use the project's OTP version; do not assume Phoenix.",),
    pitfalls=("Processes that never terminate.",),
),
```

Then add `tests/test_languages.py` coverage for detection. No other file changes: the CLI table,
`style_block` and the JSON output pick it up automatically.
