import importlib.util
import os
import shutil
import signal
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGING = ROOT / "packaging"
SH = shutil.which("sh")
MARKER = "# enigmacast-sync watchdog"


@unittest.skipUnless(SH, "no POSIX sh")
class ScriptSyntaxTests(unittest.TestCase):
    def test_all_shell_scripts_parse(self):
        for name in ("postinst", "prerm", "postrm", "sync-run", "web-run", "watchdog", "enigmacast-sync", "enigmacast-web"):
            result = subprocess.run([SH, "-n", str(PACKAGING / name)], capture_output=True, text=True)
            self.assertEqual(0, result.returncode, (name, result.stderr))

    def test_run_scripts_lower_oom_priority_before_exec(self):
        for name in ("sync-run", "web-run"):
            text = (PACKAGING / name).read_text()
            self.assertLess(text.index("oom_score_adj"), text.index("\nexec "))
            self.assertIn("echo 300 > /proc/self/oom_score_adj", text)

    def test_package_ships_the_watchdog_executable(self):
        spec = importlib.util.spec_from_file_location("build_ipk", PACKAGING / "build_ipk.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        files = module._stage("0.0.0")
        data, mode = files["usr/lib/enigma2/python/Plugins/Extensions/EnigmaCastSync/watchdog"]
        self.assertEqual(0o755, mode)
        self.assertNotIn(b"\r\n", data)
        self.assertIn(b"watchdog install", files["CONTROL/postinst"][0])
        self.assertIn(b"watchdog remove", files["CONTROL/prerm"][0])
        self.assertIn(MARKER.encode(), files["CONTROL/postrm"][0])


@unittest.skipUnless(SH, "no POSIX sh")
class CrontabTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.tab = self.dir / "root"

    def tearDown(self):
        self.tmp.cleanup()

    def _run(self, action: str):
        env = dict(os.environ, CRONTABS=str(self.dir))
        result = subprocess.run([SH, str(PACKAGING / "watchdog"), action], env=env, capture_output=True, text=True)
        self.assertEqual(0, result.returncode, result.stderr)

    def test_install_is_idempotent_and_keeps_other_jobs(self):
        self.tab.write_text("0 3 * * * /usr/script/backup.sh\n")
        for _ in range(3):  # install, reinstall, upgrade
            self._run("install")
        lines = self.tab.read_text().splitlines()
        self.assertEqual("0 3 * * * /usr/script/backup.sh", lines[0])
        ours = [line for line in lines if MARKER in line]
        self.assertEqual(1, len(ours))
        self.assertTrue(ours[0].startswith("* * * * * "))
        self.assertIn("EnigmaCastSync/watchdog", ours[0])
        self.assertEqual(0o600, self.tab.stat().st_mode & 0o777)
        self._run("remove")
        self.assertEqual("0 3 * * * /usr/script/backup.sh\n", self.tab.read_text())

    def test_install_creates_the_table_and_remove_without_table_is_harmless(self):
        self._run("remove")
        self.assertFalse(self.tab.exists())
        self._run("install")
        self.assertEqual(1, self.tab.read_text().count(MARKER))


@unittest.skipUnless(SH and shutil.which("python3"), "no POSIX sh")
class InitScriptWatchdogTests(unittest.TestCase):
    """Runs the real init script against a temporary data directory with a fake server."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.data = base / "data"
        plugin = base / "plugin"
        bindir = base / "bin"
        plugin.mkdir()
        bindir.mkdir()
        # A stand-in for sync-run whose command line looks like the real server's.
        run = plugin / "sync-run"
        run.write_text('#!/bin/sh\nexec python3 -c "import time; time.sleep(120)" enigmacast_sync.cli --data-dir "$1" serve\n')
        run.chmod(0o755)
        ssd = bindir / "start-stop-daemon"
        ssd.write_text(
            "#!/bin/sh\n"
            'while [ $# -gt 0 ]; do case "$1" in -p) pid=$2; shift 2;; -x) exe=$2; shift 2;; --) shift; break;; *) shift;; esac; done\n'
            '"$exe" "$@" >/dev/null 2>&1 &\n'
            'echo $! > "$pid"\n'
        )
        ssd.chmod(0o755)
        text = (PACKAGING / "enigmacast-sync").read_text()
        text = text.replace("PLUGIN=/usr/lib/enigma2/python/Plugins/Extensions/EnigmaCastSync", f"PLUGIN={plugin}")
        text = text.replace("/media/hdd /media/usb /media/mmc /media/cf", str(base / "no-media"))
        text = text.replace("echo /etc/enigmacast-sync", f"echo {self.data}")
        text = text.replace("id enigmacast >/dev/null 2>&1 || {", "true || {")
        self.script = base / "enigmacast-sync"
        self.script.write_text(text)
        self.env = dict(os.environ, PATH=os.environ.get("PATH", "") + ":" + str(bindir))

    def tearDown(self):
        self._init("stop")
        self.tmp.cleanup()

    def _init(self, action: str):
        return subprocess.run([SH, str(self.script), action], env=self.env, capture_output=True, text=True, timeout=30)

    def _pid(self) -> int:
        for _ in range(50):
            pid_file = self.data / "enigmacast-sync.pid"
            if pid_file.is_file() and pid_file.read_text().strip():
                pid = int(pid_file.read_text())
                if Path(f"/proc/{pid}/cmdline").exists() and b" serve" in Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\0", b" ") + b" ":
                    return pid
            time.sleep(0.1)
        self.fail("server did not start")

    @unittest.skipUnless(Path("/proc/self/cmdline").exists(), "needs /proc")
    def test_watchdog_restarts_a_killed_server_but_not_a_stopped_one(self):
        (self.data / "logs").mkdir(parents=True)
        (self.data / "logs" / "sync.log").write_bytes(b"x" * (1024 * 1024 + 10))
        self.assertEqual(0, self._init("start").returncode)
        first = self._pid()
        self.assertTrue((self.data / "logs" / "sync.log.1").is_file())  # rotated above 1 MiB
        self._init("watchdog")
        self.assertEqual(first, self._pid())  # running: left alone
        os.kill(first, signal.SIGKILL)
        time.sleep(0.3)
        self._init("watchdog")
        second = self._pid()
        self.assertNotEqual(first, second)
        self.assertIn("watchdog: enigmacast-sync was not running", (self.data / "logs" / "sync.log").read_text())
        self._init("stop")
        self.assertTrue((self.data / "enigmacast-sync.stopped").is_file())
        self._init("watchdog")
        time.sleep(0.3)
        self.assertFalse((self.data / "enigmacast-sync.pid").is_file())  # stopped on purpose
        self._init("start")
        self.assertFalse((self.data / "enigmacast-sync.stopped").is_file())
        self._pid()
