from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QTableWidget, 
                               QTableWidgetItem, QPushButton, QHeaderView, QLabel,
                               QCheckBox, QWidget)
from PySide6.QtCore import Qt


class TermExtractionDialog(QDialog):
    """Dialog for reviewing and selecting extracted terms to add to glossary."""
    
    def __init__(self, extracted_terms, glossary, parent=None):
        """
        Args:
            extracted_terms: dict of {original_term: translated_term}
            glossary: Glossary instance to add terms to
            parent: Parent widget
        """
        super().__init__(parent)
        self.extracted_terms = extracted_terms
        self.glossary = glossary
        self.selected_terms = {}
        
        self.setWindowTitle("抽出された用語の確認")
        self.resize(700, 500)
        self.setModal(True)
        
        self._setup_ui()
    
    def _setup_ui(self):
        layout = QVBoxLayout(self)
        
        # Header
        header_label = QLabel(
            f"翻訳から {len(self.extracted_terms)} 件の用語が抽出されました。\n"
            "辞書に追加する項目にチェックを入れてください。"
        )
        header_label.setStyleSheet("font-size: 12px; margin-bottom: 10px;")
        layout.addWidget(header_label)
        
        # Table
        self.table = QTableWidget()
        self.table.setColumnCount(3)
        self.table.setHorizontalHeaderLabels(["追加", "原文 (英語)", "訳文 (日本語)"])
        
        # Column widths
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Fixed)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        header.setSectionResizeMode(2, QHeaderView.Stretch)
        self.table.setColumnWidth(0, 50)
        
        self.table.verticalHeader().setDefaultSectionSize(35)
        self.table.setRowCount(len(self.extracted_terms))
        
        # Populate table
        for row, (orig, trans) in enumerate(self.extracted_terms.items()):
            # Checkbox
            checkbox = QCheckBox()
            checkbox.setChecked(True)  # Default to checked
            checkbox_widget = QWidget()
            checkbox_layout = QHBoxLayout(checkbox_widget)
            checkbox_layout.addWidget(checkbox)
            checkbox_layout.setAlignment(Qt.AlignCenter)
            checkbox_layout.setContentsMargins(0, 0, 0, 0)
            self.table.setCellWidget(row, 0, checkbox_widget)
            
            # Original term
            orig_item = QTableWidgetItem(orig)
            orig_item.setFlags(orig_item.flags() & ~Qt.ItemIsEditable)
            self.table.setItem(row, 1, orig_item)
            
            # Translated term (editable)
            trans_item = QTableWidgetItem(trans)
            self.table.setItem(row, 2, trans_item)
        
        layout.addWidget(self.table)
        
        # Select all / Deselect all buttons
        select_layout = QHBoxLayout()
        
        select_all_btn = QPushButton("すべて選択")
        select_all_btn.clicked.connect(self._select_all)
        select_layout.addWidget(select_all_btn)
        
        deselect_all_btn = QPushButton("すべて解除")
        deselect_all_btn.clicked.connect(self._deselect_all)
        select_layout.addWidget(deselect_all_btn)
        
        select_layout.addStretch()
        layout.addLayout(select_layout)
        
        # Action buttons
        btn_layout = QHBoxLayout()
        
        add_btn = QPushButton("選択した用語を辞書に追加")
        add_btn.clicked.connect(self._add_selected)
        add_btn.setStyleSheet("background-color: #4CAF50; color: white; font-weight: bold; padding: 8px 16px;")
        btn_layout.addWidget(add_btn)
        
        skip_btn = QPushButton("スキップ")
        skip_btn.clicked.connect(self.reject)
        btn_layout.addWidget(skip_btn)
        
        layout.addLayout(btn_layout)
    
    def _select_all(self):
        for row in range(self.table.rowCount()):
            widget = self.table.cellWidget(row, 0)
            if widget:
                checkbox = widget.findChild(QCheckBox)
                if checkbox:
                    checkbox.setChecked(True)
    
    def _deselect_all(self):
        for row in range(self.table.rowCount()):
            widget = self.table.cellWidget(row, 0)
            if widget:
                checkbox = widget.findChild(QCheckBox)
                if checkbox:
                    checkbox.setChecked(False)
    
    def _add_selected(self):
        """Add selected terms to glossary and close dialog."""
        selected = {}
        
        for row in range(self.table.rowCount()):
            widget = self.table.cellWidget(row, 0)
            if widget:
                checkbox = widget.findChild(QCheckBox)
                if checkbox and checkbox.isChecked():
                    orig_item = self.table.item(row, 1)
                    trans_item = self.table.item(row, 2)
                    if orig_item and trans_item:
                        orig = orig_item.text().strip()
                        trans = trans_item.text().strip()
                        if orig and trans:
                            selected[orig] = trans
        
        if selected:
            # Update glossary
            current_terms = self.glossary.get_terms()
            current_terms.update(selected)
            self.glossary.set_terms(current_terms)
            self.selected_terms = selected
        
        self.accept()
    
    def get_added_count(self):
        """Return the number of terms added."""
        return len(self.selected_terms)
