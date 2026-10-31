"""Small, free, dependency-light project starters.

`devmind new website|react|api|mobile` writes a real project you can run and
extend: no placeholders that pretend to work, no paid services, no build step
where it is avoidable.

Each starter is plain text in this module (not a zip, not a download), so it
works offline, is auditable, and tests can check it parses.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from collections.abc import Callable


@dataclass(frozen=True)
class Starter:
    kind: str
    description: str
    files: dict[str, str]
    next_steps: tuple[str, ...]
    checks: tuple[str, ...]


def _website() -> Starter:
    html = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Studio — strategy, design, build</title>
  <meta name="description" content="A small studio that designs and builds fast, accessible websites." />
  <link rel="stylesheet" href="styles.css" />
</head>
<body>
  <header class="nav">
    <a class="brand" href="#top">Studio</a>
    <nav aria-label="Main">
      <a href="#work">Work</a>
      <a href="#pricing">Pricing</a>
      <a href="#faq">FAQ</a>
      <a class="cta" href="#contact">Start a project</a>
    </nav>
  </header>

  <main id="top">
    <section class="hero">
      <p class="eyebrow">Design · build · launch</p>
      <h1>Websites that load fast and convert quietly.</h1>
      <p class="lede">Strategy, interface design and production code for small teams. Fixed scope, fixed price, no retainers.</p>
      <div class="actions">
        <a class="button" href="#contact">Book a 20-minute call</a>
        <a class="link" href="#work">See recent work</a>
      </div>
    </section>

    <section id="work" class="grid" aria-labelledby="work-title">
      <h2 id="work-title">Recent work</h2>
      <article class="card"><h3>Northwind</h3><p>B2B storefront, 1.2s LCP on 4G. Stripe + warehouse sync.</p></article>
      <article class="card"><h3>Kite Health</h3><p>Accessibility-first booking flow; WCAG 2.2 AA verified.</p></article>
      <article class="card"><h3>Field Notes</h3><p>Static publication, 14 KB of JS, offline reading.</p></article>
    </section>

    <section id="pricing" class="pricing" aria-labelledby="pricing-title">
      <h2 id="pricing-title">Pricing</h2>
      <article class="card">
        <h3>Landing page</h3>
        <p class="price">₹45,000<span>fixed</span></p>
        <ul><li>Design + build</li><li>Mobile and a11y pass</li><li>2 weeks</li></ul>
      </article>
      <article class="card featured">
        <h3>Full site</h3>
        <p class="price">₹1,20,000<span>fixed</span></p>
        <ul><li>Up to 6 pages</li><li>CMS-ready content</li><li>Analytics + SEO</li></ul>
      </article>
      <article class="card">
        <h3>Product UI</h3>
        <p class="price">₹2,00,000<span>from</span></p>
        <ul><li>Design system</li><li>React front end</li><li>Handover docs</li></ul>
      </article>
    </section>

    <section id="faq" aria-labelledby="faq-title">
      <h2 id="faq-title">FAQ</h2>
      <details><summary>Who writes the code?</summary><p>One senior engineer, with review before launch.</p></details>
      <details><summary>Do you use templates?</summary><p>No. Every page is written for the content and the audience.</p></details>
      <details><summary>What do you need from me?</summary><p>Your copy, brand assets and a decision-maker available for two calls.</p></details>
    </section>

    <section id="contact" class="contact">
      <h2>Start a project</h2>
      <p>Tell us what you are building. Reply within one working day.</p>
      <!-- Replace with your form endpoint (Formspree free tier, Netlify forms, or your API). -->
      <form id="contact-form" novalidate>
        <label>Name <input name="name" autocomplete="name" required /></label>
        <label>Email <input name="email" type="email" autocomplete="email" required /></label>
        <label>Project <textarea name="message" rows="4" required></textarea></label>
        <button class="button" type="submit">Send</button>
        <p class="status" role="status" aria-live="polite"></p>
      </form>
    </section>
  </main>

  <footer>
    <p>© <span id="year">2025</span> Studio. Built as static HTML — no framework, no tracking.</p>
    <button class="theme" id="theme-toggle" type="button" aria-label="Toggle colour theme">◐</button>
  </footer>
  <script src="app.js" defer></script>
</body>
</html>
"""
    css = """/* Premium but light: one accent, generous spacing, real focus states. */
:root {
  --bg: #0d1117; --panel: #141b24; --line: #24303d; --text: #e8eef5;
  --muted: #93a4b8; --accent: #63e6be; --radius: 16px;
  color-scheme: dark;
}
:root[data-theme="light"] {
  --bg: #f7f8fa; --panel: #ffffff; --line: #e2e6ec; --text: #101820;
  --muted: #56657a; --accent: #0b8f6b; color-scheme: light;
}
* { box-sizing: border-box; }
body {
  margin: 0; background: var(--bg); color: var(--text);
  font: 16px/1.6 system-ui, -apple-system, Segoe UI, Roboto, sans-serif;
}
a { color: var(--accent); }
.nav {
  display: flex; align-items: center; justify-content: space-between;
  gap: 16px; padding: 18px clamp(16px, 5vw, 64px);
  position: sticky; top: 0; background: color-mix(in srgb, var(--bg) 88%, transparent);
  backdrop-filter: blur(10px); border-bottom: 1px solid var(--line);
}
.brand { font-weight: 700; letter-spacing: .02em; text-decoration: none; color: var(--text); }
.nav nav { display: flex; gap: 18px; flex-wrap: wrap; }
.nav a { color: var(--muted); text-decoration: none; }
.nav a:hover, .nav a:focus-visible { color: var(--text); }
.cta { border: 1px solid var(--line); padding: 8px 14px; border-radius: 999px; color: var(--text) !important; }
main { padding: 0 clamp(16px, 5vw, 64px) 64px; max-width: 1080px; margin: 0 auto; }
.hero { padding: clamp(56px, 12vw, 120px) 0 40px; }
.eyebrow { color: var(--accent); text-transform: uppercase; letter-spacing: .14em; font-size: 12px; }
h1 { font-size: clamp(34px, 6vw, 60px); line-height: 1.05; margin: 8px 0 16px; }
.lede { color: var(--muted); max-width: 56ch; font-size: clamp(17px, 2.2vw, 20px); }
.actions { display: flex; gap: 16px; flex-wrap: wrap; align-items: center; margin-top: 28px; }
.button {
  display: inline-block; background: var(--accent); color: #04120d; font-weight: 650;
  border: 0; border-radius: 999px; padding: 13px 22px; cursor: pointer; text-decoration: none;
}
.button:hover { filter: brightness(1.06); }
.link { color: var(--text); }
section { padding: 48px 0; border-top: 1px solid var(--line); }
h2 { font-size: clamp(24px, 3vw, 32px); margin: 0 0 20px; }
.grid { display: grid; gap: 16px; }
#work { grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); }
#work h2 { grid-column: 1 / -1; }
.card { background: var(--panel); border: 1px solid var(--line); border-radius: var(--radius); padding: 20px; }
.card h3 { margin: 0 0 8px; }
.card p, .card li { color: var(--muted); }
.pricing { display: grid; gap: 16px; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); }
.pricing h2 { grid-column: 1 / -1; }
.pricing .price { font-size: 30px; color: var(--text); margin: 8px 0 12px; }
.pricing .price span { font-size: 13px; color: var(--muted); margin-left: 8px; }
.featured { border-color: var(--accent); }
details { border: 1px solid var(--line); border-radius: 12px; padding: 14px 16px; margin-bottom: 10px; }
summary { cursor: pointer; font-weight: 600; }
.contact form { display: grid; gap: 14px; max-width: 560px; }
label { display: grid; gap: 6px; color: var(--muted); font-size: 14px; }
input, textarea {
  background: var(--panel); color: var(--text); border: 1px solid var(--line);
  border-radius: 10px; padding: 12px; font: inherit;
}
input:focus-visible, textarea:focus-visible, .button:focus-visible, a:focus-visible {
  outline: 2px solid var(--accent); outline-offset: 2px;
}
.status { color: var(--accent); min-height: 1.4em; }
footer {
  display: flex; justify-content: space-between; gap: 16px; align-items: center;
  padding: 24px clamp(16px, 5vw, 64px); border-top: 1px solid var(--line); color: var(--muted);
}
.theme { background: none; border: 1px solid var(--line); color: var(--text); border-radius: 999px; padding: 6px 12px; cursor: pointer; }
@media (prefers-reduced-motion: no-preference) {
  html { scroll-behavior: smooth; }
}
"""
    js = """// Small progressive enhancements only: the page works without JS.
const root = document.documentElement;
const toggle = document.getElementById("theme-toggle");
const saved = localStorage.getItem("theme");
if (saved) root.dataset.theme = saved;

toggle?.addEventListener("click", () => {
  const next = root.dataset.theme === "light" ? "dark" : "light";
  root.dataset.theme = next;
  localStorage.setItem("theme", next);
});

document.getElementById("year").textContent = new Date().getFullYear();

document.getElementById("contact-form")?.addEventListener("submit", (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const status = form.querySelector(".status");
  if (!form.checkValidity()) {
    status.textContent = "Please fill in every field.";
    form.reportValidity();
    return;
  }
  // No backend by design: point action= at your form endpoint, or send JSON to your API.
  status.textContent = "Thanks — this demo form does not send anything yet. Wire it to your endpoint.";
  form.reset();
});
"""
    readme = """# Website starter

Static, dependency-free, accessible by default.

## Run

```bash
python3 -m http.server 8000      # or: npx serve .
```

Open http://localhost:8000.

## Make it yours

1. Edit `index.html` copy and prices.
2. Replace the form's comment with your endpoint (Formspree free tier, Netlify forms, or your own API).
3. Keep the focus styles and the `aria-live` status region — they are what make it accessible.

## Verify

No build. Check with your browser's Lighthouse (Performance/Accessibility) and:
`npx html-validate index.html` (optional).
"""
    return Starter(
        kind="website",
        description="Static marketing site: hero, work, pricing, FAQ, contact",
        files={"index.html": html, "styles.css": css, "app.js": js, "README.md": readme},
        next_steps=(
            "cd into the folder and run: python3 -m http.server 8000",
            "replace the form action/endpoint before publishing",
        ),
        checks=("python3 -m http.server 8000",),
    )


