from PySide6.QtWidgets import (QDialog, QVBoxLayout, QLabel, QLineEdit, 
                               QComboBox, QPushButton, QHBoxLayout, QFileDialog,
                               QSpinBox, QGroupBox, QFormLayout)
from PySide6.QtCore import QSettings

class SettingsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("設定")
        self.setFixedWidth(450)
        
        self.settings = QSettings("MinecraftModTranslator", "App")
        
        layout = QVBoxLayout(self)
        
        # API Key
        layout.addWidget(QLabel("OpenRouter API Key:"))
        self.api_key_input = QLineEdit()
        self.api_key_input.setEchoMode(QLineEdit.Password)
        self.api_key_input.setText(self.settings.value("api_key", ""))
        layout.addWidget(self.api_key_input)
        
        layout.addWidget(QLabel("キーはローカルに保存されます。"))
        
        layout.addSpacing(10)

        # Model
        layout.addWidget(QLabel("使用モデル:"))
        self.model_combo = QComboBox()
        self.models = [
             ('google/gemini-2.0-flash-exp:free', 'Gemini 2.0 Flash (Experimental/Free)'),
             ('google/gemini-exp-1206:free', 'Gemini Exp 1206 (Experimental/Free)'),
             ('openai/gpt-4o', 'GPT-4o (High Performance)'),
             ('openai/gpt-4o-mini', 'GPT-4o mini (Balanced)'),
             ('anthropic/claude-3.5-sonnet', 'Claude 3.5 Sonnet (Recommended)'),
             ('anthropic/claude-3-haiku', 'Claude 3 Haiku (Fast)'),
             ('openai/gpt-3.5-turbo', 'GPT-3.5 Turbo')
        ]
        for model_id, model_name in self.models:
            self.model_combo.addItem(model_name, model_id)
            
        current_model = self.settings.value("model", self.models[0][0])
        index = self.model_combo.findData(current_model)
        if index >= 0:
            self.model_combo.setCurrentIndex(index)
        layout.addWidget(self.model_combo)
        
        layout.addSpacing(10)
        
        # Performance Settings Group
        perf_group = QGroupBox("パフォーマンス設定")
        perf_layout = QFormLayout()
        
        # Parallel Count
        self.parallel_spin = QSpinBox()
        self.parallel_spin.setRange(1, 10)
        self.parallel_spin.setValue(int(self.settings.value("parallel_count", 3)))
        self.parallel_spin.setToolTip(
            "同時に送信するAPIリクエスト数。\n"
            "高い値: 翻訳速度が向上しますが、レート制限に注意。\n"
            "低い値: 安定しますが、翻訳速度は遅くなります。\n"
            "無料モデル使用時は1〜2を推奨。"
        )
        perf_layout.addRow("並列リクエスト数:", self.parallel_spin)
        
        # Help text
        help_label = QLabel("※ 無料モデル: 1〜2推奨 / 有料モデル: 3〜5推奨")
        help_label.setStyleSheet("color: #888; font-size: 11px;")
        perf_layout.addRow("", help_label)
        
        perf_group.setLayout(perf_layout)
        layout.addWidget(perf_group)
        
        layout.addSpacing(10)

        # Default Export Directory
        layout.addWidget(QLabel("デフォルト出力先フォルダ:"))
        dir_layout = QHBoxLayout()
        self.export_dir_input = QLineEdit()
        self.export_dir_input.setText(self.settings.value("export_dir", ""))
        dir_layout.addWidget(self.export_dir_input)
        
        dir_btn = QPushButton("参照...")
        dir_btn.clicked.connect(self.choose_export_dir)
        dir_layout.addWidget(dir_btn)
        layout.addLayout(dir_layout)
        
        layout.addSpacing(20)

        # Buttons
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        
        cancel_btn = QPushButton("キャンセル")
        cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(cancel_btn)
        
        save_btn = QPushButton("保存")
        save_btn.setObjectName("PrimaryButton")
        save_btn.clicked.connect(self.save_settings)
        btn_layout.addWidget(save_btn)
        
        layout.addLayout(btn_layout)

    def choose_export_dir(self):
        dir_path = QFileDialog.getExistingDirectory(self, "出力先フォルダを選択")
        if dir_path:
            self.export_dir_input.setText(dir_path)

    def save_settings(self):
        self.settings.setValue("api_key", self.api_key_input.text())
        self.settings.setValue("model", self.model_combo.currentData())
        self.settings.setValue("export_dir", self.export_dir_input.text())
        self.settings.setValue("parallel_count", self.parallel_spin.value())
        self.accept()

    def get_settings(self):
        return {
            "api_key": self.api_key_input.text(),
            "model": self.model_combo.currentData(),
            "export_dir": self.export_dir_input.text(),
            "parallel_count": self.parallel_spin.value()
        }
