import asyncio
import unittest

from services.monitor import (MONITOR_CMD, MonitorPoller, MonitorState, Thresholds,
                              level, overall_level, parse_snapshot)


def sample(uptime, cpu, core0, rx, tx, rd, wr, mem_avail=1_000_000, df_pct=50):
    return f"""##STAT
cpu  {cpu}
cpu0 {core0}
cpu1 {core0}
##MEM
MemTotal:        2000000 kB
MemAvailable:    {mem_avail} kB
SwapTotal:       1000000 kB
SwapFree:         750000 kB
##LOAD
0.50 0.40 0.30 1/200 999
##UPTIME
{uptime} 5000.00
##NET
Inter-|   Receive                                                |  Transmit
 face |bytes    packets errs drop fifo frame compressed multicast|bytes    packets errs drop fifo colls carrier compressed
    lo: 99999 10 0 0 0 0 0 0 99999 10 0 0 0 0 0 0
  eth0: {rx} 10 0 0 0 0 0 0 {tx} 5 0 0 0 0 0 0
 veth1: 5 1 0 0 0 0 0 0 5 1 0 0 0 0 0 0
##DISKSTAT
   8       0 sda 100 0 {rd} 0 50 0 {wr} 0 0 0 0
   8       1 sda1 100 0 999999 0 50 0 999999 0 0 0 0
##DF
/dev/sda1       41152812 20576406 18000000      {df_pct}% /
/dev/sda1       41152812 20576406 18000000      {df_pct}% /var/lib/docker
/dev/sdb1        1000000   900000    50000      95% /mnt/my data
##USERS
2
##PROCS
123
##TEMP
45000
52000
##TOPCPU
  1234 root             12.5  1.2 nginx
   999 www-data          3.0  0.5 php-fpm worker
##TOPMEM
   999 www-data          3.0 20.5 php-fpm worker
"""


S1 = sample(1000.0, "100 0 50 800 50 0 0 0", "50 0 25 400 25 0 0 0", 1000, 500, 0, 0)
S2 = sample(1002.0, "250 0 100 1500 150 0 0 0", "100 0 50 800 50 0 0 0", 3000, 1500, 2000, 4000)


class ParseTests(unittest.TestCase):
    def test_parse(self):
        s = parse_snapshot(S1)
        self.assertEqual(s.cpu["cpu"], (1000, 850))
        self.assertEqual(set(s.cpu), {"cpu", "cpu0", "cpu1"})
        self.assertEqual(s.net, {"eth0": (1000, 500)})            # lo and veth filtered
        self.assertEqual(list(s.disk_io), ["sda"])                 # partitions filtered
        self.assertEqual([m.mount for m in s.mounts], ["/", "/mnt/my data"])  # dup device dropped
        self.assertEqual(s.mounts[1].pct, 95)
        self.assertEqual((s.users, s.procs), (2, 123))
        self.assertEqual(s.temp_c, 52.0)
        self.assertEqual(s.top_cpu[1].cmd, "php-fpm worker")
        self.assertEqual(s.top_mem[0].mem, 20.5)
        self.assertEqual(s.load, (0.5, 0.4, 0.3))

    def test_garbage(self):
        s = parse_snapshot("##STAT\ncpu abc\n##NET\nnonsense\n")
        self.assertEqual(s.cpu, {})
        self.assertIsNone(s.temp_c)
        self.assertEqual(parse_snapshot("").mounts, [])

    def test_command_has_all_sections(self):
        for sec in ("STAT", "MEM", "NET", "DISKSTAT", "DF", "TOPCPU", "TOPMEM"):
            self.assertIn(f"##{sec}", MONITOR_CMD)


