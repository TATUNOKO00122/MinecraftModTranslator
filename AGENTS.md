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

## Project Structure

```
main.py                          # Entry point, PyInstaller frozen handling
logic/
  translator.py                  # AI translation (OpenRouter API), variable protection
  file_handler.py                # ZIP/JAR reading, resource pack generation
  resource_pack_handler.py       # Resource pack import (async QThread)
  translation_memory.py          # Compat wrapper for SQLite-backed translation memory
  translation_memory_v2.py       # SQLite-based translation memory with context
  glossary.py                    # Custom glossary (JSON persistence)
  term_extractor.py              # AI term extraction for glossary
  ftbquest_handler.py            # FTB Quests SNBT parsing/export
ui/
  main_window.py                 # Main window, MOD list, translation orchestration
  editor_widget.py               # Translation table with filter/search
  settings_dialog.py             # API key, model, parallel count settings
  glossary_dialog.py             # Glossary CRUD with import/export
  term_extraction_dialog.py      # Review extracted terms
  styles.qss                     # Dark theme (VS Code-inspired)
```

## Code Style Guidelines

### Language
- UI text and comments: **Japanese**
- Code identifiers: **English** (snake_case)
- All user-facing strings are in Japanese

### Imports
- Standard library first, then third-party, then local modules
- Use absolute imports: `from logic.translator import ...`, `from ui.main_window import ...`
- Lazy imports inside methods are acceptable for heavy PySide6 widgets to reduce startup time

### Formatting
- No trailing whitespace
- Use consistent indentation (4 spaces)
- Max line length: ~120 characters (practical, not enforced)
- Blank line between methods
- One blank line between logical sections within a method

### Naming Conventions
- **Classes**: PascalCase (`TranslatorThread`, `FileHandler`, `EditorWidget`)
- **Functions/Methods**: snake_case (`protect_variables`, `load_zip`)
- **Private methods**: leading underscore (`_normalize_escapes`, `_parse_lang`)
- **Constants**: UPPER_SNAKE_CASE (`VARIABLE_PATTERNS`, `COMPILED_PATTERNS`)
- **Signals**: lowercase (`progress`, `finished`, `error`)
- **Instance attributes**: snake_case (`self.loaded_mods`, `self.current_mod_path`)

### Type Hints
- Used sparingly but consistently in newer code (e.g., `translation_memory_v2.py`)
- Use `Optional`, `Dict`, `List`, `Tuple` from `typing`
- Return types on public API methods preferred

### Error Handling
- Catch specific exceptions (`json.JSONDecodeError`, `zipfile.BadZipFile`) over bare `except`
- Bare `except: pass` is used in a few places for non-critical operations (e.g., session restore)
- For user-facing errors: show `QMessageBox.critical()` or `QMessageBox.warning()`
- For background operations: emit `error` Signal from QThread
- Print to stdout for debug info (no logging framework)

### Threading Model
- All long-running operations use `QThread` subclasses
- Signals for thread communication: `progress(int, int)`, `finished(result)`, `error(str)`
- Use `is_running` flag pattern for cancellation (`thread.stop()` sets flag, thread checks periodically)
- Use `QApplication.processEvents()` for UI responsiveness during synchronous loops
- Use `setUpdatesEnabled(False/True)` around bulk table updates to prevent flicker

### Qt Patterns
- Signals defined as class attributes: `progress = Signal(int, int)`
- Block signals during programmatic changes: `self.mod_list.blockSignals(True)`
- Use `Qt.UserRole` for storing data in `QListWidgetItem`
- Load heavy widgets lazily (e.g., `from PySide6.QtWidgets import QPushButton` inside methods)

### Data Patterns
- Translation data stored as `dict[str, str]` (key -> text)
- MOD state: `dict[path, {"name", "original", "translations", "files", "target_file", "type"}]`
- JSON persistence with `ensure_ascii=False, indent=2`
- SQLite for translation memory (via `translation_memory_v2.py`)

### String Handling
- All file I/O uses `encoding='utf-8'`
- JSON output: `ensure_ascii=False` to preserve Japanese characters
- Normalize escaped quotes before saving: `_normalize_escapes()`
- Minecraft color codes: `§[0-9a-fk-or]` and `&[0-9a-fk-or]`
- Variable placeholders protected during translation: `{player}`, `%s`, `{0$d}`

## Key Domain Concepts

- **Resource Pack**: Output format with `assets/<modid>/lang/ja_jp.json`
- **SNBT**: FTB Quests format, requires backup before modification
- **Translation Memory**: SQLite DB that persists translations across sessions for auto-apply
- **Glossary**: Custom term dictionary injected into translation prompts
- **Variable Protection**: Format codes/placeholders are replaced with `__VAR_N__` during LLM translation

## Anti-patterns to Avoid

- Do NOT modify translation keys (LLM receives JSON, keys must remain unchanged)
- Do NOT use bare `except Exception` when specific exception is available
- Do NOT access `_MEIPASS` outside the frozen check block
- Do NOT call `.memory` property on TranslationMemory for large datasets (loads everything into memory)
