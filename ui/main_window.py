import os
import sys
import json
import re
import stat
import zipfile
import ctypes
from PySide6.QtWidgets import (QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QToolBar, 
                                QFileDialog, QMessageBox, QLabel, QProgressBar, QMenu, QSplitter, QListWidget, QApplication,
                                QDialog, QDialogButtonBox, QListWidgetItem, QToolButton, QComboBox, QPushButton,
                                QStackedWidget, QFrame, QCheckBox, QSizePolicy)
from PySide6.QtGui import QAction, QDragEnterEvent, QDropEvent, QKeySequence, QShortcut, QIcon, QColor
from PySide6.QtCore import Qt, QTimer

from logic.file_handler import FileHandler
from logic.translator import TranslatorThread
from logic.translation_memory import TranslationMemory
from logic.glossary import Glossary
from logic import ftbquest_handler
from logic import datapack_handler
from ui.editor_widget import EditorWidget
from ui.settings_dialog import SettingsDialog
from ui.glossary_dialog import GlossaryDialog
from ui.term_extraction_dialog import TermExtractionDialog, FrequentTermDialog, FrequentTermTranslateThread
from logic.term_extractor import AITermExtractorThread, AITermClassifierThread, extract_all_term_candidates, extract_frequent_terms_from_original
from logic.resource_pack_handler import ResourcePackImportThread

