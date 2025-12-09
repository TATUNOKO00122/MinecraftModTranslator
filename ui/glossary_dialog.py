from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QTableWidget, 
                               QTableWidgetItem, QPushButton, QHeaderView, QMessageBox)
from PySide6.QtCore import Qt

class GlossaryDialog(QDialog):
    def __init__(self, glossary, parent=None, initial_key="", initial_value=""):
        super().__init__(parent)
        self.glossary = glossary
        self.initial_key = initial_key
        self.initial_value = initial_value
        self.setWindowTitle("用語集編集")
        self.resize(600, 400)
        
        self.layout = QVBoxLayout(self)
        
        # Table
        self.table = QTableWidget()
        self.table.setColumnCount(2)
        self.table.setHorizontalHeaderLabels(["原文 (英語)", "訳文 (日本語)"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.verticalHeader().setDefaultSectionSize(40) # Taller rows for better readability
        self.layout.addWidget(self.table)
        
        # Buttons
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

    def save_and_close(self):
        new_terms = {}
        for i in range(self.table.rowCount()):
            key_item = self.table.item(i, 0)
            val_item = self.table.item(i, 1)
            
            if key_item and val_item:
                key = key_item.text().strip()
                val = val_item.text().strip()
                if key: # Ignore empty keys
                    new_terms[key] = val
        
        self.glossary.set_terms(new_terms)
        self.accept()
