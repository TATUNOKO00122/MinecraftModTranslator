import re
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QTableWidget,
                                QTableWidgetItem, QHeaderView, QLineEdit, QPushButton, QLabel, QComboBox, QSizePolicy)
from PySide6.QtGui import QColor, QBrush, QUndoStack, QUndoCommand
from PySide6.QtCore import Qt, Signal

from logic.translator import should_skip_translation

AUTO_LOCK_PATTERNS = [
    re.compile(r'^[a-z]+:[a-z_./]+$', re.I),
    re.compile(r'^[\d%\.\\\+\-:,/\s]+$'),
    re.compile(r'^[\W\d\s]*$', re.U),
    re.compile(r'^§[0-9a-fk-or]$', re.I),
    re.compile(r'^~?-?\d*\.?\d*$', re.U),
    re.compile(r'^#[0-9a-fA-F]{6}$'),
    re.compile(r'^@\w+$'),
    re.compile(r'^\w+\.\w+$'),
]


class TranslationEditCommand(QUndoCommand):
    def __init__(self, table, row, old_text, new_text, editor):
        super().__init__()
        self.table = table
        self.row = row
        self.old_text = old_text
        self.new_text = new_text
        self.editor = editor
        self._first_redo_done = False

    def undo(self):
        self.editor._programmatic_update = True
        key_item = self.table.item(self.row, 0)
        if key_item:
            self.editor.user_edited_keys.discard(key_item.text())
        item = self.table.item(self.row, 2)
        if item:
            item.setText(self.old_text)
            original = self.table.item(self.row, 1).text()
            self.editor._update_row_color(self.row, self.old_text, original)
        self.editor._previous_cell_texts[(self.row, 2)] = self.old_text
        self.editor._programmatic_update = False
        self.editor._emit_stats()

    def redo(self):
        if not self._first_redo_done:
            self._first_redo_done = True
            return
        self.editor._programmatic_update = True
        key_item = self.table.item(self.row, 0)
        if key_item:
            self.editor.user_edited_keys.add(key_item.text())
        item = self.table.item(self.row, 2)
        if item:
            item.setText(self.new_text)
            original = self.table.item(self.row, 1).text()
            self.editor._update_row_color(self.row, self.new_text, original)
        self.editor._previous_cell_texts[(self.row, 2)] = self.new_text
        self.editor._programmatic_update = False
        self.editor._emit_stats()


