"""Desktop entry point. --smoke-test verifies Qt startup without a display."""
import argparse
import os
import sqlite3
from pathlib import Path
import sys

from .database import Database, default_database_path
from .models import RaceError
from .service import RaceService


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='Offline Pinewood Derby race management')
    parser.add_argument('--database', type=Path, default=None, help='SQLite database path (default: user data directory)')
    parser.add_argument('--smoke-test', action='store_true', help='Open the GUI offscreen and exit automatically')
    args = parser.parse_args(argv)
    if args.smoke_test:
        os.environ['QT_QPA_PLATFORM'] = 'offscreen'
        os.environ['QT_QPA_PLATFORMTHEME'] = ''
    from PySide6.QtCore import QTimer
    from PySide6.QtGui import QFont
    from PySide6.QtWidgets import QApplication, QMessageBox
    from .gui import MainWindow

    app = QApplication([sys.argv[0]])
    app.setStyle('Fusion')
    app.setApplicationName('Pinewood Derby Race Manager')
    app.setOrganizationName('PinewoodDerby')
    app.setFont(QFont('Sans Serif', 11))
    path = args.database or default_database_path()
    try:
        database = Database(path)
    except (OSError, ValueError, sqlite3.Error, RaceError) as exc:
        print(f'Cannot open database: {exc}', file=sys.stderr)
        QMessageBox.critical(None, 'Database Error', f'Cannot open the race database:\n{exc}')
        return 1
    try:
        window = MainWindow(RaceService(database), path)
        window.show()
        if args.smoke_test:
            QTimer.singleShot(250, app.quit)
        return app.exec()
    finally:
        database.close()


if __name__ == '__main__':
    raise SystemExit(main())
