import os
import json
from PySide6.QtWidgets import (QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QToolBar, 
                               QFileDialog, QMessageBox, QLabel, QProgressBar, QMenu, QSplitter, QListWidget, QApplication)
from PySide6.QtGui import QAction, QDragEnterEvent, QDropEvent
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
        
        settings_action = QAction("設定", self)
        settings_action.triggered.connect(self.settings_dialog.show)
        self.toolbar.addAction(settings_action)

        glossary_action = QAction("用語集", self)
        glossary_action.triggered.connect(self.open_glossary)
        self.toolbar.addAction(glossary_action)
        
        export_action = QAction("パック作成", self)
        export_action.triggered.connect(self.export_resource_pack)
        self.toolbar.addAction(export_action)

        self.apply_snbt_action = QAction("SNBT適用", self)
        self.apply_snbt_action.triggered.connect(self.apply_ftbquest_snbt)
        self.apply_snbt_action.setVisible(False)  # Hidden until FTB Quest is loaded
        self.toolbar.addAction(self.apply_snbt_action)

        # Splitter Layout
        splitter = QSplitter(Qt.Horizontal)
        main_layout.addWidget(splitter)

        # Left: MOD List
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.addWidget(QLabel("読み込み済みMOD:"))
        
        # Filter dropdown
        from PySide6.QtWidgets import QComboBox
        self.mod_filter = QComboBox()
        self.mod_filter.addItem("すべて", "all")
        self.mod_filter.addItem("未翻訳", "incomplete")
        self.mod_filter.addItem("翻訳済み", "complete")
        self.mod_filter.addItem("その他あり", "has_same")
        self.mod_filter.addItem("FTBクエスト", "ftbquest")
        self.mod_filter.addItem("MODのみ", "mod")
        self.mod_filter.currentIndexChanged.connect(self.filter_mod_list)
        left_layout.addWidget(self.mod_filter)
        
        self.mod_list = NoScrollListWidget()
        self.mod_list.currentItemChanged.connect(self.on_mod_selected)
        left_layout.addWidget(self.mod_list)
        
        splitter.addWidget(left_widget)

        # Right: Editor Area
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(0,0,0,0)

        # Status Label
        self.mod_label = QLabel("MODファイル (.jar / .zip) をドラッグ＆ドロップしてください")
        self.mod_label.setAlignment(Qt.AlignCenter)
        self.mod_label.setStyleSheet("font-size: 16px; color: #94a3b8; padding: 20px;")
        right_layout.addWidget(self.mod_label)
        
        # Editor (Hidden initially)
        self.editor = EditorWidget()
        self.editor.hide()
        self.editor.table.customContextMenuRequested.connect(self.show_context_menu)
        self.editor.translationChanged.connect(self.update_current_mod_stats)
        self.editor.translate_btn.clicked.connect(self.start_auto_translate_all)
        right_layout.addWidget(self.editor)
        
        splitter.addWidget(right_widget)
        splitter.setStretchFactor(1, 4) # Make editor wider

        # Progress Bar
        self.progress_bar = QProgressBar()
        self.progress_bar.hide()
        main_layout.addWidget(self.progress_bar)

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
        except:
            pass

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
        
        # Check for unapplied FTB Quest SNBT
        unapplied_ftb = [data["name"] for path, data in self.loaded_mods.items() 
                         if data.get("type") == "ftbquest" and not data.get("snbt_applied", True)]
        
        if unapplied_ftb:
            confirm = QMessageBox.warning(
                self, "⚠️ SNBT未適用",
                f"以下のFTBクエストでSNBT適用がされていません:\n\n"
                f"{''.join('• ' + name + chr(10) for name in unapplied_ftb)}\n"
                "SNBT適用を行わないと、ゲーム内のクエストに翻訳が反映されません。\n\n"
                "本当に終了しますか？",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No
            )
            if confirm != QMessageBox.Yes:
                event.ignore()
                return
        
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

        # Load current
        if current:
            path = current.data(Qt.UserRole)
            self.current_mod_path = path
            mod_data = self.loaded_mods[path]
            
            self.mod_label.hide()
            self.editor.show()
            
            # Load data into editor
            self.editor.load_data(mod_data["original"])
            
            # Restore translations
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
            elif filter_type == "ftbquest":
                is_ftb = mod_data.get("type") == "ftbquest"
                item.setHidden(not is_ftb)
            elif filter_type == "mod":
                is_ftb = mod_data.get("type") == "ftbquest"
                item.setHidden(is_ftb)

    # --- Context Menu ---
    def show_context_menu(self, pos):
        menu = QMenu(self)
        
        translate_selected_action = QAction("選択範囲を翻訳", self)
        translate_selected_action.triggered.connect(self.start_translate_selected)
        menu.addAction(translate_selected_action)
        
        menu.addSeparator()
        
        add_glossary_action = QAction("用語集に追加", self)
        add_glossary_action.triggered.connect(lambda: self.add_selection_to_glossary())
        menu.addAction(add_glossary_action)
        
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
        
        menu.exec(self.editor.table.mapToGlobal(pos))

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
            for row in selected_rows:
                self.editor.table.item(row, 2).setText("")
                original = self.editor.table.item(row, 1).text()
                self.editor._update_row_color(row, "", original)
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
            for row in range(self.editor.table.rowCount()):
                self.editor.table.item(row, 2).setText("")
                original = self.editor.table.item(row, 1).text()
                self.editor._update_row_color(row, "", original)
            mod_data["translations"] = {}
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
                for row in range(self.editor.table.rowCount()):
                    self.editor.table.item(row, 2).setText("")
                    original = self.editor.table.item(row, 1).text()
                    self.editor._update_row_color(row, "", original)
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
            
            # Show SNBT apply button when FTB Quest is loaded
            self.apply_snbt_action.setVisible(True)
            # Mark as not yet applied
            self.loaded_mods[quests_folder]["snbt_applied"] = False
            
            if self.mod_list.count() == 1:
                self.mod_list.setCurrentItem(item)
                
        except Exception as e:
            QMessageBox.critical(self, "エラー", f"FTBクエストの読み込みに失敗しました:\n{e}")
            
    def detect_source_type(self, path):
        """Detect if path is a MOD (has en_us) or resource pack (has ja_jp)"""
        import zipfile
        has_en = False
        has_ja = False
        has_mcmeta = False
        
        try:
            if os.path.isdir(path):
                for root, dirs, files in os.walk(path):
                    for f in files:
                        if f == 'pack.mcmeta':
                            has_mcmeta = True
                        if 'en_us' in f:
                            has_en = True
                        if 'ja_jp' in f:
                            has_ja = True
            else:
                with zipfile.ZipFile(path, 'r') as zf:
                    for f in zf.namelist():
                        if 'pack.mcmeta' in f:
                            has_mcmeta = True
                        if 'en_us' in f:
                            has_en = True
                        if 'ja_jp' in f:
                            has_ja = True
        except:
            return None
        
        if has_mcmeta and has_ja and not has_en:
            return "resourcepack"
        elif has_en:
            return "mod"
        elif has_ja:
            return "resourcepack"
        return None
    
    def import_from_path(self, path):
        """Import resource pack from path and apply to all loaded MODs"""
        import zipfile
        all_translations = {}
        
        try:
            if os.path.isdir(path):
                for root, dirs, files in os.walk(path):
                    for f in files:
                        if f.endswith('ja_jp.json') or f.endswith('ja_jp.lang'):
                            full_path = os.path.join(root, f)
                            rel_path = os.path.relpath(full_path, path)
                            try:
                                with open(full_path, 'r', encoding='utf-8') as lang_file:
                                    content = lang_file.read()
                                    if f.endswith('.json'):
                                        translations = json.loads(content)
                                    else:
                                        translations = self.file_handler._parse_lang(content)
                                    if translations:
                                        all_translations[rel_path] = translations
                            except:
                                continue
            else:
                with zipfile.ZipFile(path, 'r') as zf:
                    for f in zf.namelist():
                        if f.endswith('ja_jp.json') or f.endswith('ja_jp.lang'):
                            try:
                                with zf.open(f) as zfile:
                                    content = zfile.read().decode('utf-8')
                                    if f.endswith('.json'):
                                        translations = json.loads(content)
                                    else:
                                        translations = self.file_handler._parse_lang(content)
                                    if translations:
                                        all_translations[f] = translations
                            except:
                                continue
            
            if not all_translations:
                return
            
            applied_count = 0
            matched_mods = []
            
            for mod_path, mod_data in self.loaded_mods.items():
                target_file = mod_data["target_file"]
                ja_target = target_file.replace('en_us', 'ja_jp')
                mod_type = mod_data.get("type", "mod")
                
                for pack_path, translations in all_translations.items():
                    pack_path_normalized = pack_path.replace('\\', '/')
                    ja_target_normalized = ja_target.replace('\\', '/')
                    
                    matched = False
                    
                    if pack_path_normalized.endswith(ja_target_normalized) or ja_target_normalized.endswith(pack_path_normalized):
                        matched = True
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
                QMessageBox.information(self, "リソースパック適用", f"{len(matched_mods)} MODに {applied_count} 項目を適用しました。")
        except Exception as e:
            QMessageBox.critical(self, "エラー", f"リソースパック読込に失敗: {e}")

    def open_file_dialog(self):
        msg_box = QMessageBox(self)
        msg_box.setWindowTitle("開く")
        msg_box.setText("読み込み形式を選択")
        btn_file = msg_box.addButton("ファイル (ZIP/JAR)", QMessageBox.AcceptRole)
        btn_folder = msg_box.addButton("フォルダ", QMessageBox.ActionRole)
        msg_box.addButton("キャンセル", QMessageBox.RejectRole)
        msg_box.exec()
        
        paths = []
        if msg_box.clickedButton() == btn_file:
            file_paths, _ = QFileDialog.getOpenFileNames(self, "ファイルを開く", "", "Zip/Jar Files (*.zip *.jar)")
            paths = file_paths
        elif msg_box.clickedButton() == btn_folder:
            folder_path = QFileDialog.getExistingDirectory(self, "フォルダを開く")
            if folder_path:
                paths = [folder_path]
        else:
            return
        
        for path in paths:
            self.process_path(path)

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
        self.toolbar.setEnabled(False)
        self.editor.setEnabled(False)
        self.mod_list.setEnabled(False)

        self.editor.setEnabled(False)
        self.mod_list.setEnabled(False)

        glossary_terms = self.glossary.get_terms()
        self.translation_errors = [] # Reset errors
        self.translation_start_time = None
        self.translation_total_items = len(items)
        self.translation_original_items = items.copy()  # Store original items for term extraction
        self.translator_thread = TranslatorThread(items, api_key, model, glossary_terms)
        self.translator_thread.progress.connect(self.on_translation_progress)
        self.translator_thread.finished.connect(self.on_translate_finished)
        self.translator_thread.error.connect(self.on_translation_error)
        self.translator_thread.start()

    def on_translation_progress(self, value):
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

    def on_translation_error(self, message):
        self.translation_errors.append(message)

    def start_auto_translate_all(self):
        if not self.current_mod_path: return
        missing_items = self.editor.get_missing_items()
        if not missing_items:
            QMessageBox.information(self, "情報", "未翻訳の項目はありません。")
            return
        char_count = sum(len(str(v)) for v in missing_items.values())
        self._run_translation(missing_items, 
            f"{len(missing_items)} 項目（{char_count:,} 文字）を自動翻訳しますか？\n(API使用量にご注意ください)")

    def start_translate_selected(self):
        if not self.current_mod_path: return
        selected_items = self.editor.get_selected_items()
        if not selected_items:
            QMessageBox.information(self, "情報", "項目が選択されていません。")
            return
        self._run_translation(selected_items, f"選択された {len(selected_items)} 項目を翻訳しますか？")

    def on_translate_finished(self, results):
        self.editor.update_translations(results)
        
        # Update memory immediately
        if self.current_mod_path:
             self.loaded_mods[self.current_mod_path]["translations"] = self.editor.get_translations()

        self.progress_bar.hide()
        self.toolbar.setEnabled(True)
        self.editor.setEnabled(True)
        self.mod_list.setEnabled(True)
        
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
    
    def _show_term_extraction_dialog(self, translated_items):
        """Show dialog to ask if user wants AI term extraction."""
        if not hasattr(self, 'translation_original_items') or not self.translation_original_items:
            return
        
        # Ask user if they want AI extraction
        confirm = QMessageBox.question(
            self, "用語抽出",
            "AIを使用して用語（地名、アイテム名など）を抽出しますか？\n"
            "抽出された用語は用語集に追加できます。\n\n"
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
        
        self.statusBar().showMessage("AIで用語を抽出中...")
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
            self.statusBar().showMessage("用語が見つかりませんでした", 3000)
            return
        
        # Show dialog for user to select terms
        dialog = TermExtractionDialog(extracted_terms, self.glossary, self)
        if dialog.exec():
            added_count = dialog.get_added_count()
            if added_count > 0:
                self.statusBar().showMessage(f"{added_count} 件の用語を用語集に追加しました", 5000)
            else:
                self.statusBar().showMessage("用語は追加されませんでした", 3000)
        else:
            self.statusBar().showMessage("用語抽出をスキップしました", 3000)
    
    def _on_ai_extraction_error(self, error_msg):
        """Handle AI extraction error."""
        self.toolbar.setEnabled(True)
        self.ai_extractor_thread = None
        self.statusBar().showMessage("用語抽出に失敗しました", 3000)
        QMessageBox.warning(self, "AI抽出エラー", f"用語抽出中にエラーが発生しました:\n{error_msg}")

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
        
        # Ask for parent directory
        parent_dir = QFileDialog.getExistingDirectory(self, "リソースパック保存先フォルダを選択", start_dir)
        
        if parent_dir:
            try:
                save_path = os.path.join(parent_dir, default_folder_name)
                
                # Check if exists
                if os.path.exists(save_path):
                    confirm = QMessageBox.question(self, "上書き確認", f"フォルダ '{default_folder_name}' は既に存在します。\n上書きしますか？")
                    if confirm != QMessageBox.Yes:
                        return

                # Sync current editor state first
                current_translations = self.editor.get_translations()
                self.file_handler.save_resource_pack(
                    save_path, 
                    mod_data['name'], 
                    current_translations, 
                    mod_data['target_file']
                )
                
                # Update Memory
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
            if self.current_mod_path:
                self.loaded_mods[self.current_mod_path]["translations"] = self.editor.get_translations()
            
            is_existing_pack = (os.path.exists(os.path.join(parent_dir, "pack.mcmeta")) or
                               os.path.exists(os.path.join(parent_dir, "assets")))
            
            if is_existing_pack:
                mod_data_list = list(self.loaded_mods.values())
                integrated_count = 0
                
                for mod_data in mod_data_list:
                    translations = mod_data["translations"]
                    if not translations:
                        continue
                    
                    target_file = mod_data["target_file"]
                    ja_target = target_file.replace('en_us', 'ja_jp')
                    
                    output_path = os.path.join(parent_dir, ja_target)
                    output_dir = os.path.dirname(output_path)
                    
                    existing_data = {}
                    if os.path.exists(output_path):
                        try:
                            with open(output_path, 'r', encoding='utf-8') as f:
                                existing_data = json.load(f)
                        except:
                            pass
                    
                    existing_data.update(translations)
                    
                    os.makedirs(output_dir, exist_ok=True)
                    with open(output_path, 'w', encoding='utf-8') as f:
                        json.dump(existing_data, f, ensure_ascii=False, indent=2)
                    
                    integrated_count += 1
                    self.memory.update(translations)
                
                QMessageBox.information(self, "成功", f"既存リソースパックに {integrated_count} 個のMODの翻訳を統合しました:\n{parent_dir}")
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
                    self.file_handler.save_merged_resource_pack(save_path, regular_mods)
                
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
                            ftb_mod["translations"]
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
        
        try:
            if msg_box.clickedButton() == btn_zip:
                file_path, _ = QFileDialog.getOpenFileName(self, "リソースパック (Zip) を選択", "", "Zip Files (*.zip)")
                if not file_path: return
                
                import zipfile
                with zipfile.ZipFile(file_path, 'r') as zf:
                    for f in zf.namelist():
                        if f.endswith('ja_jp.json') or f.endswith('ja_jp.lang'):
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
                        if f.endswith('ja_jp.json') or f.endswith('ja_jp.lang'):
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
                QMessageBox.warning(self, "エラー", "ja_jp ファイルが見つかりませんでした。")
                return

            applied_count = 0
            matched_mods = []
            
            for mod_path, mod_data in self.loaded_mods.items():
                target_file = mod_data["target_file"]
                ja_target = target_file.replace('en_us', 'ja_jp')
                
                for pack_path, translations in all_translations.items():
                    pack_path_normalized = pack_path.replace('\\', '/')
                    ja_target_normalized = ja_target.replace('\\', '/')
                    
                    if pack_path_normalized.endswith(ja_target_normalized) or ja_target_normalized.endswith(pack_path_normalized):
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
