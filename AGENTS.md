# AGENTS.md - MinecraftModTranslator

## Project Overview

Minecraft MOD/Modpackの日本語化を支援するPySide6デスクトップアプリケーション。
OpenRouter APIでAI翻訳を行い、リソースパック形式で出力する。

## Build / Run Commands

```bash
# Run the application
python main.py

# Build standalone EXE (PyInstaller)
pip install pyinstaller
pyinstaller --noconfirm --onefile --windowed --name "MinecraftModTranslator" --add-data "ui/styles.qss;ui" main.py

# Install dependencies
pip install PySide6 requests ftb_snbt_lib
```

## Test Commands

No formal test framework is configured. Test files are gitignored (`test_*.py`).
Manual testing: run `python main.py` and verify UI behavior.
No lint/typecheck commands are configured.

## Project Structure

```
main.py                          # Entry point, PyInstaller frozen handling
logic/
  translator.py                  # AI translation (OpenRouter API), variable protection, validation
  file_handler.py                # ZIP/JAR reading, resource pack generation, escape normalization
  resource_pack_handler.py       # Resource pack import (async QThread)
  translation_memory.py          # Compat wrapper for SQLite-backed translation memory
  translation_memory_v2.py       # SQLite-based translation memory with context, upsert, batch ops
  glossary.py                    # Custom glossary (JSON persistence)
  term_extractor.py              # AI term extraction for glossary
  ftbquest_handler.py            # FTB Quests SNBT parsing/export (optional: ftb_snbt_lib)
  datapack_handler.py            # Datapack category mapping and translation key generation
ui/
  main_window.py                 # Main window, MOD list, translation orchestration, batch ops
  editor_widget.py               # Translation table with filter/search, undo/redo (QUndoStack)
  settings_dialog.py             # API key, model, parallel count settings (QSettings persistence)
  glossary_dialog.py             # Glossary CRUD with import/export
  term_extraction_dialog.py      # Review extracted terms
  styles.qss                     # Dark theme (VS Code-inspired)
  default_glossary.md            # 684-line Minecraft terminology glossary (EN→JA)
```

## Code Style Guidelines

### Language
- UI text and comments: **Japanese**
- Code identifiers: **English** (snake_case)
- All user-facing strings are in Japanese

### Imports
- Three-tier ordering: standard library → third-party → local modules
- Absolute imports only: `from logic.translator import ...`, `from ui.main_window import ...`
- No `from __future__` imports
- Multi-line parenthesized imports for PySide6: `from PySide6.QtWidgets import (QMainWindow, QWidget, ...)`
- Lazy imports inside methods acceptable for heavy PySide6 widgets to reduce startup time
- Conditional import with fallback for optional dependencies: `try: import ftb_snbt_lib ... except ImportError: FTB_SNBT_AVAILABLE = False`

### Formatting
- No trailing whitespace
- Consistent indentation (4 spaces)
- Max line length: ~120 characters (practical, not enforced)
- Blank line between methods
- One blank line between logical sections within a method
- No TODO/FIXME/HACK comments in codebase

### Naming Conventions
- **Classes**: PascalCase (`TranslatorThread`, `FileHandler`, `EditorWidget`)
- **Functions/Methods**: snake_case (`protect_variables`, `load_zip`)
- **Private methods**: leading underscore (`_normalize_escapes`, `_parse_lang`)
- **Constants**: UPPER_SNAKE_CASE at module level (`VARIABLE_PATTERNS`, `COMPILED_PATTERNS`, `TARGET_LANGUAGES`)
- **Signals**: lowercase (`progress`, `finished`, `error`)
- **Instance attributes**: snake_case (`self.loaded_mods`, `self.current_mod_path`)

### Type Hints
- Used sparingly, only in `translation_memory.py` and `translation_memory_v2.py`
- Import from `typing`: `Optional`, `Dict`, `List`, `Tuple`
- Return types on public API methods preferred in newer code
- Older code and UI code have zero type hints — do NOT retroactively add them

### Error Handling
- Catch specific exceptions (`json.JSONDecodeError`, `zipfile.BadZipFile`, `requests.exceptions.HTTPError`) over bare `except`
- Bare `except: pass` only for non-critical operations (session restore, SNBT tag iteration, file parsing in loops)
- User-facing errors: `QMessageBox.critical(self, "エラー", ...)` or `QMessageBox.warning(self, "エラー", ...)`
- Informational: `QMessageBox.information(self, "情報", ...)`
- Transient status: `self.statusBar().showMessage("...", timeout=...)`
- Background thread errors: emit `error` Signal from QThread
- Debug info: `print()` to stdout (no logging framework)

