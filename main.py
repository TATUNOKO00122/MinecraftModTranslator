import sys
import os
import traceback
import faulthandler

# Handle frozen environment (PyInstaller with --noconsole)
# Redirect stdout/stderr to log file to capture crashes
if getattr(sys, 'frozen', False):
    _log_dir = os.path.join(os.path.dirname(sys.executable), "logs")
    os.makedirs(_log_dir, exist_ok=True)
    _log_path = os.path.join(_log_dir, "error.log")
    if sys.stdout is None or sys.stderr is None:
        _log_file = open(_log_path, 'a', encoding='utf-8')
        if sys.stdout is None:
            sys.stdout = _log_file
        if sys.stderr is None:
            sys.stderr = _log_file

faulthandler.enable()

from PySide6.QtWidgets import QApplication, QMessageBox
from ui.main_window import MainWindow

def _global_excepthook(exc_type, exc_value, exc_tb):
    tb_text = ''.join(traceback.format_exception(exc_type, exc_value, exc_tb))
    print(f"[UNHANDLED EXCEPTION]\n{tb_text}", flush=True)
    if QApplication.instance():
        QMessageBox.critical(None, "予期しないエラー", f"エラーが発生しました:\n\n{exc_value}\n\n詳細はコンソール/ログを確認してください。")

sys.excepthook = _global_excepthook

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
            
    window = MainWindow(base_path=base_path)
    window.show()
    window.raise_()
    window.activateWindow()
    
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
