"""Monitoring dashboard. Built once; controls are updated in place on each poll."""
from __future__ import annotations

import time
from typing import Optional

import flet as ft

from core.errors import TakError
from core.fmt import human_duration, human_size
from services.monitor import Metrics, ProcInfo, Thresholds, level, overall_level
from .. import theme
from ..components.common import card, confirm, toast
from ..components.gauge import LEVEL_COLORS, LineChartSpark, RingGauge
from ..context import AppContext

_HEALTH = {"ok": "Healthy", "warn": "Warning", "crit": "Critical"}


def _rate(bps: float) -> str:
    return human_size(bps) + "/s"


def _section(title: str) -> ft.Control:
    return ft.Text(title, size=15, weight=ft.FontWeight.W_600)


def _kv(label: str, value: str) -> ft.Control:
    return ft.Row([ft.Text(label, opacity=0.7), ft.Text(value, weight=ft.FontWeight.W_500)],
                  alignment=ft.MainAxisAlignment.SPACE_BETWEEN)


class MonitorView:
    def __init__(self, ctx: AppContext) -> None:
        self.ctx, self.page = ctx, ctx.page
        self.last: Optional[Metrics] = None
        self.proc_mode = "cpu"

        self.health_dot = ft.Icon(ft.Icons.CIRCLE, size=12)
        self.health_txt = ft.Text("Waiting for data…")
        self.status = ft.Text("", size=11, opacity=0.6)
        self.g_cpu, self.g_ram, self.g_swap = RingGauge("CPU"), RingGauge("RAM"), RingGauge("Swap")
        # use real `flet_charts.LineChart` sparklines when available
        # (and the user hasn't disabled them in Settings). LineChartSpark falls
        # back to the bar Sparkline on its own, so we always construct one and
        # let it pick.
        use_lines = bool(self.ctx.store.data.monitor_line_charts)
        self.sp_cpu = LineChartSpark(kind="cpu", enabled=use_lines)
        self.sp_ram = LineChartSpark(kind="ram", enabled=use_lines)
        self.sp_down = LineChartSpark(kind="down", enabled=use_lines)
        self.sp_up = LineChartSpark(kind="up", enabled=use_lines)
        self.t_cpu, self.t_ram = ft.Text("", size=12), ft.Text("", size=12)
        self.t_down, self.t_up = ft.Text("", size=12), ft.Text("", size=12)
        self.cores_col = ft.Column(spacing=4)
        self.disks_col = ft.Column(spacing=8)
        self.net_col = ft.Column(spacing=4)
        self.sys_col = ft.Column(spacing=4)
        self.proc_col = ft.Column(spacing=0)
        self.proc_toggle = ft.SegmentedButton(
            selected=["cpu"], on_change=self._on_proc_mode,
            segments=[ft.Segment(value="cpu", label=ft.Text("CPU")),
                      ft.Segment(value="mem", label=ft.Text("Memory"))])

        def spark_card(title, t_val, sp, extra=None):
            return card(ft.Column([ft.Row([ft.Text(title, size=13, weight=ft.FontWeight.W_600),
                                           t_val], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
                                   sp.control], spacing=6))

        self.control = ft.ListView(padding=theme.SPACE, spacing=theme.SPACE, expand=True, controls=[
            ft.Row([self.health_dot, self.health_txt, ft.Container(expand=True), self.status]),
            card(ft.Row([self.g_cpu.control, self.g_ram.control, self.g_swap.control],
                        alignment=ft.MainAxisAlignment.SPACE_AROUND)),
            spark_card("CPU history", self.t_cpu, self.sp_cpu),
            spark_card("RAM history", self.t_ram, self.sp_ram),
            spark_card("Network ↓ download", self.t_down, self.sp_down),
            spark_card("Network ↑ upload", self.t_up, self.sp_up),
            card(ft.ExpansionTile(title=ft.Text("Per-core CPU"), controls=[self.cores_col])),
            card(ft.Column([_section("Disks"), self.disks_col], spacing=8)),
            card(ft.Column([_section("Network & disk I/O"), self.net_col], spacing=4)),
            card(ft.Column([_section("System"), self.sys_col], spacing=4)),
            card(ft.Column([_section("Top processes"), self.proc_toggle, self.proc_col], spacing=6)),
        ])

    # ---- helpers ----
    def _thr(self) -> Thresholds:
        s = self.ctx.store.data
        return Thresholds(tuple(s.thr_cpu), tuple(s.thr_ram), tuple(s.thr_disk))

    def _on_proc_mode(self, e) -> None:
        self.proc_mode = next(iter(e.control.selected))
        if self.last:
            self._draw_procs(self.last)
            self.page.update()

    def set_status(self, text: str, update: bool = True) -> None:
        self.status.value = text
        if update:
            self.page.update()

    # ---- main update ----
    def apply(self, m: Optional[Metrics], update: bool = True) -> None:
        if m is None:
            return
        self.last = m
        t = self._thr()
        lc, lr = level(m.cpu, t.cpu), level(m.mem_pct, t.ram)
        ov = overall_level(m, t)
        self.health_dot.color = LEVEL_COLORS[ov]
        self.health_txt.value = _HEALTH[ov]
        self.status.value = "Live · " + time.strftime("%H:%M:%S", time.localtime(m.taken_at))

        self.g_cpu.set(m.cpu, lc, f"load {m.load[0]:.2f}")
        self.g_ram.set(m.mem_pct, lr, f"{human_size(m.mem_used_kb * 1024)} / {human_size(m.mem_total_kb * 1024)}")
        self.g_swap.set(m.swap_pct if m.swap_total_kb else None, level(m.swap_pct, (50, 80)),
                        human_size(m.swap_total_kb * 1024) if m.swap_total_kb else "none")

        h = self.ctx.monitor.history
        self.sp_cpu.set(h["cpu"], 100, LEVEL_COLORS[lc])
        self.sp_ram.set(h["ram"], 100, LEVEL_COLORS[lr])
        self.sp_down.set(h["down"], None, ft.Colors.BLUE)
        self.sp_up.set(h["up"], None, ft.Colors.TEAL)
        self.t_cpu.value = "—" if m.cpu is None else f"{m.cpu:.0f}%"
        self.t_ram.value = f"{m.mem_pct:.0f}%"
        self.t_down.value, self.t_up.value = _rate(m.net_down), _rate(m.net_up)

        self.cores_col.controls = [
            ft.Row([ft.Text(str(i), width=24, size=12),
                    ft.ProgressBar(value=v / 100, color=LEVEL_COLORS[level(v, t.cpu)], expand=True),
                    ft.Text(f"{v:.0f}%", width=44, size=12, text_align=ft.TextAlign.END)])
            for i, v in enumerate(m.cores)] or [ft.Text("Collecting…", size=12, opacity=0.6)]

        self.disks_col.controls = [
            ft.Column([ft.Row([ft.Text(d.mount, expand=True, no_wrap=True),
                               ft.Text(f"{human_size(d.used_kb * 1024)} / {human_size(d.size_kb * 1024)}  "
                                       f"({d.pct}%)", size=12)]),
                       ft.ProgressBar(value=d.pct / 100, color=LEVEL_COLORS[level(d.pct, t.disk)])],
                      spacing=4) for d in m.mounts] or [ft.Text("No block-device mounts found.", size=12, opacity=0.6)]

        self.net_col.controls = [_kv(f"{i}", f"↓ {_rate(d)}   ↑ {_rate(u)}") for i, (d, u) in m.net.items()]
        self.net_col.controls.append(_kv("Disk I/O", f"R {_rate(m.disk_read)}   W {_rate(m.disk_write)}"))

        rows = [_kv("Uptime", human_duration(m.uptime_s)),
                _kv("Load 1 / 5 / 15", " / ".join(f"{x:.2f}" for x in m.load)),
                _kv("Processes", str(m.procs)), _kv("Logged-in users", str(m.users))]
        if m.temp_c is not None:
            rows.append(_kv("Temperature", f"{m.temp_c:.0f} °C"))
        info = self.ctx.sysinfo
        if info:
            rows += [_kv("OS", info.distro or "—"), _kv("Kernel", info.kernel or "—")]
        self.sys_col.controls = rows
        self._draw_procs(m)
        if update:
            self.page.update()

    def _draw_procs(self, m: Metrics) -> None:
        procs = m.top_cpu if self.proc_mode == "cpu" else m.top_mem
        if not procs:
            self.proc_col.controls = [ft.Text("Process list not available on this server.",
                                              size=12, opacity=0.6)]
            return
        self.proc_col.controls = [
            ft.Text("CPU % is the average since each process started, not the live value."
                    if self.proc_mode == "cpu" else "Memory % of RAM.", size=11, opacity=0.6)
        ] + [self._proc_row(p) for p in procs]

    def _proc_row(self, p: ProcInfo) -> ft.Control:
        return ft.Row([
            ft.Column([ft.Text(p.cmd, no_wrap=True, weight=ft.FontWeight.W_500),
                       ft.Text(f"PID {p.pid} · {p.user}", size=11, opacity=0.6)], spacing=0, expand=True),
            ft.Text(f"{p.cpu:.1f}% / {p.mem:.1f}%", size=12),
            ft.IconButton(ft.Icons.CLOSE, icon_size=20, width=44, height=44,
                          tooltip="Kill process", on_click=lambda _, q=p: self._kill(q))],
            vertical_alignment=ft.CrossAxisAlignment.CENTER)

    def _kill(self, p: ProcInfo) -> None:
        async def do():
            try:
                r = await self.ctx.ssh.run(f"kill -TERM {int(p.pid)}", timeout=10)
            except TakError as e:
                toast(self.page, e.message, error=True)
                return
            if r.ok:
                toast(self.page, f"Sent SIGTERM to {p.cmd} ({p.pid}).")
            elif "not permitted" in r.stderr.lower():
                toast(self.page, "Permission denied — that process belongs to another user.", error=True)
            else:
                toast(self.page, r.stderr.strip() or "Could not signal the process.", error=True)
        confirm(self.page, "Kill process?",
                f"Send SIGTERM to '{p.cmd}' (PID {p.pid}, user {p.user}). The process may not stop "
                "immediately, and killing system processes can break the server.",
                do, yes_label="Kill")


def monitor_page(ctx: AppContext) -> ft.Control:
    view = MonitorView(ctx)
    ctx.monitor_view = view
    if ctx.monitor.last:
        view.apply(ctx.monitor.last, update=False)
    elif ctx.ssh.status.value != "connected":
        view.status.value = "Not connected"
    else:
        view.status.value = "Collecting first sample…"
    return view.control