def _react() -> Starter:
    package = """{
  "name": "studio-app",
  "private": true,
  "version": "0.1.0",
  "type": "module",
  "scripts": { "dev": "vite", "build": "vite build", "preview": "vite preview" },
  "dependencies": { "react": "^19.0.0", "react-dom": "^19.0.0" },
  "devDependencies": { "@vitejs/plugin-react": "^4.3.0", "vite": "^5.4.0" }
}
"""
    vite = """import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({ plugins: [react()] });
"""
    html = """<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>Studio — premium web apps</title>
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="/src/main.jsx"></script>
  </body>
</html>
"""
    main = """import React from "react";
import { createRoot } from "react-dom/client";
import App from "./App.jsx";
import "./styles.css";

createRoot(document.getElementById("root")).render(
  React.createElement(React.StrictMode, null, React.createElement(App))
);
"""
    app = """import { useState } from "react";

const features = [
  { title: "Fast by default", body: "Route-level code splitting and a budget you can enforce." },
  { title: "Accessible", body: "Keyboard, focus and contrast handled in the primitives." },
  { title: "Boring operations", body: "Static build, no servers to babysit." },
];

export default function App() {
  const [email, setEmail] = useState("");
  const [sent, setSent] = useState(false);

  return (
    <main>
      <header className="nav">
        <strong>Studio</strong>
        <a className="cta" href="#contact">Start a project</a>
      </header>

      <section className="hero">
        <p className="eyebrow">React · Vite · no backend required</p>
        <h1>Premium interfaces, shipped in days.</h1>
        <p className="lede">
          This starter is a real app shell: accessible by default, fast to build,
          and easy to wire to your own API.
        </p>
        <div className="grid">
          {features.map((feature) => (
            <article className="card" key={feature.title}>
              <h3>{feature.title}</h3>
              <p>{feature.body}</p>
            </article>
          ))}
        </div>
      </section>

      <section id="contact" className="card">
        <h2>Talk to us</h2>
        <form
          onSubmit={(event) => {
            event.preventDefault();
            // Replace with your endpoint. No analytics, no third-party scripts.
            setSent(true);
          }}
        >
          <label>
            Email
            <input
              type="email"
              required
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              autoComplete="email"
            />
          </label>
          <button className="button" type="submit">Request a call</button>
        </form>
        <p role="status" aria-live="polite">{sent ? "Saved locally — wire your endpoint next." : ""}</p>
      </section>
    </main>
  );
}
"""
    css = """* { box-sizing: border-box; }
body { margin: 0; background: #0d1117; color: #e8eef5; font: 16px/1.6 system-ui, sans-serif; }
main { max-width: 1040px; margin: 0 auto; padding: 0 clamp(16px, 5vw, 48px) 48px; }
.nav { display: flex; justify-content: space-between; padding: 20px 0; border-bottom: 1px solid #24303d; }
.cta, .button { background: #63e6be; color: #04120d; padding: 10px 16px; border-radius: 999px; text-decoration: none; border: 0; font-weight: 650; cursor: pointer; }
.hero { padding: 72px 0 24px; }
.eyebrow { color: #63e6be; text-transform: uppercase; letter-spacing: .14em; font-size: 12px; }
h1 { font-size: clamp(32px, 6vw, 56px); line-height: 1.05; margin: 8px 0 12px; }
.lede { color: #93a4b8; max-width: 60ch; }
.grid { display: grid; gap: 16px; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); margin-top: 32px; }
.card { background: #141b24; border: 1px solid #24303d; border-radius: 16px; padding: 20px; }
.card p { color: #93a4b8; }
section { margin-top: 32px; }
label { display: grid; gap: 6px; color: #93a4b8; font-size: 14px; }
input { background: #0d1117; border: 1px solid #24303d; color: #e8eef5; border-radius: 10px; padding: 12px; font: inherit; }
input:focus-visible, .button:focus-visible, a:focus-visible { outline: 2px solid #63e6be; outline-offset: 2px; }
form { display: grid; gap: 12px; max-width: 420px; }
"""
    readme = """# React starter (Vite)

```bash
npm install
npm run dev        # http://localhost:5173
npm run build      # static files in dist/
```

Wire the form to your own endpoint. Nothing here calls a third-party service.
"""
    return Starter(
        kind="react",
        description="React 19 + Vite app shell with a premium landing section",
        files={
            "package.json": package,
            "vite.config.js": vite,
            "index.html": html,
            "src/main.jsx": main,
            "src/App.jsx": app,
            "src/styles.css": css,
            "README.md": readme,
        },
        next_steps=("npm install", "npm run dev"),
        checks=("npm run build",),
    )