class StateTests(unittest.TestCase):
    def test_rates(self):
        st = MonitorState()
        m1 = st.update(parse_snapshot(S1))
        self.assertIsNone(m1.cpu)
        self.assertEqual(len(st.history["cpu"]), 0)
        m2 = st.update(parse_snapshot(S2))
        self.assertAlmostEqual(m2.cpu, 20.0)
        self.assertEqual(len(m2.cores), 2)
        self.assertAlmostEqual(m2.net["eth0"][0], 1000.0)
        self.assertAlmostEqual(m2.net["eth0"][1], 500.0)
        self.assertAlmostEqual(m2.disk_read, 2000 * 512 / 2)
        self.assertAlmostEqual(m2.disk_write, 4000 * 512 / 2)
        self.assertAlmostEqual(m2.mem_pct, 50.0)
        self.assertAlmostEqual(m2.swap_pct, 25.0)
        self.assertEqual(len(st.history["cpu"]), 1)

    def test_reboot_resets_deltas(self):
        st = MonitorState()
        st.update(parse_snapshot(S2))
        m = st.update(parse_snapshot(S1))       # uptime went backwards
        self.assertIsNone(m.cpu)
        self.assertEqual(m.net_down, 0.0)

    def test_history_cap(self):
        st = MonitorState()
        for i in range(200):
            st.update(parse_snapshot(sample(1000.0 + 2 * i, f"{100+i*10} 0 50 {800+i*90} 50 0 0 0",
                                            "1 0 1 1 0 0 0 0", 1000 + i, 500, 0, 0)))
        self.assertEqual(len(st.history["cpu"]), 120)


class LevelTests(unittest.TestCase):
    def test_levels(self):
        self.assertEqual(level(10, (70, 90)), "ok")
        self.assertEqual(level(70, (70, 90)), "warn")
        self.assertEqual(level(95, (70, 90)), "crit")
        self.assertEqual(level(None, (70, 90)), "ok")

    def test_overall_includes_disks(self):
        st = MonitorState()
        st.update(parse_snapshot(S1))
        m = st.update(parse_snapshot(S2))
        self.assertEqual(overall_level(m, Thresholds()), "crit")   # /mnt at 95%


class FakeSSH:
    def __init__(self, outputs):
        self.outputs, self.calls = list(outputs), 0
        self.status = type("S", (), {"value": "connected"})()

    async def run(self, cmd, timeout=30):
        self.calls += 1
        out = self.outputs[min(self.calls - 1, len(self.outputs) - 1)]
        if isinstance(out, Exception):
            raise out
        return type("R", (), {"stdout": out, "stderr": ""})()


class PollerTests(unittest.IsolatedAsyncioTestCase):
    async def test_polls_and_stops(self):
        ssh, updates, errors = FakeSSH([S1, S2]), [], []
        p = MonitorPoller(ssh, MonitorState(), lambda: 0.01, updates.append, errors.append,
                          lambda fn: asyncio.ensure_future(fn()), min_delay=0)
        p.set_active(True)
        await asyncio.sleep(0.15)
        p.set_active(False)
        await asyncio.sleep(0.05)
        n = ssh.calls
        self.assertGreaterEqual(len(updates), 2)
        await asyncio.sleep(0.1)
        self.assertEqual(ssh.calls, n)                  # no polling while inactive
        self.assertFalse(p._running)

    async def test_error_reported_and_recovers(self):
        ssh, updates, errors = FakeSSH([RuntimeError("boom"), S1]), [], []
        p = MonitorPoller(ssh, MonitorState(), lambda: 0.01, updates.append, errors.append,
                          lambda fn: asyncio.ensure_future(fn()), min_delay=0)
        p.set_active(True)
        await asyncio.sleep(0.4)
        p.set_active(False)
        await asyncio.sleep(0.1)
        self.assertEqual(errors[0], "boom")
        self.assertTrue(updates)

    async def test_no_double_loop(self):
        ssh = FakeSSH([S1])
        spawned = []
        p = MonitorPoller(ssh, MonitorState(), lambda: 0.01, lambda m: None, lambda e: None,
                          lambda fn: spawned.append(asyncio.ensure_future(fn())), min_delay=0)
        p.set_active(True); p.set_active(True); p.set_active(True)
        await asyncio.sleep(0.05)
        p.set_active(False)
        await asyncio.sleep(0.05)
        self.assertEqual(len(spawned), 1)


if __name__ == "__main__":
    unittest.main()
