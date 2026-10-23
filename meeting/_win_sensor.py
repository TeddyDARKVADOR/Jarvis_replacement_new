"""
meeting/_win_sensor.py — qui filme, sous Windows : IMFSensorActivityMonitor.

L'API de Media Foundation derrière le voyant de confidentialité (Windows 10
2004 et plus récent, mfsensorgroup.dll) : un rapport à chaque changement, par
caméra, avec le PID et l'état « en streaming » de chaque processus. Mesuré le
2026-10-16 : un processus qui ouvre la caméra apparaît en streaming, puis
disparaît à la fermeture ; un service système reste listé sans streaming.

Le moniteur vit dans son propre fil, en COM multithread (les rappels arrivent
sur un fil de Media Foundation) : le fil principal de Matthew, déjà en COM
monothread pour pycaw et pywinauto, n'est pas touché.

Les interfaces sont celles de mfidl.h, déclarées pour comtypes.
"""
from __future__ import annotations

import ctypes
import threading
import time
from ctypes import HRESULT, POINTER, byref, c_int, c_ulonglong, c_wchar_p
from ctypes.wintypes import BOOL, LPWSTR, ULONG

import comtypes
from comtypes import COMMETHOD, GUID, COMObject, IUnknown


class IMFSensorProcessActivity(IUnknown):
    _iid_ = GUID("{39DC7F4A-B141-4719-813C-A7F46162A2B8}")
    _methods_ = [
        COMMETHOD([], HRESULT, "GetProcessId", (["out"], POINTER(ULONG), "pPID")),
        COMMETHOD([], HRESULT, "GetStreamingState", (["out"], POINTER(BOOL), "pfStreaming")),
        COMMETHOD([], HRESULT, "GetStreamingMode", (["out"], POINTER(c_int), "pMode")),
        COMMETHOD([], HRESULT, "GetReportTime", (["out"], POINTER(c_ulonglong), "pft")),
    ]


class IMFSensorActivityReport(IUnknown):
    _iid_ = GUID("{3E8C4BE1-A8C2-4528-90DE-2851BDE5FEAD}")
    _methods_ = [
        COMMETHOD([], HRESULT, "GetFriendlyName", (["in"], LPWSTR, "name"), (["in"], ULONG, "cch"),
                  (["out"], POINTER(ULONG), "pcchWritten")),
        COMMETHOD([], HRESULT, "GetSymbolicLink", (["in"], LPWSTR, "link"), (["in"], ULONG, "cch"),
                  (["out"], POINTER(ULONG), "pcchWritten")),
        COMMETHOD([], HRESULT, "GetProcessCount", (["out"], POINTER(ULONG), "pc")),
        COMMETHOD([], HRESULT, "GetProcessActivity", (["in"], ULONG, "index"),
                  (["out"], POINTER(POINTER(IMFSensorProcessActivity)), "pp")),
    ]


class IMFSensorActivitiesReport(IUnknown):
    _iid_ = GUID("{683F7A5E-4A19-43CD-B1A9-DBF4AB3F7777}")
    _methods_ = [
        COMMETHOD([], HRESULT, "GetCount", (["out"], POINTER(ULONG), "pc")),
        COMMETHOD([], HRESULT, "GetActivityReport", (["in"], ULONG, "index"),
                  (["out"], POINTER(POINTER(IMFSensorActivityReport)), "pp")),
        COMMETHOD([], HRESULT, "GetActivityReportByDeviceName", (["in"], c_wchar_p, "link"),
                  (["out"], POINTER(POINTER(IMFSensorActivityReport)), "pp")),
    ]


class IMFSensorActivitiesReportCallback(IUnknown):
    _iid_ = GUID("{DE5072EE-DBE3-46DC-8A87-B6F631194751}")
    _methods_ = [
        COMMETHOD([], HRESULT, "OnActivitiesReport", (["in"], POINTER(IMFSensorActivitiesReport), "report")),
    ]


class IMFSensorActivityMonitor(IUnknown):
    _iid_ = GUID("{D0CEF145-B3F4-4340-A2E5-7A5080CA05CB}")
    _methods_ = [COMMETHOD([], HRESULT, "Start"), COMMETHOD([], HRESULT, "Stop")]


class CameraMonitor:
    """Démarré une fois ; `report` est le dernier rapport de Media Foundation."""

    def __init__(self) -> None:
        self.report: dict[str, list[tuple[int, bool]]] = {}
        self.error = ""
        self._ready = threading.Event()
        self._lock = threading.Lock()

    def start(self, wait: float = 3.0) -> bool:
        threading.Thread(target=self._run, name="meeting-camera", daemon=True).start()
        self._ready.wait(wait)
        return not self.error

    def snapshot(self) -> dict[str, list[tuple[int, bool]]]:
        with self._lock:
            return {k: list(v) for k, v in self.report.items()}

    def _run(self) -> None:
        monitor = self                       # le rappel écrit ici

        class _Callback(COMObject):
            _com_interfaces_ = [IMFSensorActivitiesReportCallback]

            def OnActivitiesReport(self, report):  # noqa: N802 — nom imposé par l'interface
                try:
                    seen: dict[str, list[tuple[int, bool]]] = {}
                    for i in range(report.GetCount()):
                        r = report.GetActivityReport(i)
                        buf = ctypes.create_unicode_buffer(512)
                        r.GetFriendlyName(buf, 512)
                        seen[buf.value or f"caméra {i}"] = [
                            (int(a.GetProcessId()), bool(a.GetStreamingState()))
                            for a in (r.GetProcessActivity(j) for j in range(r.GetProcessCount()))]
                    with monitor._lock:
                        monitor.report = seen
                except Exception as e:      # un rappel COM ne doit jamais lever
                    monitor.error = f"rapport illisible : {type(e).__name__}"
                return 0

        try:
            comtypes.CoInitializeEx(comtypes.COINIT_MULTITHREADED)   # fil neuf : son propre mode
            ctypes.WinDLL("mfplat").MFStartup(0x00020070, 0)        # MF_VERSION, MFSTARTUP_FULL
            create = ctypes.WinDLL("mfsensorgroup").MFCreateSensorActivityMonitor
            create.restype = HRESULT
            create.argtypes = [POINTER(IMFSensorActivitiesReportCallback),
                               POINTER(POINTER(IMFSensorActivityMonitor))]
            callback = _Callback()
            mon = POINTER(IMFSensorActivityMonitor)()
            create(callback.QueryInterface(IMFSensorActivitiesReportCallback), byref(mon))
            mon.Start()
        except Exception as e:
            self.error = f"{type(e).__name__}: {e}"[:200]
            self._ready.set()
            return
        self._ready.set()
        while True:                          # le fil garde l'appartement COM et le rappel en vie
            time.sleep(3600)
