import threading

from PySide6.QtCore import QEventLoop, QThread, QTimer
from PySide6.QtWidgets import QApplication, QDialog


def _reject_next_dialog():
    QTimer.singleShot(0, lambda: QApplication.activeModalWidget().reject())


def _flush_deletes():
    loop = QEventLoop()
    QTimer.singleShot(20, loop.quit)
    loop.exec()


def test_closed_pad_editors_are_freed(win):
    for _ in range(5):
        _reject_next_dialog()
        win.edit_pad((0, 0))
    _flush_deletes()
    assert len(win.findChildren(QDialog)) == 0


def test_app_picker_stops_scan_thread_on_close(win):
    import widgets

    dlg = widgets.AppPickerDialog(win)
    worker = dlg._worker
    dlg.reject()
    assert not worker.isRunning()


def test_hardware_press_is_handled_on_gui_thread(win, qapp):
    seen = []
    win.on_pad_press = lambda pad: seen.append((pad, QThread.currentThread() is qapp.thread()))
    win.init_midi()  # re-bind controller callbacks
    t = threading.Thread(target=win.lp.on_press, args=((0, 0),))
    t.start()
    t.join()
    qapp.processEvents()
    assert seen == [((0, 0), True)]
