"""Shared background-worker plumbing so blocking `requests` calls never freeze the UI thread.

One reusable Worker(QRunnable) + WorkerSignals + run_async() helper on the global QThreadPool —
callers pass a plain function (usually a client method or a common.py helper) plus success/error
callbacks; Qt signals marshal the result back onto the UI thread automatically.
"""
from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

from gui.logging_setup import logger


class WorkerSignals(QObject):
    success = Signal(object)
    error = Signal(str)
    # (เสร็จแล้วกี่ชิ้น, ทั้งหมดกี่ชิ้น) — งานที่นับชิ้นได้เท่านั้นที่ใช้ ดู run_async(on_progress=...)
    progress = Signal(int, int)


class Worker(QRunnable):
    def __init__(self, fn: Callable, *args, **kwargs):
        super().__init__()
        self.fn = fn
        self.args = args
        self.kwargs = kwargs
        self.signals = WorkerSignals()

    def report_progress(self, done: int, total: int) -> None:
        """ให้ฟังก์ชันที่รันอยู่บน thread เบื้องหลังเรียกได้ — emit เป็น queued signal ข้ามมา
        UI thread ให้เอง ห้ามให้ฟังก์ชันพวกนั้นแตะ widget ตรงๆ"""
        self.signals.progress.emit(done, total)

    def run(self):
        try:
            result = self.fn(*self.args, **self.kwargs)
        except Exception as e:
            # traceback เต็มลงไฟล์ log เสมอ ส่วน UI ได้แค่ข้อความที่แปลเป็นภาษาคนแล้ว
            # (ของเดิมใช้ traceback.print_exc() ซึ่งหายไปกับ devnull ใน build แบบ --windowed)
            logger.exception("งานเบื้องหลัง %s ล้มเหลว", getattr(self.fn, "__name__", self.fn))
            self.signals.error.emit(str(e))
        else:
            self.signals.success.emit(result)


# Worker ที่ยังส่งผลลัพธ์ไม่ถึง UI thread ต้องถูกอ้างอิงไว้ที่นี่เสมอ ดูเหตุผลใน run_async()
_pending_workers: set[Worker] = set()


def run_async(
    fn: Callable,
    *args,
    on_success: Callable | None = None,
    on_error: Callable | None = None,
    on_progress: Callable | None = None,
    **kwargs,
) -> Worker:
    """สั่งงานเบื้องหลังแล้วส่งผลกลับมาที่ UI thread ผ่าน queued signal

    ส่ง on_progress มาด้วยได้ถ้า fn รับ keyword `report_progress` — จะได้ callable ที่เรียก
    ระหว่างทางเพื่อรายงาน (เสร็จแล้วกี่ชิ้น, ทั้งหมดกี่ชิ้น) ใช้กับงานที่นับชิ้นได้เท่านั้น
    (เช่น submit_rows ที่รู้จำนวนแถวล่วงหน้า) งานที่ต้องเดาเวลาให้ใช้หลอดวิ่งแทน ดู gui/busy.py

    ค่าเริ่มต้นของ QRunnable คือ autoDelete=True — QThreadPool จะลบ runnable ทิ้งทันทีที่ run()
    จบ ซึ่งเกิดขึ้น "หลัง emit แต่ก่อน UI thread หยิบ signal ไปประมวลผล" พอ WorkerSignals ถูกทำลาย
    ก่อน signal ที่คิวไว้จะถูกส่ง callback ก็เงียบหายไปเฉยๆ (วัดได้จริง: งานสั้นๆ 60 ชิ้น callback
    ถึงแค่ 2) อาการที่เจอคือช่องประเภทงานค้างว่างเปล่าเพราะผล get_types หายกลางทาง

    จึงต้องปิด autoDelete แล้วถือ reference ฝั่ง Python ไว้จนกว่า callback จะถูกเรียกจริง
    """
    worker = Worker(fn, *args, **kwargs)
    worker.setAutoDelete(False)
    _pending_workers.add(worker)

    if on_progress:
        worker.signals.progress.connect(on_progress)
        # ส่งเข้าไปเป็น kwarg ตอนนี้ ไม่ใช่ตอนสร้าง Worker เพราะต้องอ้างถึงตัว worker เอง
        worker.kwargs["report_progress"] = worker.report_progress
    if on_success:
        worker.signals.success.connect(on_success)
    if on_error:
        worker.signals.error.connect(on_error)

    # ต่อท้าย callback ของผู้เรียก (Qt เรียก slot ตามลำดับที่ connect) — ปล่อย reference ทีหลังสุด
    worker.signals.success.connect(lambda _r: _pending_workers.discard(worker))
    worker.signals.error.connect(lambda _m: _pending_workers.discard(worker))

    QThreadPool.globalInstance().start(worker)
    return worker
