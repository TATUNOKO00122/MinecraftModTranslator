import sys
import os
from PySide6.QtWidgets import QApplication
from ui.main_window import MainWindow

def main():
    app = QApplication(sys.argv)
    
    # Load Stylesheet
    style_path = os.path.join(os.path.dirname(__file__), 'ui', 'styles.qss')
    if os.path.exists(style_path):
        with open(style_path, 'r', encoding='utf-8') as f:
            app.setStyleSheet(f.read())
            
    window = MainWindow()
    window.show()
    window.raise_()  # 最前面に表示
    window.activateWindow()  # アクティブ化
    
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
