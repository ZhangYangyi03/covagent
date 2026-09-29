"""Run a simulation, read the coverage it produced, and parse both.

Wraps the WSL side so the rest of the package never shells out itself. The
contract is: give it a directory with a Makefile, get back a RunResult carrying
the exit status, the transcript, and a CoverageReport built from the real data
the simulator wrote.
"""

import base64
import os
import platform
import re
import subprocess
import time

from . import coverage

WSL_DISTRO = os.environ.get("COVAGENT_WSL", "Ubuntu")


class RunResult:
    def __init__(self, ok, seconds, log, report, rc=None):
        self.ok = ok
        self.seconds = seconds
        self.log = log
        self.report = report
        self.rc = rc

    def to_dict(self):
        return {"ok": self.ok, "seconds": round(self.seconds, 2),
                "closure": round(self.report.closure(), 4) if self.report else None,
                "bins": len(self.report.bins) if self.report else 0,
                "log_tail": self.log[-1200:]}


def win_to_wsl(path):
    """Translate a Windows path to where the toolchain can see it.

    Three cases, and getting any of them wrong is silent rather than loud:
      Linux              the path is already usable -- do not touch it. Running
                         os.path.abspath on a relative path here is right, but
                         rewriting an absolute one produced '/mnt/d/tmp/x.v'.
      already POSIX      leave it alone on every platform, so a caller passing
                         '/tmp/x.v' (a WSL path, or a Linux path) is not mangled
                         into '/mnt/d/tmp/x.v'.
      Windows drive      D:\\a -> /mnt/d/a.
    """
    if platform.system() != "Windows":
        return path if os.path.isabs(path) else os.path.abspath(path)
    if path.startswith("/"):
        return _collapse(path) if "_collapse" in globals() else path
    p = path.replace("\\", "/")
    m = re.match(r"^([A-Za-z]):/(.*)$", p)
    if m:
        return "/mnt/" + m.group(1).lower() + "/" + m.group(2)
    back = os.path.abspath(path).replace("\\", "/")
    m = re.match(r"^([A-Za-z]):/(.*)$", back)
    return "/mnt/" + m.group(1).lower() + "/" + m.group(2) if m else back


def wsl_bash(script, timeout=900):
    """Run bash where verilator lives: WSL on Windows, bash directly on Linux.

    The Windows path carries the script as base64 because this host passes
    non-ASCII argv through wsl.exe unreliably. The Linux path exists because
    leaving it out broke CI with FileNotFoundError: 'wsl' on ubuntu-latest.
    """
    if platform.system() == "Windows":
        b64 = base64.b64encode(script.encode("utf-8")).decode("ascii")
        cmd = ["wsl", "-d", WSL_DISTRO, "--", "bash", "-lc",
               "echo %s | base64 -d > /tmp/ca_run.sh && bash /tmp/ca_run.sh" % b64]
    else:
        cmd = ["bash", "-lc", script]
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=timeout)
        return (r.stdout or b"").decode("utf-8", "replace"), r.returncode
    except subprocess.TimeoutExpired:
        return "", -9


def parse_info(path):
    """Read an lcov-format coverage.info into a CoverageReport."""
    if not os.path.exists(path):
        return coverage.CoverageReport([])
    bins = []
    cur = None
    for line in open(path, encoding="utf-8", errors="ignore"):
        line = line.strip()
        if line.startswith("SF:"):
            cur = os.path.basename(line[3:])
        elif line.startswith("DA:") and cur:
            try:
                ln, hits = line[3:].split(",")[:2]
                bins.append(coverage.Bin("line", "%s:%s" % (cur, ln),
                                         location="%s:%s" % (cur, ln),
                                         hits=int(hits)))
            except ValueError:
                pass
        elif line.startswith("BRDA:") and cur:
            parts = line[5:].split(",")
            if len(parts) >= 4:
                ln, _, _, taken = parts[:4]
                hits = 0 if taken in ("-", "") else int(taken)
                bins.append(coverage.Bin("branch", "%s:%s:%s" % (cur, ln, parts[1]),
                                         location="%s:%s" % (cur, ln), hits=hits))
        elif line.startswith("TN:") or line.startswith("end_of_record"):
            cur = None if line.startswith("end_of_record") else cur
    return coverage.CoverageReport(bins, source=path, tool="verilator")


def run_make(design_dir, target="cov", timeout=900, extra_env=""):
    """Run `make <target>` in WSL and read back the coverage it produced."""
    mount = win_to_wsl(design_dir)
    script = "cd %s || exit 7\n%s make %s 2>&1\n" % (mount, extra_env, target)
    t0 = time.time()
    log, rc = wsl_bash(script, timeout=timeout)
    dt = time.time() - t0
    info = os.path.join(design_dir, "coverage.info")
    rep = parse_info(info)
    ok = ("TB_DONE" in log) or (rc == 0 and rep.bins)
    return RunResult(ok, dt, log, rep, rc)


def write_testbench(design_dir, filename, body):
    p = os.path.join(design_dir, filename)
    with open(p, "w", encoding="utf-8", newline="\n") as f:
        f.write(body)
    return p


def toolchain_check():
    out, _ = wsl_bash("which verilator verilator_coverage iverilog 2>/dev/null; "
                      "verilator --version 2>/dev/null", 120)
    lines = [l for l in out.splitlines() if l.strip()]
    return bool(lines), " | ".join(lines)