### Threading Model
- All long-running operations use `QThread` subclasses (`TranslatorThread`, `ResourcePackImportThread`, `AITermExtractorThread`)
- Signals for thread communication: `progress(int, int)`, `finished(result)`, `error(str)`
- Cancellation via `is_running` flag: `thread.stop()` sets `False`, thread checks periodically in loop
- Parallel execution inside QThread: `ThreadPoolExecutor` with adaptive rate limiting (reduce workers on HTTP 429)
- UI responsiveness during synchronous loops: `QApplication.processEvents()`
- Bulk table updates: `setUpdatesEnabled(False/True)` to prevent flicker
- Prevent duplicate threads: `if hasattr(self, 'rp_thread') and self.rp_thread.isRunning():` before creating new thread

### Qt Patterns
- Signals defined as class attributes: `progress = Signal(int, int)`
- Block signals during programmatic changes: `self.mod_list.blockSignals(True)` / `blockSignals(False)`
- `Qt.UserRole` for storing file path data in `QListWidgetItem`
- Lazy widget imports inside methods: `from PySide6.QtWidgets import QPushButton`
- Persistent settings: `QSettings("MinecraftModTranslator", "App")` in `settings_dialog.py`
- Custom `QStyledItemDelegate` subclass for diff-mode rendering in editor
- `QUndoCommand` subclass for undo/redo in editor cells

### API Interaction (OpenRouter)
- Endpoint: `https://openrouter.ai/api/v1/chat/completions`
- Headers: `Authorization: Bearer {key}`, `Content-Type: application/json`, `X-Title: Minecraft MOD Translator Desktop`
- Timeout: 120s for translation, 60s for term extraction, 15s for model list
- Retry with exponential backoff: max 3 retries, `wait_time = 2 ** retries` on HTTP 429
- Response cleaning: strip markdown code fences (`\`\`\`json ... \`\`\``), fallback regex JSON extraction
- System prompt: professional translator role with rules about JSON keys, variable placeholders, glossary terms
- User content: `json.dumps(protected_items, ensure_ascii=False)` — JSON blob of items to translate

### Data Patterns
- Translation data: `dict[str, str]` (key → text) everywhere
- MOD state dict: `{"name", "original", "translations", "files", "target_file", "type", "_char_count"}`
- JSON persistence: `json.dump(data, f, indent=2, ensure_ascii=False)`
- SQLite for translation memory: single `translations` table with upsert (`ON CONFLICT ... DO UPDATE`)
- SQLite batch operations: `executemany()` with batch_size=1000, dynamic `IN` clauses with batch_size=500
- SQLite lazy singleton connection: `check_same_thread=False`, `row_factory = sqlite3.Row`
- Progressive save during translation: save to memory every 5 batches via `partial_save` signal

### String Handling
- All file I/O: `encoding='utf-8'` explicit
- JSON output: `ensure_ascii=False` to preserve Japanese characters
- Escape normalization before saving: `_normalize_escapes()` replaces `\\\"` and `\\"` with `"`
- Minecraft color codes: `§[0-9a-fk-or]` and `&[0-9a-fk-or]` — protected as variables during translation
- Variable placeholders: `{player}`, `%s`, `{0$d}`, `%%`, `\n`, `<br/>` — replaced with `__VAR_N__` during LLM translation, restored after
- Translation validation: checks nested braces, fullwidth chars, unreplaced placeholders, count mismatch, extreme length, untranslated text (ASCII ratio)

## Key Domain Concepts

- **Resource Pack**: Output format with `assets/<modid>/lang/ja_jp.json`
- **SNBT**: FTB Quests format, requires backup before modification
- **Datapack**: JSON files with category-based key mapping (`CATEGORY_KEY_MAP`)
- **Translation Memory**: SQLite DB that persists translations across sessions for auto-apply
- **Glossary**: Custom term dictionary injected into translation prompts
- **Variable Protection**: Format codes/placeholders replaced with `__VAR_N__` during LLM translation
- **Pack Format**: Maps Minecraft version to `pack_format` integer (`PACK_FORMATS` dict)

## Anti-patterns to Avoid

- Do NOT modify translation keys (LLM receives JSON, keys must remain unchanged)
- Do NOT use bare `except Exception` when specific exception is available
- Do NOT access `_MEIPASS` outside the frozen check block in `main.py`
- Do NOT call `.memory` property on `TranslationMemory` for large datasets (loads everything into memory)
- Do NOT retroactively add type hints to files that don't have them
- Do NOT introduce a logging framework — use `print()` consistently
- Do NOT create duplicate threads — always check `isRunning()` before starting