def _api() -> Starter:
    main = '''"""A small, honest FastAPI service: health + in-memory items.

Replace the in-memory store with your database; keep the tests passing.
"""

from __future__ import annotations

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

app = FastAPI(title="Items API", version="0.1.0")


class ItemIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    price: float = Field(ge=0)


class Item(ItemIn):
    id: int


_items: dict[int, Item] = {}
_next_id = 1


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/items")
def list_items() -> list[Item]:
    return list(_items.values())


@app.post("/items", status_code=201)
def create_item(payload: ItemIn) -> Item:
    global _next_id
    item = Item(id=_next_id, **payload.model_dump())
    _items[item.id] = item
    _next_id += 1
    return item


@app.get("/items/{item_id}")
def get_item(item_id: int) -> Item:
    if item_id not in _items:
        raise HTTPException(status_code=404, detail="item not found")
    return _items[item_id]


@app.delete("/items/{item_id}", status_code=204)
def delete_item(item_id: int) -> None:
    if _items.pop(item_id, None) is None:
        raise HTTPException(status_code=404, detail="item not found")
'''
    tests = """from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health() -> None:
    assert client.get("/health").json() == {"status": "ok"}


def test_create_and_fetch_item() -> None:
    created = client.post("/items", json={"name": "tea", "price": 120.5})
    assert created.status_code == 201
    item = created.json()
    assert client.get(f"/items/{item['id']}").json()["name"] == "tea"


def test_missing_item_is_404() -> None:
    assert client.get("/items/9999").status_code == 404
"""
    pyproject = """[project]
name = "items-api"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = ["fastapi>=0.110", "uvicorn[standard]>=0.29", "pydantic>=2"]

[tool.pytest.ini_options]
pythonpath = ["."]
testpaths = ["tests"]
"""
    readme = """# API starter (FastAPI)

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]" 2>/dev/null || pip install fastapi "uvicorn[standard]" pydantic pytest httpx
pytest -q
uvicorn app.main:app --reload
```

Endpoints: `GET /health`, `GET/POST /items`, `GET/DELETE /items/{id}`.
The store is in memory on purpose - replace it with your database and keep the
tests. No auth yet: add it before exposing anything publicly.
"""
    return Starter(
        kind="api",
        description="FastAPI service with health, CRUD and passing tests",
        files={
            "app/__init__.py": "",
            "app/main.py": main,
            "tests/test_api.py": tests,
            "pyproject.toml": pyproject,
            "README.md": readme,
        },
        next_steps=(
            "python3 -m venv .venv && . .venv/bin/activate",
            'pip install fastapi "uvicorn[standard]" pydantic pytest httpx',
            "pytest -q",
            "uvicorn app.main:app --reload",
        ),
        checks=("pytest -q",),
    )


