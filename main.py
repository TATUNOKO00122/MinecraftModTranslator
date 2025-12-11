import sys
import os

# Handle frozen environment (PyInstaller with --noconsole)
# Redirect stdout/stderr to null if they don't exist to prevent crashes
if getattr(sys, 'frozen', False):
    # When running as frozen exe without console, stdout/stderr may be None
    if sys.stdout is None:
        sys.stdout = open(os.devnull, 'w')
    if sys.stderr is None:
        sys.stderr = open(os.devnull, 'w')

from PySide6.QtWidgets import QApplication
from ui.main_window import MainWindow

def main():
    app = QApplication(sys.argv)
    
    # Get base path for resources (handles frozen exe)
    if getattr(sys, 'frozen', False):
        base_path = sys._MEIPASS
    else:
        base_path = os.path.dirname(__file__)
    
    # Load Stylesheet
    style_path = os.path.join(base_path, 'ui', 'styles.qss')
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
