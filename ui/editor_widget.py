from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QTableWidget, 
                               QTableWidgetItem, QHeaderView, QLineEdit, QPushButton, QLabel)
from PySide6.QtGui import QColor, QBrush
from PySide6.QtCore import Qt, Signal

class EditorWidget(QWidget):
    translationChanged = Signal(int, int) # translated_count, total_count

    def __init__(self, parent=None):
        super().__init__(parent)
        


        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 15, 10, 10) # Added top margin (15) for breathing room
        
        # Toolbar
        toolbar = QHBoxLayout()
        
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("検索 (キー, 原文, 訳文)...")
        self.search_input.textChanged.connect(self.filter_table)
        toolbar.addWidget(self.search_input)
        
        self.filter_btn = QPushButton("未翻訳のみ")
        self.filter_btn.clicked.connect(self._cycle_filter)
        toolbar.addWidget(self.filter_btn)
        
        # Filter state: 0=all, 1=missing, 2=same_as_original
        self.filter_state = 0
        
        self.translate_btn = QPushButton("全体翻訳")
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
        
        layout.addWidget(self.table)
        
        # Data
        self.original_data = {}
        self.translations = {}
        
    def load_data(self, data):
        self.original_data = data
        self.translations = {}
        self.populate_table()
        self._emit_stats()

    def _emit_stats(self):
        total = len(self.original_data)
        # Count as translated if translation exists (including same as original)
        translated = len([t for t in self.translations.values() if t])
        self.translationChanged.emit(translated, total)
        
    def populate_table(self):
        self.table.setUpdatesEnabled(False)
        self.table.setRowCount(0)
        self.table.setRowCount(len(self.original_data))
        
        for i, (key, value) in enumerate(self.original_data.items()):
            # Key
            key_item = QTableWidgetItem(key)
            key_item.setFlags(key_item.flags() ^ Qt.ItemIsEditable) # Read-only
            self.table.setItem(i, 0, key_item)
            
            # Original
            orig_item = QTableWidgetItem(value)
            orig_item.setFlags(orig_item.flags() ^ Qt.ItemIsEditable) # Read-only
            self.table.setItem(i, 1, orig_item)
            
            # Translation
            trans_text = self.translations.get(key, "")
            trans_item = QTableWidgetItem(trans_text)
            self.table.setItem(i, 2, trans_item)
            
            self._update_row_color(i, trans_text, value)
        
        self.table.setUpdatesEnabled(True)

    def _update_row_color(self, row, translation, original):
        # If translated and different from original: green (fully translated)
        # If translated but same as original: yellow (needs attention)
        # Otherwise: default
        if translation and translation != original:
            # Translated properly - green
            color = QColor("#2f6b36")
            text_color = QColor("#ffffff")
        elif translation and translation == original:
            # Same as original - yellow (needs attention)
            color = QColor("#6b5a2f")
            text_color = QColor("#ffffff")
        else:
            # Not translated - reset to default
            for col in range(3):
                item = self.table.item(row, col)
                if item:
                    item.setData(Qt.BackgroundRole, None)
                    item.setData(Qt.ForegroundRole, None)
            return
        
        brush = QBrush(color, Qt.SolidPattern)
        for col in range(3):
            item = self.table.item(row, col)
            if item:
                item.setBackground(brush)
                item.setForeground(text_color)

    def _cycle_filter(self):
        """Cycle through filter states: all -> missing -> same_as_original -> all"""
        self.filter_state = (self.filter_state + 1) % 3
        self.filter_table()
    
    def filter_table(self):
        filter_text = self.search_input.text().lower()
        
        # Block updates to prevent auto-scrolling
        self.table.setUpdatesEnabled(False)
        
        for i in range(self.table.rowCount()):
            key = self.table.item(i, 0).text()
            original = self.table.item(i, 1).text()
            translation = self.table.item(i, 2).text()
            
            match_search = (filter_text in key.lower() or 
                            filter_text in original.lower() or 
                            filter_text in translation.lower())
            
            # Filter logic based on state
            match_filter = True
            if self.filter_state == 1:  # Missing (untranslated)
                match_filter = not translation
            elif self.filter_state == 2:  # Same as original
                match_filter = translation and translation == original
                
            self.table.setRowHidden(i, not (match_search and match_filter))
        
        # Re-enable updates
        self.table.setUpdatesEnabled(True)

        # Update button text
        if self.filter_state == 0:
            self.filter_btn.setText("未翻訳のみ")
        elif self.filter_state == 1:
            self.filter_btn.setText("その他")
        else:
            self.filter_btn.setText("すべて表示")

    def get_translations(self):
        result = {}
        for i in range(self.table.rowCount()):
            key = self.table.item(i, 0).text()
            translation = self.table.item(i, 2).text().strip()
            if translation:
                result[key] = translation
        return result

    def update_translations(self, additional_translations):
        self.translations.update(additional_translations)
        # Block updates to prevent auto-scrolling
        self.table.setUpdatesEnabled(False)
        
        # Update UI without full reload
        for i in range(self.table.rowCount()):
            key = self.table.item(i, 0).text()
            if key in additional_translations:
                text = additional_translations[key]
                self.table.item(i, 2).setText(text)
                
                # Update color
                original = self.table.item(i, 1).text()
                self._update_row_color(i, text, original)
        
        self.table.setUpdatesEnabled(True)
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
        missing = {}
        for i in range(self.table.rowCount()):
            key = self.table.item(i, 0).text()
            original = self.table.item(i, 1).text()
            translation = self.table.item(i, 2).text().strip()
            
            if not translation:
                missing[key] = original
        return missing