class NoScrollListWidget(QListWidget):
    """Custom QListWidget that prevents auto-scrolling when items are selected"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self._scroll_enabled = True
    
    def setScrollEnabled(self, enabled):
        self._scroll_enabled = enabled
    
    def scrollTo(self, index, hint=QListWidget.EnsureVisible):
        if self._scroll_enabled:
            super().scrollTo(index, hint)

class MainWindow(QMainWindow):
    SESSION_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "session.json")
    RECOVERY_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "recovery.json")
    AUTOSAVE_INTERVAL_MS = 60000
    
    @staticmethod
    def _restrict_file_permissions(path):
        if sys.platform == 'win32':
            try:
                kernel32 = ctypes.windll.kernel32
                handle = kernel32.CreateFileW(path, 0x00010000, 0, None, 3, 0x80, None)
                if handle != -1:
                    kernel32.CloseHandle(handle)
            except (OSError, AttributeError):
                pass
        else:
            try:
                os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
            except OSError:
                pass

    @staticmethod
    def _write_json_secure(path, data):
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        MainWindow._restrict_file_permissions(path)
    
    def __init__(self, base_path=None):
        super().__init__()
        self.setWindowTitle("Minecraft MOD 翻訳ツール")
        self.resize(1200, 800)
        self.setAcceptDrops(True)
        
        icon_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "icon.ico")
        if os.path.exists(icon_path):
            self.setWindowIcon(QIcon(icon_path))

        self.file_handler = FileHandler()
        self.memory = TranslationMemory()

        default_glossary_path = None
        if base_path:
            candidate = os.path.join(base_path, 'ui', 'default_glossary.md')
            if os.path.exists(candidate):
                default_glossary_path = candidate
        self.glossary = Glossary(default_glossary_path=default_glossary_path)
        self.settings_dialog = SettingsDialog(self)
        self.translator_thread = None
        
        # State: { "path/to/mod.jar": { "name": "ModName", "original": {}, "translations": {}, "files": [], "target_file": "..." } }
        self.loaded_mods = {}
        self.current_mod_path = None
        self.translation_errors = []
        self._dirty = False
        
        self._session_token_usage = {
            'prompt_tokens': 0,
            'completion_tokens': 0,
            'total_tokens': 0,
            'api_calls': 0,
        }
        
        self._autosave_timer = QTimer(self)
        self._autosave_timer.timeout.connect(self._autosave)

        self._setup_ui()
        self._show_busy("起動中", "初期化しています...", show_progress=False, show_cancel=False)
        self._autosave_timer.start(self.AUTOSAVE_INTERVAL_MS)
        QTimer.singleShot(0, self._startup_restore)

    def _setup_ui(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        root_layout = QVBoxLayout(central_widget)
        root_layout.setContentsMargins(0,0,0,0)

        self._stack = QStackedWidget()
        root_layout.addWidget(self._stack)

        # --- Page 0: Main content ---
        main_page = QWidget()
        main_layout = QVBoxLayout(main_page)
        main_layout.setContentsMargins(0,0,0,0)
        
        # Toolbar
        self.toolbar = QToolBar("Main Toolbar")
        self.toolbar.setMovable(False)
        self.addToolBar(self.toolbar)
        
        # Toolbar Actions
        self.undo_action = QAction("元に戻す", self)
        self.undo_action.setShortcut(QKeySequence.Undo)
        self.undo_action.setEnabled(False)
        self.toolbar.addAction(self.undo_action)
        
        self.redo_action = QAction("やり直し", self)
        self.redo_action.setShortcut(QKeySequence.Redo)
        self.redo_action.setEnabled(False)
        self.toolbar.addAction(self.redo_action)
        
        self.toolbar.addSeparator()
        
        settings_action = QAction("設定", self)
        settings_action.triggered.connect(self.settings_dialog.show)
        self.toolbar.addAction(settings_action)

        dictionary_action = QAction("辞書", self)
        dictionary_action.triggered.connect(self.open_glossary)
        self.toolbar.addAction(dictionary_action)

        self.freq_terms_action = QAction("頻出語抽出", self)
        self.freq_terms_action.setToolTip("翻訳前に原文から頻出する固有名詞を抽出し、辞書に登録します")
        self.freq_terms_action.triggered.connect(self.show_frequent_terms)
        self.toolbar.addAction(self.freq_terms_action)
        
        export_action = QAction("リソースパック作成", self)
        export_action.triggered.connect(self.export_resource_pack)
        self.toolbar.addAction(export_action)

        self.apply_snbt_btn = QToolButton()
        self.apply_snbt_btn.setText("SNBT適用")
        self.apply_snbt_btn.clicked.connect(self.apply_ftbquest_snbt)
        self.apply_snbt_action = self.toolbar.addWidget(self.apply_snbt_btn)
        self.apply_snbt_action.setVisible(False)

        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.toolbar.addWidget(spacer)

        self._cumulative_token_label = QLabel("Token: 0")
        self._cumulative_token_label.setObjectName("CumulativeTokenLabel")
        self._cumulative_token_label.setToolTip("セッション累計トークン消費量")
        self.toolbar.addWidget(self._cumulative_token_label)

        # Splitter Layout
        splitter = QSplitter(Qt.Horizontal)
        main_layout.addWidget(splitter)

        # Left: MOD List
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        
        self.mod_filter = QComboBox()
        self.mod_filter.addItem("すべて", "all")
        self.mod_filter.addItem("未翻訳", "incomplete")
        self.mod_filter.addItem("翻訳済み", "complete")
        self.mod_filter.addItem("原文と同じ", "has_same")
        self.mod_filter.addItem("ローマ字あり", "has_roman")
        self.mod_filter.addItem("FTBクエスト", "ftbquest")
        self.mod_filter.addItem("データパック", "datapack")
        self.mod_filter.addItem("MODのみ", "mod")
        self.mod_filter.currentIndexChanged.connect(self.filter_mod_list)
        left_layout.addWidget(self.mod_filter)
        
        self.mod_sort = QComboBox()
        self.mod_sort.addItem("読み込み順", "load_order")
        self.mod_sort.addItem("名前順 (A→Z)", "name_asc")
        self.mod_sort.addItem("名前順 (Z→A)", "name_desc")
        self.mod_sort.addItem("アイテム数 大→小", "items_desc")
        self.mod_sort.addItem("アイテム数 小→大", "items_asc")
        self.mod_sort.addItem("翻訳率 高→低", "rate_desc")
        self.mod_sort.addItem("翻訳率 低→高", "rate_asc")
        self.mod_sort.currentIndexChanged.connect(self.sort_mod_list)
        left_layout.addWidget(self.mod_sort)
        
        self._mod_load_order = []
        
        self.batch_translate_btn = QPushButton("一括翻訳")
        self.batch_translate_btn.setToolTip("フィルター後の表示MODを順番に翻訳します")
        self.batch_translate_btn.clicked.connect(self.start_batch_translate_all_mods)
        left_layout.addWidget(self.batch_translate_btn)
        
        self.mod_list = NoScrollListWidget()
        self.mod_list.currentItemChanged.connect(self.on_mod_selected)
        self.mod_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.mod_list.customContextMenuRequested.connect(self.show_mod_list_context_menu)
        left_layout.addWidget(self.mod_list)
        
        splitter.addWidget(left_widget)

        # Right: Editor Area
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(0,0,0,0)

        self.mod_label = QLabel("MODファイルまたはMinecraftディレクトリをドラッグ＆ドロップしてください")
        self.mod_label.setAlignment(Qt.AlignCenter)
        self.mod_label.setObjectName("ModLabel")
        right_layout.addWidget(self.mod_label)
        
        self.editor = EditorWidget()
        self.editor.hide()
        self.editor.table.customContextMenuRequested.connect(self.show_context_menu)
        self.editor.translationChanged.connect(self.update_current_mod_stats)
        self.editor.translationChanged.connect(self._mark_dirty)
        self.editor.translate_btn.clicked.connect(self.start_auto_translate_all)
        self.editor.extract_terms_btn.clicked.connect(self.start_manual_term_extraction)
        self.editor.searchAllModsRequested.connect(self.search_all_mods)
        self.editor.selectionChanged.connect(self._on_editor_selection_changed)
        right_layout.addWidget(self.editor)
        
        self.undo_action.triggered.connect(self.editor.undo_stack.undo)
        self.redo_action.triggered.connect(self.editor.undo_stack.redo)
        self.editor.undo_stack.canUndoChanged.connect(self.undo_action.setEnabled)
        self.editor.undo_stack.canRedoChanged.connect(self.redo_action.setEnabled)
        
        splitter.addWidget(right_widget)
        splitter.setStretchFactor(1, 4)

        # Progress Bar and Stop Button
        progress_layout = QHBoxLayout()
        progress_layout.setContentsMargins(4, 4, 4, 4)
        
        self.progress_bar = QProgressBar()
        self.progress_bar.hide()
        progress_layout.addWidget(self.progress_bar)
        
        self.stop_translation_btn = QPushButton("中断")
        self.stop_translation_btn.setFixedWidth(80)
        self.stop_translation_btn.setObjectName("DangerButton")
        self.stop_translation_btn.clicked.connect(self.stop_translation)
        self.stop_translation_btn.hide()
        progress_layout.addWidget(self.stop_translation_btn)
        
        main_layout.addLayout(progress_layout)

        self._stack.addWidget(main_page)

        # --- Page 1: Busy / Loading screen ---
        busy_page = QWidget()
        busy_layout = QVBoxLayout(busy_page)
        busy_layout.setAlignment(Qt.AlignCenter)

        self._busy_title = QLabel("処理中")
        self._busy_title.setAlignment(Qt.AlignCenter)
        self._busy_title.setObjectName("BusyTitle")
        busy_layout.addWidget(self._busy_title)

        busy_layout.addSpacing(8)

        busy_sep = QFrame()
        busy_sep.setFixedWidth(480)
        busy_sep.setFrameShape(QFrame.HLine)
        busy_sep.setFrameShape(QFrame.HLine)
        busy_sep.setObjectName("BusySeparator")
        busy_layout.addWidget(busy_sep, alignment=Qt.AlignCenter)

        busy_layout.addSpacing(12)

        self._busy_message = QLabel("しばらくお待ちください...")
        self._busy_message.setAlignment(Qt.AlignCenter)
        self._busy_message.setObjectName("BusyMessage")
        busy_layout.addWidget(self._busy_message)

        busy_layout.addSpacing(20)

        self._busy_progress = QProgressBar()
        self._busy_progress.setRange(0, 0)
        self._busy_progress.setFixedWidth(480)
        self._busy_progress.setTextVisible(True)
        self._busy_progress.setFormat("準備中...")
        busy_layout.addWidget(self._busy_progress, alignment=Qt.AlignCenter)

        busy_layout.addSpacing(20)

        self._busy_token_label = QLabel("")
        self._busy_token_label.setAlignment(Qt.AlignCenter)
        self._busy_token_label.setObjectName("BusyTokenLabel")
        self._busy_token_label.hide()
        busy_layout.addWidget(self._busy_token_label)

        busy_layout.addSpacing(8)

        self._busy_cancel_btn = QPushButton("キャンセル")
        self._busy_cancel_btn.setFixedSize(120, 32)
        busy_layout.addWidget(self._busy_cancel_btn, alignment=Qt.AlignCenter)

        busy_layout.addStretch()

        self._stack.addWidget(busy_page)
        self._stack.setCurrentIndex(0)

        self._cancellable_thread = None
        self._busy_cancel_btn.clicked.connect(self._on_busy_cancel)

        self._setup_shortcuts()

    def _setup_shortcuts(self):
        save_shortcut = QShortcut(QKeySequence.Save, self)
        save_shortcut.activated.connect(self._manual_save)
        
        find_shortcut = QShortcut(QKeySequence.Find, self)
        find_shortcut.activated.connect(lambda: self.editor.search_input.setFocus())

    def _startup_restore(self):
        self._check_recovery_file()
        self._check_previous_session()
        self._close_busy()

    def _check_previous_session(self):
        """Check if there's a previous session and ask to restore"""
        if not os.path.exists(self.SESSION_FILE):
            return
        
        try:
            with open(self.SESSION_FILE, 'r', encoding='utf-8') as f:
                session = json.load(f)
            
            paths = session.get("mod_paths", [])
            valid_paths = [p for p in paths if os.path.exists(p)]
            
            if not valid_paths:
                return
            
            confirm = QMessageBox.question(
                self, "前回のセッション",
                f"前回 {len(valid_paths)} 個のMOD/クエストを開いていました。\n再度開きますか？",
                QMessageBox.Yes | QMessageBox.No
            )
            
            if confirm == QMessageBox.Yes:
                total = len(valid_paths)
                self._show_busy("セッション復元", f"前回のセッションを復元中... (0/{total})", show_cancel=False)
                self._busy_progress.setRange(0, total)
                self._busy_progress.setValue(0)
                self._busy_progress.setFormat(f"0/{total}")
                for i, path in enumerate(valid_paths):
                    self._update_busy_message(f"復元中... ({i+1}/{total})")
                    self._update_busy_progress(i + 1, total)
                    self._suppress_busy = True
                    self.process_path(path, silent=True)
                    self._suppress_busy = False
                self._close_busy()
                self.statusBar().showMessage("セッション復元完了", 3000)
                self._update_snbt_button_visibility()
            
            self._apply_recovery_translations()
        except:
            pass
    
    def _apply_recovery_translations(self):
        if not hasattr(self, '_pending_recovery') or not self._pending_recovery:
            self._cleanup_recovery()
            return
        
        applied_count = 0
        for path, recovery in self._pending_recovery.items():
            if path in self.loaded_mods:
                translations = recovery.get("translations", {})
                review_status = recovery.get("review_status", {})
                if translations:
                    self.loaded_mods[path]["translations"].update(translations)
                    self.loaded_mods[path]["review_status"].update(review_status)
                    applied_count += len(translations)
        
        if applied_count > 0:
            if self.current_mod_path and self.current_mod_path in self.loaded_mods:
                self.editor.update_translations(self.loaded_mods[self.current_mod_path]["translations"])
            self.statusBar().showMessage(f"クラッシュ復旧: {applied_count} 件の翻訳を復元しました", 5000)
        
        del self._pending_recovery
        self._cleanup_recovery()
    
    def _update_snbt_button_visibility(self):
        """Update SNBT button visibility based on loaded FTB quests."""
        has_ftb = any(data.get("type") == "ftbquest" for data in self.loaded_mods.values())
        print(f"[DEBUG] _update_snbt_button_visibility: has_ftb={has_ftb}, loaded_mods count={len(self.loaded_mods)}")
        # Control visibility via the toolbar action, not the button itself
        self.apply_snbt_action.setVisible(has_ftb)
        print(f"[DEBUG] apply_snbt_action.isVisible() after setVisible: {self.apply_snbt_action.isVisible()}")
        if has_ftb:
            self._update_snbt_button_style()

    def _save_session(self):
        try:
            paths = list(self.loaded_mods.keys())
            session = {"mod_paths": paths}
            self._write_json_secure(self.SESSION_FILE, session)
        except OSError:
            pass

    def _manual_save(self):
        self._autosave()
        self._save_session()
        self.statusBar().showMessage("保存しました", 3000)

    def _mark_dirty(self, translated=None, total=None):
        self._dirty = True

    def _autosave(self):
        if not self._dirty or not self.loaded_mods:
            return
        
        if self.current_mod_path:
            self.loaded_mods[self.current_mod_path]["translations"] = self.editor.get_translations()
            self.loaded_mods[self.current_mod_path]["review_status"] = self.editor.review_status
            
            user_edits = {
                k: v for k, v in self.loaded_mods[self.current_mod_path]["translations"].items()
                if k in self.editor.user_edited_keys and v
            }
            if user_edits and self.memory:
                try:
                    self.memory.set_context(
                        mod_name=self.loaded_mods[self.current_mod_path]["name"],
                        model=None,
                        sources=self.loaded_mods[self.current_mod_path]["original"]
                    )
                    self.memory.update(user_edits, origin='user')
                    self.editor.user_edited_keys -= set(user_edits.keys())
                except Exception as e:
                    print(f"Autosave TM update failed: {e}")
        
        self._save_recovery()
        self._dirty = False

    def _save_recovery(self):
        try:
            recovery_data = {}
            for path, mod_data in self.loaded_mods.items():
                recovery_data[path] = {
                    "name": mod_data.get("name", ""),
                    "translations": mod_data.get("translations", {}),
                    "review_status": mod_data.get("review_status", {}),
                }
            self._write_json_secure(self.RECOVERY_FILE, recovery_data)
        except OSError:
            pass

    def _cleanup_recovery(self):
        try:
            if os.path.exists(self.RECOVERY_FILE):
                os.remove(self.RECOVERY_FILE)
        except:
            pass

    def _check_recovery_file(self):
        if not os.path.exists(self.RECOVERY_FILE):
            return
        
        try:
            with open(self.RECOVERY_FILE, 'r', encoding='utf-8') as f:
                recovery_data = json.load(f)
            
            if not recovery_data:
                self._cleanup_recovery()
                return
            
            mod_count = len(recovery_data)
            total_keys = sum(len(v.get("translations", {})) for v in recovery_data.values())
            
            confirm = QMessageBox.question(
                self, "クラッシュ復旧",
                f"前回の予期せぬ終了時に {mod_count} 個のMOD、{total_keys} 件の翻訳データが\n"
                "自動保存されていました。\n\n復元しますか？\n\n"
                "※ 復元しない場合、自動保存データは破棄されます。",
                QMessageBox.Yes | QMessageBox.No
            )
            
            if confirm == QMessageBox.Yes:
                self._pending_recovery = recovery_data
        except:
            self._cleanup_recovery()

    def closeEvent(self, event):
        """Save session on close with unsaved changes check"""
        if hasattr(self, 'translator_thread') and self.translator_thread and self.translator_thread.isRunning():
            confirm = QMessageBox.question(
                self, "翻訳中",
                "翻訳が実行中です。\n終了すると未完了の翻訳は失われます。\n\n終了しますか？",
                QMessageBox.Yes | QMessageBox.No
            )
            if confirm == QMessageBox.No:
                event.ignore()
                return
            self.translator_thread.stop()
        
        if self.current_mod_path:
            self.loaded_mods[self.current_mod_path]["translations"] = self.editor.get_translations()
            self.loaded_mods[self.current_mod_path]["review_status"] = self.editor.review_status
        
        self._save_session()
        self._cleanup_recovery()
        self._autosave_timer.stop()
        event.accept()

    # --- Logic ---
    def on_mod_selected(self, current, previous):
        # Save scroll position before any changes
        scrollbar = self.mod_list.verticalScrollBar()
        scroll_pos = scrollbar.value()
        
        # Save previous
        if previous:
            prev_path = previous.data(Qt.UserRole)
            if prev_path in self.loaded_mods:
                self.loaded_mods[prev_path]["translations"] = self.editor.get_translations()
                self.loaded_mods[prev_path]["review_status"] = self.editor.review_status.copy()

        # Load current
        if current:
            path = current.data(Qt.UserRole)
            self.current_mod_path = path
            mod_data = self.loaded_mods[path]
            
            self.mod_label.hide()
            self.editor.show()
            
            self.editor.load_data(mod_data["original"])
            
            self.editor.review_status = mod_data.get("review_status", {})
            
            self.editor.update_translations(mod_data["translations"])
            
            # Re-apply filter if active
            self.editor.filter_table()
            
            self.setWindowTitle(f"Minecraft MOD 翻訳ツール - {mod_data['name']}")
        else:
            self.current_mod_path = None
            self.editor.hide()
            self.mod_label.show()
        
        # Restore scroll position after Qt finishes its internal scroll adjustments
        QTimer.singleShot(0, lambda: scrollbar.setValue(scroll_pos))

    def update_current_mod_stats(self, translated, total):
        if not self.current_mod_path: return
        
        self.loaded_mods[self.current_mod_path]["_translated"] = translated
        self.loaded_mods[self.current_mod_path]["_total"] = total
        
        for i in range(self.mod_list.count()):
            item = self.mod_list.item(i)
            if item.data(Qt.UserRole) == self.current_mod_path:
                mod_name = self.loaded_mods[self.current_mod_path]["name"]
                item.setText(self._format_mod_display(mod_name, translated, total))
                item.setToolTip(mod_name)
                
                if total > 0 and translated == total:
                    item.setForeground(QColor("#4ade80"))
                else:
                    item.setForeground(QColor("#d4d4d4"))
                break
    
    def _count_translated(self, mod_data):
        translations = mod_data["translations"]
        return sum(1 for t in translations.values() if t)
    
    def refresh_all_mod_colors(self):
        for i in range(self.mod_list.count()):
            item = self.mod_list.item(i)
            mod_path = item.data(Qt.UserRole)
            if mod_path in self.loaded_mods:
                mod_data = self.loaded_mods[mod_path]
                total = len(mod_data["original"])
                translated = self._count_translated(mod_data)
                
                char_count = mod_data.get("_char_count", 0)
                
                mod_name = mod_data["name"]
                item.setText(self._format_mod_display(mod_name, translated, total))
                item.setToolTip(f"{mod_name}\n原文: {char_count:,} 文字")
                
                if total > 0 and translated == total:
                    item.setForeground(QColor("#4ade80"))
                else:
                    item.setForeground(QColor("#d4d4d4"))
    
    def _refresh_mod_list_items(self, mod_paths):
        for i in range(self.mod_list.count()):
            item = self.mod_list.item(i)
            mod_path = item.data(Qt.UserRole)
            if mod_path in mod_paths and mod_path in self.loaded_mods:
                mod_data = self.loaded_mods[mod_path]
                total = len(mod_data["original"])
                translated = self._count_translated(mod_data)
                char_count = mod_data.get("_char_count", 0)
                mod_name = mod_data["name"]
                item.setText(self._format_mod_display(mod_name, translated, total))
                item.setToolTip(f"{mod_name}\n原文: {char_count:,} 文字")
                if total > 0 and translated == total:
                    item.setForeground(QColor("#4ade80"))
                else:
                    item.setForeground(QColor("#d4d4d4"))
    
    def _truncate_name(self, name, max_chars=20):
        if len(name) > max_chars:
            return name[:max_chars] + "…"
        return name
    
    def _format_mod_display(self, name, translated, total):
        display_name = self._truncate_name(name)
        count_str = f"({translated}/{total})"
        padding = 24 - len(display_name)
        return f"{display_name}{' ' * max(1, padding)}{count_str}"
    
    def filter_mod_list(self):
        filter_type = self.mod_filter.currentData()
        
        # Disable scroll for filters that hide many items
        should_scroll = filter_type in ("all", "mod")
        self.mod_list.setScrollEnabled(should_scroll)
        
        for i in range(self.mod_list.count()):
            item = self.mod_list.item(i)
            mod_path = item.data(Qt.UserRole)
            
            if mod_path not in self.loaded_mods:
                item.setHidden(False)
                continue
                
            mod_data = self.loaded_mods[mod_path]
            total = mod_data.get("_total", len(mod_data["original"]))
            translated = mod_data.get("_translated", self._count_translated(mod_data))
            is_complete = (total > 0 and translated == total)
            
            if filter_type == "all":
                item.setHidden(False)
            elif filter_type == "complete":
                item.setHidden(not is_complete)
            elif filter_type == "incomplete":
                item.setHidden(is_complete)
            elif filter_type == "has_same":
                # Check if any translation is same as original
                has_same = any(t and t == mod_data["original"].get(k, "") 
                               for k, t in mod_data["translations"].items())
                item.setHidden(not has_same)
            elif filter_type == "has_roman":
                has_roman = False
                for t in mod_data["translations"].values():
                    if t:
                        # Remove color codes (§x and &x format) and placeholders
                        text = re.sub(r'[§&][0-9a-fk-or]', '', t, flags=re.IGNORECASE)
                        text = re.sub(r'%(\d+\$)?[sdfc]', '', text)
                        if re.search(r'[A-Za-z]', text):
                            has_roman = True
                            break
                item.setHidden(not has_roman)
            elif filter_type == "ftbquest":
                is_ftb = mod_data.get("type") == "ftbquest"
                item.setHidden(not is_ftb)
            elif filter_type == "datapack":
                is_dp = mod_data.get("type") == "datapack"
                item.setHidden(not is_dp)
            elif filter_type == "mod":
                is_ftb = mod_data.get("type") == "ftbquest"
                item.setHidden(is_ftb)

    def sort_mod_list(self):
        sort_type = self.mod_sort.currentData()
        
        items_data = []
        load_order_map = {p: i for i, p in enumerate(self._mod_load_order)}
        for i in range(self.mod_list.count()):
            item = self.mod_list.item(i)
            mod_path = item.data(Qt.UserRole)
            if mod_path in self.loaded_mods:
                mod_data = self.loaded_mods[mod_path]
                total = mod_data.get("_total", len(mod_data["original"]))
                translated = mod_data.get("_translated", self._count_translated(mod_data))
                rate = translated / total if total > 0 else 0
                load_index = load_order_map.get(mod_path, 9999)
                is_ftb = 0 if mod_data.get("type") in ("ftbquest", "datapack") else 1
                items_data.append({
                    "path": mod_path,
                    "name": mod_data["name"],
                    "total": total,
                    "translated": translated,
                    "rate": rate,
                    "load_index": load_index,
                    "is_ftb": is_ftb,
                    "hidden": item.isHidden()
                })
        
        # Sort based on type (FTBクエストは常に一番上)
        if sort_type == "load_order":
            items_data.sort(key=lambda x: (x["is_ftb"], x["load_index"]))
        elif sort_type == "name_asc":
            items_data.sort(key=lambda x: (x["is_ftb"], x["name"].lower()))
        elif sort_type == "name_desc":
            items_data.sort(key=lambda x: (x["is_ftb"], x["name"].lower()), reverse=True)
        elif sort_type == "items_desc":
            items_data.sort(key=lambda x: (x["is_ftb"], -x["total"]))
        elif sort_type == "items_asc":
            items_data.sort(key=lambda x: (x["is_ftb"], x["total"]))
        elif sort_type == "rate_desc":
            items_data.sort(key=lambda x: (x["is_ftb"], -x["rate"]))
        elif sort_type == "rate_asc":
            items_data.sort(key=lambda x: (x["is_ftb"], x["rate"]))
        
        # Remember current selection
        current_item = self.mod_list.currentItem()
        current_path = current_item.data(Qt.UserRole) if current_item else None
        
        # Clear and rebuild list
        self.mod_list.blockSignals(True)
        self.mod_list.clear()
        
        new_current_item = None
        for data in items_data:
            mod_path = data["path"]
            mod_data = self.loaded_mods[mod_path]
            
            item = QListWidgetItem(self._format_mod_display(mod_data["name"], data["translated"], data["total"]))
            char_count = mod_data.get("_char_count", 0)
            item.setToolTip(f"{mod_data['name']}\n原文: {char_count:,} 文字")
            item.setData(Qt.UserRole, mod_path)
            
            if data["total"] > 0 and data["translated"] == data["total"]:
                item.setForeground(QColor("#4ade80"))
            else:
                item.setForeground(QColor("#d4d4d4"))
            
            item.setHidden(data["hidden"])
            self.mod_list.addItem(item)
            
            if mod_path == current_path:
                new_current_item = item
        
        self.mod_list.blockSignals(False)
        
        # Restore selection
        if new_current_item:
            self.mod_list.setCurrentItem(new_current_item)

    def search_all_mods(self, search_text):
        search_text = search_text.lower().strip()
        
        if not search_text:
            for i in range(self.mod_list.count()):
                self.mod_list.item(i).setHidden(False)
            self.statusBar().showMessage("検索をクリアしました", 3000)
            return
        
        if self.current_mod_path:
            self.loaded_mods[self.current_mod_path]["translations"] = self.editor.get_translations()
        
        matched_count = 0
        total_count = self.mod_list.count()
        
        for i in range(total_count):
            item = self.mod_list.item(i)
            mod_path = item.data(Qt.UserRole)
            
            if mod_path not in self.loaded_mods:
                item.setHidden(True)
                continue
            
            mod_data = self.loaded_mods[mod_path]
            found = False
            
            if search_text in mod_data["name"].lower():
                found = True
            
            if not found:
                for key, original in mod_data["original"].items():
                    if search_text in key or search_text in original.lower():
                        found = True
                        break
            
            if not found:
                for translation in mod_data["translations"].values():
                    if translation and search_text in translation.lower():
                        found = True
                        break
            
            item.setHidden(not found)
            if found:
                matched_count += 1
        
        self.statusBar().showMessage(f"「{search_text}」: {matched_count}/{total_count} MODがマッチ", 5000)

    def _check_snbt_applied(self, quests_folder):
        for root, dirs, files in os.walk(quests_folder):
            for f in files:
                if '.backup' in f or f.endswith('.snbt.bak'):
                    return True
        return False

    def _update_snbt_button_style(self):
        """Update SNBT button style based on whether there are unapplied quests."""
        unapplied = any(
            data.get("type") == "ftbquest" and not data.get("snbt_applied", False)
            for data in self.loaded_mods.values()
        )
        
        if unapplied:
            self.apply_snbt_btn.setObjectName("SnbtWarningButton")
            self.apply_snbt_btn.setToolTip("⚠️ 未適用のクエストがあります！クリックして適用してください。")
        else:
            self.apply_snbt_btn.setObjectName("")
            self.apply_snbt_btn.setToolTip("FTBクエストのSNBTファイルに翻訳を適用します")

    # --- Context Menu ---
    def show_context_menu(self, pos):
        menu = QMenu(self)
        
        translate_selected_action = QAction("選択範囲を翻訳", self)
        translate_selected_action.triggered.connect(self.start_translate_selected)
        menu.addAction(translate_selected_action)
        
        menu.addSeparator()
        
        add_dictionary_action = QAction("辞書に追加", self)
        add_dictionary_action.triggered.connect(lambda: self.add_selection_to_glossary())
        menu.addAction(add_dictionary_action)
        
        menu.addSeparator()
        
        clear_menu = menu.addMenu("翻訳をクリア")
        
        clear_selected_action = QAction("選択した行のみ", self)
        clear_selected_action.triggered.connect(self.clear_selected_translations)
        clear_menu.addAction(clear_selected_action)
        
        clear_current_action = QAction("現在のMOD全体", self)
        clear_current_action.triggered.connect(self.clear_current_mod_translations)
        clear_menu.addAction(clear_current_action)
        
        clear_all_action = QAction("全MOD (クエスト除く)", self)
        clear_all_action.triggered.connect(self.clear_all_mod_translations)
        clear_menu.addAction(clear_all_action)
        
        menu.addSeparator()
        
        mark_reviewed_action = QAction("確認済みにする", self)
        mark_reviewed_action.triggered.connect(self.mark_selected_reviewed)
        menu.addAction(mark_reviewed_action)
        
        menu.exec(self.editor.table.mapToGlobal(pos))

    def show_mod_list_context_menu(self, pos):
        """Show context menu for MOD list"""
        menu = QMenu(self)
        
        # Get clicked item
        item = self.mod_list.itemAt(pos)
        
        if item:
            mod_path = item.data(Qt.UserRole)
            mod_data = self.loaded_mods.get(mod_path, {})
            mod_name = mod_data.get("name", "このMOD")
            
            remove_action = QAction(f"「{self._truncate_name(mod_name, 15)}」を削除", self)
            remove_action.triggered.connect(lambda: self.remove_mod(mod_path))
            menu.addAction(remove_action)
            
            menu.addSeparator()
        
        remove_all_action = QAction("すべてのMOD・クエストを削除", self)
        remove_all_action.triggered.connect(self.remove_all_mods)
        menu.addAction(remove_all_action)
        
        menu.exec(self.mod_list.mapToGlobal(pos))

    def remove_mod(self, mod_path):
        """Remove a single MOD from the list"""
        if mod_path not in self.loaded_mods:
            return
        
        mod_name = self.loaded_mods[mod_path]["name"]
        
        confirm = QMessageBox.question(
            self, "確認", f"「{mod_name}」をリストから削除しますか？"
        )
        
        if confirm == QMessageBox.Yes:
            # Remove from data
            del self.loaded_mods[mod_path]
            if mod_path in self._mod_load_order:
                self._mod_load_order.remove(mod_path)
            
            # Remove from list widget
            for i in range(self.mod_list.count()):
                item = self.mod_list.item(i)
                if item.data(Qt.UserRole) == mod_path:
                    self.mod_list.takeItem(i)
                    break
            
            # Clear editor if it was the current one
            if self.current_mod_path == mod_path:
                self.current_mod_path = None
                self.editor.hide()
                self.mod_label.show()
                self.setWindowTitle("Minecraft MOD 翻訳ツール")
            
            # Update SNBT button visibility
            self._update_snbt_button_visibility()
            
            self.statusBar().showMessage(f"「{mod_name}」を削除しました", 3000)

    def remove_all_mods(self):
        """Remove all MODs and quests from the list"""
        if not self.loaded_mods:
            QMessageBox.information(self, "情報", "リストに項目がありません。")
            return
        
        count = len(self.loaded_mods)
        
        confirm = QMessageBox.question(
            self, "確認", f"すべてのMOD・クエスト ({count}個) をリストから削除しますか？"
        )
        
        if confirm == QMessageBox.Yes:
            # Clear all data
            self.loaded_mods.clear()
            self._mod_load_order.clear()
            self.mod_list.clear()
            
            # Clear editor
            self.current_mod_path = None
            self.editor.hide()
            self.mod_label.show()
            self.setWindowTitle("Minecraft MOD 翻訳ツール")
            
            # Update SNBT button visibility
            self._update_snbt_button_visibility()
            
            self.statusBar().showMessage(f"{count}個の項目を削除しました", 3000)

    def add_selection_to_glossary(self):
        # Determine what is selected
        selected_items = self.editor.table.selectedItems()
        if not selected_items:
            return

        # Simple heuristic: Use first selected English/Key cell as Key
        # Use first selected Japanese cell as Value?
        # Or Just use the text of the first selected cell as Key?
        
        key = ""
        value = ""
        
        # Get the first item
        item = selected_items[0]
        text = item.text()
        col = item.column()
        
        # Col 0 = Key, 1 = Original, 2 = Translation
        if col == 1: # Original
            key = text
        elif col == 2: # Translation
             value = text
        elif col == 0:
             key = text # Maybe user wants to key technical term
             
        # Check if we have a pair selected? (e.g. row selection)
        # If row is selected, we might have multiple items
        if len(selected_items) > 1:
            for it in selected_items:
                if it.row() == item.row(): # Same row
                    if it.column() == 1:
                        key = it.text()
                    elif it.column() == 2:
                        value = it.text()

        # If we only have value but no key, maybe key is implicitly the original of that row
        if not key and value:
             row = item.row()
             key = self.editor.table.item(row, 1).text()
             
        dialog = GlossaryDialog(self.glossary, self, initial_key=key, initial_value=value)
        dialog.exec()

    def clear_selected_translations(self):
        """Clear translations for selected rows only"""
        selected_rows = set()
        for item in self.editor.table.selectedItems():
            selected_rows.add(item.row())
        
        if not selected_rows:
            QMessageBox.information(self, "情報", "行が選択されていません。")
            return
        
        confirm = QMessageBox.question(
            self, "確認", f"選択した {len(selected_rows)} 行の翻訳をクリアしますか？"
        )
        
        if confirm == QMessageBox.Yes:
            self.editor._programmatic_update = True
            for row in selected_rows:
                self.editor.table.item(row, 2).setText("")
                self.editor._previous_cell_texts[(row, 2)] = ""
                original = self.editor.table.item(row, 1).text()
                self.editor._update_row_color(row, "", original)
            self.editor._programmatic_update = False
            self.editor.undo_stack.clear()
            self.editor._emit_stats()
            self.refresh_all_mod_colors()
            self.statusBar().showMessage(f"{len(selected_rows)} 行の翻訳をクリアしました", 3000)

    def clear_current_mod_translations(self):
        """Clear all translations for current MOD"""
        if not self.current_mod_path:
            return
        
        mod_data = self.loaded_mods[self.current_mod_path]
        total = len(mod_data["original"])
        
        confirm = QMessageBox.question(
            self, "確認", f"現在のMOD「{mod_data['name']}」の翻訳 {total} 件をすべてクリアしますか？"
        )
        
        if confirm == QMessageBox.Yes:
            self.editor._programmatic_update = True
            for row in range(self.editor.table.rowCount()):
                self.editor.table.item(row, 2).setText("")
                self.editor._previous_cell_texts[(row, 2)] = ""
                original = self.editor.table.item(row, 1).text()
                self.editor._update_row_color(row, "", original)
            self.editor._programmatic_update = False
            mod_data["translations"] = {}
            self.editor.undo_stack.clear()
            self.editor._emit_stats()
            self.refresh_all_mod_colors()
            self.statusBar().showMessage(f"翻訳をクリアしました", 3000)

    def clear_all_mod_translations(self):
        """Clear translations for all MODs (excluding FTB Quests)"""
        mods_to_clear = [(p, d) for p, d in self.loaded_mods.items() 
                         if d.get("type") != "ftbquest"]
        
        if not mods_to_clear:
            QMessageBox.information(self, "情報", "クリア対象のMODがありません。")
            return
        
        total_keys = sum(len(d["translations"]) for _, d in mods_to_clear)
        
        confirm = QMessageBox.question(
            self, "確認", 
            f"{len(mods_to_clear)} 個のMODの翻訳をすべてクリアしますか？\n"
            f"(FTBクエストは除外されます)\n"
            f"合計: {total_keys} 件"
        )
        
        if confirm == QMessageBox.Yes:
            for path, mod_data in mods_to_clear:
                mod_data["translations"] = {}
            
            if self.current_mod_path and self.loaded_mods[self.current_mod_path].get("type") != "ftbquest":
                self.editor._programmatic_update = True
                for row in range(self.editor.table.rowCount()):
                    self.editor.table.item(row, 2).setText("")
                    self.editor._previous_cell_texts[(row, 2)] = ""
                    original = self.editor.table.item(row, 1).text()
                    self.editor._update_row_color(row, "", original)
                self.editor._programmatic_update = False
                self.editor.undo_stack.clear()
                self.editor._emit_stats()
            
            self.refresh_all_mod_colors()
            self.statusBar().showMessage(f"{len(mods_to_clear)} MODの翻訳をクリアしました", 3000)

    # --- File Handling ---
    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            event.accept()
        else:
            event.ignore()

    def dropEvent(self, event: QDropEvent):
        files = [u.toLocalFile() for u in event.mimeData().urls()]
        event.accept()
        if not files:
            return
        if len(files) == 1:
            QTimer.singleShot(0, lambda path=files[0]: self._process_dropped_file(path))
        else:
            QTimer.singleShot(0, lambda: self._process_dropped_files(files))

    def _process_dropped_files(self, files):
        for path in files:
            if self._is_busy():
                break
            self._process_dropped_file(path)

    def _is_busy(self):
        if hasattr(self, 'rp_thread') and self.rp_thread and self.rp_thread.isRunning():
            return True
        if hasattr(self, 'translator_thread') and self.translator_thread and self.translator_thread.isRunning():
            return True
        if hasattr(self, '_stack') and self._stack.currentIndex() == 1:
            return True
        return False

    def _process_dropped_file(self, path):
        if self._is_busy():
            self.statusBar().showMessage("処理中のため、ファイルのドロップをスキップしました", 5000)
            return
        try:
            self.process_path(path)
        except Exception as e:
            print(f"[ERROR] dropEvent process_path failed: {e}")
            import traceback
            traceback.print_exc()
            self._close_busy()
            QMessageBox.critical(self, "エラー", f"ファイルの処理に失敗しました:\n{e}")
    
    def process_path(self, path, silent=False):
        if os.path.isdir(path):
            ftbquest_folder = ftbquest_handler.detect_ftbquests(path)
            mods_folder = os.path.join(path, "mods")
            
            loaded_items = []
            
            total_steps = 0
            if ftbquest_folder:
                total_steps += 1
            if os.path.isdir(mods_folder):
                mod_files = [os.path.join(mods_folder, f) for f in os.listdir(mods_folder) 
                             if f.endswith('.jar') or f.endswith('.zip')]
                total_steps += len(mod_files)
            else:
                mod_files = []
            openloader_dir = os.path.join(path, "config", "openloader", "data")
            dp_dirs = []
            if os.path.isdir(openloader_dir):
                dp_dirs = [
                    os.path.join(openloader_dir, d)
                    for d in os.listdir(openloader_dir)
                    if datapack_handler.detect_datapack(os.path.join(openloader_dir, d))
                ]
                total_steps += len(dp_dirs)
            if not loaded_items and not mod_files and not ftbquest_folder and not dp_dirs:
                if datapack_handler.detect_datapack(path):
                    total_steps += 1
            
            if total_steps > 0 and not getattr(self, '_suppress_busy', False):
                self._show_busy("読み込み中", "ファイルを解析中...", show_cancel=False)
                self._busy_progress.setRange(0, total_steps)
                self._busy_progress.setValue(0)
                self._busy_progress.setFormat("0/{0}".format(total_steps))
            
            step = 0
            
            if ftbquest_folder:
                step += 1
                if not getattr(self, '_suppress_busy', False):
                    self._update_busy_message(f"FTBクエストを読み込み中... ({step}/{total_steps})")
                    self._update_busy_progress(step, total_steps)
                self.load_ftbquest(ftbquest_folder, os.path.basename(path))
                loaded_items.append("FTBクエスト")
            
            if mod_files:
                for i, mod_file in enumerate(mod_files):
                    step += 1
                    if not getattr(self, '_suppress_busy', False):
                        self._update_busy_message(f"MODを読み込み中... ({step}/{total_steps})")
                        self._update_busy_progress(step, total_steps)
                    self.load_source(mod_file)
                loaded_items.append(f"MOD {len(mod_files)}個")

            for dp_dir in dp_dirs:
                step += 1
                if not getattr(self, '_suppress_busy', False):
                    self._update_busy_message(f"データパックを読み込み中... ({step}/{total_steps})")
                    self._update_busy_progress(step, total_steps)
                self.load_datapack(dp_dir, mods_dir=mods_folder)
                loaded_items.append(f"データパック: {os.path.basename(dp_dir)}")

            if not loaded_items and datapack_handler.detect_datapack(path):
                self.load_datapack(path)
                loaded_items.append("データパック")
            
            if total_steps > 0 and not getattr(self, '_suppress_busy', False):
                self._close_busy()
            
            self.statusBar().showMessage("読み込み完了", 3000)
            
            if loaded_items and not silent:
                QMessageBox.information(self, "読み込み完了", 
                    f"読み込み完了: {', '.join(loaded_items)}")
                return
        
        source_type = self.detect_source_type(path)
        if source_type == "mod":
            self.load_source(path)
        elif source_type == "resourcepack":
            self.import_from_path(path)
        else:
            self.load_source(path)
    
    def load_ftbquest(self, quests_folder, modpack_name):
        """Load FTB Quest files and extract translatable text"""
        if quests_folder in self.loaded_mods:
            return
        
        try:
            lang_dict = ftbquest_handler.load_all_quests(quests_folder, modpack_name)
            
            if not lang_dict:
                print(f"Skipping FTB Quests: No translatable text found.")
                return
            
            self.loaded_mods[quests_folder] = {
                "name": f"[FTBクエスト] {modpack_name}",
                "original": lang_dict,
                "translations": {},
                "files": [],
                "target_file": f"ftbquests/{modpack_name}",
                "type": "ftbquest"
            }
            
            memory_translations = self.memory.apply_to(lang_dict, mod_name=modpack_name)
            if memory_translations:
                self.loaded_mods[quests_folder]["translations"].update(memory_translations)
            
            total = len(lang_dict)
            translated = self._count_translated(self.loaded_mods[quests_folder])
            
            item = QListWidgetItem(self._format_mod_display(f"[FTB] {modpack_name}", translated, total))
            item.setToolTip(f"FTB Quests: {modpack_name}\n{quests_folder}")
            item.setData(Qt.UserRole, quests_folder)
            
            if total > 0 and translated == total:
                item.setForeground(QColor("#4ade80"))
            else:
                item.setForeground(QColor("#d4d4d4"))
            
            self.mod_list.addItem(item)
            
            # Track load order
            if quests_folder not in self._mod_load_order:
                self._mod_load_order.append(quests_folder)
            
            # Check if SNBT is already applied by looking for backup files
            snbt_already_applied = self._check_snbt_applied(quests_folder)
            self.loaded_mods[quests_folder]["snbt_applied"] = snbt_already_applied
            
            # Show SNBT apply button when FTB Quest is loaded
            self._update_snbt_button_visibility()
            
            if self.mod_list.count() == 1:
                self.mod_list.setCurrentItem(item)
                
        except Exception as e:
            QMessageBox.critical(self, "エラー", f"FTBクエストの読み込みに失敗しました:\n{e}")
    
    def load_datapack(self, datapack_path, mods_dir=None):
        if datapack_path in self.loaded_mods:
            return

        try:
            result = datapack_handler.load_datapack(datapack_path, mods_dir=mods_dir)
            pack_name, primary_ns, lang_dict, item_sources = result

            if not lang_dict:
                print(f"Skipping datapack {pack_name}: No translatable text found.")
                return

            char_count = sum(len(v) for v in lang_dict.values())

            self.loaded_mods[datapack_path] = {
                "name": f"[データパック] {pack_name}",
                "original": lang_dict,
                "translations": {},
                "files": [],
                "target_file": f"assets/{primary_ns}/lang/en_us.json",
                "type": "datapack",
                "namespace": primary_ns,
                "_char_count": char_count,
                "_item_sources": item_sources,
            }

            memory_translations = self.memory.apply_to(lang_dict, mod_name=pack_name)
            if memory_translations:
                self.loaded_mods[datapack_path]["translations"].update(memory_translations)

            total = len(lang_dict)
            translated = self._count_translated(self.loaded_mods[datapack_path])

            item = QListWidgetItem(self._format_mod_display(f"[DP] {pack_name}", translated, total))
            item.setToolTip(f"データパック: {pack_name}\n名前空間: {primary_ns}\n{datapack_path}")
            item.setData(Qt.UserRole, datapack_path)

            if total > 0 and translated == total:
                item.setForeground(QColor("#4ade80"))
            else:
                item.setForeground(QColor("#d4d4d4"))

            self.mod_list.addItem(item)

            if datapack_path not in self._mod_load_order:
                self._mod_load_order.append(datapack_path)

            if self.mod_list.count() == 1:
                self.mod_list.setCurrentItem(item)

        except Exception as e:
            QMessageBox.critical(self, "エラー", f"データパックの読み込みに失敗しました:\n{e}")
        
    def detect_source_type(self, path):
        settings = self.settings_dialog.get_settings()
        target_lang = settings.get("target_lang", "ja_jp")
        
        has_en = False
        has_target = False
        has_mcmeta = False
        
        try:
            if os.path.isdir(path):
                for root, dirs, files in os.walk(path):
                    for f in files:
                        if f == 'pack.mcmeta':
                            has_mcmeta = True
                        if 'en_us' in f:
                            has_en = True
                        if target_lang in f:
                            has_target = True
            else:
                with zipfile.ZipFile(path, 'r') as zf:
                    for f in zf.namelist():
                        if 'pack.mcmeta' in f:
                            has_mcmeta = True
                        if 'en_us' in f:
                            has_en = True
                        if target_lang in f:
                            has_target = True
        except:
            return None
        
        if has_mcmeta and has_target and not has_en:
            return "resourcepack"
        elif has_en:
            return "mod"
        elif has_target:
            return "resourcepack"
        return None
    
    def import_from_path(self, path):
        if hasattr(self, 'rp_thread') and self.rp_thread and self.rp_thread.isRunning():
            return

        if not self.loaded_mods:
            QMessageBox.warning(self, "警告", "先にMODを読み込んでください。")
            return

        self.progress_bar.show()
        self.progress_bar.setRange(0, 0)
        self.statusBar().showMessage("リソースパックを解析中...")
        self._show_busy("リソースパック読込", "リソースパックを解析中...", show_cancel=False)

        self.rp_thread = ResourcePackImportThread(path, self.loaded_mods, self.file_handler, self.memory,
                                                   target_lang=self.settings_dialog.get_settings().get("target_lang", "ja_jp"))
        self.rp_thread.progress.connect(self.on_rp_import_progress)
        self.rp_thread.import_finished.connect(self.on_rp_import_finished)
        self.rp_thread.error.connect(self.on_rp_import_error)
        self.rp_thread.start()

    def on_rp_import_progress(self, current, total, phase="read"):
        if total > 0:
            self.progress_bar.setRange(0, total)
            self.progress_bar.setValue(current)
            self._update_busy_progress(current, total)
            if phase == "match":
                self._update_busy_message(f"翻訳をマッチング中... ({current}/{total})")
                self.statusBar().showMessage(f"翻訳をマッチング中... ({current}/{total})")
            else:
                self._update_busy_message(f"リソースパックを読み込み中... ({current}/{total})")
                self.statusBar().showMessage(f"リソースパックを読み込み中... ({current}/{total})")

    def on_rp_import_finished(self, all_translations, applied_count, matched_mods, mod_updates):
        import time as _time
        try:
            t0 = _time.time()
            self.progress_bar.hide()
            self._close_busy()
            self.statusBar().showMessage("リソースパックの適用が完了しました", 3000)

            for mod_path, updates in mod_updates.items():
                if mod_path in self.loaded_mods:
                    self.loaded_mods[mod_path]["translations"].update(updates)
            print(f"[RP] apply translations: {_time.time() - t0:.2f}s, mods={len(mod_updates)}")

            t1 = _time.time()
            if self.current_mod_path and self.current_mod_path in mod_updates and self.current_mod_path in self.loaded_mods:
                mod_data = self.loaded_mods[self.current_mod_path]
                self.editor.load_data(mod_data["original"], translations=mod_data["translations"])
                self.editor.review_status = mod_data.get("review_status", {})
                self.editor.filter_table()
            print(f"[RP] editor reload: {_time.time() - t1:.2f}s")
            
            t2 = _time.time()
            self.mod_list.setUpdatesEnabled(False)
            try:
                self._refresh_mod_list_items(set(mod_updates.keys()))
            finally:
                self.mod_list.setUpdatesEnabled(True)
            print(f"[RP] mod list refresh: {_time.time() - t2:.2f}s")

            if matched_mods:
                QMessageBox.information(self, "リソースパック適用", 
                                        f"{len(matched_mods)} MODに {applied_count} 項目を適用しました。")
            else:
                QMessageBox.information(self, "情報", "適用可能な翻訳が見つかりませんでした。")
        except Exception as e:
            print(f"[ERROR] on_rp_import_finished failed: {e}")
            import traceback
            traceback.print_exc()
            QMessageBox.critical(self, "エラー", f"リソースパック適用中にエラーが発生しました:\n{e}")
        finally:
            self.rp_thread = None

    def on_rp_import_error(self, message):
        try:
            self.progress_bar.hide()
            self._close_busy()
            QMessageBox.critical(self, "エラー", f"リソースパック読込に失敗: {message}")
        except Exception as e:
            print(f"[ERROR] on_rp_import_error failed: {e}")
        finally:
            self.rp_thread = None

    def load_source(self, path):
        if path in self.loaded_mods:
            return

        try:
            if os.path.isdir(path):
                mod_name, found_files = self.file_handler.load_folder(path)
            else:
                mod_name, found_files = self.file_handler.load_zip(path)
                
            if not found_files:
                print(f"Skipping {mod_name}: No translation files found.")
                return
            
            # Select target
            target = next((f for f in found_files if 'en_us.json' in f), 
                          next((f for f in found_files if 'en_us.lang' in f), found_files[0]))
            
            data = self.file_handler.read_translation_file(path, target)
            
            # Filter out empty values
            data = {k: v for k, v in data.items() if v and str(v).strip()}
            
            # Calculate character count once
            char_count = sum(len(str(v)) for v in data.values())
            
            # Store data
            self.loaded_mods[path] = {
                "name": mod_name,
                "original": data,
                "translations": {},
                "files": found_files,
                "target_file": target,
                "_char_count": char_count
            }

            # Auto-apply memory
            memory_translations = self.memory.apply_to(data, mod_name=mod_name)
            if memory_translations and self.memory:
                changed = self.memory.find_changed_sources(data, mod_name=mod_name)
                if changed:
                    changed_keys = set(changed.keys())
                    memory_translations = {
                        k: v for k, v in memory_translations.items()
                        if k not in changed_keys
                    }
                    print(f"TM: {len(changed)} 件の原文変更を検出、除外しました")
                
                if memory_translations:
                    reviewed_keys = self.memory.batch_get_review_status(memory_translations.keys(), mod_name=mod_name)
                    tm_review_status = {}
                    for key in memory_translations:
                        is_reviewed = reviewed_keys.get(key, {}).get("reviewed", False)
                        is_user = reviewed_keys.get(key, {}).get("origin") == "user"
                        tm_review_status[key] = {
                            "issues": [] if (is_reviewed or is_user) else ["TM未検証"],
                            "reviewed": is_reviewed or is_user
                        }
                    
                    self.loaded_mods[path]["translations"].update(memory_translations)
                    if tm_review_status:
                        self.loaded_mods[path]["review_status"] = tm_review_status
                    print(f"Applied {len(memory_translations)} translations from memory to {mod_name}")

            total = len(data)
            translated = self._count_translated(self.loaded_mods[path])
            
            item = QListWidgetItem(self._format_mod_display(mod_name, translated, total))
            item.setToolTip(f"{mod_name}\n原文: {char_count:,} 文字")
            item.setData(Qt.UserRole, path)
            
            if total > 0 and translated == total:
                item.setForeground(QColor("#4ade80"))
            else:
                item.setForeground(QColor("#d4d4d4"))
            
            self.mod_list.addItem(item)
            
            # Track load order
            if path not in self._mod_load_order:
                self._mod_load_order.append(path)
            
            # Select if it's the first one
            if self.mod_list.count() == 1:
                self.mod_list.setCurrentItem(item)
            
        except Exception as e:
            QMessageBox.critical(self, "エラー", f"ファイルの読み込みに失敗しました ({os.path.basename(path)}):\n{e}")

    def _show_busy(self, title="処理中", message="しばらくお待ちください...",
                   show_progress=True, show_cancel=True, cancellable_thread=None):
        self._busy_title.setText(title)
        self._busy_message.setText(message)
        if show_progress:
            self._busy_progress.show()
            self._busy_progress.setRange(0, 0)
            self._busy_progress.setFormat("準備中...")
        else:
            self._busy_progress.hide()
        if show_cancel and cancellable_thread:
            self._busy_cancel_btn.show()
            self._busy_cancel_btn.setEnabled(True)
            self._busy_cancel_btn.setText("キャンセル")
            self._cancellable_thread = cancellable_thread
        else:
            self._busy_cancel_btn.hide()
            self._cancellable_thread = None

        if self._session_token_usage.get('total_tokens', 0) > 0:
            su = self._session_token_usage
            self._busy_token_label.setText(
                f"Token消費 — 入力: {self._format_tokens(su['prompt_tokens'])} / "
                f"出力: {self._format_tokens(su['completion_tokens'])} / "
                f"合計: {self._format_tokens(su['total_tokens'])}  "
                f"(API: {su['api_calls']}回)"
            )
            self._busy_token_label.show()
        else:
            self._busy_token_label.hide()

        self._stack.setCurrentIndex(1)
        QApplication.processEvents()

    def _close_busy(self):
        self._stack.setCurrentIndex(0)
        self._cancellable_thread = None

    def _update_busy_message(self, message):
        if self._stack.currentIndex() == 1:
            self._busy_message.setText(message)

    def _update_busy_progress(self, value, total=None):
        if self._stack.currentIndex() == 1:
            if total is not None and total > 0:
                self._busy_progress.setRange(0, total)
                self._busy_progress.setValue(value)
                self._busy_progress.setFormat(f"{value}/{total}")
            else:
                self._busy_progress.setRange(0, 0)
                self._busy_progress.setFormat("処理中...")

    def _on_busy_cancel(self):
        thread = self._cancellable_thread
        if thread and hasattr(thread, 'stop'):
            thread.stop()
            self._busy_cancel_btn.setEnabled(False)
            self._busy_cancel_btn.setText("キャンセル中...")
            self._busy_message.setText("キャンセルしています...")

    # --- Translation Helpers ---
    def _run_translation(self, items, confirm_message):
        if not self.current_mod_path: return

        settings = self.settings_dialog.get_settings()
        api_key = settings["api_key"]
        model = settings["model"]
        
        if not api_key:
            QMessageBox.warning(self, "エラー", "API設定が必要です。\n設定ボタンからキーを入力してください。")
            self.settings_dialog.show()
            return
            
        confirm = QMessageBox.question(self, "確認", confirm_message)
        if confirm != QMessageBox.Yes:
            return

        # Start Thread
        self.translation_errors = []
        self.translation_total_items = len(items)
        self.translation_original_items = items.copy()
        self._partial_saved_keys = set()

        self._last_token_stats = None

        glossary_terms = self.glossary.get_terms()
        parallel_count = settings.get("parallel_count", 3)

        mod_name = None
        if self.current_mod_path and self.current_mod_path in self.loaded_mods:
            mod_name = self.loaded_mods[self.current_mod_path].get("name")
        
        source_type = None
        if self.current_mod_path and self.current_mod_path in self.loaded_mods:
            source_type = self.loaded_mods[self.current_mod_path].get("type")
        
        self.translator_thread = TranslatorThread(
            items, api_key, model, glossary_terms, parallel_count,
            memory=self.memory, mod_name=mod_name,
            target_lang=settings.get("target_lang", "ja_jp"),
            source_type=source_type
        )
        self.translator_thread.progress.connect(self.on_translation_progress)
        self.translator_thread.finished.connect(self.on_translate_finished)
        self.translator_thread.stopped.connect(self.on_translate_stopped)
        self.translator_thread.error.connect(self.on_translation_error)
        self.translator_thread.partial_save.connect(self.on_partial_save)
        self.translator_thread.validation_finished.connect(self.on_validation_finished)
        self.translator_thread.consistency_warnings.connect(self.on_consistency_warnings)
        self.translator_thread.token_stats.connect(self.on_token_stats)
        self.translator_thread.start()

        self.progress_bar.setRange(0, len(items))
        self.progress_bar.setValue(0)
        self.progress_bar.show()
        self.stop_translation_btn.show()
        self._show_busy("翻訳中", f"翻訳中... 0/{len(items)}", cancellable_thread=self.translator_thread)
        self.statusBar().showMessage(f"翻訳中... 0/{len(items)} (APIリクエスト中...)")

    def on_translation_progress(self, value, total=None):
        self.progress_bar.setValue(value)
        self._update_busy_progress(value, self.translation_total_items)
        self._update_busy_message(f"翻訳中... {value}/{self.translation_total_items}")
        self.statusBar().showMessage(f"翻訳中... {value}/{self.translation_total_items}")

    def on_translation_error(self, message):
        self.translation_errors.append(message)

    def stop_translation(self):
        if hasattr(self, 'translator_thread') and self.translator_thread and self.translator_thread.isRunning():
            self.translator_thread.stop()
            self.stop_translation_btn.setEnabled(False)
            self.stop_translation_btn.setText("中断中...")
            self._update_busy_message("翻訳を中断しています...")
            self.statusBar().showMessage("翻訳を中断しています...")

    def on_translate_stopped(self, partial_results):
        if partial_results:
            self.editor.update_translations(partial_results)
                
            if self.current_mod_path:
                self.loaded_mods[self.current_mod_path]["translations"] = self.editor.get_translations()

        self.progress_bar.hide()
        self.stop_translation_btn.hide()
        self.stop_translation_btn.setEnabled(True)
        self.stop_translation_btn.setText("中断")
        self._close_busy()
        
        # Show message
        if partial_results:
            QMessageBox.information(self, "中断", 
                f"翻訳を中断しました。\n{len(partial_results)} 件の翻訳は保存されました。")
        else:
            QMessageBox.information(self, "中断", "翻訳を中断しました。")
        
        self.translator_thread = None
        self.translation_original_items = None
        self.statusBar().showMessage("翻訳が中断されました", 3000)

    def on_partial_save(self, partial_results):
        """Handle progressive save during translation."""
        if partial_results and self.current_mod_path:
            current_translations = self.editor.get_translations()
            current_translations.update(partial_results)
            self.loaded_mods[self.current_mod_path]["translations"] = current_translations

            new_results = {k: v for k, v in partial_results.items()
                           if k not in self._partial_saved_keys}
            if new_results:
                self.editor.update_translations(new_results)
                self._partial_saved_keys.update(new_results.keys())

            saved_count = len(self._partial_saved_keys)
            self.statusBar().showMessage(f"翻訳自動保存: {saved_count} 件保存済み", 3000)

    def on_validation_finished(self, validation_results):
        """Handle validation results from translator thread."""
        self.editor.update_translations({}, validation_results=validation_results)
        
        issue_count = sum(1 for v in validation_results.values() if v.get("issues"))
        if issue_count > 0:
            self.statusBar().showMessage(f"翻訳品質チェック: {issue_count} 件に警告あり（「要確認」フィルターで確認できます）", 5000)

    def on_consistency_warnings(self, warnings):
        if not warnings:
            return
        msg = "用語一貫性警告:\n" + "\n".join(f"・{w}" for w in warnings[:10])
        print(msg)
        QMessageBox.warning(self, "用語不統一", msg)

    @staticmethod
    def _format_tokens(count):
        if count >= 1_000_000:
            return f"{count / 1_000_000:.1f}M"
        if count >= 1_000:
            return f"{count / 1_000:.1f}k"
        return str(count)

    def on_token_stats(self, stats):
        self._last_token_stats = stats

        self._session_token_usage['prompt_tokens'] += stats.get('prompt_tokens', 0)
        self._session_token_usage['completion_tokens'] += stats.get('completion_tokens', 0)
        self._session_token_usage['total_tokens'] += stats.get('total_tokens', 0)
        self._session_token_usage['api_calls'] += 1

        prompt = self._format_tokens(self._session_token_usage['prompt_tokens'])
        completion = self._format_tokens(self._session_token_usage['completion_tokens'])
        total = self._format_tokens(self._session_token_usage['total_tokens'])
        calls = self._session_token_usage['api_calls']

        self._cumulative_token_label.setText(f"Token: {total}")
        self._cumulative_token_label.setToolTip(
            f"セッション累計 — 入力: {prompt} / 出力: {completion} / 合計: {total}  (API: {calls}回)"
        )

        if self._stack.currentIndex() == 1:
            self._busy_token_label.setText(
                f"Token消費 — 入力: {prompt} / 出力: {completion} / 合計: {total}  (API: {calls}回)"
            )
            self._busy_token_label.show()

        self.statusBar().showMessage(
            f"トークン消費 — 入力: {prompt} / 出力: {completion} / 合計: {total}  (API: {calls}回)", 5000
        )

    def mark_selected_reviewed(self):
        """Mark selected rows as reviewed."""
        selected_rows = set()
        for item in self.editor.table.selectedItems():
            selected_rows.add(item.row())
        
        if not selected_rows:
            return
        
        keys = []
        for row in selected_rows:
            key = self.editor.table.item(row, 0).text()
            keys.append(key)
        
        self.editor.mark_reviewed(keys)
        
        if self.memory and keys:
            try:
                current_mod_name = None
                if self.current_mod_path and self.current_mod_path in self.loaded_mods:
                    current_mod_name = self.loaded_mods[self.current_mod_path].get("name")
                self.memory.mark_reviewed(keys, reviewed=True, mod_name=current_mod_name)
            except Exception as e:
                print(f"Failed to mark reviewed in TM: {e}")
        
        self.statusBar().showMessage(f"{len(keys)} 件を確認済みにしました", 3000)

    def _on_editor_selection_changed(self, count):
        """エディタの選択変更時にステータスバーとツールチップを更新する。"""
        if count > 0:
            self.statusBar().showMessage(f"{count} 行選択中（右クリックで翻訳可能）")
        else:
            self.statusBar().showMessage("", 0)


    def start_auto_translate_all(self):
        if not self.current_mod_path: return
        
        items = self.editor.get_visible_items()
        if not items:
            QMessageBox.information(self, "情報", "翻訳対象の項目がありません。")
            return
        
        current_translations = self.editor.get_translations()
        already_count = sum(1 for k in items if k in current_translations and current_translations[k])
        remaining = {k: v for k, v in items.items() if k not in current_translations or not current_translations.get(k)}
        
        if already_count > 0 and remaining:
            resume = QMessageBox.question(
                self, "翻訳済み項目",
                f"前回の {already_count} 件は翻訳済みです。\n"
                f"残り {len(remaining)} 件のみを翻訳しますか？\n\n"
                "「はい」= 未翻訳のみ翻訳\n「いいえ」= 全て再翻訳",
                QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel
            )
            if resume == QMessageBox.Yes:
                items = remaining
            elif resume == QMessageBox.Cancel:
                return
        elif already_count > 0 and not remaining:
            retranslate = QMessageBox.question(
                self, "全件翻訳済み",
                f"全 {len(items)} 件は既に翻訳済みです。\n再翻訳しますか？",
                QMessageBox.Yes | QMessageBox.No
            )
            if retranslate != QMessageBox.Yes:
                return
        
        char_count = sum(len(str(v)) for v in items.values())
        self._run_translation(items, 
            f"表示中の {len(items)} 項目（{char_count:,} 文字）を翻訳しますか？\n(API使用量にご注意ください)")

    def start_translate_selected(self):
        if not self.current_mod_path: return
        selected_items = self.editor.get_selected_items()
        if not selected_items:
            QMessageBox.information(self, "情報", "項目が選択されていません。")
            return
        self._run_translation(selected_items, f"選択された {len(selected_items)} 項目を翻訳しますか？")

    def start_batch_translate_all_mods(self):
        """Translate all visible MODs in the list (filtered MODs)."""
        visible_mod_paths = []
        for i in range(self.mod_list.count()):
            item = self.mod_list.item(i)
            if not item.isHidden():
                mod_path = item.data(Qt.UserRole)
                if mod_path in self.loaded_mods:
                    visible_mod_paths.append(mod_path)
        
        if not visible_mod_paths:
            QMessageBox.information(self, "情報", "翻訳対象のMODがありません。")
            return
        
        filter_type = self.mod_filter.currentData()
        
        self._batch_translate_queue = []
        total_char_count = 0
        
        for mod_path in visible_mod_paths:
            mod_data = self.loaded_mods[mod_path]
            original = mod_data["original"]
            translations = mod_data["translations"]
            
            mod_items = {}
            for key, text in original.items():
                if not text:
                    continue
                
                translation = translations.get(key, "")
                
                include_item = False
                
                if filter_type == "all" or filter_type == "mod" or filter_type == "ftbquest":
                    include_item = True
                elif filter_type == "incomplete":
                    include_item = not translation
                elif filter_type == "complete":
                    include_item = True
                elif filter_type == "has_same":
                    include_item = translation and translation == text
                elif filter_type == "has_roman":
                    if translation:
                        text_without_codes = re.sub(r'§.', '', translation)
                        text_without_codes = re.sub(r'%(\d+\$)?[sdfc]', '', text_without_codes)
                        include_item = bool(re.search(r'[A-Za-z]', text_without_codes))
                
                if include_item:
                    mod_items[key] = text
            
            if mod_items:
                self._batch_translate_queue.append({
                    'mod_path': mod_path,
                    'items': mod_items,
                    'mod_name': mod_data['name'],
                    'source_type': mod_data.get('type'),
                })
                total_char_count += sum(len(str(v)) for v in mod_items.values())
        
        if not self._batch_translate_queue:
            QMessageBox.information(self, "情報", "翻訳対象の項目がありません。")
            return
        
        total_items = sum(len(e['items']) for e in self._batch_translate_queue)
        total_mods = len(self._batch_translate_queue)
        
        confirm = QMessageBox.question(
            self, "全MOD一括翻訳",
            f"表示中の {total_mods} MODから\n"
            f"{total_items} 項目（{total_char_count:,} 文字）を翻訳しますか？\n\n"
            "(API使用量にご注意ください)",
            QMessageBox.Yes | QMessageBox.No
        )
        
        if confirm != QMessageBox.Yes:
            return
        
        if self.current_mod_path:
            self.loaded_mods[self.current_mod_path]["translations"] = self.editor.get_translations()
        
        self._batch_total_mods = total_mods
        self._batch_completed_mods = 0
        self._batch_all_results = {}
        self.translation_errors = []
        self._last_token_stats = None
        
        self._start_next_batch_mod()

    def _start_next_batch_mod(self):
        """キューから次のMODを取り出して翻訳開始。"""
        if not hasattr(self, '_batch_translate_queue') or not self._batch_translate_queue:
            return
        
        entry = self._batch_translate_queue.pop(0)
        mod_path = entry['mod_path']
        items = entry['items']
        
        self.current_mod_path = mod_path
        self._batch_current_mod_path = mod_path
        
        settings = self.settings_dialog.get_settings()
        api_key = settings["api_key"]
        model = settings["model"]
        
        if not api_key:
            QMessageBox.warning(self, "エラー", "API設定が必要です。\n設定ボタンからキーを入力してください。")
            self.settings_dialog.show()
            return

        self.translation_original_items = items.copy()
        self.translation_total_items = len(items)
        self._partial_saved_keys = set()
        
        glossary_terms = self.glossary.get_terms()
        parallel_count = settings.get("parallel_count", 3)
        
        mod_name = entry['mod_name']
        
        self.translator_thread = TranslatorThread(
            items, api_key, model, glossary_terms, parallel_count,
            memory=self.memory, mod_name=mod_name,
            target_lang=settings.get("target_lang", "ja_jp"),
            source_type=entry['source_type'],
        )
        self.translator_thread.progress.connect(self.on_translation_progress)
        self.translator_thread.finished.connect(self._on_batch_mod_finished)
        self.translator_thread.stopped.connect(self._on_batch_mod_stopped)
        self.translator_thread.error.connect(self.on_translation_error)
        self.translator_thread.partial_save.connect(self._on_batch_mod_partial_save)
        self.translator_thread.validation_finished.connect(self.on_validation_finished)
        self.translator_thread.consistency_warnings.connect(self.on_consistency_warnings)
        self.translator_thread.token_stats.connect(self.on_token_stats)
        self.translator_thread.start()

        self.progress_bar.setRange(0, len(items))
        self.progress_bar.setValue(0)
        self.progress_bar.show()
        self.stop_translation_btn.show()
        self._show_busy(
            "一括翻訳中",
            f"MOD翻訳中 ({self._batch_completed_mods + 1}/{self._batch_total_mods}): {mod_name}",
            cancellable_thread=self.translator_thread
        )
        self.statusBar().showMessage(
            f"一括翻訳中 ({self._batch_completed_mods + 1}/{self._batch_total_mods}): {mod_name}"
        )

    def _on_batch_mod_partial_save(self, partial_results):
        if not partial_results:
            return
        mod_path = getattr(self, '_batch_current_mod_path', None)
        if not mod_path or mod_path not in self.loaded_mods:
            return
        
        mod_data = self.loaded_mods[mod_path]
        original = mod_data["original"]
        for key in original.keys():
            if key in partial_results:
                mod_data["translations"][key] = partial_results[key]
        
        if self.current_mod_path == mod_path:
            new_results = {k: v for k, v in partial_results.items()
                           if k not in self._partial_saved_keys and k in original}
            if new_results:
                self.editor.update_translations(new_results)
                self._partial_saved_keys.update(new_results.keys())

        saved_count = len(self._partial_saved_keys)
        total_items = getattr(self, 'translation_total_items', 0)
        remaining_mods = len(self._batch_translate_queue) if self._batch_translate_queue else 0

        self._update_busy_message(
            f"一括翻訳中... {saved_count}/{total_items} "
            f"(残り {remaining_mods} MOD)"
        )
        self.statusBar().showMessage(
            f"一括翻訳: {saved_count}/{total_items} 件完了 (残り {remaining_mods} MOD)", 3000
        )

    def _on_batch_mod_finished(self, results):
        mod_path = getattr(self, '_batch_current_mod_path', None)
        if mod_path and mod_path in self.loaded_mods:
            self.loaded_mods[mod_path]["translations"].update(results)
            if self.current_mod_path == mod_path:
                self.editor.update_translations(results)
        
        self._batch_all_results.update(results)
        self._batch_completed_mods += 1
        
        self.refresh_all_mod_colors()
        
        if self._batch_translate_queue:
            self._start_next_batch_mod()
        else:
            self._finish_batch_translate()

    def _on_batch_mod_stopped(self, partial_results):
        mod_path = getattr(self, '_batch_current_mod_path', None)
        if mod_path and partial_results and mod_path in self.loaded_mods:
            self.loaded_mods[mod_path]["translations"].update(partial_results)
            if self.current_mod_path == mod_path:
                self.editor.update_translations(partial_results)
        
        self._batch_all_results.update(partial_results or {})
        self.refresh_all_mod_colors()
        self._finish_batch_translate(interrupted=True, partial_results=partial_results)

    def _finish_batch_translate(self, interrupted=False, partial_results=None):
        self.progress_bar.hide()
        self.stop_translation_btn.hide()
        self.stop_translation_btn.setEnabled(True)
        self.stop_translation_btn.setText("中断")
        self._close_busy()
        
        completed_count = len(self._batch_all_results)
        total_mods = getattr(self, '_batch_total_mods', 0)
        completed_mods = getattr(self, '_batch_completed_mods', 0)

        ts = getattr(self, '_last_token_stats', None)
        token_summary = ""
        if ts and ts.get('total_tokens', 0) > 0:
            prompt = self._format_tokens(ts['prompt_tokens'])
            completion = self._format_tokens(ts['completion_tokens'])
            total = self._format_tokens(ts['total_tokens'])
            calls = ts.get('api_calls', 0)
            token_summary = (
                f"\n\nトークン消費量:\n"
                f"  入力: {prompt} / 出力: {completion} / 合計: {total}\n"
                f"  API呼び出し: {calls}回"
            )
        
        if interrupted:
            if partial_results:
                QMessageBox.information(self, "中断",
                    f"一括翻訳を中断しました。\n"
                    f"{completed_mods}/{total_mods} MOD、{completed_count} 件の翻訳は保存されました。{token_summary}")
            else:
                QMessageBox.information(self, "中断", "一括翻訳を中断しました。")
            self.statusBar().showMessage("一括翻訳が中断されました", 3000)
        else:
            self.statusBar().showMessage("一括翻訳完了", 3000)
            if self.translation_errors:
                error_count = len(self.translation_errors)
                details = "\n".join(self.translation_errors[:3])
                if error_count > 3:
                    details += f"\n...他 {error_count - 3} 件"
                QMessageBox.warning(self, "完了 (一部エラーあり)",
                    f"{total_mods} MODの一括翻訳完了。{completed_count} 件翻訳、{error_count} 件エラー。\n\n{details}{token_summary}")
            else:
                QMessageBox.information(self, "完了",
                    f"{total_mods} MODの一括翻訳が完了しました！\n翻訳件数: {completed_count} 件{token_summary}")
        
        self._batch_translate_queue = None
        self._batch_total_mods = None
        self._batch_completed_mods = None
        self._batch_all_results = None
        self._batch_current_mod_path = None
        self.translator_thread = None
        self.translation_original_items = None


    def show_frequent_terms(self):
        """翻訳前に原文から頻出する固有名詞を抽出し、辞書登録ダイアログを表示する。"""
        if not self.loaded_mods:
            QMessageBox.information(self, "情報", "MODが読み込まれていません。")
            return

        settings = self.settings_dialog.get_settings()
        api_key = settings.get("api_key")
        freq_model = settings.get("freq_model")
        if not freq_model:
            freq_model = "deepseek/deepseek-chat"

        Toggle = QCheckBox

        dlg = QDialog(self)
        dlg.setWindowTitle("頻出語抽出")
        dlg.setModal(True)
        dlg_layout = QVBoxLayout(dlg)

        dlg_layout.addWidget(QLabel("対象を選択:"))

        self._ft_target_combo = QComboBox()
        has_current = self.current_mod_path is not None
        if has_current:
            self._ft_target_combo.addItem("現在のMOD", "current")
        self._ft_target_combo.addItem("全MOD", "all")
        dlg_layout.addWidget(self._ft_target_combo)

        dlg_layout.addSpacing(8)

        self._ft_translate_toggle = Toggle("AIで仮翻訳も同時に実行")
        self._ft_translate_toggle.setChecked(False)
        if api_key and freq_model:
            self._ft_translate_toggle.setEnabled(True)
        else:
            self._ft_translate_toggle.setEnabled(False)
            self._ft_translate_toggle.setToolTip("API設定が必要です")
        dlg_layout.addWidget(self._ft_translate_toggle)

        dlg_layout.addSpacing(12)

        btn_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btn_box.button(QDialogButtonBox.Ok).setText("実行")
        btn_box.accepted.connect(dlg.accept)
        btn_box.rejected.connect(dlg.reject)
        dlg_layout.addWidget(btn_box)

        if dlg.exec() != QDialog.Accepted:
            return

        is_current = self._ft_target_combo.currentData() == "current"
        do_translate = self._ft_translate_toggle.isChecked()

        if is_current:
            mod_data = self.loaded_mods[self.current_mod_path]
            all_original = dict(mod_data.get("original", {}))
        else:
            all_original = {}
            for path, mod_data in self.loaded_mods.items():
                all_original.update(mod_data.get("original", {}))

        if not all_original:
            QMessageBox.information(self, "情報", "原文テキストが見つかりません。")
            return

        frequent = extract_frequent_terms_from_original(
            all_original, min_count=2, existing_glossary=self.glossary.get_terms()
        )

        if not frequent:
            QMessageBox.information(self, "情報", "頻出する固有名詞候補が見つかりませんでした。")
            return

        initial_translations = {}
        if do_translate:
            terms = [term for term, _, _ in frequent]
            thread = FrequentTermTranslateThread(terms, api_key, freq_model)

            progress_dlg = QMessageBox(self)
            progress_dlg.setWindowTitle("AI仮翻訳")
            progress_dlg.setText("AIで仮翻訳を生成中...")
            progress_dlg.setStandardButtons(QMessageBox.Cancel)

            _result_holder = {}

            def on_progress(msg):
                progress_dlg.setText(msg)

            def on_finished(translations):
                _result_holder["data"] = translations
                progress_dlg.done(QMessageBox.Ok)

            def on_error(err):
                progress_dlg.done(QMessageBox.Ok)

            thread.finished.connect(on_finished)
            thread.error.connect(on_error)
            thread.progress.connect(on_progress)
            thread.start()

            result = progress_dlg.exec()
            if result == QMessageBox.Cancel:
                thread.stop()
            thread.wait(3000)
            initial_translations = _result_holder.get("data", {})

        dialog = FrequentTermDialog(
            frequent, self.glossary, self,
            api_key=api_key, freq_model=freq_model,
            initial_translations=initial_translations,
        )
        if dialog.exec():
            added = dialog.get_added_count()
            if added > 0:
                self.statusBar().showMessage(
                    f"{added} 件の用語を辞書に追加しました", 5000
                )


    def start_manual_term_extraction(self):
        """Start manual AI term extraction via button click."""
        if not self.current_mod_path: return
        
        # Get all current translations (merged with original where missing?)
        # For extraction, we ideally want original + current translation
        mod_data = self.loaded_mods[self.current_mod_path]
        original_data = mod_data["original"]
        current_translations = self.editor.get_translations() # This gets ALL non-empty translations from table
        
        if not current_translations:
             QMessageBox.information(self, "情報", "翻訳済みの項目がありません。\n先に翻訳を行ってください。")
             return

        confirm = QMessageBox.question(
            self, "辞書作成 (AI)",
            f"{len(current_translations)} 件の翻訳から辞書を作成しますか？\n"
            "(DeepSeek APIを使用)",
            QMessageBox.Yes | QMessageBox.No
        )
        
        if confirm == QMessageBox.Yes:
            # We need to pass original items corresponding to the translations
            # Create a dict of relevant original items
            relevant_originals = {}
            filtered_translations = {}
            
            for key, trans in current_translations.items():
                if key in original_data and trans:
                    relevant_originals[key] = original_data[key]
                    filtered_translations[key] = trans
            
            # Use temp variable to store originals (used by extractor thread logic if needed, 
            # though AITermExtractorThread takes them as args, 
            # BUT _show_term_extraction_dialog relies on self.translation_original_items for regex fallback/filtering)
            # Actually, `_start_ai_term_extraction` uses `self.translation_original_items`.
            # We should set it or pass it directly.
            # Let's check `_start_ai_term_extraction` implementation again.
            # It uses `self.translation_original_items`. So we must set it.
            
            self.translation_original_items = relevant_originals
            self._start_ai_term_extraction(filtered_translations)

    def on_translate_finished(self, results):
        self.editor.update_translations(results)
        
        if self.current_mod_path:
            self.loaded_mods[self.current_mod_path]["translations"] = self.editor.get_translations()
            self.loaded_mods[self.current_mod_path]["review_status"] = self.editor.review_status

        self.progress_bar.hide()
        self.stop_translation_btn.hide()
        self._close_busy()
        
        result_count = len(results)
        auto_saved = len(self._partial_saved_keys) if hasattr(self, '_partial_saved_keys') else 0

        token_summary = ""
        ts = getattr(self, '_last_token_stats', None)
        if ts and ts.get('total_tokens', 0) > 0:
            prompt = self._format_tokens(ts['prompt_tokens'])
            completion = self._format_tokens(ts['completion_tokens'])
            total = self._format_tokens(ts['total_tokens'])
            calls = ts.get('api_calls', 0)
            token_summary = (
                f"\n\nトークン消費量:\n"
                f"  入力: {prompt} / 出力: {completion} / 合計: {total}\n"
                f"  API呼び出し: {calls}回"
            )

        if self.translation_errors:
            error_count = len(self.translation_errors)
            details = "\n".join(self.translation_errors[:3])
            if error_count > 3:
                details += f"\n...他 {error_count - 3} 件"
            QMessageBox.warning(self, "完了 (一部エラーあり)",
                                f"自動翻訳は完了しましたが、{error_count} 件のエラーが発生しました。\n"
                                f"エラーが発生した箇所は翻訳されていません。\n\n詳細:\n{details}{token_summary}")
        else:
            QMessageBox.information(self, "完了",
                                    f"自動翻訳が完了しました！{token_summary}")
        
        if auto_saved > 0:
            self.statusBar().showMessage(f"翻訳完了: {result_count} 件（うち {auto_saved} 件を自動保存済み）", 5000)
        else:
            self.statusBar().showMessage(f"翻訳完了: {result_count} 件", 3000)
        
        self._show_post_translation_summary(results)
        
        self.translator_thread = None
        self.translation_original_items = None
        self._partial_saved_keys = set()
    
    def create_dictionary_from_all_mods(self):
        """Create dictionary from ALL loaded MODs, excluding quests."""
        if not self.loaded_mods:
            QMessageBox.information(self, "情報", "MODが読み込まれていません。")
            return
            
        # Filter and collect text
        target_items = {}
        processed_mods = 0
        excluded_mods = 0
        
        for path, mod_data in self.loaded_mods.items():
            # Exclude FTB Quests explicitly
            if mod_data.get("type") == "ftbquest":
                excluded_mods += 1
                continue
            
            # Add original text
            # We want to extract terms from Original English text
            # Some might have translations, we can pass them to improve context if available,
            # but for term extraction, Original text is the primary source.
            # However, AITermExtractorThread logic primarily looks at "original" -> "Japanese" pairs 
            # to find Proper Nouns that need to be consistent.
            # If we only have English, AI needs to infer what are the proper nouns.
            # Wait, `extract_terms_from_batch` uses regex on color codes in BOTH original and translation.
            # `AITermExtractorThread` asks AI to "Extract proper nouns... from the following text pairs".
            # If we don't have translations yet, we can only provide English.
            # But the user likely wants to create a dictionary BEFORE translating everything, to ensure consistency.
            # OR, they might have some translations.
            
            # Let's collect items.
            original_data = mod_data.get("original", {})
            translation_data = mod_data.get("translations", {})
            
            for key, orig in original_data.items():
                if not orig or not isinstance(orig, str): continue
                
                # If translation exists, use it. If not, pass None or empty string?
                # AITermExtractorThread handles {key: {"original": ..., "translation": ...}}?
                # No, AITermExtractorThread's __init__ takes:
                # (original_items, translated_items, ...)
                # original_items: dict {key: orig}
                # translated_items: dict {key: trans}
                
                target_items[key] = {
                    "original": orig,
                    "translation": translation_data.get(key, "")
                }
            
            processed_mods += 1

        if not target_items:
            QMessageBox.information(self, "情報", "対象となるテキストが見つかりませんでした。\n(すべてのMODが除外されたか、空です)")
            return

        # Confirm
        confirm = QMessageBox.question(
            self, "全MODから辞書作成",
            f"対象MOD: {processed_mods} 件 (除外: {excluded_mods} 件)\n"
            f"テキスト項目数: {len(target_items)} 件\n\n"
            "AIを使用して辞書を作成しますか？\n"
            "(項目数が多い場合、時間がかかることがあります)",
            QMessageBox.Yes | QMessageBox.No
        )
        
        if confirm == QMessageBox.Yes:
            # Prepare data for AITermExtractorThread
            original_items = {k: v["original"] for k, v in target_items.items()}
            translated_items = {k: v["translation"] for k, v in target_items.items() if v["translation"]}
            
            # Start extraction
            self._start_ai_term_extraction_custom(original_items, translated_items)

    def _start_ai_term_extraction_custom(self, original_items, translated_items):
        settings = self.settings_dialog.get_settings()
        api_key = settings.get("api_key")
        
        if not api_key:
            QMessageBox.warning(self, "エラー", "API設定が必要です。")
            return
        
        self.statusBar().showMessage(f"AIで辞書を作成中... (対象: {len(original_items)} 項目)")
        self._show_busy("AI辞書作成", f"AIで辞書を作成中... (対象: {len(original_items)} 項目)", show_cancel=False)
        
        existing_glossary = self.glossary.get_terms()
        
        self.ai_extractor_thread = AITermExtractorThread(
            original_items,
            translated_items,
            api_key,
            model="deepseek/deepseek-chat",
            existing_glossary=existing_glossary
        )
        self.ai_extractor_thread.finished.connect(self._on_ai_extraction_finished)
        self.ai_extractor_thread.error.connect(self._on_ai_extraction_error)
        self.ai_extractor_thread.progress.connect(self._on_ai_extraction_progress)
        self.ai_extractor_thread.start()
    
    def _show_post_translation_summary(self, translated_items):
        """翻訳完了後の用語提案を1つの統合ダイアログにまとめる。"""

        local_consistent = {}
        local_inconsistent = {}
        if hasattr(self, 'translation_original_items') and self.translation_original_items:
            local_consistent, local_inconsistent = extract_all_term_candidates(
                self.translation_original_items,
                translated_items,
                self.glossary.get_terms()
            )

        total_suggestions = len(local_consistent) + len(local_inconsistent)

        if total_suggestions == 0:
            return

        msg = QMessageBox(self)
        msg.setWindowTitle("翻訳完了・用語提案")

        summary_text = f"翻訳完了: {len(translated_items)} 件\n\n"

        if local_consistent:
            summary_text += f"一貫した用語: {len(local_consistent)} 件\n"
        if local_inconsistent:
            summary_text += f"翻訳ブレあり: {len(local_inconsistent)} 件\n"

        summary_text += "\n辞書に追加する用語を確認しますか？"
        msg.setText(summary_text)

        btn_review = msg.addButton("辞書を確認", QMessageBox.AcceptRole)
        btn_ai = msg.addButton("AIで詳細抽出", QMessageBox.ActionRole)
        btn_skip = msg.addButton("閉じる", QMessageBox.RejectRole)
        msg.exec()

        clicked = msg.clickedButton()

        if clicked == btn_review:
            dialog = TermExtractionDialog(
                local_consistent, self.glossary, self,
                inconsistent_terms=local_inconsistent
            )
            if dialog.exec():
                added = dialog.get_added_count()
                if added > 0:
                    self.statusBar().showMessage(
                        f"{added} 件を辞書に追加しました", 5000
                    )

        elif clicked == btn_ai:
            self._start_ai_term_extraction(translated_items)

    def _start_ai_term_extraction(self, translated_items):
        settings = self.settings_dialog.get_settings()
        api_key = settings.get("api_key")
        
        if not api_key:
            QMessageBox.warning(self, "エラー", "API設定が必要です。")
            return
        
        self.statusBar().showMessage("AIで辞書を作成中...")
        self._show_busy("AI辞書作成", "AIで辞書を作成中...", show_cancel=False)
        
        existing_glossary = self.glossary.get_terms()
        
        self.ai_extractor_thread = AITermExtractorThread(
            self.translation_original_items,
            translated_items,
            api_key,
            model="deepseek/deepseek-chat",
            existing_glossary=existing_glossary
        )
        self.ai_extractor_thread.finished.connect(self._on_ai_extraction_finished)
        self.ai_extractor_thread.error.connect(self._on_ai_extraction_error)
        self.ai_extractor_thread.progress.connect(self._on_ai_extraction_progress)
        self.ai_extractor_thread.start()
    
    def _on_ai_extraction_progress(self, msg):
        self.statusBar().showMessage(msg)
        self._update_busy_message(msg)

    def _on_ai_extraction_finished(self, extracted_terms):
        self._close_busy()
        self.ai_extractor_thread = None
        
        if not extracted_terms:
            self.statusBar().showMessage("辞書に追加する項目が見つかりませんでした", 3000)
            return
        
        # Show dialog for user to select terms
        dialog = TermExtractionDialog(extracted_terms, self.glossary, self)
        if dialog.exec():
            added_count = dialog.get_added_count()
            if added_count > 0:
                self.statusBar().showMessage(f"{added_count} 件を辞書に追加しました", 5000)
            else:
                self.statusBar().showMessage("辞書に追加されませんでした", 3000)
        else:
            self.statusBar().showMessage("辞書作成をスキップしました", 3000)
    
    def _on_ai_extraction_error(self, error_msg):
        self._close_busy()
        self.ai_extractor_thread = None
        self.statusBar().showMessage("辞書作成に失敗しました", 3000)
        QMessageBox.warning(self, "AI辞書作成エラー", f"辞書作成中にエラーが発生しました:\n{error_msg}")

    # --- Export ---
    def export_resource_pack(self):
        if not self.loaded_mods:
            return

        # Determine mode: Single or Merged
        mode = "single"
        if len(self.loaded_mods) > 1:
            msg_box = QMessageBox(self)
            msg_box.setWindowTitle("パック作成")
            msg_box.setText("リソースパックの作成方法を選択してください")
            btn_single = msg_box.addButton("現在のMODのみ", QMessageBox.AcceptRole)
            btn_merged = msg_box.addButton("読み込み済み全て", QMessageBox.ActionRole)
            msg_box.addButton("キャンセル", QMessageBox.RejectRole)
            msg_box.exec()
            
            if msg_box.clickedButton() == btn_single:
                mode = "single"
            elif msg_box.clickedButton() == btn_merged:
                mode = "merged"
            else:
                return

        if mode == "single":
            if not self.current_mod_path: return
            self._export_single()
        else:
            self._export_merged()

    def _get_export_dir(self):
        settings = self.settings_dialog.get_settings()
        export_dir = settings.get("export_dir", "")
        if export_dir and os.path.isdir(export_dir):
            return export_dir
        return ""

    def _export_single(self):
        mod_data = self.loaded_mods[self.current_mod_path]
        settings = self.settings_dialog.get_settings()

        if mod_data.get("type") == "datapack":
            namespace = mod_data.get("namespace", "unknown")
            default_folder_name = f"datapack-{namespace}-resources"
        else:
            default_folder_name = f"{mod_data['name']}-resources"
        
        start_dir = self._get_export_dir()
        
        parent_dir = QFileDialog.getExistingDirectory(self, "リソースパック保存先フォルダを選択", start_dir)
        
        if parent_dir:
            try:
                self._show_busy("リソースパック作成", "リソースパックを作成中...", show_cancel=False)

                is_existing_pack = (os.path.exists(os.path.join(parent_dir, "pack.mcmeta")) or
                                    os.path.exists(os.path.join(parent_dir, "assets")))

                if is_existing_pack:
                    save_path = parent_dir
                else:
                    save_path = os.path.join(parent_dir, default_folder_name)

                    if os.path.exists(save_path):
                        self._close_busy()
                        confirm = QMessageBox.question(self, "上書き確認", f"フォルダ '{default_folder_name}' は既に存在します。\n上書きしますか？")
                        if confirm != QMessageBox.Yes:
                            return
                        self._show_busy("リソースパック作成", "リソースパックを作成中...", show_cancel=False)

                current_translations = self.editor.get_translations()

                if mod_data.get("type") == "datapack":
                    namespace = mod_data.get("namespace", "unknown")
                    non_empty = {k: v for k, v in current_translations.items() if v}
                    datapack_handler.export_datapack_translations(
                        save_path, namespace, non_empty,
                        pack_format=settings.get("pack_format", 15),
                        target_lang=settings.get("target_lang", "ja_jp")
                    )
                else:
                    self.file_handler.save_resource_pack(
                        save_path, 
                        mod_data['name'], 
                        current_translations, 
                        mod_data['target_file'],
                        pack_format=settings.get("pack_format", 15),
                        target_lang=settings.get("target_lang", "ja_jp")
                    )
                
                self.memory.set_context(
                    mod_name=mod_data["name"],
                    model=None,
                    sources=mod_data["original"]
                )
                user_keys = self.editor.user_edited_keys
                user_trans = {k: v for k, v in current_translations.items() if k in user_keys}
                ai_trans = {k: v for k, v in current_translations.items() if k not in user_keys}
                if user_trans:
                    self.memory.update(user_trans, origin='user')
                if ai_trans:
                    self.memory.update(ai_trans, origin='ai')
                self.editor.user_edited_keys -= set(user_trans.keys())
                
                self._close_busy()
                QMessageBox.information(self, "成功", f"リソースパックを保存しました:\n{save_path}")
            except Exception as e:
                self._close_busy()
                QMessageBox.critical(self, "エラー", f"保存に失敗しました:\n{e}")

    def _export_merged(self):
        default_folder_name = "merged-resources"
        start_dir = self._get_export_dir()
        
        parent_dir = QFileDialog.getExistingDirectory(self, "リソースパック保存先フォルダを選択", start_dir)
        
        if not parent_dir:
            return
        
        try:
            self._show_busy("リソースパック作成", "リソースパックを作成中...", show_cancel=False)
            settings = self.settings_dialog.get_settings()
            target_lang = settings.get("target_lang", "ja_jp")
            pack_format = settings.get("pack_format", 15)
            
            if self.current_mod_path:
                self.loaded_mods[self.current_mod_path]["translations"] = self.editor.get_translations()
            
            is_existing_pack = (os.path.exists(os.path.join(parent_dir, "pack.mcmeta")) or
                               os.path.exists(os.path.join(parent_dir, "assets")))
            
            if is_existing_pack:
                mod_data_list = list(self.loaded_mods.values())
                integrated_count = 0
                ftb_count = 0
                
                for mod_data in mod_data_list:
                    if mod_data.get("type") in ("ftbquest", "datapack"):
                        continue
                        
                    translations = mod_data["translations"]
                    if not translations:
                        continue
                    
                    target_file = mod_data["target_file"]
                    lang_target = target_file.replace('en_us', target_lang)
                    
                    output_path = os.path.join(parent_dir, lang_target)
                    output_dir = os.path.dirname(output_path)
                    
                    existing_data = {}
                    if os.path.exists(output_path):
                        try:
                            with open(output_path, 'r', encoding='utf-8') as f:
                                existing_data = json.load(f)
                        except:
                            pass
                    
                    existing_data.update(translations)
                    
                    # Normalize escapes before saving
                    normalized = self.file_handler._normalize_translations(existing_data)
                    
                    os.makedirs(output_dir, exist_ok=True)
                    with open(output_path, 'w', encoding='utf-8') as f:
                        json.dump(normalized, f, ensure_ascii=False, indent=2)
                    
                    integrated_count += 1
                    self.memory.update(translations, origin='ai')
                
                # FTBクエストを処理
                for path, mod_data in self.loaded_mods.items():
                    if mod_data.get("type") != "ftbquest":
                        continue
                    
                    translations = mod_data["translations"]
                    if not translations:
                        continue
                    
                    modpack_name = mod_data["name"].replace("[FTBクエスト] ", "")
                    ftbquest_handler.export_ftbquest(
                        path,
                        parent_dir,
                        modpack_name,
                        translations,
                        target_lang=target_lang
                    )
                    ftb_count += 1
                    self.memory.update(translations)
                
                # データパックを処理
                dp_count = 0
                for path, mod_data in self.loaded_mods.items():
                    if mod_data.get("type") != "datapack":
                        continue
                    
                    translations = mod_data["translations"]
                    if not translations:
                        continue
                    
                    namespace = mod_data.get("namespace", "unknown")
                    non_empty = {k: v for k, v in translations.items() if v}
                    datapack_handler.export_datapack_translations(
                        parent_dir, namespace, non_empty,
                        pack_format=pack_format,
                        target_lang=target_lang
                    )
                    dp_count += 1
                    self.memory.update(translations)
                
                msg = f"既存リソースパックに統合しました:\n{parent_dir}\n"
                msg += f"MOD: {integrated_count} 件"
                if ftb_count > 0:
                    msg += f"\nFTBクエスト: {ftb_count} 件"
                if dp_count > 0:
                    msg += f"\nデータパック: {dp_count} 件"
                self._close_busy()
                QMessageBox.information(self, "成功", msg)
            else:
                save_path = os.path.join(parent_dir, default_folder_name)
                
                if os.path.exists(save_path):
                    self._close_busy()
                    confirm = QMessageBox.question(self, "上書き確認", f"フォルダ '{default_folder_name}' は既に存在します。\n上書きしますか？")
                    if confirm != QMessageBox.Yes:
                        return
                    self._show_busy("リソースパック作成", "リソースパックを作成中...", show_cancel=False)
                
                mod_data_list = list(self.loaded_mods.values())
                
                ftb_mods = [m for m in mod_data_list if m.get("type") == "ftbquest"]
                dp_mods = [m for m in mod_data_list if m.get("type") == "datapack"]
                regular_mods = [m for m in mod_data_list if m.get("type") not in ("ftbquest", "datapack")]
                
                if regular_mods:
                    self.file_handler.save_merged_resource_pack(
                        save_path, regular_mods,
                        pack_format=pack_format,
                        target_lang=target_lang
                    )
                
                for ftb_mod in ftb_mods:
                    quests_folder = None
                    for path, data in self.loaded_mods.items():
                        if data == ftb_mod:
                            quests_folder = path
                            break
                    
                    if quests_folder:
                        modpack_name = ftb_mod["name"].replace("[FTBクエスト] ", "")
                        ftbquest_handler.export_ftbquest(
                            quests_folder, 
                            save_path, 
                            modpack_name, 
                            ftb_mod["translations"],
                            target_lang=target_lang
                        )
                
                for dp_mod in dp_mods:
                    namespace = dp_mod.get("namespace", "unknown")
                    translations = dp_mod.get("translations", {})
                    non_empty = {k: v for k, v in translations.items() if v}
                    if non_empty:
                        datapack_handler.export_datapack_translations(
                            save_path, namespace, non_empty,
                            pack_format=pack_format,
                            target_lang=target_lang
                        )
                
                for mod in mod_data_list:
                    self.memory.update(mod["translations"])
                
                msg = f"{len(mod_data_list)} 個のMOD/クエストを保存しました:\n{save_path}"
                if ftb_mods:
                    msg += f"\n(FTBクエスト {len(ftb_mods)} 件の言語ファイルを含む)"
                if dp_mods:
                    msg += f"\n(データパック {len(dp_mods)} 件の言語ファイルを含む)"
                self._close_busy()
                QMessageBox.information(self, "成功", msg)
        except Exception as e:
            self._close_busy()
            QMessageBox.critical(self, "エラー", f"保存に失敗しました:\n{e}")

    def apply_ftbquest_snbt(self):
        """Apply FTB Quest translations by converting SNBT files in place with backup"""
        ftb_mods = [(path, data) for path, data in self.loaded_mods.items() 
                    if data.get("type") == "ftbquest"]
        
        if not ftb_mods:
            QMessageBox.warning(self, "警告", "FTBクエストが読み込まれていません。")
            return
        
        if self.current_mod_path:
            self.loaded_mods[self.current_mod_path]["translations"] = self.editor.get_translations()
        
        confirm = QMessageBox.warning(
            self, "SNBT適用確認",
            f"{len(ftb_mods)} 件のFTBクエストのSNBTファイルを変換します。\n"
            "元のファイルは .backup_日時 としてバックアップされます。\n\n"
            "翻訳キーはSNBT内のIDフィールドを使用するため、\n"
            "フォルダ名の変更に影響されません。\n\n"
            "続行しますか？",
            QMessageBox.Yes | QMessageBox.No
        )
        
        if confirm != QMessageBox.Yes:
            return
        
        try:
            self._show_busy("SNBT適用", "SNBTファイルを変換中...", show_cancel=False)
            total_converted = 0
            total_backups = 0
            
            for quests_folder, ftb_data in ftb_mods:
                modpack_name = ftb_data["name"].replace("[FTBクエスト] ", "")
                translations = ftb_data["translations"]
                
                converted, backups = ftbquest_handler.apply_snbt_with_backup(
                    quests_folder, modpack_name, translations
                )
                total_converted += converted
                total_backups += backups
                
                # Mark as applied
                ftb_data["snbt_applied"] = True
            
            # Update button style (remove warning)
            self._update_snbt_button_style()
            
            self._close_busy()
            QMessageBox.information(
                self, "SNBT適用完了",
                f"{total_converted} 個のSNBTファイルを変換しました。\n"
                f"{total_backups} 個のバックアップを作成しました。"
            )
        except Exception as e:
            self._close_busy()
            QMessageBox.critical(self, "エラー", f"SNBT適用に失敗しました:\n{e}")

    def import_resource_pack(self):
        if not self.loaded_mods:
            QMessageBox.warning(self, "警告", "先にMODを読み込んでください。")
            return

        msg_box = QMessageBox(self)
        msg_box.setWindowTitle("インポート形式")
        msg_box.setText("リソースパックの形式を選択してください")
        btn_zip = msg_box.addButton("Zipファイル", QMessageBox.AcceptRole)
        btn_dir = msg_box.addButton("フォルダ", QMessageBox.ActionRole)
        msg_box.addButton("キャンセル", QMessageBox.RejectRole)
        msg_box.exec()

        if msg_box.clickedButton() == btn_zip:
            file_path, _ = QFileDialog.getOpenFileName(self, "リソースパック (Zip) を選択", "", "Zip Files (*.zip)")
            if file_path:
                self.import_from_path(file_path)
        elif msg_box.clickedButton() == btn_dir:
            dir_path = QFileDialog.getExistingDirectory(self, "リソースパック (フォルダ) を選択")
            if dir_path:
                self.import_from_path(dir_path)

    def open_glossary(self):
        dialog = GlossaryDialog(self.glossary, self)
        dialog.exec()
