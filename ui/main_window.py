import os
import json
from PySide6.QtWidgets import (QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QToolBar, 
                               QFileDialog, QMessageBox, QLabel, QProgressBar, QMenu, QSplitter, QListWidget, QApplication)
from PySide6.QtGui import QAction, QDragEnterEvent, QDropEvent, QKeySequence, QShortcut
from PySide6.QtCore import Qt

from logic.file_handler import FileHandler
from logic.translator import TranslatorThread
from logic.translation_memory import TranslationMemory
from logic.glossary import Glossary
from logic import ftbquest_handler
from ui.editor_widget import EditorWidget
from ui.settings_dialog import SettingsDialog
from ui.glossary_dialog import GlossaryDialog
from ui.term_extraction_dialog import TermExtractionDialog
from logic.term_extractor import AITermExtractorThread
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
    
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Minecraft MOD 翻訳ツール")
        self.resize(1200, 800)
        self.setAcceptDrops(True)
        
        # Set window icon
        from PySide6.QtGui import QIcon
        icon_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "icon.ico")
        if os.path.exists(icon_path):
            self.setWindowIcon(QIcon(icon_path))

        self.file_handler = FileHandler()
        self.memory = TranslationMemory()
        self.glossary = Glossary()
        self.settings_dialog = SettingsDialog(self)
        self.translator_thread = None
        
        # State: { "path/to/mod.jar": { "name": "ModName", "original": {}, "translations": {}, "files": [], "target_file": "..." } }
        self.loaded_mods = {}
        self.current_mod_path = None
        self.translation_errors = []

        self._setup_ui()
        self._check_previous_session()

    def _setup_ui(self):
        # Central Widget
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(0,0,0,0)
        
        # Toolbar
        self.toolbar = QToolBar("Main Toolbar")
        self.toolbar.setMovable(False)
        self.addToolBar(self.toolbar)
        
        # Toolbar Actions
        open_action = QAction("開く", self)
        open_action.triggered.connect(self.open_file_dialog)
        self.toolbar.addAction(open_action)
        
        self.toolbar.addSeparator()
        
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
        
        export_action = QAction("リソースパック作成", self)
        export_action.triggered.connect(self.export_resource_pack)
        self.toolbar.addAction(export_action)

        # SNBT Apply Button (using QToolButton for styling)
        from PySide6.QtWidgets import QToolButton
        self.apply_snbt_btn = QToolButton()
        self.apply_snbt_btn.setText("SNBT適用")
        self.apply_snbt_btn.clicked.connect(self.apply_ftbquest_snbt)
        # Store the action returned by addWidget to control visibility
        self.apply_snbt_action = self.toolbar.addWidget(self.apply_snbt_btn)
        self.apply_snbt_action.setVisible(False)  # Hidden until FTB Quest is loaded

        # Splitter Layout
        splitter = QSplitter(Qt.Horizontal)
        main_layout.addWidget(splitter)

        # Left: MOD List
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        # Filter dropdown (no label needed)
        
        # Filter dropdown
        from PySide6.QtWidgets import QComboBox
        self.mod_filter = QComboBox()
        self.mod_filter.addItem("すべて", "all")
        self.mod_filter.addItem("未翻訳", "incomplete")
        self.mod_filter.addItem("翻訳済み", "complete")
        self.mod_filter.addItem("原文と同じ", "has_same")
        self.mod_filter.addItem("ローマ字あり", "has_roman")
        self.mod_filter.addItem("FTBクエスト", "ftbquest")
        self.mod_filter.addItem("MODのみ", "mod")
        self.mod_filter.currentIndexChanged.connect(self.filter_mod_list)
        left_layout.addWidget(self.mod_filter)
        
        # Sort dropdown
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
        
        # Track original load order
        self._mod_load_order = []
        
        # Batch translate button
        from PySide6.QtWidgets import QPushButton
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

        # Status Label
        self.mod_label = QLabel("MODファイルまたはMinecraftディレクトリをドラッグ＆ドロップしてください")
        self.mod_label.setAlignment(Qt.AlignCenter)
        self.mod_label.setStyleSheet("font-size: 16px; color: #94a3b8; padding: 20px;")
        right_layout.addWidget(self.mod_label)
        
        # Editor (Hidden initially)
        self.editor = EditorWidget()
        self.editor.hide()
        self.editor.table.customContextMenuRequested.connect(self.show_context_menu)
        self.editor.translationChanged.connect(self.update_current_mod_stats)
        self.editor.translate_btn.clicked.connect(self.start_auto_translate_all)
        self.editor.extract_terms_btn.clicked.connect(self.start_manual_term_extraction)
        self.editor.searchAllModsRequested.connect(self.search_all_mods)
        right_layout.addWidget(self.editor)
        
        # Connect undo/redo toolbar actions
        self.undo_action.triggered.connect(self.editor.undo_stack.undo)
        self.redo_action.triggered.connect(self.editor.undo_stack.redo)
        self.editor.undo_stack.canUndoChanged.connect(self.undo_action.setEnabled)
        self.editor.undo_stack.canRedoChanged.connect(self.redo_action.setEnabled)
        
        splitter.addWidget(right_widget)
        splitter.setStretchFactor(1, 4) # Make editor wider

        # Progress Bar and Stop Button
        from PySide6.QtWidgets import QPushButton
        progress_layout = QHBoxLayout()
        progress_layout.setContentsMargins(4, 4, 4, 4)
        
        self.progress_bar = QProgressBar()
        self.progress_bar.hide()
        progress_layout.addWidget(self.progress_bar)
        
        self.stop_translation_btn = QPushButton("中断")
        self.stop_translation_btn.setFixedWidth(80)
        self.stop_translation_btn.setStyleSheet("""
            QPushButton {
                background-color: #dc2626;
                color: white;
                border: none;
                padding: 6px 12px;
                border-radius: 4px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #b91c1c;
            }
            QPushButton:pressed {
                background-color: #991b1b;
            }
        """)
        self.stop_translation_btn.clicked.connect(self.stop_translation)
        self.stop_translation_btn.hide()
        progress_layout.addWidget(self.stop_translation_btn)
        
        main_layout.addLayout(progress_layout)
        
        self._setup_shortcuts()

    def _setup_shortcuts(self):
        save_shortcut = QShortcut(QKeySequence.Save, self)
        save_shortcut.activated.connect(self._save_session)
        
        find_shortcut = QShortcut(QKeySequence.Find, self)
        find_shortcut.activated.connect(lambda: self.editor.search_input.setFocus())

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
                self.progress_bar.show()
                self.progress_bar.setMaximum(len(valid_paths))
                for i, path in enumerate(valid_paths):
                    self.statusBar().showMessage(f"復元中... ({i+1}/{len(valid_paths)})")
                    self.progress_bar.setValue(i + 1)
                    QApplication.processEvents()
                    self.process_path(path)
                self.progress_bar.hide()
                self.statusBar().showMessage("セッション復元完了", 3000)
                # Update SNBT button visibility after session restore
                self._update_snbt_button_visibility()
        except:
            pass
    
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
        """Save current session (loaded MOD paths)"""
        try:
            paths = list(self.loaded_mods.keys())
            session = {"mod_paths": paths}
            with open(self.SESSION_FILE, 'w', encoding='utf-8') as f:
                json.dump(session, f, ensure_ascii=False, indent=2)
        except:
            pass

    def closeEvent(self, event):
        """Save session on close"""
        if self.current_mod_path:
            self.loaded_mods[self.current_mod_path]["translations"] = self.editor.get_translations()
            self.loaded_mods[self.current_mod_path]["review_status"] = self.editor.review_status
        
        self._save_session()
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
        from PySide6.QtCore import QTimer
        QTimer.singleShot(0, lambda: scrollbar.setValue(scroll_pos))

    def update_current_mod_stats(self, translated, total):
        if not self.current_mod_path: return
        
        from PySide6.QtGui import QColor
        
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
        """Count items with translations (including same as original for MOD list)"""
        translations = mod_data["translations"]
        return len([t for t in translations.values() if t])
    
    def refresh_all_mod_colors(self):
        from PySide6.QtGui import QColor
        
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
            total = len(mod_data["original"])
            translated = self._count_translated(mod_data)
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
                # Check if any translation contains Roman letters (excluding color codes and placeholders)
                import re
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
            elif filter_type == "mod":
                is_ftb = mod_data.get("type") == "ftbquest"
                item.setHidden(is_ftb)

    def sort_mod_list(self):
        """Sort MOD list based on selected sort option"""
        sort_type = self.mod_sort.currentData()
        
        # Collect all items with their data
        items_data = []
        for i in range(self.mod_list.count()):
            item = self.mod_list.item(i)
            mod_path = item.data(Qt.UserRole)
            if mod_path in self.loaded_mods:
                mod_data = self.loaded_mods[mod_path]
                total = len(mod_data["original"])
                translated = self._count_translated(mod_data)
                rate = translated / total if total > 0 else 0
                load_index = self._mod_load_order.index(mod_path) if mod_path in self._mod_load_order else 9999
                is_ftb = 0 if mod_data.get("type") == "ftbquest" else 1  # FTBクエストを優先 (0 = 上)
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
        
        from PySide6.QtWidgets import QListWidgetItem
        from PySide6.QtGui import QColor
        
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
        """全MODを横断検索し、検索語がヒットしたMODのみをMOD一覧に表示"""
        search_text = search_text.lower().strip()
        
        # 検索語が空の場合はすべて表示
        if not search_text:
            for i in range(self.mod_list.count()):
                self.mod_list.item(i).setHidden(False)
            self.statusBar().showMessage("検索をクリアしました", 3000)
            return
        
        # 現在のMODの翻訳を保存
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
            
            # MOD名で検索
            if search_text in mod_data["name"].lower():
                found = True
            
            # 原文で検索
            if not found:
                for key, original in mod_data["original"].items():
                    if search_text in key.lower() or search_text in original.lower():
                        found = True
                        break
            
            # 翻訳文で検索
            if not found:
                for key, translation in mod_data["translations"].items():
                    if translation and search_text in translation.lower():
                        found = True
                        break
            
            item.setHidden(not found)
            if found:
                matched_count += 1
        
        self.statusBar().showMessage(f"「{search_text}」: {matched_count}/{total_count} MODがマッチ", 5000)

    def _check_snbt_applied(self, quests_folder):
        """Check if SNBT has already been applied by looking for backup files."""
        import os
        # Look for backup files in the quests folder (both .snbt.bak and .backup_xxx formats)
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
            # Red border warning style
            self.apply_snbt_btn.setStyleSheet("""
                QToolButton {
                    border: 2px solid #ff4444;
                    background-color: #442222;
                    color: #ffaaaa;
                    padding: 4px 8px;
                }
                QToolButton:hover {
                    background-color: #553333;
                }
            """)
            self.apply_snbt_btn.setToolTip("⚠️ 未適用のクエストがあります！クリックして適用してください。")
        else:
            # Normal style
            self.apply_snbt_btn.setStyleSheet("")
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
        for f in files:
            self.process_path(f)
    
    def process_path(self, path):
        """Process a path: Minecraft folder, MOD, FTB Quest, or resource pack"""
        if os.path.isdir(path):
            ftbquest_folder = ftbquest_handler.detect_ftbquests(path)
            mods_folder = os.path.join(path, "mods")
            
            loaded_items = []
            
            if ftbquest_folder:
                self.statusBar().showMessage("FTBクエストを読み込み中...")
                self.load_ftbquest(ftbquest_folder, os.path.basename(path))
                loaded_items.append("FTBクエスト")
            
            if os.path.isdir(mods_folder):
                mod_files = [os.path.join(mods_folder, f) for f in os.listdir(mods_folder) 
                             if f.endswith('.jar') or f.endswith('.zip')]
                if mod_files:
                    self.progress_bar.show()
                    self.progress_bar.setMaximum(len(mod_files))
                    for i, mod_file in enumerate(mod_files):
                        self.statusBar().showMessage(f"MODを読み込み中... ({i+1}/{len(mod_files)})")
                        self.progress_bar.setValue(i + 1)
                        QApplication.processEvents()
                        self.load_source(mod_file)
                    self.progress_bar.hide()
                    loaded_items.append(f"MOD {len(mod_files)}個")
            
            self.statusBar().showMessage("読み込み完了", 3000)
            
            if loaded_items:
                QMessageBox.information(self, "Minecraftフォルダ検出", 
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
            
            memory_translations = self.memory.apply_to(lang_dict)
            if memory_translations:
                self.loaded_mods[quests_folder]["translations"].update(memory_translations)
            
            from PySide6.QtWidgets import QListWidgetItem
            from PySide6.QtGui import QColor
            
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
            
    def detect_source_type(self, path):
        """Detect if path is a MOD (has en_us) or resource pack (has target lang file)"""
        import zipfile
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
        """Import resource pack from path and apply to all loaded MODs (Asynchronous)"""
        if hasattr(self, 'rp_thread') and self.rp_thread and self.rp_thread.isRunning():
            return

        self.progress_bar.show()
        self.progress_bar.setRange(0, 0) # Indeterminate initially
        self.statusBar().showMessage("リソースパックを解析中...")
        self.toolbar.setEnabled(False)
        self.mod_list.setEnabled(False)
        self.editor.setEnabled(False)

        self.rp_thread = ResourcePackImportThread(path, self.loaded_mods, self.file_handler, self.memory,
                                                   target_lang=self.settings_dialog.get_settings().get("target_lang", "ja_jp"))
        self.rp_thread.progress.connect(self.on_rp_import_progress)
        self.rp_thread.finished.connect(self.on_rp_import_finished)
        self.rp_thread.error.connect(self.on_rp_import_error)
        self.rp_thread.start()

    def on_rp_import_progress(self, current, total):
        if total > 0:
            self.progress_bar.setRange(0, total)
            self.progress_bar.setValue(current)
            self.statusBar().showMessage(f"リソースパックを読み込み中... ({current}/{total})")

    def on_rp_import_finished(self, all_translations, applied_count, matched_mods):
        self.progress_bar.hide()
        self.toolbar.setEnabled(True)
        self.mod_list.setEnabled(True)
        self.editor.setEnabled(True)
        self.statusBar().showMessage("リソースパックの適用が完了しました", 3000)

        # Update current mod display if it was affected
        if self.current_mod_path and self.current_mod_path in self.loaded_mods:
            self.editor.update_translations(self.loaded_mods[self.current_mod_path]["translations"])
        
        self.refresh_all_mod_colors()

        if matched_mods:
            QMessageBox.information(self, "リソースパック適用", 
                                    f"{len(matched_mods)} MODに {applied_count} 項目を適用しました。")
        else:
            QMessageBox.information(self, "情報", "適用可能な翻訳が見つかりませんでした。")
        
        self.rp_thread = None

    def on_rp_import_error(self, message):
        self.progress_bar.hide()
        self.toolbar.setEnabled(True)
        self.mod_list.setEnabled(True)
        self.editor.setEnabled(True)
        QMessageBox.critical(self, "エラー", f"リソースパック読込に失敗: {message}")
        self.rp_thread = None

    def open_file_dialog(self):
        # Ask user what to open
        msg_box = QMessageBox(self)
        msg_box.setWindowTitle("開く")
        msg_box.setText("何を開きますか？")
        btn_file = msg_box.addButton("MODファイル", QMessageBox.AcceptRole)
        btn_folder = msg_box.addButton("Minecraftディレクトリ", QMessageBox.ActionRole)
        msg_box.addButton("キャンセル", QMessageBox.RejectRole)
        msg_box.exec()
        
        if msg_box.clickedButton() == btn_file:
            # Native file dialog for MOD files
            file_paths, _ = QFileDialog.getOpenFileNames(
                self, "MODファイルを開く", "", "MODファイル (*.zip *.jar);;すべてのファイル (*)"
            )
            for path in file_paths:
                self.process_path(path)
        elif msg_box.clickedButton() == btn_folder:
            # Native folder dialog for Minecraft directory
            folder_path = QFileDialog.getExistingDirectory(
                self, "Minecraftディレクトリを開く"
            )
            if folder_path:
                self.process_path(folder_path)

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
            memory_translations = self.memory.apply_to(data)
            if memory_translations:
                self.loaded_mods[path]["translations"].update(memory_translations)
                print(f"Applied {len(memory_translations)} translations from memory to {mod_name}")

            # Add to list
            from PySide6.QtWidgets import QListWidgetItem
            from PySide6.QtGui import QColor
            
            # Initial stats
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
        self.progress_bar.setRange(0, len(items))
        self.progress_bar.setValue(0)
        self.progress_bar.show()
        self.stop_translation_btn.show()  # Show stop button
        self.toolbar.setEnabled(False)
        self.editor.setEnabled(False)
        self.mod_list.setEnabled(False)

        glossary_terms = self.glossary.get_terms()
        parallel_count = settings.get("parallel_count", 3)
        self.translation_errors = [] # Reset errors
        self.translation_start_time = None
        self.translation_total_items = len(items)
        self.translation_original_items = items.copy()  # Store original items for term extraction
        
        # Get current MOD name for context
        mod_name = None
        if self.current_mod_path and self.current_mod_path in self.loaded_mods:
            mod_name = self.loaded_mods[self.current_mod_path].get("name")
        
        # Create translator thread with memory for progressive saving
        self.translator_thread = TranslatorThread(
            items, api_key, model, glossary_terms, parallel_count,
            memory=self.memory, mod_name=mod_name,
            target_lang=settings.get("target_lang", "ja_jp")
        )
        self.translator_thread.progress.connect(self.on_translation_progress)
        self.translator_thread.finished.connect(self.on_translate_finished)
        self.translator_thread.stopped.connect(self.on_translate_stopped)
        self.translator_thread.error.connect(self.on_translation_error)
        self.translator_thread.partial_save.connect(self.on_partial_save)
        self.translator_thread.validation_finished.connect(self.on_validation_finished)
        self.translator_thread.start()
        
        # Show immediate feedback
        self.statusBar().showMessage(f"翻訳中... 0/{len(items)} (APIリクエスト中...)")
        QApplication.processEvents()

    def on_translation_progress(self, value, total=None):
        import time
        
        if self.translation_start_time is None:
            self.translation_start_time = time.time()
        
        self.progress_bar.setValue(value)
        
        if value > 0:
            elapsed = time.time() - self.translation_start_time
            avg_per_item = elapsed / value
            remaining_items = self.translation_total_items - value
            eta_seconds = int(avg_per_item * remaining_items)
            
            if eta_seconds >= 60:
                eta_min = eta_seconds // 60
                eta_sec = eta_seconds % 60
                eta_str = f"{eta_min}分{eta_sec}秒"
            else:
                eta_str = f"{eta_seconds}秒"
            
            self.statusBar().showMessage(f"翻訳中... {value}/{self.translation_total_items} (残り約 {eta_str})")
        
        # Force UI update
        QApplication.processEvents()

    def on_translation_error(self, message):
        self.translation_errors.append(message)

    def stop_translation(self):
        """Stop the current translation process."""
        if hasattr(self, 'translator_thread') and self.translator_thread and self.translator_thread.isRunning():
            self.translator_thread.stop()
            self.stop_translation_btn.setEnabled(False)
            self.stop_translation_btn.setText("中断中...")
            self.statusBar().showMessage("翻訳を中断しています...")

    def on_translate_stopped(self, partial_results):
        """Handle translation stopped with partial results."""
        # Apply partial results to editor
        if partial_results:
            self.editor.update_translations(partial_results)
            
            # Update memory immediately
            if self.current_mod_path:
                self.loaded_mods[self.current_mod_path]["translations"] = self.editor.get_translations()

        # Reset UI
        self.progress_bar.hide()
        self.stop_translation_btn.hide()
        self.stop_translation_btn.setEnabled(True)
        self.stop_translation_btn.setText("中断")
        self.toolbar.setEnabled(True)
        self.editor.setEnabled(True)
        self.mod_list.setEnabled(True)
        
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

    def on_validation_finished(self, validation_results):
        """Handle validation results from translator thread."""
        self.editor.update_translations({}, validation_results=validation_results)
        
        issue_count = sum(1 for v in validation_results.values() if v.get("issues"))
        if issue_count > 0:
            self.statusBar().showMessage(f"翻訳品質チェック: {issue_count} 件に警告あり（「要確認」フィルターで確認できます）", 5000)

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
        self.statusBar().showMessage(f"{len(keys)} 件を確認済みにしました", 3000)


    def start_auto_translate_all(self):
        if not self.current_mod_path: return
        
        # 表示中の全項目を翻訳対象にする
        items = self.editor.get_visible_items()
        if not items:
            QMessageBox.information(self, "情報", "翻訳対象の項目がありません。")
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
        # Get visible (not hidden) MOD paths
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
        
        # Get current filter type to apply row-level filtering
        import re
        filter_type = self.mod_filter.currentData()
        
        # Collect items from visible MODs based on filter condition
        all_items = {}
        total_char_count = 0
        mod_item_counts = {}
        
        for mod_path in visible_mod_paths:
            mod_data = self.loaded_mods[mod_path]
            original = mod_data["original"]
            translations = mod_data["translations"]
            
            mod_items = {}
            for key, text in original.items():
                if not text:
                    continue
                
                translation = translations.get(key, "")
                
                # Apply row-level filter based on MOD list filter type
                include_item = False
                
                if filter_type == "all" or filter_type == "mod" or filter_type == "ftbquest":
                    # Include all items
                    include_item = True
                elif filter_type == "incomplete":
                    # Only untranslated items
                    include_item = not translation
                elif filter_type == "complete":
                    # All items from completed MODs (re-translate all)
                    include_item = True
                elif filter_type == "has_same":
                    # Only items where translation equals original
                    include_item = translation and translation == text
                elif filter_type == "has_roman":
                    # Only items where translation contains Roman letters (excluding color codes/placeholders)
                    if translation:
                        text_without_codes = re.sub(r'§.', '', translation)
                        text_without_codes = re.sub(r'%(\d+\$)?[sdfc]', '', text_without_codes)
                        include_item = bool(re.search(r'[A-Za-z]', text_without_codes))
                
                if include_item:
                    mod_items[key] = text
            
            if mod_items:
                all_items.update(mod_items)
                total_char_count += sum(len(str(v)) for v in mod_items.values())
                mod_item_counts[mod_path] = len(mod_items)
        
        if not all_items:
            QMessageBox.information(self, "情報", "翻訳対象の項目がありません。")
            return
        
        # Confirm
        confirm = QMessageBox.question(
            self, "全MOD一括翻訳",
            f"表示中の {len(visible_mod_paths)} MODから\n"
            f"{len(all_items)} 項目（{total_char_count:,} 文字）を翻訳しますか？\n\n"
            "(API使用量にご注意ください)",
            QMessageBox.Yes | QMessageBox.No
        )
        
        if confirm != QMessageBox.Yes:
            return
        
        # Save current MOD editor state
        if self.current_mod_path:
            self.loaded_mods[self.current_mod_path]["translations"] = self.editor.get_translations()
        
        # Store batch translation info
        self._batch_translate_mod_paths = visible_mod_paths
        self._batch_translate_mod_item_counts = mod_item_counts
        self._batch_translate_all_items = all_items
        
        # Start translation with batch mode flag
        self._run_batch_translation(all_items)

    def _run_batch_translation(self, items):
        """Run translation for batch MOD translation."""
        settings = self.settings_dialog.get_settings()
        api_key = settings["api_key"]
        model = settings["model"]
        
        if not api_key:
            QMessageBox.warning(self, "エラー", "API設定が必要です。\n設定ボタンからキーを入力してください。")
            self.settings_dialog.show()
            return

        # Start Thread
        self.progress_bar.setRange(0, len(items))
        self.progress_bar.setValue(0)
        self.progress_bar.show()
        self.stop_translation_btn.show()  # Show stop button
        self.toolbar.setEnabled(False)
        self.editor.setEnabled(False)
        self.mod_list.setEnabled(False)
        self.batch_translate_btn.setEnabled(False)

        glossary_terms = self.glossary.get_terms()
        parallel_count = settings.get("parallel_count", 3)
        self.translation_errors = []
        self.translation_start_time = None
        self.translation_total_items = len(items)
        self.translation_original_items = items.copy()
        
        self.translator_thread = TranslatorThread(items, api_key, model, glossary_terms, parallel_count,
                                                   target_lang=settings.get("target_lang", "ja_jp"))
        self.translator_thread.progress.connect(self.on_translation_progress)
        self.translator_thread.finished.connect(self._on_batch_translate_finished)
        self.translator_thread.stopped.connect(self._on_batch_translate_stopped)
        self.translator_thread.error.connect(self.on_translation_error)
        self.translator_thread.validation_finished.connect(self.on_validation_finished)
        self.translator_thread.start()
        
        # Show immediate feedback
        self.statusBar().showMessage(f"翻訳中... 0/{len(items)} (APIリクエスト中...)")
        QApplication.processEvents()

    def _on_batch_translate_finished(self, results):
        """Handle batch translation completion."""
        # Distribute results back to each MOD
        for mod_path in self._batch_translate_mod_paths:
            mod_data = self.loaded_mods[mod_path]
            original = mod_data["original"]
            
            for key in original.keys():
                if key in results:
                    mod_data["translations"][key] = results[key]
        
        # Update current editor if it's one of the translated MODs
        if self.current_mod_path in self._batch_translate_mod_paths:
            self.editor.update_translations(self.loaded_mods[self.current_mod_path]["translations"])
        
        self.progress_bar.hide()
        self.stop_translation_btn.hide()  # Hide stop button
        self.toolbar.setEnabled(True)
        self.editor.setEnabled(True)
        self.mod_list.setEnabled(True)
        self.batch_translate_btn.setEnabled(True)
        self.statusBar().showMessage("一括翻訳完了", 3000)  # Clear "translating" message
        
        self.refresh_all_mod_colors()
        
        if self.translation_errors:
            error_count = len(self.translation_errors)
            details = "\n".join(self.translation_errors[:3])
            if error_count > 3:
                details += f"\n...他 {error_count - 3} 件"
                
            QMessageBox.warning(self, "完了 (一部エラーあり)", 
                                f"一括翻訳完了。{len(results)} 件翻訳、{error_count} 件エラー。\n\n{details}")
        else:
            QMessageBox.information(self, "完了", 
                                    f"{len(self._batch_translate_mod_paths)} MODの一括翻訳が完了しました！\n"
                                    f"翻訳件数: {len(results)} 件")
        
        # Cleanup
        self._batch_translate_mod_paths = None
        self._batch_translate_mod_item_counts = None
        self._batch_translate_all_items = None
        self.translator_thread = None
        self.translation_original_items = None

    def _on_batch_translate_stopped(self, partial_results):
        """Handle batch translation stopped with partial results."""
        # Distribute partial results back to each MOD
        if partial_results and hasattr(self, '_batch_translate_mod_paths') and self._batch_translate_mod_paths:
            for mod_path in self._batch_translate_mod_paths:
                mod_data = self.loaded_mods[mod_path]
                original = mod_data["original"]
                
                for key in original.keys():
                    if key in partial_results:
                        mod_data["translations"][key] = partial_results[key]
            
            # Update current editor if it's one of the translated MODs
            if self.current_mod_path in self._batch_translate_mod_paths:
                self.editor.update_translations(self.loaded_mods[self.current_mod_path]["translations"])

        # Reset UI
        self.progress_bar.hide()
        self.stop_translation_btn.hide()
        self.stop_translation_btn.setEnabled(True)
        self.stop_translation_btn.setText("中断")
        self.toolbar.setEnabled(True)
        self.editor.setEnabled(True)
        self.mod_list.setEnabled(True)
        self.batch_translate_btn.setEnabled(True)
        
        self.refresh_all_mod_colors()
        
        # Show message
        if partial_results:
            QMessageBox.information(self, "中断", 
                f"一括翻訳を中断しました。\n{len(partial_results)} 件の翻訳は保存されました。")
        else:
            QMessageBox.information(self, "中断", "一括翻訳を中断しました。")
        
        # Cleanup
        self._batch_translate_mod_paths = None
        self._batch_translate_mod_item_counts = None
        self._batch_translate_all_items = None
        self.translator_thread = None
        self.translation_original_items = None
        self.statusBar().showMessage("一括翻訳が中断されました", 3000)


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
        self.stop_translation_btn.hide()  # Hide stop button
        self.toolbar.setEnabled(True)
        self.editor.setEnabled(True)
        self.mod_list.setEnabled(True)
        self.statusBar().showMessage("翻訳完了", 3000)  # Clear "translating" message
        
        if self.translation_errors:
            error_count = len(self.translation_errors)
            # Show the first few errors
            details = "\n".join(self.translation_errors[:3])
            if error_count > 3:
                details += f"\n...他 {error_count - 3} 件"
                
            QMessageBox.warning(self, "完了 (一部エラーあり)", 
                                f"自動翻訳は完了しましたが、{error_count} 件のエラーが発生しました。\n"
                                f"エラーが発生した箇所は翻訳されていません。\n\n詳細:\n{details}")
        else:
            QMessageBox.information(self, "完了", "自動翻訳が完了しました！")
        
        # Extract terms from color codes and show dialog
        self._show_term_extraction_dialog(results)
        
        self.translator_thread = None
        self.translation_original_items = None
    
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
        """Start AI extraction with specific items."""
        settings = self.settings_dialog.get_settings()
        api_key = settings.get("api_key")
        
        if not api_key:
            QMessageBox.warning(self, "エラー", "API設定が必要です。")
            return
        
        self.statusBar().showMessage(f"AIで辞書を作成中... (対象: {len(original_items)} 項目)")
        self.toolbar.setEnabled(False)
        
        existing_glossary = self.glossary.get_terms()
        
        # Use DeepSeek
        self.ai_extractor_thread = AITermExtractorThread(
            original_items,
            translated_items,
            api_key,
            model="deepseek/deepseek-chat",
            existing_glossary=existing_glossary
        )
        self.ai_extractor_thread.finished.connect(self._on_ai_extraction_finished)
        self.ai_extractor_thread.error.connect(self._on_ai_extraction_error)
        self.ai_extractor_thread.progress.connect(lambda msg: self.statusBar().showMessage(msg))
        self.ai_extractor_thread.start()

    def _show_term_extraction_dialog(self, translated_items):
        """Show dialog to ask if user wants AI term extraction."""
        if not hasattr(self, 'translation_original_items') or not self.translation_original_items:
            return
        
        # Ask user if they want AI extraction
        confirm = QMessageBox.question(
            self, "辞書ツール",
            "AIを使用して辞書を作成しますか？\n"
            "抽出された用語は辞書に追加できます。\n\n"
            "(DeepSeek APIを使用 - 安価)",
            QMessageBox.Yes | QMessageBox.No
        )
        
        if confirm == QMessageBox.Yes:
            self._start_ai_term_extraction(translated_items)
    
    def _start_ai_term_extraction(self, translated_items):
        """Start AI-based term extraction in background."""
        settings = self.settings_dialog.get_settings()
        api_key = settings.get("api_key")
        
        if not api_key:
            QMessageBox.warning(self, "エラー", "API設定が必要です。")
            return
        
        self.statusBar().showMessage("AIで辞書を作成中...")
        self.toolbar.setEnabled(False)
        
        existing_glossary = self.glossary.get_terms()
        
        # Use DeepSeek by default (cheap and fast)
        self.ai_extractor_thread = AITermExtractorThread(
            self.translation_original_items,
            translated_items,
            api_key,
            model="deepseek/deepseek-chat",
            existing_glossary=existing_glossary
        )
        self.ai_extractor_thread.finished.connect(self._on_ai_extraction_finished)
        self.ai_extractor_thread.error.connect(self._on_ai_extraction_error)
        self.ai_extractor_thread.progress.connect(lambda msg: self.statusBar().showMessage(msg))
        self.ai_extractor_thread.start()
    
    def _on_ai_extraction_finished(self, extracted_terms):
        """Handle AI extraction completion."""
        self.toolbar.setEnabled(True)
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
        """Handle AI extraction error."""
        self.toolbar.setEnabled(True)
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
        default_folder_name = f"{mod_data['name']}-resources"
        
        start_dir = self._get_export_dir()
        
        parent_dir = QFileDialog.getExistingDirectory(self, "リソースパック保存先フォルダを選択", start_dir)
        
        if parent_dir:
            settings = self.settings_dialog.get_settings()
            try:
                save_path = os.path.join(parent_dir, default_folder_name)
                
                if os.path.exists(save_path):
                    confirm = QMessageBox.question(self, "上書き確認", f"フォルダ '{default_folder_name}' は既に存在します。\n上書きしますか？")
                    if confirm != QMessageBox.Yes:
                        return

                current_translations = self.editor.get_translations()
                self.file_handler.save_resource_pack(
                    save_path, 
                    mod_data['name'], 
                    current_translations, 
                    mod_data['target_file'],
                    pack_format=settings.get("pack_format", 15),
                    target_lang=settings.get("target_lang", "ja_jp")
                )
                
                self.memory.update(current_translations)
                
                QMessageBox.information(self, "成功", f"リソースパックを保存しました:\n{save_path}")
            except Exception as e:
                QMessageBox.critical(self, "エラー", f"保存に失敗しました:\n{e}")

    def _export_merged(self):
        default_folder_name = "merged-resources"
        start_dir = self._get_export_dir()
        
        parent_dir = QFileDialog.getExistingDirectory(self, "リソースパック保存先フォルダを選択", start_dir)
        
        if not parent_dir:
            return
        
        try:
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
                    if mod_data.get("type") == "ftbquest":
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
                    self.memory.update(translations)
                
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
                
                msg = f"既存リソースパックに統合しました:\n{parent_dir}\n"
                msg += f"MOD: {integrated_count} 件"
                if ftb_count > 0:
                    msg += f"\nFTBクエスト: {ftb_count} 件"
                QMessageBox.information(self, "成功", msg)
            else:
                save_path = os.path.join(parent_dir, default_folder_name)
                
                if os.path.exists(save_path):
                    confirm = QMessageBox.question(self, "上書き確認", f"フォルダ '{default_folder_name}' は既に存在します。\n上書きしますか？")
                    if confirm != QMessageBox.Yes:
                        return
                
                mod_data_list = list(self.loaded_mods.values())
                
                ftb_mods = [m for m in mod_data_list if m.get("type") == "ftbquest"]
                regular_mods = [m for m in mod_data_list if m.get("type") != "ftbquest"]
                
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
                
                for mod in mod_data_list:
                    self.memory.update(mod["translations"])
                
                msg = f"{len(mod_data_list)} 個のMOD/クエストを保存しました:\n{save_path}"
                if ftb_mods:
                    msg += f"\n(FTBクエスト {len(ftb_mods)} 件の言語ファイルを含む)"
                QMessageBox.information(self, "成功", msg)
        except Exception as e:
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
            "⚠️ 注意: 翻訳キーはフォルダ名から生成されます。\n"
            "フォルダ名を変更すると、リソースパックのインポート時に\n"
            "キーが一致しなくなります。\n\n"
            "続行しますか？",
            QMessageBox.Yes | QMessageBox.No
        )
        
        if confirm != QMessageBox.Yes:
            return
        
        try:
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
            
            QMessageBox.information(
                self, "SNBT適用完了",
                f"{total_converted} 個のSNBTファイルを変換しました。\n"
                f"{total_backups} 個のバックアップを作成しました。"
            )
        except Exception as e:
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
        
        all_translations = {}
        
        settings = self.settings_dialog.get_settings()
        target_lang = settings.get("target_lang", "ja_jp")
        
        try:
            if msg_box.clickedButton() == btn_zip:
                file_path, _ = QFileDialog.getOpenFileName(self, "リソースパック (Zip) を選択", "", "Zip Files (*.zip)")
                if not file_path: return
                
                import zipfile
                with zipfile.ZipFile(file_path, 'r') as zf:
                    for f in zf.namelist():
                        if f.endswith(f'{target_lang}.json') or f.endswith(f'{target_lang}.lang'):
                            try:
                                with zf.open(f) as zfile:
                                    content = zfile.read().decode('utf-8')
                                    if f.endswith('.json'):
                                        translations = json.loads(content)
                                    else:
                                        translations = self.file_handler._parse_lang(content)
                                    if translations:
                                        all_translations[f] = translations
                            except (json.JSONDecodeError, Exception):
                                continue

            elif msg_box.clickedButton() == btn_dir:
                dir_path = QFileDialog.getExistingDirectory(self, "リソースパック (フォルダ) を選択")
                if not dir_path: return
                
                for root, dirs, files in os.walk(dir_path):
                    for f in files:
                        if f.endswith(f'{target_lang}.json') or f.endswith(f'{target_lang}.lang'):
                            full_path = os.path.join(root, f)
                            rel_path = os.path.relpath(full_path, dir_path)
                            try:
                                with open(full_path, 'r', encoding='utf-8') as lang_file:
                                    content = lang_file.read()
                                    if f.endswith('.json'):
                                        translations = json.loads(content)
                                    else:
                                        translations = self.file_handler._parse_lang(content)
                                    if translations:
                                        all_translations[rel_path] = translations
                            except (json.JSONDecodeError, Exception):
                                continue
            else:
                return

            if not all_translations:
                QMessageBox.warning(self, "エラー", f"{target_lang} ファイルが見つかりませんでした。")
                return

            applied_count = 0
            matched_mods = []
            
            for mod_path, mod_data in self.loaded_mods.items():
                target_file = mod_data["target_file"]
                lang_target = target_file.replace('en_us', target_lang)
                mod_type = mod_data.get("type", "mod")
                
                for pack_path, translations in all_translations.items():
                    pack_path_normalized = pack_path.replace('\\', '/')
                    ja_target_normalized = lang_target.replace('\\', '/')
                    
                    matched = False
                    
                    # Standard path matching
                    if pack_path_normalized.endswith(ja_target_normalized) or ja_target_normalized.endswith(pack_path_normalized):
                        matched = True
                    # FTB Quest special matching
                    elif mod_type == "ftbquest" and "ftbquests" in pack_path_normalized:
                        matched = True
                    
                    if matched:
                        matching_keys = set(translations.keys()) & set(mod_data["original"].keys())
                        if matching_keys:
                            for key in matching_keys:
                                mod_data["translations"][key] = translations[key]
                            applied_count += len(matching_keys)
                            matched_mods.append(mod_data["name"])
                            self.memory.update({k: translations[k] for k in matching_keys})
                            break
                
                # Fallback: For FTB quests, try key matching across all pack translations
                if mod_type == "ftbquest" and mod_data["name"] not in matched_mods:
                    for pack_path, translations in all_translations.items():
                        matching_keys = set(translations.keys()) & set(mod_data["original"].keys())
                        if matching_keys:
                            for key in matching_keys:
                                mod_data["translations"][key] = translations[key]
                            applied_count += len(matching_keys)
                            matched_mods.append(mod_data["name"])
                            self.memory.update({k: translations[k] for k in matching_keys})
                            break   
            
            if self.current_mod_path and self.current_mod_path in self.loaded_mods:
                self.editor.update_translations(self.loaded_mods[self.current_mod_path]["translations"])
            
            self.refresh_all_mod_colors()
            
            if matched_mods:
                QMessageBox.information(self, "成功", f"{len(matched_mods)} MODに対して {applied_count} 項目をインポートしました。\n\n適用: {', '.join(matched_mods[:5])}{'...' if len(matched_mods) > 5 else ''}")
            else:
                QMessageBox.information(self, "情報", "読み込み済みMODに一致する翻訳は見つかりませんでした。")

        except Exception as e:
            QMessageBox.critical(self, "エラー", f"インポートに失敗しました:\n{e}")

    def open_glossary(self):
        dialog = GlossaryDialog(self.glossary, self)
        dialog.exec()