class EditorWidget(QWidget):
    translationChanged = Signal(int, int)
    searchAllModsRequested = Signal(str)
    selectionChanged = Signal(int)

    _COLOR_LOCKED = QColor("#4a4a5a")
    _COLOR_LOCKED_FG = QColor("#8888aa")
    _COLOR_UNTRANSLATED = QColor("#1a1a2e")
    _COLOR_UNTRANSLATED_FG = QColor("#666688")
    _COLOR_TM_MATCH = QColor("#2a4a5a")
    _COLOR_TM_MATCH_FG = QColor("#88ccdd")
    _COLOR_AI_TRANS = QColor("#2f6b36")
    _COLOR_AI_TRANS_FG = QColor("#ffffff")
    _COLOR_AI_ISSUES = QColor("#8b5a00")
    _COLOR_AI_ISSUES_FG = QColor("#ffcc66")
    _COLOR_USER_TRANS = QColor("#1a5a3a")
    _COLOR_USER_TRANS_FG = QColor("#88eebb")
    _COLOR_VALIDATED = QColor("#1a4a2a")
    _COLOR_VALIDATED_FG = QColor("#66dd99")
    _COLOR_SAME = QColor("#6b5a2f")
    _COLOR_SAME_FG = QColor("#ffffff")
    _COLOR_TRANSLATED = _COLOR_AI_TRANS
    _COLOR_TRANSLATED_FG = _COLOR_AI_TRANS_FG
    _COLOR_ISSUES = _COLOR_AI_ISSUES
    _COLOR_ISSUES_FG = _COLOR_AI_ISSUES_FG

    _BRUSH_LOCKED = QBrush(_COLOR_LOCKED, Qt.SolidPattern)
    _BRUSH_UNTRANSLATED = QBrush(_COLOR_UNTRANSLATED, Qt.SolidPattern)
    _BRUSH_TM_MATCH = QBrush(_COLOR_TM_MATCH, Qt.SolidPattern)
    _BRUSH_AI_TRANS = QBrush(_COLOR_AI_TRANS, Qt.SolidPattern)
    _BRUSH_AI_ISSUES = QBrush(_COLOR_AI_ISSUES, Qt.SolidPattern)
    _BRUSH_USER_TRANS = QBrush(_COLOR_USER_TRANS, Qt.SolidPattern)
    _BRUSH_VALIDATED = QBrush(_COLOR_VALIDATED, Qt.SolidPattern)
    _BRUSH_SAME = QBrush(_COLOR_SAME, Qt.SolidPattern)
    _BRUSH_TRANSLATED = _BRUSH_AI_TRANS
    _BRUSH_ISSUES = _BRUSH_AI_ISSUES

    def __init__(self, parent=None):
        super().__init__(parent)
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 15, 10, 10)
        
        self.undo_stack = QUndoStack(self)
        self._programmatic_update = False
        self._previous_cell_texts = {}
        self.review_status = {}
        self.user_edited_keys = set()
        
        # Toolbar
        toolbar = QHBoxLayout()
        
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("検索...")
        self.search_input.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.search_input.textChanged.connect(self.filter_table)
        toolbar.addWidget(self.search_input)
        
        # All MODs search button
        self.search_all_mods_btn = QPushButton("検索")
        self.search_all_mods_btn.setToolTip("検索語が含まれるMODのみを一覧に表示します")
        self.search_all_mods_btn.clicked.connect(self._on_search_all_mods_clicked)
        toolbar.addWidget(self.search_all_mods_btn)
        
        # Filter Combo
        self.filter_combo = QComboBox()
        self.filter_combo.addItem("すべて表示", 0)
        self.filter_combo.addItem("未翻訳のみ", 1)
        self.filter_combo.addItem("原文と同じ", 2)
        self.filter_combo.addItem("ローマ字あり", 3)
        self.filter_combo.addItem("要確認", 4)
        self.filter_combo.addItem("翻訳済み", 5)
        self.filter_combo.addItem("翻訳不要（ロック）", 6)
        self.filter_combo.addItem("TM自動適用", 7)
        self.filter_combo.addItem("AI翻訳のみ", 8)
        self.filter_combo.addItem("ユーザー翻訳", 9)
        self.filter_combo.currentIndexChanged.connect(self.filter_table)
        toolbar.addWidget(self.filter_combo)
        
        self.extract_terms_btn = QPushButton("辞書作成")
        self.extract_terms_btn.setToolTip("翻訳から辞書をAI作成します")
        toolbar.addWidget(self.extract_terms_btn)
        
        self.translate_btn = QPushButton("一括翻訳")
        toolbar.addWidget(self.translate_btn)
        
        layout.addLayout(toolbar)
        
        # Table
        self.table = QTableWidget()
        self.table.setColumnCount(3)
        self.table.setHorizontalHeaderLabels(["ID", "原文 (英語)", "翻訳 (日本語)"])
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.setAlternatingRowColors(False)

        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Interactive)
        header.setSectionResizeMode(1, QHeaderView.Interactive)
        header.setSectionResizeMode(2, QHeaderView.Stretch)
        header.setStretchLastSection(True)
        
        self.table.setColumnWidth(0, 250)
        self.table.setColumnWidth(1, 200)
        
        v_header = self.table.verticalHeader()
        v_header.setSectionResizeMode(QHeaderView.ResizeToContents)
        v_header.setDefaultAlignment(Qt.AlignCenter)
        
        self.table.cellChanged.connect(self._on_cell_changed)
        self.table.itemSelectionChanged.connect(self._on_selection_changed)
        
        layout.addWidget(self.table)
        
        # Data
        self.original_data = {}
        self.translations = {}
        self._key_to_row = {}
        
    def load_data(self, data, translations=None):
        self.original_data = data
        self.translations = translations if translations is not None else {}
        self.review_status = {}
        self.user_edited_keys = set()
        self.undo_stack.clear()
        self._previous_cell_texts.clear()
        self._key_to_row.clear()
        self.populate_table()
        self._emit_stats()

    def _on_cell_changed(self, row, col):
        if col != 2 or self._programmatic_update:
            return
        
        item = self.table.item(row, col)
        if not item:
            return

        key = self.table.item(row, 0).text()
        review = self.review_status.get(key, {})
        if review.get("locked", False):
            return
        
        new_text = item.text()
        old_text = self._previous_cell_texts.get((row, col), "")
        
        self._previous_cell_texts[(row, col)] = new_text
        
        if new_text == old_text:
            return
        
        cmd = TranslationEditCommand(self.table, row, old_text, new_text, self)
        self.undo_stack.push(cmd)
        
        key = self.table.item(row, 0).text()
        self.user_edited_keys.add(key)
        
        if key not in self.review_status:
            self.review_status[key] = {"issues": [], "reviewed": False, "quality_score": 1.0}
        self.review_status[key]["origin"] = "user"
        
        original = self.table.item(row, 1).text()
        self._update_row_color(row, new_text, original)
        self._emit_stats()

    def _emit_stats(self):
        total = self.table.rowCount() or len(self.original_data)
        translated = 0
        for i in range(self.table.rowCount()):
            item = self.table.item(i, 2)
            if item and item.text().strip():
                translated += 1
        self.translationChanged.emit(translated, total)
        
    def populate_table(self):
        self._programmatic_update = True
        self.table.setUpdatesEnabled(False)
        self.table.setRowCount(0)
        self.table.setRowCount(len(self.original_data))
        
        self._key_to_row.clear()
        for i, (key, value) in enumerate(self.original_data.items()):
            self._key_to_row[key] = i
            key_item = QTableWidgetItem(key)
            key_item.setFlags(key_item.flags() ^ Qt.ItemIsEditable)
            self.table.setItem(i, 0, key_item)
            
            orig_item = QTableWidgetItem(value)
            orig_item.setFlags(orig_item.flags() ^ Qt.ItemIsEditable)
            self.table.setItem(i, 1, orig_item)
            
            trans_text = self.translations.get(key, "")
            trans_item = QTableWidgetItem(trans_text)
            trans_item.setToolTip(f"原文: {value}")
            self.table.setItem(i, 2, trans_item)
            
            self._previous_cell_texts[(i, 2)] = trans_text
            self._update_row_color(i, trans_text, value)
        
        self.table.setUpdatesEnabled(True)
        self._programmatic_update = False

    def _is_lockable(self, key, original):
        if should_skip_translation(original):
            return True
        trimmed = original.strip()
        for pattern in AUTO_LOCK_PATTERNS:
            if pattern.match(trimmed):
                return True
        return False

    def _update_row_color(self, row, translation, original):
        key = self.table.item(row, 0).text()
        review = self.review_status.get(key, {})
        origin = review.get("origin", "")
        is_locked = review.get("locked", False)

        if not is_locked and not translation and self._is_lockable(key, original):
            is_locked = True

        if is_locked:
            brush, fg = self._BRUSH_LOCKED, self._COLOR_LOCKED_FG
            item = self.table.item(row, 2)
            if item:
                item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                if not translation:
                    item.setText(original)
                    self._previous_cell_texts[(row, 2)] = original
        elif review.get("reviewed") and key in self.user_edited_keys:
            brush, fg = self._BRUSH_VALIDATED, self._COLOR_VALIDATED_FG
        elif key in self.user_edited_keys and translation and translation != original:
            has_issues = bool(review.get("issues"))
            if has_issues and not review.get("reviewed", False):
                brush, fg = self._BRUSH_AI_ISSUES, self._COLOR_AI_ISSUES_FG
            else:
                brush, fg = self._BRUSH_USER_TRANS, self._COLOR_USER_TRANS_FG
        elif translation and translation != original:
            has_issues = bool(review.get("issues"))
            is_reviewed = review.get("reviewed", False)
            if has_issues and not is_reviewed:
                brush, fg = self._BRUSH_AI_ISSUES, self._COLOR_AI_ISSUES_FG
            elif origin == "tm":
                brush, fg = self._BRUSH_TM_MATCH, self._COLOR_TM_MATCH_FG
            else:
                brush, fg = self._BRUSH_AI_TRANS, self._COLOR_AI_TRANS_FG
        elif translation and translation == original:
            brush, fg = self._BRUSH_SAME, self._COLOR_SAME_FG
        else:
            for col in range(3):
                item = self.table.item(row, col)
                if item:
                    item.setData(Qt.BackgroundRole, None)
                    item.setData(Qt.ForegroundRole, None)
            return

        for col in range(3):
            item = self.table.item(row, col)
            if item:
                item.setBackground(brush)
                item.setForeground(fg)

    def filter_table(self):
        filter_text = self.search_input.text().lower()
        filter_state = self.filter_combo.currentData()
        
        # Block updates to prevent auto-scrolling
        self.table.setUpdatesEnabled(False)
        
        for i in range(self.table.rowCount()):
            key = self.table.item(i, 0).text()
            original = self.table.item(i, 1).text()
            translation = self.table.item(i, 2).text()
            
            match_search = (filter_text in key.lower() or 
                            filter_text in original.lower() or 
                            filter_text in translation.lower())
            
            match_filter = True
            if filter_state == 1:  # Missing (untranslated)
                match_filter = not translation
            elif filter_state == 2:  # Same as original (excluding locked)
                review = self.review_status.get(key, {})
                is_locked = review.get("locked", False)
                match_filter = translation and translation == original and not is_locked
            elif filter_state == 3:  # Contains Roman letters (excluding color codes and placeholders)
                # Remove Minecraft color codes (§x and &x format)
                text_without_codes = re.sub(r'[§&][0-9a-fk-or]', '', translation, flags=re.IGNORECASE)
                # Remove format placeholders (%s, %d, %1$s, %2$d, etc.)
                text_without_codes = re.sub(r'%(\d+\$)?[sdfc]', '', text_without_codes)
                # Check if any Roman letters remain
                match_filter = bool(re.search(r'[A-Za-z]', text_without_codes))
            elif filter_state == 4:  # Needs review (has issues, not reviewed)
                review = self.review_status.get(key, {})
                match_filter = bool(review.get("issues")) and not review.get("reviewed", False)
            elif filter_state == 5:  # Translated (has translation, differs from original)
                match_filter = bool(translation) and translation != original
            elif filter_state == 6:  # Locked (translation not needed)
                review = self.review_status.get(key, {})
                match_filter = review.get("locked", False) or (not translation and self._is_lockable(key, original))
            elif filter_state == 7:  # TM auto-applied
                review = self.review_status.get(key, {})
                match_filter = review.get("origin") == "tm"
            elif filter_state == 8:  # AI translated only
                review = self.review_status.get(key, {})
                match_filter = review.get("origin") == "ai"
            elif filter_state == 9:  # User translated
                match_filter = key in self.user_edited_keys and bool(translation)
            
            self.table.setRowHidden(i, not (match_search and match_filter))
        
        # Re-enable updates
        self.table.setUpdatesEnabled(True)

    def get_translations(self):
        result = {}
        for i in range(self.table.rowCount()):
            key_item = self.table.item(i, 0)
            if not key_item:
                continue
            translation = self.table.item(i, 2).text().strip()
            if translation:
                result[key_item.text()] = translation
        return result

    def update_translations(self, additional_translations, validation_results=None):
        self._programmatic_update = True
        self.translations.update(additional_translations)
        self.table.setUpdatesEnabled(False)
        
        if validation_results:
            self.review_status.update(validation_results)
        
        for key, text in additional_translations.items():
            row = self._key_to_row.get(key)
            if row is not None:
                item = self.table.item(row, 2)
                item.setText(text)
                original = self.table.item(row, 1).text()
                item.setToolTip(f"原文: {original}")
                self._previous_cell_texts[(row, 2)] = text
                self._update_row_color(row, text, original)
        
        self.table.setUpdatesEnabled(True)
        self._programmatic_update = False
        self.undo_stack.clear()
        self._emit_stats()

    def get_selected_items(self):
        """Returns a dict of selected items {key: original_text}"""
        selected_rows = set()
        for item in self.table.selectedItems():
            selected_rows.add(item.row())
        
        result = {}
        for row in selected_rows:
            key = self.table.item(row, 0).text()
            original = self.table.item(row, 1).text()
            # Only include if original text exists
            if original:
                result[key] = original
        return result

    def get_missing_items(self):
        """現在表示されている行の中から未翻訳の項目を取得"""
        missing = {}
        for i in range(self.table.rowCount()):
            # 非表示行はスキップ（フィルター適用後の表示行のみを対象）
            if self.table.isRowHidden(i):
                continue
                
            key = self.table.item(i, 0).text()
            original = self.table.item(i, 1).text()
            translation = self.table.item(i, 2).text().strip()
            
            if not translation:
                missing[key] = original
        return missing
    
    def get_visible_items(self):
        """現在表示されている全ての行を取得（翻訳済み含む）"""
        visible = {}
        for i in range(self.table.rowCount()):
            # 非表示行はスキップ
            if self.table.isRowHidden(i):
                continue
                
            key = self.table.item(i, 0).text()
            original = self.table.item(i, 1).text()
            if original:
                visible[key] = original
        return visible
    
    def _on_search_all_mods_clicked(self):
        search_text = self.search_input.text()
        self.searchAllModsRequested.emit(search_text)

    def _on_selection_changed(self):
        selected_rows = set()
        for item in self.table.selectedItems():
            selected_rows.add(item.row())
        count = len(selected_rows)
        self.selectionChanged.emit(count)

    def mark_reviewed(self, keys):
        self.table.setUpdatesEnabled(False)
        for key in keys:
            if key in self.review_status:
                self.review_status[key]["reviewed"] = True
            else:
                self.review_status[key] = {"issues": [], "reviewed": True, "quality_score": 1.0}
            row = self._key_to_row.get(key)
            if row is not None:
                original = self.table.item(row, 1).text()
                translation = self.table.item(row, 2).text()
                self._update_row_color(row, translation, original)
        self.table.setUpdatesEnabled(True)

    def toggle_lock(self, keys, lock):
        self.table.setUpdatesEnabled(False)
        for key in keys:
            if key not in self.review_status:
                self.review_status[key] = {"issues": [], "reviewed": False, "quality_score": 1.0}
            self.review_status[key]["locked"] = lock
            if lock:
                self.review_status[key]["origin"] = "skipped"
            row = self._key_to_row.get(key)
            if row is not None:
                original = self.table.item(row, 1).text()
                translation = self.table.item(row, 2).text()
                item = self.table.item(row, 2)
                if lock:
                    item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                    if not translation:
                        item.setText(original)
                        self._previous_cell_texts[(row, 2)] = original
                        translation = original
                else:
                    item.setFlags(item.flags() | Qt.ItemIsEditable)
                self._update_row_color(row, translation, original)
        self.table.setUpdatesEnabled(True)
        self._emit_stats()

    def mark_validated(self, keys):
        self.table.setUpdatesEnabled(False)
        for key in keys:
            if key in self.review_status:
                self.review_status[key]["reviewed"] = True
            else:
                self.review_status[key] = {"issues": [], "reviewed": True, "quality_score": 1.0}
            self.user_edited_keys.add(key)
            row = self._key_to_row.get(key)
            if row is not None:
                original = self.table.item(row, 1).text()
                translation = self.table.item(row, 2).text()
                self._update_row_color(row, translation, original)
        self.table.setUpdatesEnabled(True)