def _mobile() -> Starter:
    pubspec = """name: starter_app
description: A Flutter starter with a real navigation shell.
publish_to: none
version: 0.1.0
environment:
  sdk: ">=3.4.0 <4.0.0"
dependencies:
  flutter:
    sdk: flutter
dev_dependencies:
  flutter_test:
    sdk: flutter
  flutter_lints: ^4.0.0
flutter:
  uses-material-design: true
"""
    main = """import 'package:flutter/material.dart';

void main() => runApp(const StarterApp());

class StarterApp extends StatelessWidget {
  const StarterApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'Starter',
      debugShowCheckedModeBanner: false,
      theme: ThemeData(
        colorScheme: ColorScheme.fromSeed(
          seedColor: const Color(0xFF63E6BE),
          brightness: Brightness.dark,
        ),
        useMaterial3: true,
      ),
      home: const HomeShell(),
    );
  }
}

class HomeShell extends StatefulWidget {
  const HomeShell({super.key});

  @override
  State<HomeShell> createState() => _HomeShellState();
}

class _HomeShellState extends State<HomeShell> {
  int _index = 0;

  static const _titles = ['Overview', 'Items', 'Settings'];

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: Text(_titles[_index])),
      body: switch (_index) {
        0 => const _Overview(),
        1 => const _Items(),
        _ => const _Settings(),
      },
      bottomNavigationBar: NavigationBar(
        selectedIndex: _index,
        onDestinationSelected: (value) => setState(() => _index = value),
        destinations: const [
          NavigationDestination(icon: Icon(Icons.dashboard_outlined), label: 'Overview'),
          NavigationDestination(icon: Icon(Icons.list_alt_outlined), label: 'Items'),
          NavigationDestination(icon: Icon(Icons.settings_outlined), label: 'Settings'),
        ],
      ),
    );
  }
}

class _Overview extends StatelessWidget {
  const _Overview();

  @override
  Widget build(BuildContext context) {
    return ListView(
      padding: const EdgeInsets.all(16),
      children: [
        Card(
          child: Padding(
            padding: const EdgeInsets.all(16),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text('Welcome', style: Theme.of(context).textTheme.titleLarge),
                const SizedBox(height: 8),
                const Text('Replace this with data from your API. '
                    'Keep the navigation shell and the theme.'),
              ],
            ),
          ),
        ),
      ],
    );
  }
}

class _Items extends StatelessWidget {
  const _Items();

  @override
  Widget build(BuildContext context) {
    final items = List.generate(12, (i) => 'Item ${i + 1}');
    return ListView.separated(
      itemCount: items.length,
      separatorBuilder: (_, __) => const Divider(height: 1),
      itemBuilder: (context, index) => ListTile(
        leading: const Icon(Icons.check_circle_outline),
        title: Text(items[index]),
        subtitle: const Text('Local placeholder'),
        onTap: () => ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text('Tapped ${items[index]}')),
        ),
      ),
    );
  }
}

class _Settings extends StatefulWidget {
  const _Settings();

  @override
  State<_Settings> createState() => _SettingsState();
}

class _SettingsState extends State<_Settings> {
  bool _offlineOnly = true;

  @override
  Widget build(BuildContext context) {
    return ListView(
      children: [
        SwitchListTile(
          title: const Text('Offline only'),
          subtitle: const Text('No network calls when enabled'),
          value: _offlineOnly,
          onChanged: (value) => setState(() => _offlineOnly = value),
        ),
      ],
    );
  }
}
"""
    test = """import 'package:flutter_test/flutter_test.dart';
import 'package:starter_app/main.dart';

void main() {
  testWidgets('shell navigates between tabs', (tester) async {
    await tester.pumpWidget(const StarterApp());
    expect(find.text('Overview'), findsWidgets);
    await tester.tap(find.text('Settings'));
    await tester.pumpAndSettle();
    expect(find.text('Offline only'), findsOneWidget);
  });
}
"""
    readme = """# Flutter starter

```bash
flutter create . --project-name starter_app   # once, if platform folders are missing
flutter pub get
flutter test
flutter run
```

`lib/main.dart` holds a Material 3 shell with three tabs and a real widget test.
Keep the theme and the navigation; replace the placeholder lists with your data.
"""
    return Starter(
        kind="mobile",
        description="Flutter (Material 3) app shell with navigation and a widget test",
        files={
            "pubspec.yaml": pubspec,
            "lib/main.dart": main,
            "test/widget_test.dart": test,
            "README.md": readme,
        },
        next_steps=(
            "flutter create . --project-name starter_app",
            "flutter pub get",
            "flutter test",
        ),
        checks=("flutter test",),
    )


_BUILDERS: dict[str, Callable[[], Starter]] = {
    "website": _website,
    "react": _react,
    "api": _api,
    "mobile": _mobile,
}


def available() -> dict[str, str]:
    return {kind: builder().description for kind, builder in _BUILDERS.items()}


def starter(kind: str) -> Starter:
    if kind not in _BUILDERS:
        raise KeyError(kind)
    return _BUILDERS[kind]()


def write_starter(kind: str, destination: str | Path, *, force: bool = False) -> list[Path]:
    """Write a starter into ``destination``; never overwrites without ``force``."""
    target = Path(destination).expanduser()
    if target.exists() and any(target.iterdir()) and not force:
        raise FileExistsError(f"{target} is not empty (use --force to write anyway)")
    template = starter(kind)
    written: list[Path] = []
    for relative, content in template.files.items():
        path = target / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        written.append(path)
    return written
