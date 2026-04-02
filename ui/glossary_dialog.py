from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QTableWidget,
                                QTableWidgetItem, QPushButton, QHeaderView, QMessageBox,
                                QFileDialog, QLabel, QLineEdit)
from PySide6.QtCore import Qt
import json

class GlossaryDialog(QDialog):
    def __init__(self, glossary, parent=None, initial_key="", initial_value=""):
        super().__init__(parent)
        self.glossary = glossary
        self.initial_key = initial_key
        self.initial_value = initial_value
        self.setWindowTitle("辞書編集")
        self.resize(600, 450) # Increased height slightly
        
        self.layout = QVBoxLayout(self)

        search_layout = QHBoxLayout()
        search_label = QLabel("検索:")
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("原文または訳文で絞り込み...")
        self.search_input.textChanged.connect(self._filter_table)
        search_layout.addWidget(search_label)
        search_layout.addWidget(self.search_input)
        self.layout.addLayout(search_layout)

        self.table = QTableWidget()
        self.table.setColumnCount(2)
        self.table.setHorizontalHeaderLabels(["原文 (英語)", "訳文 (日本語)"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.verticalHeader().setDefaultSectionSize(40) # Taller rows for better readability
        self.layout.addWidget(self.table)
        
        # New Feature Button (Above standard buttons)
        extra_btn_layout = QHBoxLayout()
        create_all_btn = QPushButton("全MODから辞書作成 (クエスト除外 - AI)")
        create_all_btn.setObjectName("PrimaryButton")
        create_all_btn.clicked.connect(self.request_dictionary_creation)
        extra_btn_layout.addWidget(create_all_btn)
        self.layout.addLayout(extra_btn_layout)
        
        # IO Buttons
        io_layout = QHBoxLayout()
        import_btn = QPushButton("インポート (JSON)")
        import_btn.clicked.connect(self.import_dictionary)
        io_layout.addWidget(import_btn)
        
        export_btn = QPushButton("エクスポート (JSON)")
        export_btn.clicked.connect(self.export_dictionary)
        io_layout.addWidget(export_btn)
        self.layout.addLayout(io_layout)
        
        # Standard Buttons
        btn_layout = QHBoxLayout()
        
        add_btn = QPushButton("追加")
        add_btn.clicked.connect(lambda: self.add_row())
        btn_layout.addWidget(add_btn)
        
        remove_btn = QPushButton("削除")
        remove_btn.clicked.connect(self.remove_row)
        btn_layout.addWidget(remove_btn)
        
        save_btn = QPushButton("保存して閉じる")
        save_btn.clicked.connect(self.save_and_close)
        btn_layout.addWidget(save_btn)
        
        self.layout.addLayout(btn_layout)
        
        self.load_data()
        
        # If initial values provided, add row and focus
        if self.initial_key or self.initial_value:
             self.add_row(self.initial_key, self.initial_value)

    def load_data(self):
        terms = self.glossary.get_terms()
        self.table.setRowCount(len(terms))
        for i, (k, v) in enumerate(terms.items()):
            self.table.setItem(i, 0, QTableWidgetItem(k))
            self.table.setItem(i, 1, QTableWidgetItem(v))

    def add_row(self, key="", value=""):
        row = self.table.rowCount()
        self.table.insertRow(row)
        self.table.setItem(row, 0, QTableWidgetItem(key))
        self.table.setItem(row, 1, QTableWidgetItem(value))
        if not key:
             self.table.editItem(self.table.item(row, 0))
        elif not value:
             self.table.editItem(self.table.item(row, 1))
        else:
             self.table.scrollToItem(self.table.item(row, 0))
             self.table.selectRow(row)

    def remove_row(self):
        current_row = self.table.currentRow()
        if current_row >= 0:
            self.table.removeRow(current_row)

    def request_dictionary_creation(self):
        """Call parent window's dictionary creation method."""
        if hasattr(self.parent(), 'create_dictionary_from_all_mods'):
            self.parent().create_dictionary_from_all_mods()
            # Note: The process runs in background. 
            # The dialog won't auto-refresh until user manually reloads or re-opens,
            # but the Terms Extraction Dialog will appear on top.

    def import_dictionary(self):
        file_path, _ = QFileDialog.getOpenFileName(self, "辞書をインポート", "", "JSON Files (*.json)")
        if not file_path:
            return
            
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            if not isinstance(data, dict):
                QMessageBox.warning(self, "エラー", "無効なJSON形式です。")
                return
                
            # Ask for confirmation if not empty
            if self.table.rowCount() > 0:
                ret = QMessageBox.question(self, "確認", "インポートすると既存のリストにマージされます。\n同じキーがある場合は上書きされます。\nよろしいですか？",
                                           QMessageBox.Yes | QMessageBox.No)
                if ret != QMessageBox.Yes:
                    return

            # Merge data into table
            # First, preserve current table data to a dict
            current_data = {}
            for i in range(self.table.rowCount()):
                k = self.table.item(i, 0).text().strip()
                v = self.table.item(i, 1).text().strip()
                if k:
                    current_data[k] = v
            
            # Update with imported data
            current_data.update(data)
            
            # Reload table
            self.table.setRowCount(len(current_data))
            for i, (k, v) in enumerate(current_data.items()):
                self.table.setItem(i, 0, QTableWidgetItem(k))
                self.table.setItem(i, 1, QTableWidgetItem(v))
                
            QMessageBox.information(self, "完了", f"{len(data)} 件の用語をインポートしました。")
            
        except Exception as e:
            QMessageBox.critical(self, "エラー", f"インポートに失敗しました:\n{str(e)}")

    def export_dictionary(self):
        file_path, _ = QFileDialog.getSaveFileName(self, "辞書をエクスポート", "glossary_export.json", "JSON Files (*.json)")
        if not file_path:
            return
            
        try:
            data = {}
            for i in range(self.table.rowCount()):
                key_item = self.table.item(i, 0)
                val_item = self.table.item(i, 1)
                if key_item and val_item:
                    key = key_item.text().strip()
                    val = val_item.text().strip()
                    if key:
                        data[key] = val
            
            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
                
            QMessageBox.information(self, "完了", "辞書をエクスポートしました。")
            
        except Exception as e:
            QMessageBox.critical(self, "エラー", f"エクスポートに失敗しました:\n{str(e)}")

    def save_and_close(self):
        new_terms = {}
        for i in range(self.table.rowCount()):
            key_item = self.table.item(i, 0)
            val_item = self.table.item(i, 1)
            
            if key_item and val_item:
                key = key_item.text().strip()
                val = val_item.text().strip()
                if key:
                    new_terms[key] = val
        
        self.glossary.set_terms(new_terms)
        self.accept()

    def _filter_table(self, text):
        query = text.lower().strip()
        for row in range(self.table.rowCount()):
            if not query:
                self.table.setRowHidden(row, False)
                continue
            key_item = self.table.item(row, 0)
            val_item = self.table.item(row, 1)
            key = key_item.text().lower() if key_item else ""
            val = val_item.text().lower() if val_item else ""
            self.table.setRowHidden(row, query not in key and query not in val)
