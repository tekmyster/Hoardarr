from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import json
import os
import pathlib
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "build-offline-apt-repository.py"
SPEC = importlib.util.spec_from_file_location("hoardarr_offline_repo", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
offline_repo = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = offline_repo
SPEC.loader.exec_module(offline_repo)


PCP_TRACE_PHASES = (
    ("01-fixture-creation", "fixture-creation"),
    ("02-package-download", "package-download"),
    ("03-package-hash", "package-hash"),
    ("04-package-extract", "package-extract"),
    ("05-mount-namespace", "mount-namespace"),
    ("06-old-failure", "old-failure"),
    ("07-guard-preparation", "guard-preparation"),
    ("08-pcp-configure", "pcp-configure"),
    ("09-all-denied-presets", "all-denied-presets"),
    ("10-host-manager-isolation", "host-manager-isolation"),
    ("11-interrupted-retention", "interrupted-retention"),
    ("12-final-disable-readback", "final-disable-readback"),
    ("13-retained-manifest", "retained-manifest"),
    ("14-peer-isolation", "peer-isolation"),
    ("15-fixture-cleanup", "fixture-cleanup"),
)
PCP_TRACE_MAX_BYTES = 8192
PCP_TRACE_MAX_LINE_BYTES = 240
PCP_TRACE_RECORD = re.compile(
    r"^HPCP\|1\|(BEGIN|PASS|EXIT)\|([0-9]{2}-[a-z0-9-]+)\|"
    r"status=(-|[0-9]{1,3})\|line=(-|[0-9]{1,6})\|"
    r"function=(-|[A-Za-z_][A-Za-z0-9_]*)\|label=([a-z0-9-]+)$"
)
PCP_MANAGER_ROOT_RECEIPT_MAX_BYTES = 32 * 1024
PCP_MANAGER_ROOT_RECEIPT_MAX_ENTRIES = 128
PCP_MANAGER_ROOT_PATH_MAX_BYTES = 192
PCP_MANAGER_ROOT_TYPES = {
    "directory",
    "regular",
    "symlink",
    "socket",
    "fifo",
    "block",
    "char",
    "other",
}
PCP_MANAGER_ROOT_HEADER = re.compile(
    r"^HMROOT\|1\|(before|after)\|status=(-|[0-9]{1,3})$"
)
PCP_MANAGER_ROOT_COMPONENT = re.compile(r"^[A-Za-z0-9_.@:+,-]+$")
PCP_SYSTEMD_SOURCE_RECEIPT_MAX_BYTES = 8192
PCP_SYSTEMD_CAUSAL_RECEIPT_MAX_BYTES = 4096
PCP_SYSTEMD_UPSTREAM_REPOSITORY = "https://github.com/systemd/systemd-stable"
PCP_SYSTEMD_UPSTREAM_TAG = "v255.4"
PCP_SYSTEMD_UPSTREAM_REVISION = "387a14a7b67b8b76adaed4175e14bb7e39b2f738"
PCP_SYSTEMD_ANALYZE_SOURCE_PATH = "src/analyze/analyze-condition.c"
PCP_SYSTEMD_ANALYZE_SOURCE_FUNCTION = "verify_conditions:68-114"
PCP_SYSTEMD_ANALYZE_SOURCE_SHA256 = (
    "3f89216b21faa202099f290615cdd8ed4ee5f98a2f0094242d447670248a9b89"
)
PCP_SYSTEMD_MANAGER_SOURCE_PATH = "src/core/manager.c"
PCP_SYSTEMD_MANAGER_SOURCE_FUNCTION = "manager_ready:1891-1910"
PCP_SYSTEMD_MANAGER_SOURCE_SHA256 = (
    "58af3c261e43b6de343be931a46c049152eb57c856f24f81dd53bdd9abafa72e"
)
PCP_SYSTEMD_MARKER_PATH = "/run/systemd/systemd-units-load"
PCP_SYSTEMD_FALSE_CONDITION = (
    "ConditionPathExists=/dev/null/hoardarr-offline-service-guard/pmcd.service"
)
F19_DIAGNOSTIC_SCHEMA = 1
F19_DIAGNOSTIC_MAX_BYTES = 256 * 1024
F19_COMMAND_TRACE_MAX_BYTES = 64 * 1024
F19_TARGET_UNITS = (
    "corosync.service",
    "iscsid.service",
    "iscsid.socket",
    "iscsi.service",
    "open-iscsi.service",
)
F19_MOUNT_ROOTS = (
    "/usr/lib/systemd/system",
    "/etc/systemd/system",
    "/var/lib/systemd",
    "/run/systemd",
)
F20_DIAGNOSTIC_SCHEMA = 1
F20_DIAGNOSTIC_MAX_BYTES = 256 * 1024
F20_OUTPUT_MAX_BYTES = 32 * 1024
F21_CAPTURE_ERROR_SCHEMA = 1
F21_CAPTURE_ERROR_MAX_STDERR_BYTES = 8 * 1024
F20_SYSV_PATHS = (
    "/etc/init.d/iscsid",
    "/usr/lib/systemd/systemd-sysv-install",
    "/usr/sbin/update-rc.d",
    "/usr/sbin/invoke-rc.d",
)
F20_RC_DIRS = tuple(f"/etc/rc{level}.d" for level in (*range(7), "S"))
F20_GENERATOR_ROOTS = (
    "/run/systemd/generator",
    "/run/systemd/generator.early",
    "/run/systemd/generator.late",
)


def _f20_object(path: pathlib.Path) -> dict[str, object]:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return {"path": path.as_posix(), "type": "absent"}
    if path.is_symlink():
        kind = "symlink"
    elif path.is_file():
        kind = "regular"
    elif path.is_dir():
        kind = "directory"
    else:
        kind = "other"
    record: dict[str, object] = {
        "path": path.as_posix(),
        "type": kind,
        "uid": metadata.st_uid,
        "gid": metadata.st_gid,
        "mode": format(metadata.st_mode & 0o7777, "04o"),
        "size": metadata.st_size,
    }
    if kind == "symlink":
        record["link_target"] = os.readlink(path)
        resolved = path.resolve(strict=True)
        if resolved.is_file() and not resolved.is_symlink():
            record["resolved_sha256"] = hashlib.sha256(
                resolved.read_bytes()
            ).hexdigest()
    elif kind == "regular":
        record["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    return record


def _capture_f20_host_manifest() -> tuple[dict[str, object], str]:
    records = [_f20_object(pathlib.Path(path)) for path in F20_SYSV_PATHS]
    directories: list[dict[str, object]] = []
    for raw in F20_RC_DIRS:
        root = pathlib.Path(raw)
        record = _f20_object(root)
        entries = []
        if root.is_dir() and not root.is_symlink():
            for path in sorted(root.iterdir(), key=lambda item: item.name):
                if "iscsid" not in path.name and "open-iscsi" not in path.name:
                    continue
                if len(entries) >= 32:
                    raise AssertionError("F20 host SysV entry cap exceeded")
                entries.append(_f20_object(path))
        record["entries"] = entries
        directories.append(record)
    manifest = {"schema_version": 1, "objects": records, "rc_directories": directories}
    encoded = (
        json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode()
    if len(encoded) > 128 * 1024:
        raise AssertionError("F20 host manifest exceeds cap")
    return manifest, hashlib.sha256(encoded).hexdigest()


F20_SNAPSHOT_SCRIPT = r"""#!/usr/bin/python3
from __future__ import annotations
import base64, hashlib, json, os, pathlib, re, stat, subprocess, sys

SCHEMA=1
MAX_RECEIPT=256*1024
MAX_CONTENT=64*1024
MAX_OUTPUT=32*1024
OBJECTS=("/etc/init.d/iscsid","/usr/lib/systemd/systemd-sysv-install","/usr/sbin/update-rc.d","/usr/sbin/invoke-rc.d")
RC_DIRS=tuple(f"/etc/rc{x}.d" for x in (*range(7),"S"))
GENERATORS=("/run/systemd/generator","/run/systemd/generator.early","/run/systemd/generator.late")
SAFE_PACKAGE=re.compile(r"^[a-z0-9][a-z0-9+.-]*(?::[a-z0-9]+)?$")

def digest(path):
    h=hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda:stream.read(1024*1024),b""): h.update(block)
    return h.hexdigest()

def classify(path,content=False):
    try: metadata=path.lstat()
    except FileNotFoundError: return {"path":str(path),"type":"absent","package":None}
    mode=metadata.st_mode
    kind="symlink" if stat.S_ISLNK(mode) else "regular" if stat.S_ISREG(mode) else "directory" if stat.S_ISDIR(mode) else "other"
    row={"path":str(path),"type":kind,"uid":metadata.st_uid,"gid":metadata.st_gid,"mode":format(stat.S_IMODE(mode),"04o"),"size":metadata.st_size}
    if kind=="symlink":
        row["link_target"]=os.readlink(path)
        resolved=path.resolve(strict=False)
        row["resolved_path"]=str(resolved)
        row["resolved_confined"]=any(resolved==root or root in resolved.parents for root in (pathlib.Path("/etc"),pathlib.Path("/usr")))
    elif kind=="regular":
        if metadata.st_size>MAX_CONTENT: raise SystemExit("F20 object exceeds cap")
        row["sha256"]=digest(path)
        if content: row["content_base64"]=base64.b64encode(path.read_bytes()).decode("ascii")
    query=subprocess.run(["/usr/bin/dpkg-query","-S","--",str(path)],text=True,capture_output=True,check=False)
    owners=[]
    if query.returncode==0:
        for raw in query.stdout.splitlines():
            owner,separator,_=raw.partition(": ")
            if not separator or not SAFE_PACKAGE.fullmatch(owner) or owner in owners: raise SystemExit("F20 package owner is unsafe or duplicated")
            version=subprocess.run(["/usr/bin/dpkg-query","-W",f"-f=${{Status}}\\t${{Version}}\\n",owner],text=True,capture_output=True,check=False)
            fields=version.stdout.rstrip("\n").split("\t")
            if version.returncode!=0 or len(fields)!=2 or fields[0]!="install ok installed" or not fields[1] or len(fields[1])>128: raise SystemExit("F20 package version is invalid")
            owners.append({"package":owner,"version":fields[1]})
    elif query.returncode!=1: raise SystemExit("F20 package lookup status is invalid")
    row["package"]=owners or None
    return row

def mount(path,source):
    matches=[]
    for raw in pathlib.Path("/proc/self/mountinfo").read_text().splitlines():
        left,right=raw.split(" - ",1); fields=left.split(); point=fields[4].replace("\\040"," ")
        if point==path: matches.append((fields,right.split()))
    if len(matches)!=1: raise SystemExit(f"F20 private mount is missing or ambiguous: {path}")
    fields,right=matches[0]; target=pathlib.Path(path); fixture=pathlib.Path(source)
    return {"mountpoint":path,"mount_id":int(fields[0]),"root":fields[3],"filesystem_type":right[0],"source":right[1],"fixture_source":source,"fixture_identity":f"{fixture.stat().st_dev}:{fixture.stat().st_ino}","mountpoint_identity":f"{target.stat().st_dev}:{target.stat().st_ino}","bind_identity_matches":fixture.stat().st_dev==target.stat().st_dev and fixture.stat().st_ino==target.stat().st_ino}

def entries(root):
    output=[]
    if root.is_dir() and not root.is_symlink():
        for path in sorted(root.iterdir(),key=lambda item:item.name):
            if "iscsid" not in path.name and "open-iscsi" not in path.name: continue
            if len(output)>=32 or not re.fullmatch(r"[A-Za-z0-9_.@:+,-]+",path.name): raise SystemExit("F20 SysV entry is unsafe or excessive")
            row=classify(path,content=True); row["name"]=path.name; output.append(row)
    return output

def helper(stage,work):
    names=("f20-helper-invocation.tsv","f20-helper-stdout.bin","f20-helper-stderr.bin","f20-helper-status.txt")
    paths=[work/name for name in names]
    partials=[work/"f20-helper-stdout.bin.partial",work/"f20-helper-stderr.bin.partial"]
    repeat=work/"f20-helper-repeat.txt"
    entry_path=work/"f25-helper-entry.json"
    entry_partial=work/"f25-helper-entry.json.partial"
    real=work/"f20-helper-real"
    source_hash=(work/"f20-helper-source.sha256").read_text("ascii").strip()
    if not re.fullmatch(r"[0-9a-f]{64}",source_hash) or digest(real)!=source_hash: raise SystemExit("F20 copied helper identity is invalid")
    identity={"path":str(real),"size":real.stat().st_size,"mode":format(stat.S_IMODE(real.stat().st_mode),"04o"),"sha256":source_hash}
    if stage=="before":
        if any(path.exists() or path.is_symlink() for path in (*paths,*partials,repeat,entry_path,entry_partial)): raise SystemExit("F20 helper evidence exists before call")
        return {"invoked":False,"real_helper":identity,"entry_guard":{"entry_reached":False}}
    entry={"entry_reached":False}
    if entry_path.exists() or entry_path.is_symlink() or entry_partial.exists() or entry_partial.is_symlink():
        if entry_partial.exists() or entry_partial.is_symlink(): raise SystemExit("F25 helper entry partial evidence remains")
        metadata=entry_path.lstat()
        if not stat.S_ISREG(metadata.st_mode) or entry_path.is_symlink() or stat.S_IMODE(metadata.st_mode)!=0o600 or metadata.st_uid!=0 or metadata.st_gid!=0 or metadata.st_nlink!=1 or metadata.st_size>8192: raise SystemExit("F25 helper entry metadata is invalid")
        try: entry=json.loads(entry_path.read_text("utf-8"))
        except (UnicodeDecodeError,json.JSONDecodeError) as exc: raise SystemExit("F25 helper entry is invalid JSON") from exc
        keys=("expected_argc","exact_vector","systemd_offline","dpkg_maintscripts_package","dpkg_maintscripts_name","exact_private_path","wrapper_identity_mode","real_helper_identity_mode")
        if not isinstance(entry,dict) or set(entry)!={"schema_version","entry_reached","argc","argv","predicates","guard_outcome"} or entry.get("schema_version")!=1 or entry.get("entry_reached") is not True: raise SystemExit("F25 helper entry schema is invalid")
        argc=entry.get("argc"); argv=entry.get("argv"); predicates=entry.get("predicates")
        if not isinstance(argc,int) or isinstance(argc,bool) or not 0<=argc<=16 or not isinstance(argv,list) or len(argv)!=argc: raise SystemExit("F25 helper entry argc is invalid")
        values=[]
        for index,item in enumerate(argv):
            if not isinstance(item,dict) or item.get("position")!=index: raise SystemExit("F25 helper entry argv order is invalid")
            if item.get("classification")=="ALLOWLISTED":
                if set(item)!={"position","classification","value"} or item.get("value") not in {"--root=/","disable","iscsid"}: raise SystemExit("F25 helper entry raw argv escaped allowlist")
                values.append(item["value"])
            elif item.get("classification")=="UNEXPECTED":
                if set(item)!={"position","classification","byte_length","sha256"} or not isinstance(item.get("byte_length"),int) or isinstance(item.get("byte_length"),bool) or not 0<=item["byte_length"]<=128*1024 or not re.fullmatch(r"[0-9a-f]{64}",str(item.get("sha256",""))): raise SystemExit("F25 helper entry unexpected argv is invalid")
                values.append(None)
            else: raise SystemExit("F25 helper entry argv classification is invalid")
        if not isinstance(predicates,dict) or set(predicates)!=set(keys) or any(type(value) is not bool for value in predicates.values()): raise SystemExit("F25 helper entry predicates are invalid")
        if predicates["expected_argc"] is not (argc==3) or predicates["exact_vector"] is not (values==["--root=/","disable","iscsid"]): raise SystemExit("F25 helper entry predicates are inconsistent")
        if entry.get("guard_outcome")!=("ACCEPTED" if all(predicates.values()) else "REJECTED"): raise SystemExit("F25 helper entry outcome is inconsistent")
    present=[path for path in (*paths,*partials,repeat) if path.exists() or path.is_symlink()]
    if not present: return {"invoked":False,"real_helper":identity,"entry_guard":entry}
    if repeat.exists() or repeat.is_symlink(): raise SystemExit("F20 helper was invoked more than once")
    if any(path.exists() or path.is_symlink() for path in partials): raise SystemExit("F20 helper partial evidence remains")
    if not all(path.is_file() and not path.is_symlink() for path in paths): raise SystemExit("F20 helper evidence is incomplete")
    for path in paths:
        metadata=path.stat()
        if stat.S_IMODE(metadata.st_mode)!=0o600 or metadata.st_uid!=0 or metadata.st_gid!=0 or metadata.st_nlink!=1: raise SystemExit("F20 helper evidence metadata is invalid")
    invocation=paths[0].read_text("ascii").splitlines()
    if len(invocation)!=6 or invocation[0]!="F20HELPER\t1" or invocation[1]!="ARGC\t3" or invocation[2]!="ARGV0\t--root=/" or invocation[3]!="ARGV1\tdisable" or invocation[4]!="ARGV2\tiscsid" or invocation[5]!="ENV\tSYSTEMD_OFFLINE=1": raise SystemExit("F20 helper invocation is invalid")
    status_bytes=paths[3].read_bytes()
    if not re.fullmatch(rb"[0-9]{1,3}\n",status_bytes) or int(status_bytes)>255: raise SystemExit("F20 helper status is invalid")
    outputs={}
    for label,path in (("stdout",paths[1]),("stderr",paths[2])):
        raw=path.read_bytes()
        if len(raw)>MAX_OUTPUT: raise SystemExit("F20 helper output exceeds cap")
        first=raw.splitlines()[0].decode("utf-8","backslashreplace")[:240] if raw else ""
        if any(ord(ch)<32 and ch not in "\\t" for ch in first): raise SystemExit("F20 helper first line is unsafe")
        outputs[label]={"size":len(raw),"sha256":hashlib.sha256(raw).hexdigest(),"safe_first_line":first,"content_base64":base64.b64encode(raw).decode("ascii")}
    return {"invoked":True,"real_helper":identity,"entry_guard":entry,"argv":["--root=/","disable","iscsid"],"environment":{"SYSTEMD_OFFLINE":"1"},"status":int(status_bytes),"invocation_sha256":digest(paths[0]),"outputs":outputs}

def main():
    if len(sys.argv)!=5: raise SystemExit("F20 snapshot argv invalid")
    stage,output,work_arg,private_root=sys.argv[1:]
    if stage not in {"before","after"}: raise SystemExit("F20 stage invalid")
    work=pathlib.Path(work_arg); private=pathlib.Path(private_root); destination=pathlib.Path(output)
    if destination.parent!=work or private.parent!=work or private.name!="f20-sysv": raise SystemExit("F20 fixture paths invalid")
    objects=[classify(pathlib.Path(path),content=True) for path in OBJECTS]
    rc=[{"path":path,"identity":classify(pathlib.Path(path)),"entries":entries(pathlib.Path(path))} for path in RC_DIRS]
    generators=[]
    for raw in GENERATORS:
        root=pathlib.Path(raw); generators.append({"path":raw,"identity":classify(root),"entries":entries(root)})
    mounts=[mount("/etc/init.d",str(private/"init.d"))]
    mounts += [mount(path,str(private/pathlib.Path(path).name)) for path in RC_DIRS]
    mounts.append(mount("/usr/lib/systemd/systemd-sysv-install",str(work/"f20-helper-wrapper")))
    receipt={"schema_version":SCHEMA,"stage":stage,"objects":objects,"rc_directories":rc,"generators":generators,"mounts":mounts,"helper":helper(stage,work)}
    encoded=(json.dumps(receipt,indent=2,sort_keys=True)+"\n").encode()
    if len(encoded)>MAX_RECEIPT: raise SystemExit("F20 receipt exceeds cap")
    partial=destination.with_suffix(destination.suffix+".partial")
    if destination.exists() or partial.exists(): raise SystemExit("F20 receipt destination exists")
    partial.write_bytes(encoded); os.chmod(partial,0o600); partial.replace(destination)
    with destination.open("rb") as stream: os.fsync(stream.fileno())
    return 0
raise SystemExit(main())
"""

F21_CAPTURE_ERROR_SCRIPT = r"""#!/usr/bin/python3
from __future__ import annotations
import hashlib, json, os, pathlib, re, stat, sys

SCHEMA=1
MAX_STDERR=8*1024
SAFE_MESSAGES={
    "F20 helper evidence is incomplete":"f20-helper-evidence-incomplete",
    "F20 helper partial evidence remains":"f20-helper-partial-evidence",
    "F20 helper was invoked more than once":"f20-helper-repeat-evidence",
    "F20 copied helper identity is invalid":"f20-helper-identity-invalid",
}
SECRET=re.compile(rb"(?:-----BEGIN [A-Z ]*PRIVATE KEY-----|ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|Authorization:[ \t]*Bearer|(?:token|password|secret)=\S+)",re.IGNORECASE)

def classify_error(stage,raw):
    if not raw or len(raw)>MAX_STDERR or SECRET.search(raw): raise SystemExit("F21 capture stderr unsafe or truncated")
    try: message=raw.decode("utf-8").rstrip("\n")
    except UnicodeDecodeError as exc: raise SystemExit("F21 capture stderr encoding invalid") from exc
    if "\n" in message or "\r" in message: raise SystemExit("F21 capture stderr framing invalid")
    classification=SAFE_MESSAGES.get(message)
    if classification is None:
        if stage=="f19-after" and re.fullmatch(r"F19 [A-Za-z0-9 ,._'():+-]{1,240}",message): classification="f19-snapshot-validation-error"
        elif stage=="f20-after" and re.fullmatch(r"F20 [A-Za-z0-9 ,._'():+-]{1,240}",message): classification="f20-snapshot-validation-error"
        else: raise SystemExit("F21 capture stderr is not sanitizable")
    return classification

def main():
    if len(sys.argv)!=6: raise SystemExit("F21 capture argv invalid")
    stage,status_text,stderr_arg,output_arg,work_arg=sys.argv[1:]
    if stage not in {"f19-after","f20-after"}: raise SystemExit("F21 capture stage invalid")
    if not re.fullmatch(r"[1-9][0-9]{0,2}",status_text) or int(status_text)>255: raise SystemExit("F21 capture status invalid")
    work=pathlib.Path(work_arg).resolve(strict=True)
    stderr_path=pathlib.Path(stderr_arg)
    output=pathlib.Path(output_arg)
    if stderr_path.parent.resolve(strict=True)!=work or output.parent.resolve(strict=True)!=work: raise SystemExit("F21 capture path escapes fixture")
    if stderr_path.name!=stage+".stderr" or output.name!="f21-capture-error.json": raise SystemExit("F21 capture path identity invalid")
    if output.exists() or output.is_symlink() or output.with_suffix(output.suffix+".partial").exists(): raise SystemExit("F21 capture record already exists")
    metadata=stderr_path.lstat()
    if not stat.S_ISREG(metadata.st_mode) or stderr_path.is_symlink() or metadata.st_uid!=0 or metadata.st_gid!=0 or stat.S_IMODE(metadata.st_mode)!=0o600 or metadata.st_nlink!=1: raise SystemExit("F21 capture stderr metadata invalid")
    raw=stderr_path.read_bytes()
    classification=classify_error(stage,raw)
    receipt={"schema_version":SCHEMA,"stage":stage,"status":int(status_text),"stderr_size":len(raw),"stderr_sha256":hashlib.sha256(raw).hexdigest(),"stderr_class":classification,"stderr_uid":metadata.st_uid,"stderr_gid":metadata.st_gid,"stderr_mode":format(stat.S_IMODE(metadata.st_mode),"04o")}
    encoded=(json.dumps(receipt,sort_keys=True,separators=(",",":"))+"\n").encode("ascii")
    partial=output.with_suffix(output.suffix+".partial")
    fd=os.open(partial,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,"wb") as stream: stream.write(encoded); stream.flush(); os.fsync(stream.fileno())
    os.replace(partial,output)
    with output.open("rb") as stream: os.fsync(stream.fileno())
    return 0
raise SystemExit(main())
"""

F29_F21_RUNNER_SCRIPT = r"""#!/usr/bin/python3
from __future__ import annotations
import hashlib, json, os, pathlib, re, stat, subprocess, sys

SCHEMA=1
MAX_OUTPUT=8*1024
SECRET=re.compile(rb"(?:-----BEGIN [A-Z ]*PRIVATE KEY-----|ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|Authorization:[ \t]*Bearer|(?:token|password|secret)=\S+)",re.IGNORECASE)
KNOWN={
    "F21 capture stderr unsafe or truncated":"UNSAFE_OR_TRUNCATED",
    "F21 capture stderr encoding invalid":"ENCODING_INVALID",
    "F21 capture stderr framing invalid":"FRAMING_INVALID",
    "F21 capture argv invalid":"ARGV_INVALID",
    "F21 capture path escapes fixture":"PATH_IDENTITY_INVALID",
    "F21 capture path identity invalid":"PATH_IDENTITY_INVALID",
    "F21 capture stderr metadata invalid":"METADATA_INVALID",
    "F21 capture record already exists":"OUTPUT_EXISTS",
}

def regular(path,mode):
    try: meta=path.lstat()
    except OSError: raise SystemExit("F29 required path is unavailable")
    if not stat.S_ISREG(meta.st_mode) or path.is_symlink() or meta.st_uid!=0 or meta.st_gid!=0 or stat.S_IMODE(meta.st_mode)!=mode or meta.st_nlink!=1: raise SystemExit("F29 required path metadata is invalid")
    return meta

def source_file(path):
    try: meta=path.lstat()
    except OSError: raise SystemExit("F29 source is unavailable")
    if not stat.S_ISREG(meta.st_mode) or path.is_symlink() or stat.S_IMODE(meta.st_mode)!=0o644 or meta.st_nlink!=1: raise SystemExit("F29 source metadata is invalid")
    return meta

def classify(raw):
    if not raw: return "EMPTY"
    if len(raw)>MAX_OUTPUT or SECRET.search(raw): return "UNSAFE_OR_TRUNCATED"
    try: text=raw.decode("utf-8")
    except UnicodeDecodeError: return "ENCODING_INVALID"
    if text.count("\n")!=1 or not text.endswith("\n") or "\r" in text: return "FRAMING_INVALID"
    message=text[:-1]
    if message in KNOWN: return KNOWN[message]
    if "Permission denied" in message: return "PERMISSION_DENIED"
    if "Traceback" in message or "Exception" in message: return "PYTHON_EXCEPTION_SANITIZED"
    return "UNCLASSIFIED_BOUNDED"

def main():
    if len(sys.argv)!=9: raise SystemExit("F29 runner argv invalid")
    stage,status_text,stderr_arg,source_arg,output_arg,attempt_arg,work_arg,source_sha=sys.argv[1:]
    if stage not in {"f19-after","f20-after"} or not re.fullmatch(r"[1-9][0-9]{0,2}",status_text) or int(status_text)>255: raise SystemExit("F29 runner stage or status is invalid")
    work=pathlib.Path(work_arg).resolve(strict=True)
    stderr_path=pathlib.Path(stderr_arg); source=pathlib.Path(source_arg); output=pathlib.Path(output_arg); attempt=pathlib.Path(attempt_arg)
    if stderr_path.parent.resolve(strict=True)!=work or stderr_path.name!=stage+".stderr" or output.parent.resolve(strict=True)!=work or output.name!="f21-capture-error.json" or attempt.parent.resolve(strict=True)!=work or attempt.name!="f29-f21-attempt.json": raise SystemExit("F29 runner path identity is invalid")
    if source.parent.resolve(strict=True)!=work.parent.resolve(strict=True) or source.name!="f21-capture-error.py" or not re.fullmatch(r"[0-9a-f]{64}",source_sha): raise SystemExit("F29 runner source identity is invalid")
    regular(stderr_path,0o600); source_file(source)
    if hashlib.sha256(source.read_bytes()).hexdigest()!=source_sha: raise SystemExit("F29 runner source digest is invalid")
    if output.exists() or output.is_symlink() or attempt.exists() or attempt.is_symlink() or attempt.with_suffix(attempt.suffix+".partial").exists(): raise SystemExit("F29 runner output exists")
    timed_out=False
    try:
        child=subprocess.run([sys.executable,str(source),stage,status_text,str(stderr_path),str(output),str(work)],capture_output=True,check=False,timeout=15)
        child_status=child.returncode; stdout=child.stdout; stderr=child.stderr
    except subprocess.TimeoutExpired as exc:
        timed_out=True; child_status=124; stdout=exc.stdout or b""; stderr=exc.stderr or b""
    if len(stdout)>MAX_OUTPUT or len(stderr)>MAX_OUTPUT: stdout=stdout[:MAX_OUTPUT+1]; stderr=stderr[:MAX_OUTPUT+1]
    if child_status==0:
        if not output.exists() or output.is_symlink(): raise SystemExit("F29 successful child did not create F21 receipt")
        return 0
    receipt={"schema_version":SCHEMA,"stage":stage,"snapshot_status":int(status_text),"child_status":child_status,"timed_out":timed_out,"stdout_size":len(stdout),"stdout_sha256":hashlib.sha256(stdout).hexdigest(),"stderr_size":len(stderr),"stderr_sha256":hashlib.sha256(stderr).hexdigest(),"stderr_class":classify(stderr),"source_sha256":source_sha}
    encoded=(json.dumps(receipt,sort_keys=True,separators=(",",":"))+"\n").encode("ascii")
    if len(encoded)>2048: raise SystemExit("F29 runner receipt exceeds cap")
    partial=attempt.with_suffix(attempt.suffix+".partial")
    fd=os.open(partial,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,"wb") as stream: stream.write(encoded); stream.flush(); os.fsync(stream.fileno())
    try:
        os.link(partial,attempt,follow_symlinks=False)
    except FileExistsError:
        raise SystemExit("F29 attempt receipt already exists")
    finally:
        try: partial.unlink()
        except FileNotFoundError: pass
    with attempt.open("rb") as stream: os.fsync(stream.fileno())
    regular(attempt,0o600)
    return child_status
raise SystemExit(main())
"""

F29_OUTER_RUNNER_SCRIPT = r"""#!/usr/bin/python3
from __future__ import annotations
import hashlib, json, os, pathlib, re, stat, subprocess, sys

SCHEMA=1
MAX_OUTPUT=8*1024
SECRET=re.compile(rb"(?:-----BEGIN [A-Z ]*PRIVATE KEY-----|ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|Authorization:[ \t]*Bearer|(?:token|password|secret)=\S+)",re.IGNORECASE)
KNOWN={"F29 runner argv invalid":"ARGV_INVALID","F29 runner stage or status is invalid":"ARGV_INVALID","F29 runner path identity is invalid":"PATH_IDENTITY_INVALID","F29 runner source identity is invalid":"PATH_IDENTITY_INVALID","F29 required path metadata is invalid":"METADATA_INVALID","F29 source metadata is invalid":"METADATA_INVALID","F29 runner source digest is invalid":"PATH_IDENTITY_INVALID","F29 runner output exists":"OUTPUT_EXISTS"}

def regular(path,mode,root=True):
    try: meta=path.lstat()
    except OSError: raise SystemExit("F29 outer required path is unavailable")
    if not stat.S_ISREG(meta.st_mode) or path.is_symlink() or stat.S_IMODE(meta.st_mode)!=mode or meta.st_nlink!=1 or (root and (meta.st_uid!=0 or meta.st_gid!=0)): raise SystemExit("F29 outer required path metadata is invalid")

def classify(raw):
    if not raw: return "EMPTY"
    if len(raw)>MAX_OUTPUT or SECRET.search(raw): return "UNSAFE_OR_TRUNCATED"
    try: text=raw.decode("utf-8")
    except UnicodeDecodeError: return "ENCODING_INVALID"
    if text.count("\n")!=1 or not text.endswith("\n") or "\r" in text: return "FRAMING_INVALID"
    message=text[:-1]
    if message in KNOWN: return KNOWN[message]
    if "Permission denied" in message: return "PERMISSION_DENIED"
    if "Traceback" in message or "Exception" in message: return "PYTHON_EXCEPTION_SANITIZED"
    return "UNCLASSIFIED_BOUNDED"

def main():
    if len(sys.argv)!=12: raise SystemExit("F29 outer argv invalid")
    stage,status_text,stderr_arg,f21_arg,output_arg,attempt_arg,work_arg,f21_sha,f29_arg,f29_sha,receipt_arg=sys.argv[1:]
    if stage not in {"f19-after","f20-after"} or not re.fullmatch(r"[1-9][0-9]{0,2}",status_text) or int(status_text)>255 or not re.fullmatch(r"[0-9a-f]{64}",f21_sha) or not re.fullmatch(r"[0-9a-f]{64}",f29_sha): raise SystemExit("F29 outer stage or digest is invalid")
    work=pathlib.Path(work_arg).resolve(strict=True); stderr_path=pathlib.Path(stderr_arg); f21=pathlib.Path(f21_arg); output=pathlib.Path(output_arg); attempt=pathlib.Path(attempt_arg); f29=pathlib.Path(f29_arg); receipt=pathlib.Path(receipt_arg)
    if stderr_path.parent.resolve(strict=True)!=work or stderr_path.name!=stage+".stderr" or output.parent.resolve(strict=True)!=work or output.name!="f21-capture-error.json" or attempt.parent.resolve(strict=True)!=work or attempt.name!="f29-f21-attempt.json" or receipt.parent.resolve(strict=True)!=work or receipt.name!="f29-outer-"+stage+".json" or f21.parent.resolve(strict=True)!=work.parent.resolve(strict=True) or f21.name!="f21-capture-error.py" or f29.parent.resolve(strict=True)!=work.parent.resolve(strict=True) or f29.name!="f29-f21-runner.py": raise SystemExit("F29 outer path identity is invalid")
    regular(stderr_path,0o600); regular(f21,0o644,False); regular(f29,0o644,False)
    if hashlib.sha256(f21.read_bytes()).hexdigest()!=f21_sha or hashlib.sha256(f29.read_bytes()).hexdigest()!=f29_sha or receipt.exists() or receipt.is_symlink() or receipt.with_suffix(receipt.suffix+".partial").exists(): raise SystemExit("F29 outer source or receipt identity is invalid")
    timed_out=False
    try:
        child=subprocess.run([sys.executable,str(f29),stage,status_text,str(stderr_path),str(f21),str(output),str(attempt),str(work),f21_sha],capture_output=True,check=False,timeout=15)
        status=child.returncode; stdout=child.stdout; stderr=child.stderr
    except subprocess.TimeoutExpired as exc:
        timed_out=True; status=124; stdout=exc.stdout or b""; stderr=exc.stderr or b""
    stdout=stdout[:MAX_OUTPUT+1]; stderr=stderr[:MAX_OUTPUT+1]
    data={"schema_version":SCHEMA,"stage":stage,"runner_invoked":True,"runner_status":status,"timed_out":timed_out,"stdout_size":len(stdout),"stdout_sha256":hashlib.sha256(stdout).hexdigest(),"stderr_size":len(stderr),"stderr_sha256":hashlib.sha256(stderr).hexdigest(),"stderr_class":classify(stderr),"attempt_exists":attempt.exists() and not attempt.is_symlink(),"output_exists":output.exists() and not output.is_symlink(),"source_sha256":f29_sha}
    encoded=(json.dumps(data,sort_keys=True,separators=(",",":"))+"\n").encode("ascii")
    if len(encoded)>2048: raise SystemExit("F29 outer receipt exceeds cap")
    partial=receipt.with_suffix(receipt.suffix+".partial"); fd=os.open(partial,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,"wb") as stream: stream.write(encoded); stream.flush(); os.fsync(stream.fileno())
    try: os.link(partial,receipt,follow_symlinks=False)
    except FileExistsError: raise SystemExit("F29 outer receipt already exists")
    finally:
        try: partial.unlink()
        except FileNotFoundError: pass
    with receipt.open("rb") as stream: os.fsync(stream.fileno())
    regular(receipt,0o600)
    return status
raise SystemExit(main())
"""

F23_SYSTEMCTL_OUTPUT_MAX_BYTES = 8 * 1024
F24_SYSTEMCTL_OUTPUT_MAX_LINES = 64
F24_SYSTEMCTL_OUTPUT_MAX_LINE_BYTES = 512
F25_ENTRY_SCHEMA = 1
F25_ENTRY_MAX_BYTES = 8 * 1024
F25_ENTRY_MAX_ARGC = 16
F25_ENTRY_ALLOWLIST = ("--root=/", "disable", "iscsid")
F25_ENTRY_PREDICATES = (
    "expected_argc",
    "exact_vector",
    "systemd_offline",
    "dpkg_maintscripts_package",
    "dpkg_maintscripts_name",
    "exact_private_path",
    "wrapper_identity_mode",
    "real_helper_identity_mode",
)
F23_ROOT_READER_SCRIPT = r"""import os, stat, sys
path=sys.argv[1]
expected_dev=int(sys.argv[2])
expected_ino=int(sys.argv[3])
limit=int(sys.argv[4])
flags=os.O_RDONLY|getattr(os,"O_NOFOLLOW",0)|getattr(os,"O_CLOEXEC",0)
fd=os.open(path,flags)
try:
    metadata=os.fstat(fd)
    if (not stat.S_ISREG(metadata.st_mode) or metadata.st_dev!=expected_dev or
        metadata.st_ino!=expected_ino or metadata.st_uid!=0 or metadata.st_gid!=0 or
        stat.S_IMODE(metadata.st_mode)!=0o600 or metadata.st_nlink!=1):
        raise SystemExit(73)
    raw=os.read(fd,limit+1)
    if len(raw)>limit: raise SystemExit(74)
    if os.read(fd,1): raise SystemExit(74)
finally:
    os.close(fd)
os.write(1,raw)
"""


def _read_strict_root_file(
    path: pathlib.Path,
    fixture_root: pathlib.Path,
    *,
    expected_name: str,
    max_bytes: int,
) -> bytes:
    root = fixture_root.resolve(strict=True)
    if path.name != expected_name:
        raise AssertionError("root receipt name is invalid")
    try:
        metadata = path.lstat()
    except FileNotFoundError as exc:
        raise AssertionError("root receipt is missing") from exc
    if (
        path.is_symlink()
        or not stat.S_ISREG(metadata.st_mode)
        or path.resolve(strict=True).parent != root
        or metadata.st_uid != 0
        or metadata.st_gid != 0
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or metadata.st_nlink != 1
    ):
        raise AssertionError("root receipt metadata is invalid")
    if sys.platform == "win32":
        raw = path.read_bytes()
    else:
        completed = subprocess.run(
            [
                "sudo",
                "-n",
                "/usr/bin/python3",
                "-c",
                F23_ROOT_READER_SCRIPT,
                str(path),
                str(metadata.st_dev),
                str(metadata.st_ino),
                str(max_bytes + 1),
            ],
            capture_output=True,
            check=False,
            timeout=15,
        )
        if completed.returncode != 0:
            raise AssertionError("bounded root receipt reader failed")
        if completed.stderr:
            raise AssertionError("bounded root receipt reader emitted stderr")
        raw = completed.stdout
    if len(raw) > max_bytes:
        raise AssertionError("root receipt exceeds cap")
    return raw


def _instrument_f23_disable_unmasked_units(function: str) -> str:
    original = "            >/dev/null 2>&1 || disable_status=$?"
    replacement = (
        '            >"${f23_systemctl_stdout_by_unit[$unit]:-/dev/null}" '
        '2>"${f23_systemctl_stderr_by_unit[$unit]:-/dev/null}" || disable_status=$?'
    )
    if function.count(original) != 1:
        raise AssertionError("F23 systemctl redirection source is not unique")
    instrumented = function.replace(original, replacement, 1)
    if instrumented.count(replacement) != 1 or instrumented.count(original) != 0:
        raise AssertionError("F23 systemctl redirection substitution failed")
    return instrumented


def _validate_f23_systemctl_output(
    path: pathlib.Path, fixture_root: pathlib.Path, expected_name: str
) -> dict[str, object]:
    raw = _read_strict_root_file(
        path,
        fixture_root,
        expected_name=expected_name,
        max_bytes=F23_SYSTEMCTL_OUTPUT_MAX_BYTES,
    )
    if len(raw) > F23_SYSTEMCTL_OUTPUT_MAX_BYTES:
        raise AssertionError("F24 systemctl output exceeds total cap")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise AssertionError("F23 systemctl output is not UTF-8") from exc
    if any(ord(character) < 32 and character not in "\t\n" for character in text):
        raise AssertionError("F23 systemctl output contains control characters")
    if re.search(
        r"(?:-----BEGIN [A-Z ]*PRIVATE KEY-----|ghp_[A-Za-z0-9]{20,}|"
        r"github_pat_[A-Za-z0-9_]{20,}|Authorization:[ \t]*Bearer|"
        r"(?:token|password|secret)=\S+)",
        text,
        flags=re.IGNORECASE,
    ):
        raise AssertionError("F23 systemctl output contains secret-like material")
    trailing_lf = text.endswith("\n")
    lines = text.split("\n")
    if trailing_lf:
        lines.pop()
    if not text:
        lines = []
    if len(lines) > F24_SYSTEMCTL_OUTPUT_MAX_LINES:
        raise AssertionError("F24 systemctl output exceeds line-count cap")
    if any(
        len(line.encode("utf-8")) > F24_SYSTEMCTL_OUTPUT_MAX_LINE_BYTES
        for line in lines
    ):
        raise AssertionError("F24 systemctl output exceeds per-line cap")
    reconstructed = "\n".join(lines) + ("\n" if trailing_lf else "")
    if reconstructed.encode("utf-8") != raw:
        raise AssertionError(
            "F24 systemctl line representation changed validated bytes"
        )
    return {
        "size": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "lines": lines,
        "trailing_lf": trailing_lf,
    }


def _validate_f25_entry_guard(value: object) -> dict[str, object]:
    if value == {"entry_reached": False}:
        return {"entry_reached": False}
    if not isinstance(value, dict) or set(value) != {
        "schema_version",
        "entry_reached",
        "argc",
        "argv",
        "predicates",
        "guard_outcome",
    }:
        raise AssertionError("F25 entry receipt schema is not exact")
    if (
        value["schema_version"] != F25_ENTRY_SCHEMA
        or value["entry_reached"] is not True
    ):
        raise AssertionError("F25 entry receipt version/state is invalid")
    argc = value["argc"]
    argv = value["argv"]
    if (
        not isinstance(argc, int)
        or isinstance(argc, bool)
        or not 0 <= argc <= F25_ENTRY_MAX_ARGC
        or not isinstance(argv, list)
        or len(argv) != argc
    ):
        raise AssertionError("F25 entry argc/vector is unbounded")
    classified: list[str | None] = []
    for index, item in enumerate(argv):
        if not isinstance(item, dict) or item.get("position") != index:
            raise AssertionError("F25 entry argv order/schema is invalid")
        classification = item.get("classification")
        if classification == "ALLOWLISTED":
            if set(item) != {"position", "classification", "value"}:
                raise AssertionError("F25 allowlisted argv schema is invalid")
            raw = item["value"]
            if raw not in F25_ENTRY_ALLOWLIST:
                raise AssertionError("F25 raw argv escaped its allowlist")
            classified.append(str(raw))
        elif classification == "UNEXPECTED":
            if (
                set(item) != {"position", "classification", "byte_length", "sha256"}
                or not isinstance(item["byte_length"], int)
                or isinstance(item["byte_length"], bool)
                or not 0 <= item["byte_length"] <= 128 * 1024
                or not re.fullmatch(r"[0-9a-f]{64}", str(item["sha256"]))
            ):
                raise AssertionError("F25 unexpected argv schema is invalid")
            classified.append(None)
        else:
            raise AssertionError("F25 argv classification is invalid")
    predicates = value["predicates"]
    if (
        not isinstance(predicates, dict)
        or set(predicates) != set(F25_ENTRY_PREDICATES)
        or any(type(result) is not bool for result in predicates.values())
    ):
        raise AssertionError("F25 guard predicate schema is invalid")
    expected_argc = argc == 3
    exact_vector = classified == ["--root=/", "disable", "iscsid"]
    if predicates["expected_argc"] is not expected_argc:
        raise AssertionError("F25 argc predicate is inconsistent")
    if predicates["exact_vector"] is not exact_vector:
        raise AssertionError("F25 vector predicate is inconsistent")
    expected_outcome = "ACCEPTED" if all(predicates.values()) else "REJECTED"
    if value["guard_outcome"] != expected_outcome:
        raise AssertionError("F25 guard outcome is inconsistent")
    return value


def _classify_f25_entry(
    systemctl_stderr: dict[str, object], helper: dict[str, object]
) -> str:
    lines = systemctl_stderr.get("lines")
    if not isinstance(lines, list):
        raise TypeError("F25 systemctl lines are unavailable")
    prefix = "Executing: /usr/lib/systemd/systemd-sysv-install "
    attempted = [
        line for line in lines if isinstance(line, str) and line.startswith(prefix)
    ]
    if len(attempted) != 1:
        raise AssertionError("F25 attempted helper argv is missing or ambiguous")
    attempted_argv = attempted[0][len(prefix) :].split(" ")
    if not 1 <= len(attempted_argv) <= F25_ENTRY_MAX_ARGC or any(
        argument not in F25_ENTRY_ALLOWLIST for argument in attempted_argv
    ):
        raise AssertionError("F25 attempted helper argv is outside its allowlist")
    entry = _validate_f25_entry_guard(helper.get("entry_guard"))
    invoked = helper.get("invoked")
    if invoked not in {True, False}:
        raise AssertionError("F25 post-guard invocation state is invalid")
    if entry == {"entry_reached": False}:
        if invoked is not False:
            raise AssertionError("F25 invocation cannot precede wrapper entry")
        return "HELPER_EXEC_NOT_REACHED"
    argv = entry["argv"]
    assert isinstance(argv, list)
    values = [
        item.get("value")
        for item in argv
        if isinstance(item, dict) and item.get("classification") == "ALLOWLISTED"
    ]
    if len(values) != len(argv) or values != attempted_argv:
        raise AssertionError("F25 systemctl and wrapper argv disagree")
    predicates = entry["predicates"]
    assert isinstance(predicates, dict)
    if all(predicates.values()):
        if invoked is not True:
            raise AssertionError("F25 accepted guard lacks post-guard evidence")
        return "WRAPPER_ENTRY_ACCEPTED"
    if invoked is not False:
        raise AssertionError("F25 rejected guard cannot invoke the real helper")
    return "WRAPPER_ENTRY_GUARD_REJECTION"


F19_SNAPSHOT_SCRIPT = r"""#!/usr/bin/python3
from __future__ import annotations

import base64
import fnmatch
import hashlib
import json
import os
import pathlib
import re
import stat
import subprocess
import sys

SCHEMA = 1
MAX_RECEIPT = 256 * 1024
MAX_CONTENT = 64 * 1024
MAX_ENTRIES = 192
UNITS = (
    "corosync.service",
    "iscsid.service",
    "iscsid.socket",
    "iscsi.service",
    "open-iscsi.service",
)
MOUNTS = (
    "/usr/lib/systemd/system",
    "/etc/systemd/system",
    "/var/lib/systemd",
    "/run/systemd",
)
SAFE_TOKEN = re.compile(r"^[A-Za-z0-9_.@:+,=/ -]{0,512}$")


def sha(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def classify(path: pathlib.Path, *, include_content: bool = False) -> dict[str, object]:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return {"path": str(path), "type": "absent"}
    mode = metadata.st_mode
    if stat.S_ISLNK(mode):
        kind = "symlink"
    elif stat.S_ISREG(mode):
        kind = "regular"
    elif stat.S_ISDIR(mode):
        kind = "directory"
    elif stat.S_ISSOCK(mode):
        kind = "socket"
    else:
        kind = "other"
    record: dict[str, object] = {
        "path": str(path),
        "type": kind,
        "uid": metadata.st_uid,
        "gid": metadata.st_gid,
        "mode": format(stat.S_IMODE(mode), "04o"),
        "size": metadata.st_size,
    }
    if kind == "symlink":
        record["link_target"] = os.readlink(path)
        resolved = path.resolve(strict=False)
        record["resolved_path"] = str(resolved)
        record["resolved_confined"] = any(
            resolved == root or root in resolved.parents
            for root in (
                pathlib.Path("/etc/systemd/system"),
                pathlib.Path("/usr/lib/systemd/system"),
                pathlib.Path("/dev/null"),
            )
        )
    elif kind == "regular":
        if metadata.st_size > MAX_CONTENT:
            raise SystemExit(f"regular object exceeds diagnostic cap: {path}")
        record["sha256"] = sha(path)
        if include_content:
            record["content_base64"] = base64.b64encode(path.read_bytes()).decode("ascii")
    return record


def mount_records(work: pathlib.Path) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    for raw in pathlib.Path("/proc/self/mountinfo").read_text(encoding="utf-8").splitlines():
        left, right = raw.split(" - ", 1)
        fields = left.split()
        mountpoint = fields[4].replace("\\040", " ").replace("\\011", "\t")
        if mountpoint not in MOUNTS:
            continue
        right_fields = right.split()
        records.append(
            {
                "mount_id": int(fields[0]),
                "parent_id": int(fields[1]),
                "major_minor": fields[2],
                "root": fields[3],
                "mountpoint": mountpoint,
                "mount_options": fields[5].split(","),
                "optional_fields": fields[6:],
                "filesystem_type": right_fields[0],
                "source": right_fields[1],
                "super_options": right_fields[2].split(","),
            }
        )
    if [record["mountpoint"] for record in records] != list(MOUNTS):
        by_mount = {str(record["mountpoint"]): record for record in records}
        if set(by_mount) != set(MOUNTS) or len(records) != len(MOUNTS):
            raise SystemExit("fixture mount roots are incomplete or ambiguous")
        records = [by_mount[mount] for mount in MOUNTS]
    sources = {
        "/usr/lib/systemd/system": work / "vendor-units",
        "/etc/systemd/system": work / "etc-systemd",
        "/var/lib/systemd": work / "systemd-state",
        "/run/systemd": work / "run-systemd",
    }
    for record in records:
        source = sources[str(record["mountpoint"])]
        mountpoint = pathlib.Path(str(record["mountpoint"]))
        record["fixture_source"] = str(source)
        record["fixture_source_identity"] = f"{source.stat().st_dev}:{source.stat().st_ino}"
        record["mountpoint_identity"] = f"{mountpoint.stat().st_dev}:{mountpoint.stat().st_ino}"
        record["bind_identity_matches"] = (
            record["fixture_source_identity"] == record["mountpoint_identity"]
        )
    return records


def enabled_state(unit: str) -> dict[str, object]:
    completed = subprocess.run(
        ["/usr/bin/systemctl", "--root=/", "is-enabled", unit],
        env={**os.environ, "SYSTEMD_OFFLINE": "1"},
        text=True,
        capture_output=True,
        check=False,
    )
    first = (completed.stdout + completed.stderr).splitlines()
    return {
        "unit": unit,
        "first_line": first[0] if first else "",
        "status": completed.returncode,
    }


def matching_etc_entries() -> list[dict[str, object]]:
    root = pathlib.Path("/etc/systemd/system")
    records: list[dict[str, object]] = []
    for parent, directories, files in os.walk(root, followlinks=False):
        directories.sort()
        files.sort()
        for name in directories + files:
            path = pathlib.Path(parent) / name
            relative = path.relative_to(root)
            parts = relative.parts
            if not any(
                unit in parts or f"{unit}.d" in parts or name == unit for unit in UNITS
            ):
                continue
            if len(parts) > 5 or any(part in {"", ".", ".."} for part in parts):
                raise SystemExit("unsafe unit entry path")
            record = classify(path)
            record["relative_path"] = relative.as_posix()
            records.append(record)
            if len(records) > MAX_ENTRIES:
                raise SystemExit("too many matching unit entries")
    records.sort(key=lambda item: str(item["relative_path"]))
    if len({str(item["relative_path"]) for item in records}) != len(records):
        raise SystemExit("duplicate matching unit entry")
    return records


def preset_records() -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    roots = (
        pathlib.Path("/etc/systemd/system-preset"),
        pathlib.Path("/run/systemd/system-preset"),
        pathlib.Path("/usr/local/lib/systemd/system-preset"),
        pathlib.Path("/usr/lib/systemd/system-preset"),
    )
    selected: dict[str, pathlib.Path] = {}
    for root in roots:
        if not root.is_dir():
            continue
        for path in sorted(root.glob("*.preset")):
            selected.setdefault(path.name, path)
    files: list[dict[str, object]] = []
    rules: list[tuple[str, str, str, int]] = []
    for name in sorted(selected):
        path = selected[name]
        metadata = path.stat()
        if metadata.st_size > MAX_CONTENT:
            raise SystemExit(f"preset exceeds diagnostic cap: {path}")
        content = path.read_text(encoding="utf-8")
        files.append(
            {
                "name": name,
                "path": str(path),
                "size": metadata.st_size,
                "sha256": sha(path),
                "content_base64": base64.b64encode(content.encode()).decode("ascii"),
            }
        )
        for line_number, raw in enumerate(content.splitlines(), 1):
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            fields = line.split()
            if len(fields) >= 2 and fields[0] in {"enable", "disable", "ignore"}:
                rules.append((fields[0], fields[1], name, line_number))
    effective: list[dict[str, object]] = []
    for unit in UNITS:
        match = next((rule for rule in rules if fnmatch.fnmatchcase(unit, rule[1])), None)
        if match is None:
            effective.append({"unit": unit, "action": "enable", "source": "default"})
        else:
            effective.append(
                {
                    "unit": unit,
                    "action": match[0],
                    "pattern": match[1],
                    "source": match[2],
                    "line": match[3],
                }
            )
    return files, effective


def phase09(path: pathlib.Path) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for raw in path.read_text(encoding="ascii").splitlines():
        fields = raw.split("\t")
        if len(fields) != 2 or fields[0] not in UNITS or fields[1] != "0":
            raise SystemExit("invalid phase-09 outcome")
        rows.append({"unit": fields[0], "status": 0})
    if [row["unit"] for row in rows] != list(UNITS):
        raise SystemExit("phase-09 target outcomes are incomplete or out of order")
    return rows


def main() -> int:
    if len(sys.argv) != 10:
        raise SystemExit("invalid diagnostic snapshot argv")
    stage, output, function_path, phase09_path, trace_path = sys.argv[1:6]
    failure_status, failure_line, failure_function, failure_command = sys.argv[6:10]
    if stage not in {"before", "after"}:
        raise SystemExit("invalid diagnostic stage")
    if not SAFE_TOKEN.fullmatch(failure_command):
        raise SystemExit("unsafe failure command label")
    destination = pathlib.Path(output)
    work = destination.parent
    function = pathlib.Path(function_path)
    systemctl = pathlib.Path("/usr/bin/systemctl")
    version = subprocess.run(
        [str(systemctl), "--version"], text=True, capture_output=True, check=True
    ).stdout.splitlines()[0]
    unit_records = [classify(pathlib.Path("/usr/lib/systemd/system") / unit, include_content=True) for unit in UNITS]
    presets, effective_rules = preset_records()
    command_trace: dict[str, object] | None = None
    if stage == "after":
        trace = pathlib.Path(trace_path)
        size = trace.stat().st_size
        if size <= 0 or size > 64 * 1024:
            raise SystemExit("command trace size is outside bounds")
        command_trace = {"size": size, "sha256": sha(trace)}
    receipt = {
        "schema_version": SCHEMA,
        "stage": stage,
        "inputs": {
            "payload_sha256": "3116215f4f2dde376f591b06cb192b3cc725e4261885c5a0bc88e23b8867005b",
            "verifier_sha256": "f188d76e7c19ba38472a5125c68d53e428bcf095d36878ac688e56a93fc627ad",
            "disable_unmasked_units_sha256": sha(function),
        },
        "systemd": {
            "version_first_line": version,
            "systemctl_path": str(systemctl.resolve(strict=True)),
            "systemctl_sha256": sha(systemctl),
        },
        "mounts": mount_records(work),
        "vendor_units": unit_records,
        "etc_entries": matching_etc_entries(),
        "presets": presets,
        "effective_preset_rules": effective_rules,
        "phase09_outcomes": phase09(pathlib.Path(phase09_path)),
        "enabled_states": [enabled_state(unit) for unit in UNITS],
        "phase_boundaries": {
            "deferred_validator_reached": (work / "f19-validator-reached").exists(),
            "phase13_reached": (work / "f19-phase13-reached").exists(),
            "phase14_reached": (work / "f19-phase14-reached").exists(),
            "phase15_reached": (work / "f19-phase15-reached").exists(),
        },
        "failure": None if stage == "before" else {
            "status": int(failure_status),
            "line": int(failure_line),
            "function": failure_function,
            "command": failure_command,
        },
        "command_trace": command_trace,
    }
    encoded = (json.dumps(receipt, indent=2, sort_keys=True) + "\n").encode("utf-8")
    if len(encoded) > MAX_RECEIPT:
        raise SystemExit("diagnostic receipt exceeds cap")
    partial = destination.with_suffix(destination.suffix + ".partial")
    if destination.exists() or partial.exists():
        raise SystemExit("diagnostic receipt destination already exists")
    partial.write_bytes(encoded)
    os.chmod(partial, 0o600)
    partial.replace(destination)
    with destination.open("rb") as stream:
        os.fsync(stream.fileno())
    return 0


raise SystemExit(main())
"""

PCP_MANAGER_ROOT_SNAPSHOT_FUNCTION = r"""
manager_root_snapshot_abort() {
    local status="$1"
    shift
    /usr/bin/rm -f -- "$@" || return 126
    return "$status"
}
manager_root_snapshot() {
    local stage="$1"
    local condition_status="$2"
    local output="$3"
    local root="$work/run-systemd"
    local expected_output
    local raw="${output}.raw"
    local sorted="${output}.sorted"
    local entries="${output}.entries"
    local partial="${output}.partial"
    local deep relative previous= object type metadata mode uid gid
    local count=0
    local LC_ALL=C
    case "$stage:$condition_status" in
        before:-) expected_output="$work/manager-root-before.tsv" ;;
        after:[1-9]|after:[1-9][0-9]|after:[12][0-9][0-9])
            (( condition_status <= 255 )) || return 120
            expected_output="$work/manager-root-after.tsv"
            ;;
        *) return 120 ;;
    esac
    [[ "$output" == "$expected_output" && "${output%/*}" == "$work" ]] || return 120
    [[ "$root" == "$work/run-systemd" && -d "$root" && ! -L "$root" ]] || return 120
    for path in "$output" "$raw" "$sorted" "$entries" "$partial"; do
        [[ ! -e "$path" && ! -L "$path" ]] || return 120
    done
    : >"$raw" || return 121
    : >"$sorted" || manager_root_snapshot_abort 121 "$raw"
    : >"$entries" || manager_root_snapshot_abort 121 "$raw" "$sorted"
    : >"$partial" || manager_root_snapshot_abort 121 "$raw" "$sorted" "$entries"
    deep="$(/usr/bin/find "$root" -xdev -mindepth 6 -printf x -quit)" || \
        manager_root_snapshot_abort 122 "$raw" "$sorted" "$entries" "$partial"
    [[ -z "$deep" ]] || \
        manager_root_snapshot_abort 122 "$raw" "$sorted" "$entries" "$partial"
    /usr/bin/find "$root" -xdev -mindepth 1 -maxdepth 5 -printf '%P\0' >"$raw" || \
        manager_root_snapshot_abort 122 "$raw" "$sorted" "$entries" "$partial"
    /usr/bin/sort -z -- "$raw" >"$sorted" || \
        manager_root_snapshot_abort 122 "$raw" "$sorted" "$entries" "$partial"
    while IFS= read -r -d '' relative; do
        count=$((count + 1))
        (( count <= 128 )) || \
            manager_root_snapshot_abort 123 "$raw" "$sorted" "$entries" "$partial"
        (( ${#relative} > 0 && ${#relative} <= 192 )) || \
            manager_root_snapshot_abort 123 "$raw" "$sorted" "$entries" "$partial"
        [[ "$relative" =~ ^[A-Za-z0-9_.@:+,-]+(/[A-Za-z0-9_.@:+,-]+){0,4}$ ]] || \
            manager_root_snapshot_abort 123 "$raw" "$sorted" "$entries" "$partial"
        IFS=/ read -r -a components <<<"$relative"
        for component in "${components[@]}"; do
            [[ "$component" != . && "$component" != .. ]] || \
                manager_root_snapshot_abort 123 "$raw" "$sorted" "$entries" "$partial"
        done
        [[ -z "$previous" || "$previous" != "$relative" ]] || \
            manager_root_snapshot_abort 123 "$raw" "$sorted" "$entries" "$partial"
        previous="$relative"
        object="$root/$relative"
        if [[ -L "$object" ]]; then type=symlink
        elif [[ -d "$object" ]]; then type=directory
        elif [[ -f "$object" ]]; then type=regular
        elif [[ -S "$object" ]]; then type=socket
        elif [[ -p "$object" ]]; then type=fifo
        elif [[ -b "$object" ]]; then type=block
        elif [[ -c "$object" ]]; then type=char
        else type=other
        fi
        metadata="$(/usr/bin/stat -c '%a %u %g' -- "$object")" || \
            manager_root_snapshot_abort 124 "$raw" "$sorted" "$entries" "$partial"
        read -r mode uid gid extra <<<"$metadata"
        [[ -z "${extra:-}" && "$mode" =~ ^[0-7]{3,4}$ && \
            "$uid" =~ ^[0-9]+$ && "$gid" =~ ^[0-9]+$ ]] || \
            manager_root_snapshot_abort 124 "$raw" "$sorted" "$entries" "$partial"
        printf 'ENTRY\t%s\t%s\t%s\t%s\t%s\n' \
            "$relative" "$type" "$mode" "$uid" "$gid" >>"$entries" || \
            manager_root_snapshot_abort 124 "$raw" "$sorted" "$entries" "$partial"
    done <"$sorted"
    printf 'HMROOT|1|%s|status=%s\n' "$stage" "$condition_status" >"$partial" || \
        manager_root_snapshot_abort 125 "$raw" "$sorted" "$entries" "$partial"
    /usr/bin/cat -- "$entries" >>"$partial" || \
        manager_root_snapshot_abort 125 "$raw" "$sorted" "$entries" "$partial"
    (( $(/usr/bin/stat -c %s -- "$partial") <= 32768 )) || \
        manager_root_snapshot_abort 125 "$raw" "$sorted" "$entries" "$partial"
    /usr/bin/mv -- "$partial" "$output" || \
        manager_root_snapshot_abort 125 "$raw" "$sorted" "$entries" "$partial"
    /usr/bin/rm -f -- "$raw" "$sorted" "$entries" || return 126
}
""".strip()

PCP_SYSTEMD_SOURCE_RECEIPT = rf"""
systemd_source_receipt="$work/systemd-source.tsv"
systemd_source_partial="${{systemd_source_receipt}}.partial"
[[ ! -e "$systemd_source_receipt" && ! -L "$systemd_source_receipt" ]]
[[ ! -e "$systemd_source_partial" && ! -L "$systemd_source_partial" ]]
systemd_analyze=/usr/bin/systemd-analyze
[[ -f "$systemd_analyze" && ! -L "$systemd_analyze" ]]
[[ "$(/usr/bin/readlink -e -- "$systemd_analyze")" == "$systemd_analyze" ]]
systemd_package_record="$(/usr/bin/dpkg-query -W \
    -f='${{binary:Package}}\t${{Version}}\t${{Architecture}}\n' systemd)"
IFS=$'\t' read -r systemd_package systemd_package_version systemd_package_arch extra \
    <<<"$systemd_package_record"
[[ -z "${{extra:-}}" && "$systemd_package" == systemd && \
    "$systemd_package_arch" == amd64 ]]
[[ "$systemd_package_version" =~ ^255\.4-[0-9A-Za-z.+:~]+$ ]]
systemd_owner="$(/usr/bin/dpkg-query -S -- "$systemd_analyze")"
[[ "$systemd_owner" == "systemd: /usr/bin/systemd-analyze" ]]
systemd_version_output="$work/systemd-analyze-version.txt"
[[ ! -e "$systemd_version_output" && ! -L "$systemd_version_output" ]]
"$systemd_analyze" --version >"$systemd_version_output"
[[ -f "$systemd_version_output" && ! -L "$systemd_version_output" ]]
(( $(/usr/bin/stat -c %s -- "$systemd_version_output") > 0 ))
(( $(/usr/bin/stat -c %s -- "$systemd_version_output") <= 4096 ))
IFS= read -r systemd_version_first <"$systemd_version_output"
[[ "$systemd_version_first" == "systemd 255 ($systemd_package_version)" ]]
[[ "$systemd_version_first" =~ ^systemd\ 255\ \(255\.4-[0-9A-Za-z.+:~]+\)$ ]]
systemd_version_sha256="$(/usr/bin/sha256sum -- "$systemd_version_output")"
systemd_version_sha256="${{systemd_version_sha256%% *}}"
systemd_executable_sha256="$(/usr/bin/sha256sum -- "$systemd_analyze")"
systemd_executable_sha256="${{systemd_executable_sha256%% *}}"
systemd_executable_metadata="$(/usr/bin/stat -c '%a %u %g %s %h' -- "$systemd_analyze")"
read -r systemd_executable_mode systemd_executable_uid systemd_executable_gid \
    systemd_executable_size systemd_executable_links extra \
    <<<"$systemd_executable_metadata"
[[ -z "${{extra:-}}" && "$systemd_executable_mode" =~ ^[0-7]{{3,4}}$ && \
    "$systemd_executable_uid" == 0 && "$systemd_executable_gid" == 0 && \
    "$systemd_executable_size" =~ ^[1-9][0-9]*$ && \
    "$systemd_executable_links" =~ ^[1-9][0-9]*$ && \
    "$systemd_version_sha256" =~ ^[0-9a-f]{{64}}$ && \
    "$systemd_executable_sha256" =~ ^[0-9a-f]{{64}}$ ]]
{{
    printf '%s\n' 'HSOURCE|1'
    printf 'PACKAGE\t%s\t%s\t%s\n' \
        systemd "$systemd_package_version" "$systemd_package_arch"
    printf 'VERSION\t%s\t%s\n' \
        "$systemd_version_first" "$systemd_version_sha256"
    printf 'EXECUTABLE\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
        /usr/bin/systemd-analyze "$systemd_executable_sha256" \
        "$systemd_executable_mode" 0 0 "$systemd_executable_size" \
        "$systemd_executable_links" systemd
    printf 'UPSTREAM\t%s\t%s\t%s\n' \
        '{PCP_SYSTEMD_UPSTREAM_REPOSITORY}' '{PCP_SYSTEMD_UPSTREAM_TAG}' \
        '{PCP_SYSTEMD_UPSTREAM_REVISION}'
    printf 'SOURCE\t%s\t%s\t%s\n' \
        '{PCP_SYSTEMD_ANALYZE_SOURCE_PATH}' \
        '{PCP_SYSTEMD_ANALYZE_SOURCE_FUNCTION}' \
        '{PCP_SYSTEMD_ANALYZE_SOURCE_SHA256}'
    printf 'SOURCE\t%s\t%s\t%s\n' \
        '{PCP_SYSTEMD_MANAGER_SOURCE_PATH}' \
        '{PCP_SYSTEMD_MANAGER_SOURCE_FUNCTION}' \
        '{PCP_SYSTEMD_MANAGER_SOURCE_SHA256}'
    printf 'CHAIN\t%s\n' \
        'verb_condition>verify_conditions>manager_startup>manager_ready>touch_file'
    printf 'MARKER\t%s\t%s\t%s\t%s\t%s\n' \
        '{PCP_SYSTEMD_MARKER_PATH}' regular 0444 zero-length manager-ready
}} >"$systemd_source_partial"
(( $(/usr/bin/stat -c %s -- "$systemd_source_partial") <= {PCP_SYSTEMD_SOURCE_RECEIPT_MAX_BYTES} ))
/usr/bin/mv -- "$systemd_source_partial" "$systemd_source_receipt"
/usr/bin/sync -f "$systemd_source_receipt"
""".strip()

PCP_SYSTEMD_CAUSAL_PROOF = rf"""
systemd_causal_parent="$work/systemd-causal-controls"
systemd_positive_root="$systemd_causal_parent/positive"
systemd_negative_root="$systemd_causal_parent/negative"
systemd_causal_receipt="$work/systemd-causal.tsv"
systemd_causal_partial="${{systemd_causal_receipt}}.partial"
[[ ! -e "$systemd_causal_parent" && ! -L "$systemd_causal_parent" ]]
[[ ! -e "$systemd_causal_receipt" && ! -L "$systemd_causal_receipt" ]]
[[ ! -e "$systemd_causal_partial" && ! -L "$systemd_causal_partial" ]]
mkdir -- "$systemd_causal_parent"
mkdir -- "$systemd_positive_root" "$systemd_negative_root"
[[ -d "$systemd_causal_parent" && ! -L "$systemd_causal_parent" ]]
[[ -d "$systemd_positive_root" && ! -L "$systemd_positive_root" ]]
[[ -d "$systemd_negative_root" && ! -L "$systemd_negative_root" ]]
systemd_mount_id() {{
    /usr/bin/awk '$5 == "/run/systemd" {{ id=$1 }} END {{ print id }}' /proc/self/mountinfo
}}
systemd_underlay_mount_id="$(systemd_mount_id)"
[[ "$systemd_underlay_mount_id" =~ ^[1-9][0-9]*$ ]]
systemd_causal_mounted=false
systemd_causal_cleanup_root() {{
    local control_root="$1"
    local expected_entry="$2"
    if [[ "$systemd_causal_mounted" == true ]]; then
        umount -- /run/systemd || return 131
        systemd_causal_mounted=false
        [[ "$(systemd_mount_id)" == "$systemd_underlay_mount_id" ]] || return 132
    fi
    if [[ -n "$expected_entry" ]]; then
        [[ "$expected_entry" == "$control_root/systemd-units-load" ]] || return 133
        [[ -f "$expected_entry" && ! -L "$expected_entry" ]] || return 133
        rm -f -- "$expected_entry" || return 133
    fi
    [[ -z "$(find "$control_root" -mindepth 1 -print -quit)" ]] || return 134
    rmdir -- "$control_root" || return 134
}}
systemd_causal_abort() {{
    local status="$1"
    local control_root="${{2:-}}"
    local expected_entry="${{3:-}}"
    if [[ -n "$control_root" && -d "$control_root" && ! -L "$control_root" ]]; then
        systemd_causal_cleanup_root "$control_root" "$expected_entry" || status=135
    fi
    if [[ -d "$systemd_positive_root" && ! -L "$systemd_positive_root" ]]; then
        [[ -z "$(find "$systemd_positive_root" -mindepth 1 -print -quit)" ]] && \
            rmdir -- "$systemd_positive_root" || status=135
    fi
    if [[ -d "$systemd_negative_root" && ! -L "$systemd_negative_root" ]]; then
        [[ -z "$(find "$systemd_negative_root" -mindepth 1 -print -quit)" ]] && \
            rmdir -- "$systemd_negative_root" || status=135
    fi
    if [[ -d "$systemd_causal_parent" && ! -L "$systemd_causal_parent" ]]; then
        [[ -z "$(find "$systemd_causal_parent" -mindepth 1 -print -quit)" ]] && \
            rmdir -- "$systemd_causal_parent" || status=135
    fi
    rm -f -- "$systemd_causal_partial" || status=135
    return "$status"
}}

# Negative control: the same fresh private root remains empty when no
# systemd-analyze command is run.
[[ -z "$(find "$systemd_negative_root" -mindepth 1 -print -quit)" ]] || \
    systemd_causal_abort 136
mount --bind "$systemd_negative_root" /run/systemd || systemd_causal_abort 137
systemd_causal_mounted=true
mount --make-private /run/systemd || \
    systemd_causal_abort 138 "$systemd_negative_root"
[[ "$(systemd_mount_id)" =~ ^[1-9][0-9]*$ && \
    "$(systemd_mount_id)" != "$systemd_underlay_mount_id" && \
    "$(stat -c %d:%i -- /run/systemd)" == "$(stat -c %d:%i -- "$systemd_negative_root")" ]] || \
    systemd_causal_abort 139 "$systemd_negative_root"
[[ -z "$(find /run/systemd -mindepth 1 -print -quit)" ]] || \
    systemd_causal_abort 140 "$systemd_negative_root"
[[ -z "$(find /run/systemd -xdev -type s -print -quit)" ]] || \
    systemd_causal_abort 141 "$systemd_negative_root"
[[ -z "$(find /run/systemd -mindepth 1 -print -quit)" ]] || \
    systemd_causal_abort 142 "$systemd_negative_root"
systemd_causal_cleanup_root "$systemd_negative_root" "" || \
    systemd_causal_abort 143

# Positive control: exactly one false condition invocation in an otherwise
# identical fresh private root creates only the documented local marker.
[[ -z "$(find "$systemd_positive_root" -mindepth 1 -print -quit)" ]] || \
    systemd_causal_abort 144
mount --bind "$systemd_positive_root" /run/systemd || systemd_causal_abort 145
systemd_causal_mounted=true
mount --make-private /run/systemd || \
    systemd_causal_abort 146 "$systemd_positive_root"
[[ "$(systemd_mount_id)" =~ ^[1-9][0-9]*$ && \
    "$(systemd_mount_id)" != "$systemd_underlay_mount_id" && \
    "$(stat -c %d:%i -- /run/systemd)" == "$(stat -c %d:%i -- "$systemd_positive_root")" ]] || \
    systemd_causal_abort 147 "$systemd_positive_root"
[[ -z "$(find /run/systemd -mindepth 1 -print -quit)" ]] || \
    systemd_causal_abort 148 "$systemd_positive_root"
[[ -z "$(find /run/systemd -xdev -type s -print -quit)" ]] || \
    systemd_causal_abort 149 "$systemd_positive_root"
systemd_positive_status=0
/usr/bin/systemd-analyze condition \
    "{PCP_SYSTEMD_FALSE_CONDITION}" >/dev/null 2>&1 || systemd_positive_status=$?
[[ "$systemd_positive_status" -eq 1 ]] || \
    systemd_causal_abort 150 "$systemd_positive_root"
mapfile -d '' systemd_positive_entries \
    < <(find /run/systemd -xdev -mindepth 1 -maxdepth 1 -printf '%f\0' | sort -z)
[[ "${{#systemd_positive_entries[@]}}" -eq 1 && \
    "${{systemd_positive_entries[0]}}" == systemd-units-load ]] || \
    systemd_causal_abort 151 "$systemd_positive_root"
systemd_marker=/run/systemd/systemd-units-load
[[ -f "$systemd_marker" && ! -L "$systemd_marker" ]] || \
    systemd_causal_abort 152 "$systemd_positive_root"
systemd_marker_metadata="$(stat -c '%a %u %g %s %h %d' -- "$systemd_marker")"
read -r systemd_marker_mode systemd_marker_uid systemd_marker_gid \
    systemd_marker_size systemd_marker_links systemd_marker_device extra \
    <<<"$systemd_marker_metadata"
systemd_root_device="$(stat -c %d -- /run/systemd)"
[[ -z "${{extra:-}}" && "$systemd_marker_mode" == 444 && \
    "$systemd_marker_uid" == 0 && "$systemd_marker_gid" == 0 && \
    "$systemd_marker_size" == 0 && "$systemd_marker_links" == 1 && \
    "$systemd_marker_device" == "$systemd_root_device" ]] || \
    systemd_causal_abort 153 "$systemd_positive_root" "$systemd_positive_root/systemd-units-load"
cmp -s -- "$systemd_marker" /dev/null || \
    systemd_causal_abort 154 "$systemd_positive_root" "$systemd_positive_root/systemd-units-load"
[[ -z "$(find /run/systemd -xdev -type s -print -quit)" ]] || \
    systemd_causal_abort 155 "$systemd_positive_root" "$systemd_positive_root/systemd-units-load"
systemd_causal_cleanup_root \
    "$systemd_positive_root" "$systemd_positive_root/systemd-units-load" || \
    systemd_causal_abort 156
[[ -z "$(find "$systemd_causal_parent" -mindepth 1 -print -quit)" ]]
rmdir -- "$systemd_causal_parent"
[[ ! -e "$systemd_causal_parent" && ! -L "$systemd_causal_parent" ]]
{{
    printf '%s\n' 'HCAUSE|1'
    printf 'CONTROL\t%s\tcommand=%s\tstatus=%s\tbefore=%s\tafter=%s\tmanager_endpoints_before=%s\tmanager_endpoints_after=%s\tcleanup=%s\n' \
        negative none - 0 0 0 0 removed
    printf 'CONTROL\t%s\tcommand=%s\tstatus=%s\tbefore=%s\tafter=%s\tmanager_endpoints_before=%s\tmanager_endpoints_after=%s\tcleanup=%s\n' \
        positive systemd-analyze-condition 1 0 1 0 0 removed
    printf 'MARKER\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
        systemd-units-load regular "$systemd_marker_mode" "$systemd_marker_uid" \
        "$systemd_marker_gid" "$systemd_marker_size" "$systemd_marker_links" \
        same-filesystem
}} >"$systemd_causal_partial"
(( $(stat -c %s -- "$systemd_causal_partial") <= {PCP_SYSTEMD_CAUSAL_RECEIPT_MAX_BYTES} ))
mv -- "$systemd_causal_partial" "$systemd_causal_receipt"
sync -f "$systemd_causal_receipt"
[[ "$(systemd_mount_id)" == "$systemd_underlay_mount_id" ]]
""".strip()

PCP_LOCAL_SYSTEMD_MARKER_ORACLE = r"""
local_systemd_marker_cleanup_count=0
validate_and_remove_local_systemd_marker() {
    local receipt marker private_marker mounted_root private_root
    local expected_receipt expected_receipt_size receipt_hash_before receipt_hash_after
    local mount_id mounted_identity private_identity marker_metadata marker_metadata_now
    local marker_mode marker_uid marker_gid marker_size marker_links marker_device
    local marker_inode root_device extra entry_file
    local -a entries=()
    [[ "$#" -eq 1 ]] || return 160
    receipt="$1"
    mounted_root=/run/systemd
    private_root="$work/run-systemd"
    marker="$mounted_root/systemd-units-load"
    private_marker="$private_root/systemd-units-load"
    entry_file="$work/local-systemd-marker.entries"
    [[ "$receipt" == "$work/manager-root-after.tsv" && \
        "${receipt%/*}" == "$work" && -f "$receipt" && ! -L "$receipt" ]] || return 161
    [[ "$condition_status" -eq 1 ]] || return 162
    expected_receipt=$'HMROOT|1|after|status=1\nENTRY\tsystemd-units-load\tregular\t444\t0\t0\n'
    expected_receipt_size="${#expected_receipt}"
    [[ "$(/usr/bin/stat -c %s -- "$receipt")" == "$expected_receipt_size" && \
        "$(/usr/bin/cat -- "$receipt")"$'\n' == "$expected_receipt" ]] || return 163
    receipt_hash_before="$(/usr/bin/sha256sum -- "$receipt")" || return 164
    receipt_hash_before="${receipt_hash_before%% *}"
    [[ "$receipt_hash_before" =~ ^[0-9a-f]{64}$ ]] || return 164
    /usr/bin/sync -f "$receipt" || return 164
    [[ "$private_root" == "$work/run-systemd" && -d "$private_root" && \
        ! -L "$private_root" && -d "$mounted_root" && ! -L "$mounted_root" ]] || return 165
    [[ "$(/usr/bin/readlink -e -- "$private_root")" == "$private_root" && \
        "$(/usr/bin/readlink -e -- "$mounted_root")" == "$mounted_root" ]] || return 165
    mount_id="$(systemd_mount_id)" || return 166
    [[ "$mount_id" =~ ^[1-9][0-9]*$ && "$mount_id" == "$systemd_underlay_mount_id" ]] || return 166
    mounted_identity="$(/usr/bin/stat -c '%d:%i' -- "$mounted_root")" || return 167
    private_identity="$(/usr/bin/stat -c '%d:%i' -- "$private_root")" || return 167
    [[ "$mounted_identity" == "$private_identity" ]] || return 167
    [[ ! -e "$entry_file" && ! -L "$entry_file" ]] || return 168
    /usr/bin/find "$mounted_root" -xdev -mindepth 1 -maxdepth 1 -print0 \
        >"$entry_file" || return 168
    mapfile -d '' -t entries <"$entry_file" || return 168
    /usr/bin/rm -- "$entry_file" || return 168
    [[ "${#entries[@]}" -eq 1 && "${entries[0]}" == "$marker" ]] || return 169
    [[ -z "$(/usr/bin/find "$mounted_root" -xdev -mindepth 2 -print -quit)" ]] || return 170
    [[ -z "$(/usr/bin/find "$mounted_root" -xdev -type s -print -quit)" ]] || return 171
    [[ "$marker" == /run/systemd/systemd-units-load && \
        "$private_marker" == "$work/run-systemd/systemd-units-load" && \
        -f "$marker" && ! -L "$marker" && -f "$private_marker" && \
        ! -L "$private_marker" ]] || return 172
    [[ "$(/usr/bin/stat -c '%d:%i' -- "$marker")" == \
        "$(/usr/bin/stat -c '%d:%i' -- "$private_marker")" ]] || return 172
    marker_metadata="$(/usr/bin/stat -c '%a %u %g %s %h %d %i' -- "$marker")" || return 173
    read -r marker_mode marker_uid marker_gid marker_size marker_links marker_device \
        marker_inode extra <<<"$marker_metadata"
    root_device="$(/usr/bin/stat -c %d -- "$mounted_root")" || return 173
    [[ -z "${extra:-}" && "$marker_mode" == 444 && "$marker_uid" == 0 && \
        "$marker_gid" == 0 && "$marker_size" == 0 && "$marker_links" == 1 && \
        "$marker_device" == "$root_device" && "$marker_inode" =~ ^[1-9][0-9]*$ ]] || return 173
    /usr/bin/cmp -s -- "$marker" /dev/null || return 174
    [[ "$local_systemd_marker_cleanup_count" -eq 0 ]] || return 175
    [[ "$(systemd_mount_id)" == "$systemd_underlay_mount_id" && \
        "$(/usr/bin/stat -c '%d:%i' -- "$mounted_root")" == "$private_identity" ]] || return 176
    marker_metadata_now="$(/usr/bin/stat -c '%a %u %g %s %h %d %i' -- "$marker")" || return 176
    [[ "$marker_metadata_now" == "$marker_metadata" ]] || return 176
    [[ "$(/usr/bin/sha256sum -- "$receipt")" == "$receipt_hash_before  $receipt" ]] || return 177
    /usr/bin/rm -- "$marker" || return 178
    local_systemd_marker_cleanup_count=$((local_systemd_marker_cleanup_count + 1))
    /usr/bin/sync -f "$private_root" || return 179
    [[ "$local_systemd_marker_cleanup_count" -eq 1 && \
        ! -e "$marker" && ! -L "$marker" && \
        ! -e "$private_marker" && ! -L "$private_marker" && \
        -z "$(/usr/bin/find "$mounted_root" -xdev -mindepth 1 -print -quit)" ]] || return 180
    receipt_hash_after="$(/usr/bin/sha256sum -- "$receipt")" || return 181
    receipt_hash_after="${receipt_hash_after%% *}"
    [[ "$receipt_hash_after" == "$receipt_hash_before" && \
        "$(/usr/bin/stat -c %s -- "$receipt")" == "$expected_receipt_size" && \
        "$(/usr/bin/cat -- "$receipt")"$'\n' == "$expected_receipt" ]] || return 181
}
""".strip()

PCP_PHASE11_WATCHDOG_GUARD_LOOKUP = r"""
watchdog_guard="${recovery_guard_paths_by_unit[watchdog.service]-}"
[[ -n "$watchdog_guard" ]]
[[ "${recovery_guard_paths_by_unit[watchdog.service]-}" == "$watchdog_guard" ]]
[[ "${recovery_guard_path_owners[$watchdog_guard]-}" == watchdog.service ]]
watchdog_guard_inode="${recovery_guard_file_inodes[$watchdog_guard]-}"
[[ "$watchdog_guard_inode" =~ ^[1-9][0-9]*$ && \
    -f "$watchdog_guard" && ! -L "$watchdog_guard" ]]
watchdog_guard_inode_now="$(stat -c %i -- "$watchdog_guard")"
[[ "$watchdog_guard_inode_now" == "$watchdog_guard_inode" ]]
[[ -n "${recovery_guard_condition_paths[$watchdog_guard]+present}" ]]
watchdog_condition="${recovery_guard_condition_paths[$watchdog_guard]-}"
[[ -n "$watchdog_condition" ]]
""".strip()

PCP_PHASE14_PEER_GUARD_LOOKUP = r"""
[[ -n "$peer_guard" && \
    "${recovery_guard_paths_by_unit[zfs.target]-}" == "$peer_guard" ]]
[[ "${recovery_guard_path_owners[$peer_guard]-}" == zfs.target ]]
peer_guard_inode="${recovery_guard_file_inodes[$peer_guard]-}"
[[ "$peer_guard_inode" =~ ^[1-9][0-9]*$ && \
    -f "$peer_guard" && ! -L "$peer_guard" ]]
peer_guard_inode_now="$(stat -c %i -- "$peer_guard")"
[[ "$peer_guard_inode_now" == "$peer_guard_inode" ]]
[[ -n "${recovery_guard_condition_paths[$peer_guard]+present}" ]]
peer_condition="${recovery_guard_condition_paths[$peer_guard]-}"
[[ -n "$peer_condition" ]]
""".strip()


def _assert_recovery_guard_path_key_contract(harness: str) -> None:
    for direct_key in (
        "recovery_guard_condition_paths[watchdog.service]",
        "recovery_guard_condition_paths[zfs.target]",
    ):
        if direct_key in harness:
            raise AssertionError(f"condition map uses unit-name key: {direct_key}")
    required = (
        PCP_PHASE11_WATCHDOG_GUARD_LOOKUP,
        '"ConditionPathExists=$watchdog_condition"',
        PCP_PHASE14_PEER_GUARD_LOOKUP,
        '"ConditionPathExists=$peer_condition"',
    )
    for fragment in required:
        if harness.count(fragment) != 1:
            raise AssertionError(
                f"recovery guard path-key contract is ambiguous: {fragment}"
            )
    if harness.index(PCP_PHASE11_WATCHDOG_GUARD_LOOKUP) > harness.index(
        '"ConditionPathExists=$watchdog_condition"'
    ):
        raise AssertionError("watchdog condition runs before path-key validation")
    if harness.index(PCP_PHASE14_PEER_GUARD_LOOKUP) > harness.index(
        '"ConditionPathExists=$peer_condition"'
    ):
        raise AssertionError("peer condition runs before path-key validation")


def _service_policy_readback_validator(payload: str) -> str:
    anchor = (
        'python3 - "$target" "$retained_repo/evidence/compatibility-matrix.json" '
        '\\\n    "$state_root/service-policy-readback.tsv" '
        "\"$state_root/service-policy-readback.json\" <<'PY'\n"
    )
    start = payload.index(anchor) + len(anchor)
    end = payload.index("\nPY\ncleanup_service_guards", start)
    validator = payload[start:end]
    if "activity_verification" not in validator:
        raise AssertionError("service readback validator lacks activity authority")
    return validator


def _validate_f19_snapshot(
    path: pathlib.Path,
    fixture_root: pathlib.Path,
    stage: str,
    expected_function_sha256: str,
) -> tuple[dict[str, object], str]:
    root = fixture_root.resolve(strict=True)
    receipt_path = path.resolve(strict=True)
    if receipt_path.parent != root or receipt_path.is_symlink():
        raise AssertionError("F19 receipt escapes its disposable fixture")
    raw = receipt_path.read_bytes()
    if not raw or len(raw) > F19_DIAGNOSTIC_MAX_BYTES or not raw.endswith(b"\n"):
        raise AssertionError("F19 receipt framing or size is invalid")
    try:
        receipt = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AssertionError("F19 receipt is not strict UTF-8 JSON") from exc
    expected_keys = {
        "schema_version",
        "stage",
        "inputs",
        "systemd",
        "mounts",
        "vendor_units",
        "etc_entries",
        "presets",
        "effective_preset_rules",
        "phase09_outcomes",
        "enabled_states",
        "phase_boundaries",
        "failure",
        "command_trace",
    }
    if not isinstance(receipt, dict) or set(receipt) != expected_keys:
        raise AssertionError("F19 receipt schema is not exact")
    if receipt["schema_version"] != F19_DIAGNOSTIC_SCHEMA or receipt["stage"] != stage:
        raise AssertionError("F19 receipt version/stage is invalid")
    inputs = receipt["inputs"]
    if not isinstance(inputs, dict) or inputs != {
        "payload_sha256": "3116215f4f2dde376f591b06cb192b3cc725e4261885c5a0bc88e23b8867005b",
        "verifier_sha256": "f188d76e7c19ba38472a5125c68d53e428bcf095d36878ac688e56a93fc627ad",
        "disable_unmasked_units_sha256": expected_function_sha256,
    }:
        raise AssertionError("F19 immutable input identity is invalid")
    systemd = receipt["systemd"]
    if not isinstance(systemd, dict) or set(systemd) != {
        "version_first_line",
        "systemctl_path",
        "systemctl_sha256",
    }:
        raise AssertionError("F19 systemd identity schema is invalid")
    if (
        not re.fullmatch(
            r"systemd 255 \(255\.4-[0-9A-Za-z.+:~]+\)",
            str(systemd["version_first_line"]),
        )
        or systemd["systemctl_path"] != "/usr/bin/systemctl"
        or not re.fullmatch(r"[0-9a-f]{64}", str(systemd["systemctl_sha256"]))
    ):
        raise AssertionError("F19 systemd identity is invalid")
    mounts = receipt["mounts"]
    if not isinstance(mounts, list) or len(mounts) != len(F19_MOUNT_ROOTS):
        raise AssertionError("F19 mount coverage is incomplete")
    if [
        record.get("mountpoint") for record in mounts if isinstance(record, dict)
    ] != list(F19_MOUNT_ROOTS):
        raise AssertionError("F19 mount order is invalid")
    for record in mounts:
        if (
            not isinstance(record, dict)
            or record.get("bind_identity_matches") is not True
        ):
            raise AssertionError("F19 mount does not identify its fixture bind source")
        if not isinstance(record.get("mount_id"), int) or record["mount_id"] <= 0:
            raise AssertionError("F19 mount ID is invalid")
    vendor = receipt["vendor_units"]
    if not isinstance(vendor, list) or len(vendor) != len(F19_TARGET_UNITS):
        raise AssertionError("F19 vendor-unit coverage is incomplete")
    if [
        pathlib.PurePosixPath(str(item.get("path"))).name
        for item in vendor
        if isinstance(item, dict)
    ] != list(F19_TARGET_UNITS):
        raise AssertionError("F19 vendor-unit order is invalid")
    for item in vendor:
        if not isinstance(item, dict) or item.get("type") not in {
            "absent",
            "regular",
            "symlink",
            "directory",
        }:
            raise AssertionError("F19 vendor-unit object is invalid")
        if item["type"] == "regular":
            if not re.fullmatch(r"[0-9a-f]{64}", str(item.get("sha256", ""))):
                raise AssertionError("F19 regular unit hash is invalid")
            try:
                decoded = __import__("base64").b64decode(
                    str(item.get("content_base64", "")), validate=True
                )
            except ValueError as exc:
                raise AssertionError("F19 regular unit content is invalid") from exc
            if hashlib.sha256(decoded).hexdigest() != item["sha256"]:
                raise AssertionError("F19 regular unit content/hash mismatch")
        if item["type"] == "symlink" and item.get("resolved_confined") is not True:
            raise AssertionError("F19 vendor symlink escapes fixture roots")
    etc_entries = receipt["etc_entries"]
    if not isinstance(etc_entries, list) or len(etc_entries) > 192:
        raise AssertionError("F19 unit-entry coverage is unbounded")
    relative_paths = []
    for item in etc_entries:
        if not isinstance(item, dict):
            raise TypeError("F19 unit entry is not an object")
        relative = str(item.get("relative_path", ""))
        pure = pathlib.PurePosixPath(relative)
        if (
            not relative
            or pure.is_absolute()
            or ".." in pure.parts
            or len(pure.parts) > 5
            or item.get("type") not in {"regular", "symlink", "directory"}
        ):
            raise AssertionError("F19 unit entry path/type is unsafe")
        if item["type"] == "symlink" and item.get("resolved_confined") is not True:
            raise AssertionError("F19 unit-entry symlink escapes fixture roots")
        relative_paths.append(relative)
    if relative_paths != sorted(set(relative_paths)):
        raise AssertionError("F19 unit entries are duplicated or unsorted")
    presets = receipt["presets"]
    if not isinstance(presets, list) or len(presets) > 128:
        raise AssertionError("F19 preset coverage is unbounded")
    preset_names = []
    for item in presets:
        if not isinstance(item, dict) or set(item) != {
            "name",
            "path",
            "size",
            "sha256",
            "content_base64",
        }:
            raise AssertionError("F19 preset schema is invalid")
        if (
            not re.fullmatch(r"[A-Za-z0-9_.@:+,-]+\.preset", str(item["name"]))
            or not isinstance(item["size"], int)
            or not 0 <= item["size"] <= 64 * 1024
            or not re.fullmatch(r"[0-9a-f]{64}", str(item["sha256"]))
        ):
            raise AssertionError("F19 preset identity is invalid")
        try:
            decoded = __import__("base64").b64decode(
                str(item["content_base64"]), validate=True
            )
        except ValueError as exc:
            raise AssertionError("F19 preset content is invalid") from exc
        if (
            len(decoded) != item["size"]
            or hashlib.sha256(decoded).hexdigest() != item["sha256"]
        ):
            raise AssertionError("F19 preset content/hash mismatch")
        preset_names.append(str(item["name"]))
    if preset_names != sorted(set(preset_names)):
        raise AssertionError("F19 preset identities are duplicated or unsorted")
    effective_rules = receipt["effective_preset_rules"]
    if not isinstance(effective_rules, list) or [
        row.get("unit") for row in effective_rules if isinstance(row, dict)
    ] != list(F19_TARGET_UNITS):
        raise AssertionError("F19 effective preset coverage is incomplete")
    for row in effective_rules:
        if not isinstance(row, dict) or row.get("action") not in {
            "enable",
            "disable",
            "ignore",
        }:
            raise AssertionError("F19 effective preset rule is invalid")
        if row.get("source") == "default":
            if set(row) != {"unit", "action", "source"} or row["action"] != "enable":
                raise AssertionError("F19 default preset rule is invalid")
        elif set(row) != {"unit", "action", "pattern", "source", "line"}:
            raise AssertionError("F19 explicit preset rule schema is invalid")
    phase09 = receipt["phase09_outcomes"]
    if phase09 != [{"status": 0, "unit": unit} for unit in F19_TARGET_UNITS]:
        raise AssertionError("F19 phase-09 outcomes are incomplete")
    enabled = receipt["enabled_states"]
    if not isinstance(enabled, list) or [
        row.get("unit") for row in enabled if isinstance(row, dict)
    ] != list(F19_TARGET_UNITS):
        raise AssertionError("F19 enablement observations are incomplete")
    for row in enabled:
        if not isinstance(row, dict) or set(row) != {"unit", "first_line", "status"}:
            raise AssertionError("F19 enablement row schema is invalid")
        if not isinstance(row["status"], int) or not 0 <= row["status"] <= 255:
            raise AssertionError("F19 enablement status is invalid")
        if not re.fullmatch(r"[A-Za-z0-9_.@:+,-]{0,128}", str(row["first_line"])):
            raise AssertionError("F19 enablement output is unsafe")
    boundaries = receipt["phase_boundaries"]
    if not isinstance(boundaries, dict) or set(boundaries) != {
        "deferred_validator_reached",
        "phase13_reached",
        "phase14_reached",
        "phase15_reached",
    }:
        raise AssertionError("F19 phase-boundary schema is invalid")
    if any(value is not False for value in boundaries.values()):
        raise AssertionError("F19 diagnostic unexpectedly crossed a later phase")
    if stage == "before":
        if receipt["failure"] is not None or receipt["command_trace"] is not None:
            raise AssertionError("F19 before receipt contains failure evidence")
    else:
        failure = receipt["failure"]
        if not isinstance(failure, dict) or set(failure) != {
            "status",
            "line",
            "function",
            "command",
        }:
            raise AssertionError("F19 failure schema is invalid")
        if (
            failure["status"] != 1
            or not isinstance(failure["line"], int)
            or not 1 <= failure["line"] <= 999999
            or failure["function"] != "main"
            or failure["command"] != "return 1"
        ):
            raise AssertionError("F19 first failure identity is invalid")
        command_trace = receipt["command_trace"]
        if not isinstance(command_trace, dict) or set(command_trace) != {
            "size",
            "sha256",
        }:
            raise AssertionError("F19 command-trace identity is invalid")
        if (
            not isinstance(command_trace["size"], int)
            or not 0 < command_trace["size"] <= F19_COMMAND_TRACE_MAX_BYTES
            or not re.fullmatch(r"[0-9a-f]{64}", str(command_trace["sha256"]))
        ):
            raise AssertionError("F19 command-trace bounds are invalid")
    return receipt, hashlib.sha256(raw).hexdigest()


def _validate_f19_command_trace(
    path: pathlib.Path, fixture_root: pathlib.Path, expected_sha256: str
) -> tuple[str, dict[str, bool]]:
    root = fixture_root.resolve(strict=True)
    trace = path.resolve(strict=True)
    if trace.parent != root or trace.is_symlink():
        raise AssertionError("F19 command trace escapes its fixture")
    raw = trace.read_bytes()
    if not raw or len(raw) > F19_COMMAND_TRACE_MAX_BYTES or not raw.endswith(b"\n"):
        raise AssertionError("F19 command trace framing/size is invalid")
    if hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise AssertionError("F19 command trace hash mismatch")
    try:
        text = raw.decode("ascii")
    except UnicodeDecodeError as exc:
        raise AssertionError("F19 command trace is not ASCII") from exc
    lines = text.splitlines()
    if len(lines) > 1024 or any(len(line) > 768 for line in lines):
        raise AssertionError("F19 command trace exceeds line bounds")
    if any(not re.fullmatch(r"\++F19X\|.*", line) for line in lines):
        raise AssertionError("F19 command trace has an invalid prefix")
    checks = {
        "disable_iscsid": any(
            "systemctl --root=/ disable iscsid.service" in line for line in lines
        ),
        "disable_fallback_executed": any(
            "disable_status=" in line
            and "iscsid.service" in "\n".join(lines[max(0, index - 4) : index + 1])
            for index, line in enumerate(lines)
        ),
        "is_enabled_iscsid": any(
            "systemctl --root=/ is-enabled iscsid.service" in line for line in lines
        ),
        "enabled_output": any("enabled_state=enabled" in line for line in lines),
        "enabled_status_zero": any("enabled_status=0" in line for line in lines),
    }
    for required in (
        "disable_iscsid",
        "is_enabled_iscsid",
        "enabled_output",
        "enabled_status_zero",
    ):
        if not checks[required]:
            raise AssertionError(f"F19 command trace lacks {required}")
    sanitized = "\n".join(
        line
        for line in lines
        if any(
            token in line
            for token in (
                "disable iscsid.service",
                "is-enabled iscsid.service",
                "disable_status=",
                "enabled_state=",
                "enabled_status=",
                "return 1",
            )
        )
    )
    return sanitized + "\n", checks


def _validate_f20_snapshot(
    path: pathlib.Path, fixture_root: pathlib.Path, stage: str
) -> tuple[dict[str, object], str]:
    root = fixture_root.resolve(strict=True)
    receipt_path = path.resolve(strict=True)
    if receipt_path.parent != root or receipt_path.is_symlink():
        raise AssertionError("F20 receipt escapes its fixture")
    raw = receipt_path.read_bytes()
    if not raw or len(raw) > F20_DIAGNOSTIC_MAX_BYTES or not raw.endswith(b"\n"):
        raise AssertionError("F20 receipt framing/size is invalid")
    try:
        receipt = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AssertionError("F20 receipt is not strict UTF-8 JSON") from exc
    if not isinstance(receipt, dict) or set(receipt) != {
        "schema_version",
        "stage",
        "objects",
        "rc_directories",
        "generators",
        "mounts",
        "helper",
    }:
        raise AssertionError("F20 receipt schema is not exact")
    if receipt["schema_version"] != F20_DIAGNOSTIC_SCHEMA or receipt["stage"] != stage:
        raise AssertionError("F20 receipt version/stage is invalid")

    def validate_object(item: object, expected_path: str | None = None) -> None:
        if not isinstance(item, dict) or item.get("type") not in {
            "absent",
            "regular",
            "symlink",
            "directory",
        }:
            raise AssertionError("F20 object type is invalid")
        if expected_path is not None and item.get("path") != expected_path:
            raise AssertionError("F20 object path is invalid")
        packages = item.get("package")
        if packages is not None:
            if not isinstance(packages, list) or not 1 <= len(packages) <= 4:
                raise AssertionError("F20 package ownership is invalid")
            names = []
            for owner in packages:
                if (
                    not isinstance(owner, dict)
                    or set(owner) != {"package", "version"}
                    or not re.fullmatch(
                        r"[a-z0-9][a-z0-9+.-]*(?::[a-z0-9]+)?",
                        str(owner["package"]),
                    )
                    or not isinstance(owner["version"], str)
                    or not 1 <= len(owner["version"]) <= 128
                ):
                    raise AssertionError("F20 package identity is invalid")
                names.append(owner["package"])
            if names != list(dict.fromkeys(names)):
                raise AssertionError("F20 package identity is duplicated")
        if item["type"] == "regular":
            if not re.fullmatch(r"[0-9a-f]{64}", str(item.get("sha256", ""))):
                raise AssertionError("F20 regular object hash is invalid")
            content = item.get("content_base64")
            if content is not None:
                try:
                    decoded = __import__("base64").b64decode(
                        str(content), validate=True
                    )
                except ValueError as exc:
                    raise AssertionError("F20 object content is invalid") from exc
                if (
                    len(decoded) != item.get("size")
                    or len(decoded) > 64 * 1024
                    or hashlib.sha256(decoded).hexdigest() != item["sha256"]
                ):
                    raise AssertionError("F20 object content/hash is invalid")
        if item["type"] == "symlink" and item.get("resolved_confined") is not True:
            raise AssertionError("F20 symlink escapes fixed roots")

    objects = receipt["objects"]
    if not isinstance(objects, list) or len(objects) != len(F20_SYSV_PATHS):
        raise AssertionError("F20 object coverage is incomplete")
    for item, expected in zip(objects, F20_SYSV_PATHS, strict=True):
        validate_object(item, expected)

    rc_rows = receipt["rc_directories"]
    if not isinstance(rc_rows, list) or len(rc_rows) != len(F20_RC_DIRS):
        raise AssertionError("F20 rc-directory coverage is incomplete")
    for row, expected in zip(rc_rows, F20_RC_DIRS, strict=True):
        if not isinstance(row, dict) or set(row) != {"path", "identity", "entries"}:
            raise AssertionError("F20 rc-directory schema is invalid")
        if row["path"] != expected:
            raise AssertionError("F20 rc-directory order is invalid")
        validate_object(row["identity"], expected)
        entries = row["entries"]
        if not isinstance(entries, list) or len(entries) > 32:
            raise AssertionError("F20 rc-directory entries are unbounded")
        names = []
        for item in entries:
            validate_object(item)
            name = str(item.get("name", ""))
            if not re.fullmatch(r"[A-Za-z0-9_.@:+,-]+", name):
                raise AssertionError("F20 rc-entry name is unsafe")
            names.append(name)
        if names != sorted(set(names)):
            raise AssertionError("F20 rc entries are duplicated or unsorted")

    generators = receipt["generators"]
    if not isinstance(generators, list) or len(generators) != len(F20_GENERATOR_ROOTS):
        raise AssertionError("F20 generator coverage is incomplete")
    for row, expected in zip(generators, F20_GENERATOR_ROOTS, strict=True):
        if not isinstance(row, dict) or set(row) != {"path", "identity", "entries"}:
            raise AssertionError("F20 generator schema is invalid")
        if row["path"] != expected:
            raise AssertionError("F20 generator order is invalid")
        validate_object(row["identity"], expected)
        if not isinstance(row["entries"], list) or len(row["entries"]) > 32:
            raise AssertionError("F20 generator entries are unbounded")
        names = []
        for item in row["entries"]:
            validate_object(item)
            name = str(item.get("name", ""))
            if not re.fullmatch(r"[A-Za-z0-9_.@:+,-]+", name):
                raise AssertionError("F20 generator entry name is unsafe")
            names.append(name)
        if names != sorted(set(names)):
            raise AssertionError("F20 generator entries are duplicated or unsorted")

    mounts = receipt["mounts"]
    expected_mounts = [
        "/etc/init.d",
        *F20_RC_DIRS,
        "/usr/lib/systemd/systemd-sysv-install",
    ]
    if (
        not isinstance(mounts, list)
        or [item.get("mountpoint") for item in mounts if isinstance(item, dict)]
        != expected_mounts
    ):
        raise AssertionError("F20 private mount coverage/order is invalid")
    for item in mounts:
        if (
            not isinstance(item, dict)
            or item.get("bind_identity_matches") is not True
            or not isinstance(item.get("mount_id"), int)
            or item["mount_id"] <= 0
        ):
            raise AssertionError("F20 private mount identity is invalid")

    helper = receipt["helper"]
    if not isinstance(helper, dict) or not isinstance(helper.get("real_helper"), dict):
        raise TypeError("F20 helper identity is missing")
    entry_guard = _validate_f25_entry_guard(helper.get("entry_guard"))
    real = helper["real_helper"]
    if (
        set(real) != {"path", "size", "mode", "sha256"}
        or not re.fullmatch(r"[0-9a-f]{64}", str(real["sha256"]))
        or not isinstance(real["size"], int)
        or not 0 < real["size"] <= 64 * 1024
    ):
        raise AssertionError("F20 copied helper identity is invalid")
    if stage == "before" or helper.get("invoked") is False:
        if (
            set(helper) != {"invoked", "real_helper", "entry_guard"}
            or helper["invoked"] is not False
        ):
            raise AssertionError("F20 helper was invoked before phase 12")
        if stage == "before" and entry_guard != {"entry_reached": False}:
            raise AssertionError("F25 helper entry exists before phase 12")
    else:
        if (
            set(helper)
            != {
                "invoked",
                "real_helper",
                "entry_guard",
                "argv",
                "environment",
                "status",
                "invocation_sha256",
                "outputs",
            }
            or helper["invoked"] is not True
        ):
            raise AssertionError("F20 helper invocation evidence is incomplete")
        if helper["argv"] != ["--root=/", "disable", "iscsid"] or helper[
            "environment"
        ] != {"SYSTEMD_OFFLINE": "1"}:
            raise AssertionError("F20 helper argv/environment changed")
        if not isinstance(helper["status"], int) or not 0 <= helper["status"] <= 255:
            raise AssertionError("F20 helper status is invalid")
        if not re.fullmatch(r"[0-9a-f]{64}", str(helper["invocation_sha256"])):
            raise AssertionError("F20 invocation receipt hash is invalid")
        outputs = helper["outputs"]
        if not isinstance(outputs, dict) or set(outputs) != {"stdout", "stderr"}:
            raise AssertionError("F20 helper outputs are incomplete")
        for output in outputs.values():
            if (
                not isinstance(output, dict)
                or set(output)
                != {"size", "sha256", "safe_first_line", "content_base64"}
                or not isinstance(output["size"], int)
                or not 0 <= output["size"] <= F20_OUTPUT_MAX_BYTES
                or not re.fullmatch(r"[0-9a-f]{64}", str(output["sha256"]))
                or not isinstance(output["safe_first_line"], str)
                or len(output["safe_first_line"]) > 240
            ):
                raise AssertionError("F20 helper output schema is invalid")
            try:
                decoded = __import__("base64").b64decode(
                    str(output["content_base64"]), validate=True
                )
            except ValueError as exc:
                raise AssertionError("F20 helper output encoding is invalid") from exc
            if (
                len(decoded) != output["size"]
                or hashlib.sha256(decoded).hexdigest() != output["sha256"]
            ):
                raise AssertionError("F20 helper output identity is invalid")
            if re.search(
                rb"(?:-----BEGIN [A-Z ]*PRIVATE KEY-----|ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|Authorization:[ \t]*Bearer|(?:token|password|secret)=\S+)",
                decoded,
                flags=re.IGNORECASE,
            ):
                raise AssertionError("F20 helper output contains secret-like material")
    return receipt, hashlib.sha256(raw).hexdigest()


def _validate_f21_capture_error(
    path: pathlib.Path, fixture_root: pathlib.Path
) -> tuple[dict[str, object], str]:
    raw = _read_strict_root_file(
        path,
        fixture_root,
        expected_name="f21-capture-error.json",
        max_bytes=1024,
    )
    if not raw or len(raw) > 1024 or not raw.endswith(b"\n"):
        raise AssertionError("F21 capture-error framing is invalid")
    try:
        receipt = json.loads(raw.decode("ascii"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AssertionError("F21 capture-error JSON is invalid") from exc
    if not isinstance(receipt, dict) or set(receipt) != {
        "schema_version",
        "stage",
        "status",
        "stderr_size",
        "stderr_sha256",
        "stderr_class",
        "stderr_uid",
        "stderr_gid",
        "stderr_mode",
    }:
        raise AssertionError("F21 capture-error schema is not exact")
    if receipt["schema_version"] != F21_CAPTURE_ERROR_SCHEMA or receipt[
        "stage"
    ] not in {"f19-after", "f20-after"}:
        raise AssertionError("F21 capture-error version/stage is invalid")
    if (
        not isinstance(receipt["status"], int)
        or not 1 <= receipt["status"] <= 255
        or not isinstance(receipt["stderr_size"], int)
        or not 1 <= receipt["stderr_size"] <= F21_CAPTURE_ERROR_MAX_STDERR_BYTES
        or not re.fullmatch(r"[0-9a-f]{64}", str(receipt["stderr_sha256"]))
        or not re.fullmatch(r"[a-z0-9-]{1,64}", str(receipt["stderr_class"]))
        or receipt["stderr_uid"] != 0
        or receipt["stderr_gid"] != 0
        or receipt["stderr_mode"] != "0600"
    ):
        raise AssertionError("F21 capture-error values are invalid")
    return receipt, hashlib.sha256(raw).hexdigest()


F29_F21_ATTEMPT_MAX_BYTES = 2048
F29_F21_OUTPUT_MAX_BYTES = 8 * 1024 + 1
F29_F21_CLASSES = {
    "EMPTY",
    "UNSAFE_OR_TRUNCATED",
    "ENCODING_INVALID",
    "FRAMING_INVALID",
    "ARGV_INVALID",
    "PATH_IDENTITY_INVALID",
    "METADATA_INVALID",
    "OUTPUT_EXISTS",
    "PERMISSION_DENIED",
    "PYTHON_EXCEPTION_SANITIZED",
    "UNCLASSIFIED_BOUNDED",
}
F29_OUTER_RECEIPT_MAX_BYTES = 2048
F29_OUTER_CLASSES = {
    "EMPTY",
    "UNSAFE_OR_TRUNCATED",
    "ENCODING_INVALID",
    "FRAMING_INVALID",
    "ARGV_INVALID",
    "PATH_IDENTITY_INVALID",
    "METADATA_INVALID",
    "OUTPUT_EXISTS",
    "PERMISSION_DENIED",
    "PYTHON_EXCEPTION_SANITIZED",
    "UNCLASSIFIED_BOUNDED",
}
F29_DIRECT_OUTPUT_MAX_BYTES = 8 * 1024
F29_DIRECT_STATUS_MAX_BYTES = 4
F29_DIRECT_SECRET_PATTERN = re.compile(
    rb"(?:-----BEGIN [A-Z ]*PRIVATE KEY-----|ghp_[A-Za-z0-9]{20,}|"
    rb"github_pat_[A-Za-z0-9_]{20,}|Authorization:[ \t]*Bearer|"
    rb"(?:token|password|secret)=\S+)",
    re.IGNORECASE,
)
F29_DIRECT_STREAM_CLASSES = {
    "EMPTY",
    "OUTER_GUARD",
    "PERMISSION_DENIED",
    "ENCODING_INVALID",
    "FRAMING_INVALID",
    "UNSAFE_OR_TRUNCATED",
    "PYTHON_EXCEPTION_SANITIZED",
    "UNCLASSIFIED_BOUNDED",
}
F29_DIRECT_OUTER_GUARDS = {
    "F29 outer argv invalid",
    "F29 outer stage or digest is invalid",
    "F29 outer path identity is invalid",
    "F29 outer required path is unavailable",
    "F29 outer source or receipt identity is invalid",
    "F29 outer receipt exceeds cap",
    "F29 outer receipt already exists",
}
F29_DIRECT_REQUIRED_PATH_MESSAGE = "F29 outer required path metadata is invalid"
F29_REQUIRED_OBJECT_MODES = {
    "F20_SNAPSHOT_STDERR": 0o600,
    "F21_CAPTURE_SOURCE": 0o644,
    "F29_RUNNER_SOURCE": 0o644,
}
F29_REQUIRED_PATH_CLASSES = {
    f"{object_name}_{predicate}"
    for object_name in F29_REQUIRED_OBJECT_MODES
    for predicate in (
        "REGULAR_FILE",
        "NON_SYMLINK",
        "EXPECTED_MODE",
        "LINK_COUNT_ONE",
    )
} | {"F20_SNAPSHOT_STDERR_EXPECTED_OWNER"}
F29_DIRECT_STREAM_CLASSES |= F29_REQUIRED_PATH_CLASSES


def _validate_f29_f21_attempt(
    path: pathlib.Path, fixture_root: pathlib.Path, expected_source_sha256: str
) -> tuple[dict[str, object], str]:
    raw = _read_strict_root_file(
        path,
        fixture_root,
        expected_name="f29-f21-attempt.json",
        max_bytes=F29_F21_ATTEMPT_MAX_BYTES,
    )
    if not raw or not raw.endswith(b"\n"):
        raise AssertionError("F29 attempt framing is invalid")
    try:
        receipt = json.loads(raw.decode("ascii"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AssertionError("F29 attempt JSON is invalid") from exc
    if not isinstance(receipt, dict) or set(receipt) != {
        "schema_version",
        "stage",
        "snapshot_status",
        "child_status",
        "timed_out",
        "stdout_size",
        "stdout_sha256",
        "stderr_size",
        "stderr_sha256",
        "stderr_class",
        "source_sha256",
    }:
        raise AssertionError("F29 attempt schema is not exact")
    if (
        receipt["schema_version"] != 1
        or receipt["stage"] not in {"f19-after", "f20-after"}
        or not isinstance(receipt["snapshot_status"], int)
        or not 1 <= receipt["snapshot_status"] <= 255
        or not isinstance(receipt["child_status"], int)
        or not 1 <= receipt["child_status"] <= 255
        or type(receipt["timed_out"]) is not bool
        or receipt["timed_out"] is not (receipt["child_status"] == 124)
        or receipt["stderr_class"] not in F29_F21_CLASSES
        or receipt["source_sha256"] != expected_source_sha256
    ):
        raise AssertionError("F29 attempt values are invalid")
    for key in ("stdout_size", "stderr_size"):
        if (
            not isinstance(receipt[key], int)
            or isinstance(receipt[key], bool)
            or not 0 <= receipt[key] <= F29_F21_OUTPUT_MAX_BYTES
        ):
            raise AssertionError("F29 attempt output size is invalid")
    for key in ("stdout_sha256", "stderr_sha256"):
        if not re.fullmatch(r"[0-9a-f]{64}", str(receipt[key])):
            raise AssertionError("F29 attempt output digest is invalid")
    return receipt, hashlib.sha256(raw).hexdigest()


def _validate_f29_outer_receipt(
    path: pathlib.Path,
    fixture_root: pathlib.Path,
    stage: str,
    expected_source_sha256: str,
) -> tuple[dict[str, object], str]:
    raw = _read_strict_root_file(
        path,
        fixture_root,
        expected_name=f"f29-outer-{stage}.json",
        max_bytes=F29_OUTER_RECEIPT_MAX_BYTES,
    )
    if not raw or not raw.endswith(b"\n"):
        raise AssertionError("F29 outer receipt framing is invalid")
    try:
        receipt = json.loads(raw.decode("ascii"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AssertionError("F29 outer receipt JSON is invalid") from exc
    expected_keys = {
        "schema_version",
        "stage",
        "runner_invoked",
        "runner_status",
        "timed_out",
        "stdout_size",
        "stdout_sha256",
        "stderr_size",
        "stderr_sha256",
        "stderr_class",
        "attempt_exists",
        "output_exists",
        "source_sha256",
    }
    if not isinstance(receipt, dict) or set(receipt) != expected_keys:
        raise AssertionError("F29 outer receipt schema is not exact")
    if (
        receipt["schema_version"] != 1
        or receipt["stage"] != stage
        or receipt["runner_invoked"] is not True
        or not isinstance(receipt["runner_status"], int)
        or isinstance(receipt["runner_status"], bool)
        or type(receipt["timed_out"]) is not bool
        or receipt["stderr_class"] not in F29_OUTER_CLASSES
        or receipt["source_sha256"] != expected_source_sha256
        or type(receipt["attempt_exists"]) is not bool
        or type(receipt["output_exists"]) is not bool
    ):
        raise AssertionError("F29 outer receipt values are invalid")
    for key in ("stdout_size", "stderr_size"):
        if (
            not isinstance(receipt[key], int)
            or isinstance(receipt[key], bool)
            or not 0 <= receipt[key] <= F29_F21_OUTPUT_MAX_BYTES
        ):
            raise AssertionError("F29 outer receipt output size is invalid")
    for key in ("stdout_sha256", "stderr_sha256"):
        if not re.fullmatch(r"[0-9a-f]{64}", str(receipt[key])):
            raise AssertionError("F29 outer receipt output digest is invalid")
    runner_status = receipt["runner_status"]
    assert isinstance(runner_status, int) and not isinstance(runner_status, bool)
    if runner_status == 0:
        empty_sha256 = hashlib.sha256(b"").hexdigest()
        if (
            receipt["timed_out"] is not False
            or receipt["stdout_size"] != 0
            or receipt["stderr_size"] != 0
            or receipt["stdout_sha256"] != empty_sha256
            or receipt["stderr_sha256"] != empty_sha256
            or receipt["stderr_class"] != "EMPTY"
            or receipt["attempt_exists"] is not False
            or receipt["output_exists"] is not True
        ):
            raise AssertionError("F29 outer success envelope is invalid")
    elif not 1 <= runner_status <= 255 or receipt["timed_out"] is not (
        runner_status == 124
    ):
        raise AssertionError("F29 outer receipt values are invalid")
    return receipt, hashlib.sha256(raw).hexdigest()


def _classify_f29_direct_stream(raw: bytes) -> str:
    if not raw:
        return "EMPTY"
    if len(raw) > F29_DIRECT_OUTPUT_MAX_BYTES or F29_DIRECT_SECRET_PATTERN.search(raw):
        return "UNSAFE_OR_TRUNCATED"
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return "ENCODING_INVALID"
    if text.count("\n") != 1 or not text.endswith("\n") or "\r" in text:
        return "FRAMING_INVALID"
    message = text[:-1]
    if message == F29_DIRECT_REQUIRED_PATH_MESSAGE:
        return "REQUIRED_PATH_METADATA"
    if "F29 outer required path" in message:
        return "REQUIRED_PATH_METADATA_INVALID"
    if message in F29_DIRECT_OUTER_GUARDS:
        return "OUTER_GUARD"
    if "Permission denied" in message:
        return "PERMISSION_DENIED"
    if "Traceback" in message or "Exception" in message:
        return "PYTHON_EXCEPTION_SANITIZED"
    if "/" in message or "\\" in message:
        return "UNSAFE_OR_TRUNCATED"
    return "UNCLASSIFIED_BOUNDED"


def _classify_f29_required_path_predicate(
    required_paths: dict[str, tuple[pathlib.Path, int, tuple[int, int] | None]],
) -> str:
    if set(required_paths) != set(F29_REQUIRED_OBJECT_MODES):
        raise AssertionError("F29 required-object schema is invalid")
    failures: list[str] = []
    for object_name, declared_mode in F29_REQUIRED_OBJECT_MODES.items():
        path, expected_mode, expected_owner = required_paths[object_name]
        if expected_mode != declared_mode:
            raise AssertionError("F29 required-object mode contract is invalid")
        try:
            metadata = path.lstat()
        except OSError as exc:
            raise AssertionError("F29 required-object observation failed") from exc
        if path.is_symlink():
            failures.append(f"{object_name}_NON_SYMLINK")
        elif not stat.S_ISREG(metadata.st_mode):
            failures.append(f"{object_name}_REGULAR_FILE")
        elif stat.S_IMODE(metadata.st_mode) != expected_mode:
            failures.append(f"{object_name}_EXPECTED_MODE")
        elif metadata.st_nlink != 1:
            failures.append(f"{object_name}_LINK_COUNT_ONE")
        elif (
            expected_owner is not None
            and (
                metadata.st_uid,
                metadata.st_gid,
            )
            != expected_owner
        ):
            failures.append(f"{object_name}_EXPECTED_OWNER")
    if len(failures) != 1 or failures[0] not in F29_REQUIRED_PATH_CLASSES:
        raise AssertionError("F29 required-path predicate is ambiguous")
    return failures[0]


def _validate_f29_direct_capture(
    fixture_root: pathlib.Path,
    stage: str,
    *,
    required_paths: (
        dict[str, tuple[pathlib.Path, int, tuple[int, int] | None]] | None
    ) = None,
) -> dict[str, object]:
    if stage not in {"f19-after", "f20-after"}:
        raise AssertionError("F29 direct capture stage is invalid")
    prefix = f"f29-direct-{stage}"
    status_raw = _read_strict_root_file(
        fixture_root / f"{prefix}.status",
        fixture_root,
        expected_name=f"{prefix}.status",
        max_bytes=F29_DIRECT_STATUS_MAX_BYTES,
    )
    if not re.fullmatch(rb"(?:0|[1-9][0-9]{0,2})\n", status_raw):
        raise AssertionError("F29 direct capture status is invalid")
    status = int(status_raw[:-1])
    if status > 255:
        raise AssertionError("F29 direct capture status is out of range")
    streams: dict[str, dict[str, object]] = {}
    for label in ("stdout", "stderr"):
        raw = _read_strict_root_file(
            fixture_root / f"{prefix}.{label}",
            fixture_root,
            expected_name=f"{prefix}.{label}",
            max_bytes=F29_DIRECT_OUTPUT_MAX_BYTES,
        )
        classification = _classify_f29_direct_stream(raw)
        if classification == "REQUIRED_PATH_METADATA":
            if stage != "f20-after" or required_paths is None:
                raise AssertionError("F29 required-path context is unavailable")
            classification = _classify_f29_required_path_predicate(required_paths)
        if classification not in F29_DIRECT_STREAM_CLASSES:
            raise AssertionError("F29 direct capture stream class is invalid")
        streams[label] = {
            "size": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(),
            "classification": classification,
        }
    return {
        "stage": stage,
        "attempted": True,
        "exit_status": status,
        "completed_status_record": True,
        "timed_out": False,
        "streams": streams,
    }


def _require_f29_outer_success_correlation(
    outer_receipts: list[tuple[dict[str, object], str]],
    direct_captures: list[dict[str, object]],
    capture_error: tuple[dict[str, object], str] | None,
) -> tuple[dict[str, object], str]:
    outer_stages = [str(receipt[0]["stage"]) for receipt in outer_receipts]
    if not outer_stages or outer_stages not in (
        ["f19-after"],
        ["f20-after"],
        ["f19-after", "f20-after"],
    ):
        raise AssertionError("F29 outer success order is invalid")
    if any(receipt[0]["runner_status"] != 0 for receipt in outer_receipts):
        raise AssertionError("F29 outer success status is invalid")
    if [str(capture["stage"]) for capture in direct_captures] != outer_stages:
        raise AssertionError("F29 outer/direct success stages do not match")
    empty_sha256 = hashlib.sha256(b"").hexdigest()
    for capture in direct_captures:
        streams = capture.get("streams")
        if (
            capture.get("attempted") is not True
            or capture.get("exit_status") != 0
            or capture.get("completed_status_record") is not True
            or capture.get("timed_out") is not False
            or not isinstance(streams, dict)
            or set(streams) != {"stdout", "stderr"}
        ):
            raise AssertionError("F29 direct success envelope is invalid")
        for stream in streams.values():
            if (
                not isinstance(stream, dict)
                or set(stream) != {"size", "sha256", "classification"}
                or stream["size"] != 0
                or stream["sha256"] != empty_sha256
                or stream["classification"] != "EMPTY"
            ):
                raise AssertionError("F29 direct success stream is invalid")
    if capture_error is None:
        raise AssertionError("F29 outer success has no validated F21 capture error")
    return capture_error


def _format_f29_direct_captures(captures: list[dict[str, object]]) -> str:
    expected_keys = {
        "stage",
        "attempted",
        "exit_status",
        "completed_status_record",
        "timed_out",
        "streams",
    }
    if any(
        not isinstance(capture, dict) or set(capture) != expected_keys
        for capture in captures
    ):
        raise AssertionError("F29 direct capture schema is invalid")
    stages = [str(capture["stage"]) for capture in captures]
    if stages not in (
        ["f19-after"],
        ["f20-after"],
        ["f19-after", "f20-after"],
    ):
        raise AssertionError("F29 direct capture order is invalid")
    fields: list[str] = []
    for capture in captures:
        streams = capture["streams"]
        if (
            capture["attempted"] is not True
            or capture["completed_status_record"] is not True
            or capture["timed_out"] is not False
            or not isinstance(streams, dict)
        ):
            raise AssertionError("F29 direct capture values are invalid")
        if set(streams) != {"stdout", "stderr"}:
            raise AssertionError("F29 direct capture stream schema is invalid")
        for stream in streams.values():
            if (
                not isinstance(stream, dict)
                or set(stream) != {"size", "sha256", "classification"}
                or not isinstance(stream["size"], int)
                or isinstance(stream["size"], bool)
                or not 0 <= stream["size"] <= F29_DIRECT_OUTPUT_MAX_BYTES
                or not re.fullmatch(r"[0-9a-f]{64}", str(stream["sha256"]))
                or stream["classification"] not in F29_DIRECT_STREAM_CLASSES
                or stream["classification"] == "REQUIRED_PATH_METADATA"
            ):
                raise AssertionError("F29 direct capture stream values are invalid")
        fields.append(
            "stage={stage} attempted=true exit_status={status} timed_out=false "
            "stdout_size={stdout_size} stdout_sha256={stdout_sha} "
            "stdout_class={stdout_class} stderr_size={stderr_size} "
            "stderr_sha256={stderr_sha} stderr_class={stderr_class}".format(
                stage=capture["stage"],
                status=capture["exit_status"],
                stdout_size=streams["stdout"]["size"],
                stdout_sha=streams["stdout"]["sha256"],
                stdout_class=streams["stdout"]["classification"],
                stderr_size=streams["stderr"]["size"],
                stderr_sha=streams["stderr"]["sha256"],
                stderr_class=streams["stderr"]["classification"],
            )
        )
    return f"F29 direct outer invocations: count={len(captures)} " + " | ".join(fields)


def _pcp_phase_ten_with_causal_proof() -> str:
    prefix = "trace_begin 10-host-manager-isolation host-manager-isolation\n"
    if PCP_OFFLINE_NONACTIVATION_PROOF.count(prefix) != 1:
        raise AssertionError("real PCP phase-10 entry is missing or ambiguous")
    return PCP_OFFLINE_NONACTIVATION_PROOF.replace(
        prefix,
        prefix
        + PCP_SYSTEMD_SOURCE_RECEIPT
        + "\n"
        + PCP_SYSTEMD_CAUSAL_PROOF
        + "\n"
        + PCP_LOCAL_SYSTEMD_MARKER_ORACLE
        + "\n",
        1,
    )


PCP_OFFLINE_NONACTIVATION_PROOF = r"""
trace_begin 10-host-manager-isolation host-manager-isolation
[[ -z "$(find "$work/run-systemd" -mindepth 1 -print -quit)" ]]
preset_enabled_state="$(SYSTEMD_OFFLINE=1 systemctl --root=/ is-enabled pmcd.service)"
[[ "$preset_enabled_state" == enabled ]]
post_configure_start_status=0
"$policy" pmcd.service start || post_configure_start_status=$?
[[ "$post_configure_start_status" -eq 101 ]]
validate_recovery_unit_guards
pmcd_guard="${recovery_guard_paths_by_unit[pmcd.service]}"
expected_pmcd_guard="$mask_root/pmcd.service.d/90-hoardarr-offline-recovery.conf"
[[ "$pmcd_guard" == "$expected_pmcd_guard" ]]
[[ "${recovery_guard_path_owners[$pmcd_guard]-}" == pmcd.service ]]
[[ "${recovery_guard_file_inodes[$pmcd_guard]-}" =~ ^[1-9][0-9]*$ ]]
[[ -f "$pmcd_guard" && ! -L "$pmcd_guard" ]]
entry_is_root_owned "$pmcd_guard"
[[ "$(stat -c %a -- "$pmcd_guard")" == 644 ]]
[[ "$(stat -c %i -- "$pmcd_guard")" == "${recovery_guard_file_inodes[$pmcd_guard]}" ]]
pmcd_guard_count=0
for guard in "${recovery_guard_files[@]}"; do
    [[ "$guard" != "$pmcd_guard" ]] || pmcd_guard_count=$((pmcd_guard_count + 1))
done
[[ "$pmcd_guard_count" -eq 1 ]]
expected_pmcd_condition=/dev/null/hoardarr-offline-service-guard/pmcd.service
[[ "${recovery_guard_condition_paths[$pmcd_guard]-}" == "$expected_pmcd_condition" ]]
expected_pmcd_content="$(printf '[Unit]\nConditionPathExists=%s\n' "$expected_pmcd_condition")"$'\n'
[[ "${recovery_guard_contents[$pmcd_guard]-}" == "$expected_pmcd_content" ]]
[[ "$(cat -- "$pmcd_guard")"$'\n' == "$expected_pmcd_content" ]]
[[ ! -e "$expected_pmcd_condition" && ! -L "$expected_pmcd_condition" ]]
[[ ! -e "$recovery_guard_authorization_root" && ! -L "$recovery_guard_authorization_root" ]]
[[ "$expected_pmcd_condition" != "$recovery_guard_authorization_root" && \
    "$expected_pmcd_condition" != "$recovery_guard_authorization_root/"* ]]
manager_root_snapshot before - "$work/manager-root-before.tsv"
systemd-analyze condition "ConditionPathExists=$expected_pmcd_condition" \
    >/dev/null 2>&1 && exit 100
condition_status=$?
manager_root_snapshot after "$condition_status" "$work/manager-root-after.tsv"
validate_and_remove_local_systemd_marker "$work/manager-root-after.tsv"
[[ "$local_systemd_marker_cleanup_count" -eq 1 ]]
[[ -z "$(find "$work/run-systemd" -mindepth 1 -print -quit)" ]]
trace_pass
""".strip()


def _assert_pcp_offline_nonactivation_contract(harness: str) -> None:
    required = (
        '[[ -z "$(find "$work/run-systemd" -mindepth 1 -print -quit)" ]]',
        "SYSTEMD_OFFLINE=1 systemctl --root=/ is-enabled pmcd.service",
        '"$policy" pmcd.service start || post_configure_start_status=$?',
        '[[ "$post_configure_start_status" -eq 101 ]]',
        "validate_recovery_unit_guards",
        'expected_pmcd_guard="$mask_root/pmcd.service.d/90-hoardarr-offline-recovery.conf"',
        '[[ "${recovery_guard_path_owners[$pmcd_guard]-}" == pmcd.service ]]',
        'entry_is_root_owned "$pmcd_guard"',
        '[[ "$(stat -c %a -- "$pmcd_guard")" == 644 ]]',
        "expected_pmcd_condition=/dev/null/hoardarr-offline-service-guard/pmcd.service",
        '[[ ! -e "$expected_pmcd_condition" && ! -L "$expected_pmcd_condition" ]]',
        '[[ ! -e "$recovery_guard_authorization_root" && ! -L "$recovery_guard_authorization_root" ]]',
        'manager_root_snapshot before - "$work/manager-root-before.tsv"',
        'systemd-analyze condition "ConditionPathExists=$expected_pmcd_condition"',
        "condition_status=$?",
        'manager_root_snapshot after "$condition_status" "$work/manager-root-after.tsv"',
        'validate_and_remove_local_systemd_marker "$work/manager-root-after.tsv"',
        '[[ "$local_systemd_marker_cleanup_count" -eq 1 ]]',
    )
    for fragment in required:
        if fragment not in harness:
            raise AssertionError(
                f"generated PCP harness lacks offline proof: {fragment}"
            )
    manager_root_check = (
        '[[ -z "$(find "$work/run-systemd" -mindepth 1 -print -quit)" ]]'
    )
    if harness.count(manager_root_check) != 2:
        raise AssertionError(
            "generated PCP harness must bracket offline proof with manager-root checks"
        )
    snapshot_sequence = (
        'manager_root_snapshot before - "$work/manager-root-before.tsv"\n'
        'systemd-analyze condition "ConditionPathExists=$expected_pmcd_condition" \\\n'
        "    >/dev/null 2>&1 && exit 100\n"
        "condition_status=$?\n"
        'manager_root_snapshot after "$condition_status" '
        '"$work/manager-root-after.tsv"\n'
        "validate_and_remove_local_systemd_marker "
        '"$work/manager-root-after.tsv"\n'
        '[[ "$local_systemd_marker_cleanup_count" -eq 1 ]]\n' + manager_root_check
    )
    if snapshot_sequence not in harness:
        raise AssertionError(
            "generated PCP harness changes condition or snapshot ordering semantics"
        )
    if re.search(r"systemctl\s+is-active\s+pmcd\.service", harness):
        raise AssertionError("generated PCP harness queries manager-dependent activity")
    for obsolete in ("pcp_active_state", "pcp_active_status"):
        if obsolete in harness:
            raise AssertionError(f"generated PCP harness retains obsolete {obsolete}")


def _pcp_trace_shell_prelude() -> str:
    return r"""
trace_file="$5"
current_phase=01-fixture-creation
current_label=fixture-creation
trace_terminal=false
trace_write() {
    local record="$1"
    (( ${#record} <= 240 ))
    printf '%s\n' "$record" >>"$trace_file"
}
trace_begin() {
    current_phase="$1"
    current_label="$2"
    trace_write "HPCP|1|BEGIN|$current_phase|status=-|line=-|function=-|label=$current_label"
}
trace_pass() {
    trace_write "HPCP|1|PASS|$current_phase|status=-|line=-|function=-|label=$current_label"
}
trace_failure() {
    local status="$1"
    local line="$2"
    local function="${FUNCNAME[1]:-main}"
    trap - ERR EXIT
    if [[ "$trace_terminal" != true ]]; then
        trace_terminal=true
        trace_write "HPCP|1|EXIT|$current_phase|status=$status|line=$line|function=$function|label=$current_label" || :
    fi
    exit "$status"
}
trace_exit() {
    local status="$1"
    local line="$2"
    trap - ERR EXIT
    if [[ "$trace_terminal" != true ]]; then
        trace_terminal=true
        trace_write "HPCP|1|EXIT|$current_phase|status=$status|line=$line|function=main|label=$current_label" || :
    fi
    exit "$status"
}
trap 'trace_failure "$?" "$LINENO"' ERR
trap 'trace_exit "$?" "$LINENO"' EXIT
""".strip()


def _append_pcp_trace_phase(
    trace_path: pathlib.Path, phase_index: int, kind: str
) -> None:
    phase, label = PCP_TRACE_PHASES[phase_index]
    if kind not in {"BEGIN", "PASS"}:
        raise AssertionError("invalid PCP trace phase marker")
    with trace_path.open("a", encoding="ascii", newline="\n") as trace:
        trace.write(f"HPCP|1|{kind}|{phase}|status=-|line=-|function=-|label={label}\n")


def _validate_pcp_trace(
    trace_path: pathlib.Path,
    fixture_root: pathlib.Path,
    namespace_path: pathlib.Path,
) -> tuple[str, int]:
    root = fixture_root.resolve(strict=True)
    namespace = namespace_path.resolve(strict=False)
    trace = trace_path.resolve(strict=False)
    if trace.parent != root or trace == namespace or namespace in trace.parents:
        raise AssertionError("PCP trace is outside the exact fixture root")
    if trace_path.is_symlink() or not trace_path.is_file():
        raise AssertionError("PCP trace is missing or is not a regular file")
    raw = trace_path.read_bytes()
    if not raw or len(raw) > PCP_TRACE_MAX_BYTES:
        raise AssertionError("PCP trace size is missing or unbounded")
    try:
        text = raw.decode("ascii")
    except UnicodeDecodeError as exc:
        raise AssertionError("PCP trace is not bounded ASCII") from exc
    if not text.endswith("\n"):
        raise AssertionError("PCP trace is not newline terminated")
    lines = text.splitlines()
    if any(len(line.encode("ascii")) > PCP_TRACE_MAX_LINE_BYTES for line in lines):
        raise AssertionError("PCP trace contains an unbounded line")

    expected = list(PCP_TRACE_PHASES)
    expected_index = 0
    open_phase: tuple[str, str] | None = None
    terminal_status: int | None = None
    for index, line in enumerate(lines):
        match = PCP_TRACE_RECORD.fullmatch(line)
        if match is None:
            raise AssertionError(f"PCP trace record {index + 1} is malformed")
        kind, phase, status_text, line_text, function, label = match.groups()
        if kind == "BEGIN":
            if terminal_status is not None or open_phase is not None:
                raise AssertionError("PCP trace phase is duplicate or out of order")
            if (
                expected_index >= len(expected)
                or (phase, label) != expected[expected_index]
            ):
                raise AssertionError(
                    "PCP trace phase is unknown, missing, or out of order"
                )
            if (status_text, line_text, function) != ("-", "-", "-"):
                raise AssertionError("PCP BEGIN record contains unexpected fields")
            open_phase = (phase, label)
        elif kind == "PASS":
            if terminal_status is not None or open_phase != (phase, label):
                raise AssertionError("PCP trace PASS is duplicate or out of order")
            if (status_text, line_text, function) != ("-", "-", "-"):
                raise AssertionError("PCP PASS record contains unexpected fields")
            open_phase = None
            expected_index += 1
        else:
            if terminal_status is not None:
                raise AssertionError(
                    "PCP trace terminal receipt is duplicate or misplaced"
                )
            status = int(status_text) if status_text != "-" else -1
            source_line = int(line_text) if line_text != "-" else -1
            if not 0 <= status <= 255 or source_line <= 0 or function == "-":
                raise AssertionError(
                    "PCP trace terminal status or source identity is invalid"
                )
            if status == 0:
                if (
                    open_phase is not None
                    or expected_index != len(expected)
                    or (phase, label) != expected[-1]
                ):
                    raise AssertionError(
                        "PCP trace reports success before every phase passed"
                    )
            elif open_phase != (phase, label):
                raise AssertionError("PCP trace failure is not tied to the open phase")
            terminal_status = status
    if terminal_status is None:
        raise AssertionError(f"PCP trace has no terminal receipt:\n{text}")
    return text, terminal_status


def _validate_manager_root_receipt(
    receipt_path: pathlib.Path,
    fixture_root: pathlib.Path,
    manager_root: pathlib.Path,
    expected_stage: str,
) -> tuple[str, int | None, tuple[tuple[str, str, str, int, int], ...]]:
    if expected_stage not in {"before", "after"}:
        raise AssertionError("manager-root receipt stage is unsupported")
    root = fixture_root.resolve(strict=True)
    manager = manager_root.resolve(strict=True)
    expected = root / f"manager-root-{expected_stage}.tsv"
    if manager != root / "run-systemd" or manager.is_symlink():
        raise AssertionError("manager-root receipt uses an unexpected private root")
    if receipt_path != expected or receipt_path.parent != root:
        raise AssertionError("manager-root receipt is outside its exact fixture path")
    if receipt_path.is_symlink() or not receipt_path.is_file():
        raise AssertionError("manager-root receipt is missing or is not regular")
    raw = receipt_path.read_bytes()
    if not raw or len(raw) > PCP_MANAGER_ROOT_RECEIPT_MAX_BYTES:
        raise AssertionError("manager-root receipt size is missing or unbounded")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise AssertionError("manager-root receipt is not UTF-8") from exc
    if not text.isascii() or not text.endswith("\n") or "\r" in text:
        raise AssertionError("manager-root receipt encoding is not deterministic")
    if any(ord(character) < 32 and character not in {"\t", "\n"} for character in text):
        raise AssertionError("manager-root receipt contains a control character")
    if "\x7f" in text:
        raise AssertionError("manager-root receipt contains a control character")
    lines = text[:-1].split("\n")
    if not lines or not lines[0]:
        raise AssertionError("manager-root receipt header is missing")
    header = PCP_MANAGER_ROOT_HEADER.fullmatch(lines[0])
    if header is None:
        raise AssertionError("manager-root receipt header is malformed")
    stage, status_text = header.groups()
    if stage != expected_stage:
        raise AssertionError("manager-root receipt stage does not match its file")
    if stage == "before":
        if status_text != "-":
            raise AssertionError("before manager-root receipt has a status")
        condition_status: int | None = None
    else:
        if status_text == "-":
            raise AssertionError("after manager-root receipt lacks condition status")
        condition_status = int(status_text)
        if condition_status <= 0 or condition_status > 255:
            raise AssertionError("manager-root condition status is out of range")
    entry_lines = lines[1:]
    if len(entry_lines) > PCP_MANAGER_ROOT_RECEIPT_MAX_ENTRIES:
        raise AssertionError("manager-root receipt has too many entries")
    entries: list[tuple[str, str, str, int, int]] = []
    previous_path: bytes | None = None
    for index, line in enumerate(entry_lines, start=1):
        fields = line.split("\t")
        if len(fields) != 6 or fields[0] != "ENTRY":
            raise AssertionError(f"manager-root receipt entry {index} is malformed")
        relative_path, object_type, mode, uid_text, gid_text = fields[1:]
        path_bytes = relative_path.encode("ascii")
        if not path_bytes or len(path_bytes) > PCP_MANAGER_ROOT_PATH_MAX_BYTES:
            raise AssertionError("manager-root receipt path is missing or unbounded")
        if relative_path.startswith("/") or "\\" in relative_path:
            raise AssertionError(
                "manager-root receipt path is absolute or noncanonical"
            )
        components = relative_path.split("/")
        if len(components) > 5 or any(
            component in {"", ".", ".."}
            or PCP_MANAGER_ROOT_COMPONENT.fullmatch(component) is None
            for component in components
        ):
            raise AssertionError("manager-root receipt path is unsafe")
        if previous_path is not None and path_bytes <= previous_path:
            raise AssertionError("manager-root receipt paths are duplicate or unsorted")
        previous_path = path_bytes
        if object_type not in PCP_MANAGER_ROOT_TYPES:
            raise AssertionError("manager-root receipt object type is unknown")
        if re.fullmatch(r"[0-7]{3,4}", mode) is None:
            raise AssertionError("manager-root receipt mode is malformed")
        if not uid_text.isascii() or not uid_text.isdecimal():
            raise AssertionError("manager-root receipt UID is malformed")
        if not gid_text.isascii() or not gid_text.isdecimal():
            raise AssertionError("manager-root receipt GID is malformed")
        uid = int(uid_text)
        gid = int(gid_text)
        if uid > 2**32 - 1 or gid > 2**32 - 1:
            raise AssertionError("manager-root receipt ownership is out of range")
        entries.append((relative_path, object_type, mode, uid, gid))
    return text, condition_status, tuple(entries)


def _read_bounded_ascii_receipt(
    receipt_path: pathlib.Path,
    fixture_root: pathlib.Path,
    expected_name: str,
    maximum_bytes: int,
) -> str:
    root = fixture_root.resolve(strict=True)
    expected = root / expected_name
    if receipt_path != expected or receipt_path.parent != root:
        raise AssertionError("receipt is outside its exact fixture path")
    if receipt_path.is_symlink() or not receipt_path.is_file():
        raise AssertionError("receipt is missing or is not regular")
    raw = receipt_path.read_bytes()
    if not raw or len(raw) > maximum_bytes:
        raise AssertionError("receipt size is missing or unbounded")
    try:
        text = raw.decode("ascii")
    except UnicodeDecodeError as exc:
        raise AssertionError("receipt is not bounded ASCII") from exc
    if not text.endswith("\n") or "\r" in text:
        raise AssertionError("receipt encoding is not deterministic")
    if any(ord(character) < 32 and character not in {"\t", "\n"} for character in text):
        raise AssertionError("receipt contains a control character")
    if "\x7f" in text:
        raise AssertionError("receipt contains a control character")
    return text


def _validate_systemd_source_receipt(
    receipt_path: pathlib.Path,
    fixture_root: pathlib.Path,
) -> tuple[str, str, str]:
    text = _read_bounded_ascii_receipt(
        receipt_path,
        fixture_root,
        "systemd-source.tsv",
        PCP_SYSTEMD_SOURCE_RECEIPT_MAX_BYTES,
    )
    lines = text[:-1].split("\n")
    if len(lines) != 9 or lines[0] != "HSOURCE|1":
        raise AssertionError("systemd source receipt schema is incomplete")
    package = lines[1].split("\t")
    if len(package) != 4 or package[0:2] != ["PACKAGE", "systemd"]:
        raise AssertionError("systemd package identity is malformed")
    package_version, architecture = package[2:]
    if re.fullmatch(r"255\.4-[0-9A-Za-z.+:~]+", package_version) is None:
        raise AssertionError("systemd package version is not tied to upstream v255.4")
    if architecture != "amd64":
        raise AssertionError("systemd package architecture is unexpected")
    version = lines[2].split("\t")
    if len(version) != 3 or version[0] != "VERSION":
        raise AssertionError("systemd version receipt is malformed")
    if version[1] != f"systemd 255 ({package_version})":
        raise AssertionError("systemd executable and package versions diverge")
    if re.fullmatch(r"[0-9a-f]{64}", version[2]) is None:
        raise AssertionError("systemd version-output hash is malformed")
    executable = lines[3].split("\t")
    if len(executable) != 9 or executable[0:2] != [
        "EXECUTABLE",
        "/usr/bin/systemd-analyze",
    ]:
        raise AssertionError("systemd executable identity is malformed")
    executable_hash, mode, uid, gid, size, links, owner = executable[2:]
    if (
        re.fullmatch(r"[0-9a-f]{64}", executable_hash) is None
        or re.fullmatch(r"[0-7]{3,4}", mode) is None
        or uid != "0"
        or gid != "0"
        or not size.isdecimal()
        or int(size) <= 0
        or not links.isdecimal()
        or int(links) <= 0
        or owner != "systemd"
    ):
        raise AssertionError("systemd executable metadata is invalid")
    expected_lines = (
        (
            f"UPSTREAM\t{PCP_SYSTEMD_UPSTREAM_REPOSITORY}\t"
            f"{PCP_SYSTEMD_UPSTREAM_TAG}\t{PCP_SYSTEMD_UPSTREAM_REVISION}"
        ),
        (
            f"SOURCE\t{PCP_SYSTEMD_ANALYZE_SOURCE_PATH}\t"
            f"{PCP_SYSTEMD_ANALYZE_SOURCE_FUNCTION}\t"
            f"{PCP_SYSTEMD_ANALYZE_SOURCE_SHA256}"
        ),
        (
            f"SOURCE\t{PCP_SYSTEMD_MANAGER_SOURCE_PATH}\t"
            f"{PCP_SYSTEMD_MANAGER_SOURCE_FUNCTION}\t"
            f"{PCP_SYSTEMD_MANAGER_SOURCE_SHA256}"
        ),
        "CHAIN\tverb_condition>verify_conditions>manager_startup>manager_ready>touch_file",
        f"MARKER\t{PCP_SYSTEMD_MARKER_PATH}\tregular\t0444\tzero-length\tmanager-ready",
    )
    if tuple(lines[4:]) != expected_lines:
        raise AssertionError("systemd immutable upstream source identity diverges")
    return text, package_version, executable_hash


def _validate_systemd_causal_receipt(
    receipt_path: pathlib.Path,
    fixture_root: pathlib.Path,
) -> str:
    text = _read_bounded_ascii_receipt(
        receipt_path,
        fixture_root,
        "systemd-causal.tsv",
        PCP_SYSTEMD_CAUSAL_RECEIPT_MAX_BYTES,
    )
    expected = (
        "HCAUSE|1\n"
        "CONTROL\tnegative\tcommand=none\tstatus=-\tbefore=0\tafter=0\t"
        "manager_endpoints_before=0\tmanager_endpoints_after=0\tcleanup=removed\n"
        "CONTROL\tpositive\tcommand=systemd-analyze-condition\tstatus=1\t"
        "before=0\tafter=1\tmanager_endpoints_before=0\t"
        "manager_endpoints_after=0\tcleanup=removed\n"
        "MARKER\tsystemd-units-load\tregular\t444\t0\t0\t0\t1\t"
        "same-filesystem\n"
    )
    if text != expected:
        raise AssertionError("systemd causal receipt is malformed or incomplete")
    return text


def _extract_systemd_receipt_writer(shell: str, partial_variable: str) -> str:
    end_marker = f'}} >"${partial_variable}"'
    end = shell.find(end_marker)
    if end < 0 or shell.find(end_marker, end + 1) >= 0:
        raise AssertionError(
            f"receipt writer for {partial_variable} is missing or ambiguous"
        )
    start = shell.rfind("{\n", 0, end)
    if start < 0:
        raise AssertionError(
            f"receipt writer for {partial_variable} has no group start"
        )
    writer = shell[start : end + len(end_marker)]
    if writer.count("printf ") < 2 or "%b" in writer or "echo -e" in writer:
        raise AssertionError(f"receipt writer for {partial_variable} is not field-safe")
    return writer


class OfflineApplianceTests(unittest.TestCase):
    @staticmethod
    def _actual_install_fragment(payload: str) -> str:
        match = re.search(
            r'^chroot "\$target" apt-get "\$\{apt_options\[@\]\}" \\\n'
            r'    --yes [^\n]+ install "\$\{exact_roots\[@\]\}"$',
            payload,
            flags=re.MULTILINE,
        )
        if match is None:
            raise AssertionError("missing unique production actual-install command")
        if payload.count(match.group(0)) != 1:
            raise AssertionError("production actual-install command is ambiguous")
        return match.group(0)

    @classmethod
    def _assert_actual_install_contract(cls, payload: str) -> None:
        fragment = cls._actual_install_fragment(payload)
        expected_fragment = (
            'chroot "$target" apt-get "${apt_options[@]}" \\\n'
            '    --yes --no-install-recommends install "${exact_roots[@]}"'
        )
        if fragment != expected_fragment:
            raise AssertionError("production actual-install argv changed")
        required = (
            "sha256sum --check --strict SHA256SUMS",
            'cp -a -- "$source_repo" "$retained_repo"',
            "deb [signed-by=/usr/share/keyrings/hoardarr-offline-archive-keyring.gpg] file:/opt/hoardarr/offline-repository noble main",
            "*.hoardarr-online-disabled",
            '-o "Dir::Etc::sourcelist=/etc/apt/sources.list.d/hoardarr-offline.list"',
            '-o "Dir::Etc::sourceparts=-"',
            '-o "Acquire::Retries=0"',
            '-o "Acquire::http::Proxy=false"',
            '-o "Acquire::https::Proxy=false"',
            "policy-rc.d",
            "export SYSTEMD_OFFLINE=1",
            "package_transaction_started=true",
            "service_readback_complete=true",
            "Failed to preset unit",
            "AUTO -all",
            'global_filter = [ "r|.*|" ]',
            'devnode ".*"',
            'mapfile -t exact_roots <"$retained_repo/evidence/root-package-versions.txt"',
            'simulation="$(chroot "$target" apt-get "${apt_options[@]}" --simulate --no-install-recommends install "${exact_roots[@]}")"',
            'chroot "$target" apt-get "${apt_options[@]}" --simulate check',
            "package-readback.json",
            "service-policy-readback.json",
            "service-retained-guards.json",
        )
        for value in required:
            if value not in payload:
                raise AssertionError(f"missing offline install safeguard: {value}")
        lowered = payload.lower()
        for forbidden in (
            "trusted=yes",
            "allow-unauthenticated",
            "allowinsecurerepositories=true",
        ):
            if forbidden in lowered:
                raise AssertionError(f"signature safeguard weakened: {forbidden}")
        if "http://" in payload or "https://" in payload:
            raise AssertionError("network source introduced into offline payload")
        if "--no-download" in fragment:
            raise AssertionError(
                "actual install cannot acquire from the file repository"
            )

    @staticmethod
    def _assert_runtime_mount_contract(payload: str) -> None:
        required = (
            "exec {parent_namespace_fd}< /proc/self/ns/mnt",
            "unshare --mount --propagation private",
            "--hoardarr-private-mount-namespace",
            "unset HOARDARR_OFFLINE_PRIVATE_MOUNT_NAMESPACE HOARDARR_OFFLINE_PARENT_MOUNT_NAMESPACE",
            'current_mount_namespace="$(readlink -- /proc/self/ns/mnt)"',
            'open("/proc/self/mountinfo", encoding="utf-8")',
            "runtime_mount_paths=(proc sys dev dev/pts run)",
            "runtime_mount_sources=(/proc /sys /dev /dev/pts /run)",
            'mount --bind -- "$source" "$destination"',
            'if mount --bind -- "$source" "$destination"; then',
            'runtime_mount_ids["$destination"]="$mount_id"',
            'mount --make-private -- "$destination"',
            'if mount --make-private -- "$destination"; then',
            'prepare_runtime_mounts_failure "$bind_status"',
            'prepare_runtime_mounts_failure "$propagation_status"',
            'rollback_just_attempted_runtime_mount "$destination"',
            "if ! printf 'mount_id\\tparent_id\\tmajor_minor",
            'if ! sync -f "$runtime_mount_record"',
            'umount -- "$destination"',
            "cleanup_runtime_mounts || cleanup_status=1",
            "trap 'exit_cleanup $?' EXIT",
            "trap 'signal_exit 143' TERM",
            "prepare_runtime_mounts",
            "cleanup_service_guards",
            "disable_unmasked_units",
            "export SYSTEMD_OFFLINE=1",
            "cleanup_runtime_mounts",
            "cleanup_guard 0",
            "trap - EXIT HUP INT TERM",
        )
        for value in required:
            if value not in payload:
                raise AssertionError(f"missing runtime mount safeguard: {value}")
        exact_counts = {
            'if mount --bind -- "$source" "$destination"; then': 1,
            'if mount --make-private -- "$destination"; then': 1,
            'prepare_runtime_mounts_failure "$bind_status"': 2,
            'prepare_runtime_mounts_failure "$propagation_status"': 1,
            'rollback_just_attempted_runtime_mount "$destination"': 4,
        }
        for value, expected in exact_counts.items():
            if payload.count(value) != expected:
                raise AssertionError(
                    f"runtime mutation handling count changed: {value}"
                )
        for forbidden in ("mount --rbind", "umount -l", "umount --lazy"):
            if forbidden in payload:
                raise AssertionError(f"unsafe runtime mount operation: {forbidden}")
        prepare = payload.rindex("\nprepare_runtime_mounts\n")
        first_chroot = payload.index('chroot "$target" apt-get', prepare)
        service_cleanup = payload.rindex("\ncleanup_service_guards\n")
        disable = payload.rindex("\ndisable_unmasked_units\n")
        runtime_cleanup = payload.rindex("\ncleanup_runtime_mounts\n")
        success = payload.rindex(
            'echo "Hoardarr offline package payload installed and verified."'
        )
        if not (
            prepare
            < first_chroot
            < disable
            < service_cleanup
            < runtime_cleanup
            < success
        ):
            raise AssertionError("runtime mount lifecycle ordering changed")

    def test_target_runtime_mount_contract_rejects_mutations(self) -> None:
        payload = (
            ROOT / "packaging" / "appliance" / "install-offline-payload.sh"
        ).read_text(encoding="utf-8")
        self._assert_runtime_mount_contract(payload)
        mutations = {
            "caller sentinel trusted": payload.replace(
                "unset HOARDARR_OFFLINE_PRIVATE_MOUNT_NAMESPACE HOARDARR_OFFLINE_PARENT_MOUNT_NAMESPACE",
                ":",
            ),
            "no private namespace": payload.replace(
                "unshare --mount --propagation private",
                "unshare --mount --propagation unchanged",
            ),
            "mountinfo removed": payload.replace(
                'open("/proc/self/mountinfo", encoding="utf-8")',
                'open("/etc/mtab", encoding="utf-8")',
            ),
            "runtime path missing": payload.replace(
                "runtime_mount_paths=(proc sys dev dev/pts run)",
                "runtime_mount_paths=(proc sys dev run)",
            ),
            "ID not recorded": payload.replace(
                'runtime_mount_ids["$destination"]="$mount_id"',
                ":",
                1,
            ),
            "propagation not isolated": payload.replace(
                'if mount --make-private -- "$destination"; then',
                ":",
            ),
            "bind status not checked": payload.replace(
                'if mount --bind -- "$source" "$destination"; then',
                'mount --bind -- "$source" "$destination"\n        if true; then',
            ),
            "bind failure cleanup missing": payload.replace(
                'prepare_runtime_mounts_failure "$bind_status"',
                "return 1",
                1,
            ),
            "ambiguous bind rollback missing": payload.replace(
                'rollback_just_attempted_runtime_mount "$destination"',
                "false",
            ),
            "lazy cleanup": payload.replace(
                'umount -- "$destination"',
                'umount --lazy -- "$destination"',
                1,
            ),
            "EXIT cleanup missing": payload.replace(
                "trap 'exit_cleanup $?' EXIT",
                ":",
            ),
            "TERM cleanup missing": payload.replace(
                "trap 'signal_exit 143' TERM",
                ":",
            ),
        }
        for name, mutation in mutations.items():
            with self.subTest(name=name), self.assertRaises(AssertionError):
                self._assert_runtime_mount_contract(mutation)

    @unittest.skipUnless(sys.platform.startswith("linux"), "requires Linux mounts")
    def test_target_runtime_mount_lifecycle_and_package_postinst(self) -> None:
        payload = (
            ROOT / "packaging" / "appliance" / "install-offline-payload.sh"
        ).read_text(encoding="utf-8")
        self._assert_runtime_mount_contract(payload)

        def shell_function(name: str) -> str:
            match = re.search(
                rf"^{re.escape(name)}\(\) \{{\n.*?^\}}\n",
                payload,
                flags=re.MULTILINE | re.DOTALL,
            )
            self.assertIsNotNone(match, f"missing production function {name}")
            assert match is not None
            return match.group(0)

        required = (
            "bash",
            "dpkg",
            "dpkg-deb",
            "mount",
            "sudo",
            "umount",
            "unshare",
        )
        missing = [command for command in required if shutil.which(command) is None]
        self.assertEqual(missing, [], f"missing runtime integration tools: {missing}")
        sudo = subprocess.run(
            ["sudo", "-n", "true"], text=True, capture_output=True, check=False
        )
        self.assertEqual(sudo.returncode, 0, sudo.stderr)
        names = (
            "mountinfo_exact_record",
            "runtime_path_is_safe",
            "runtime_record_field",
            "prepare_runtime_mounts_failure",
            "runtime_mount_matches_source",
            "runtime_mount_path_is_absent",
            "rollback_just_attempted_runtime_mount",
            "prepare_runtime_mounts",
            "cleanup_runtime_mounts",
            "cleanup_guard",
            "exit_cleanup",
            "signal_exit",
        )
        fragment = "\n".join(
            (
                "runtime_mount_paths=(proc sys dev dev/pts run)",
                "runtime_mount_sources=(/proc /sys /dev /dev/pts /run)",
                "created_runtime_mounts=()",
                "declare -A runtime_mount_ids=()",
                "declare -A runtime_mount_records=()",
                *(shell_function(name) for name in names),
            )
        )
        with tempfile.TemporaryDirectory() as temporary:
            temporary_root = pathlib.Path(temporary)
            wrapper_root = temporary_root / "bin"
            wrapper_root.mkdir()
            launcher_marker = temporary_root / "unshare-invoked"
            real_unshare = pathlib.Path(shutil.which("unshare") or "")
            unshare_wrapper = wrapper_root / "unshare"
            unshare_wrapper.write_text(
                "#!/bin/sh\n"
                f"printf '%s\\n' invoked >'{launcher_marker}'\n"
                f"exec '{real_unshare}' \"$@\"\n",
                encoding="utf-8",
                newline="\n",
            )
            unshare_wrapper.chmod(0o755)
            launcher_target = temporary_root / "launcher-target"
            launcher_repository = temporary_root / "launcher-repository"
            launcher_target.mkdir()
            launcher_repository.mkdir()
            launcher_payload = temporary_root / "install-offline-payload.sh"
            shutil.copyfile(
                ROOT / "packaging" / "appliance" / "install-offline-payload.sh",
                launcher_payload,
            )
            launcher_payload.chmod(0o755)
            launcher = subprocess.run(
                [
                    "sudo",
                    "-n",
                    "env",
                    f"PATH={wrapper_root}:{os.environ.get('PATH', '')}",
                    "HOARDARR_OFFLINE_PRIVATE_MOUNT_NAMESPACE=1",
                    "HOARDARR_OFFLINE_PARENT_MOUNT_NAMESPACE=mnt:[1]",
                    str(launcher_payload),
                    str(launcher_target),
                    str(launcher_repository),
                ],
                text=True,
                capture_output=True,
                check=False,
                timeout=30,
            )
            self.assertNotEqual(launcher.returncode, 0)
            self.assertTrue(
                launcher_marker.is_file(),
                "preseeded variables bypassed the production unshare launcher",
            )
            self.assertIn(
                "offline payload target must be the real /target directory",
                launcher.stderr,
            )
            fragment_path = pathlib.Path(temporary) / "production-runtime-functions.sh"
            fragment_path.write_text(fragment, encoding="utf-8", newline="\n")
            result = subprocess.run(
                [
                    "sudo",
                    "-n",
                    "bash",
                    str(
                        ROOT
                        / "tests"
                        / "appliance"
                        / "test-target-chroot-runtime-mounts.sh"
                    ),
                    str(fragment_path),
                ],
                text=True,
                capture_output=True,
                check=False,
                timeout=240,
            )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("private_namespace_containment=true", result.stdout)
        self.assertIn("postinst_runtime_probe=passed", result.stdout)
        self.assertIn("partial_and_signal_cleanup=passed", result.stdout)

    def test_offline_service_masks_are_classified_and_cleaned_executably(self) -> None:
        payload = (
            ROOT / "packaging" / "appliance" / "install-offline-payload.sh"
        ).read_text(encoding="utf-8")

        def shell_function(name: str) -> str:
            match = re.search(
                rf"^{re.escape(name)}\(\) \{{\n.*?^\}}\n",
                payload,
                flags=re.MULTILINE | re.DOTALL,
            )
            self.assertIsNotNone(match, f"missing production function {name}")
            assert match is not None
            return match.group(0)

        if sys.platform == "win32":
            candidates = (
                pathlib.Path(r"C:\Program Files\Git\bin\bash.exe"),
                pathlib.Path(r"C:\msys64\usr\bin\bash.exe"),
            )
            bash = next((str(path) for path in candidates if path.is_file()), None)
        else:
            bash = shutil.which("bash")
        self.assertIsNotNone(bash, "Bash is required for executable mask regression")
        assert bash is not None

        script = "\n".join(
            (
                "set -euo pipefail",
                "temporary_masks=()",
                "declare -A temporary_mask_inodes=()",
                "temporary_masks_cleanup_complete=false",
                "policy_cleanup_complete=false",
                "service_guard_cleanup_complete=false",
                "package_transaction_started=false",
                "denied_units_finalized=false",
                "service_readback_complete=false",
                "created_runtime_mounts=()",
                "declare -A runtime_mount_ids=()",
                "declare -A runtime_mount_records=()",
                "declare -A preserved_unit_masks=()",
                "declare -A preserved_unit_mask_inodes=()",
                "declare -A preserved_package_aliases=()",
                "declare -A preserved_package_alias_inodes=()",
                "declare -A preserved_package_alias_targets=()",
                "declare -A preserved_package_alias_canonical_units=()",
                "declare -A policy_guarded_canonical_units=()",
                "declare -A policy_guarded_absent_units=()",
                "recovery_guard_files=()",
                "recovery_guard_created_directories=()",
                "declare -A recovery_guard_file_inodes=()",
                "declare -A recovery_guard_contents=()",
                "declare -A recovery_guard_condition_paths=()",
                "declare -A recovery_guard_directory_inodes=()",
                "declare -A recovery_guard_paths_by_unit=()",
                "declare -A recovery_guard_path_owners=()",
                "declare -A recovery_guard_paths_retained=()",
                "declare -A recovery_guard_retained_states=()",
                "recovery_guards_cleanup_complete=false",
                "recovery_guard_authorization_root=",
                shell_function("entry_is_root_owned"),
                shell_function("exact_iscsi_alias_parents_are_safe"),
                shell_function("unit_declares_exact_alias"),
                shell_function("is_exact_package_backed_iscsi_alias"),
                shell_function("record_package_backed_iscsi_alias"),
                shell_function("validate_preserved_unit_objects"),
                shell_function("prepare_recovery_unit_guard"),
                shell_function("validate_recovery_unit_guards"),
                shell_function("retain_recovery_unit_guards"),
                shell_function("remove_recovery_unit_guards"),
                shell_function("prepare_temporary_unit_mask"),
                shell_function("cleanup_temporary_masks"),
                shell_function("remove_denied_unit_enablement_links"),
                shell_function("disable_unmasked_units"),
                "reset_tracking() {",
                "  temporary_masks=()",
                "  temporary_mask_inodes=()",
                "  preserved_unit_masks=()",
                "  preserved_unit_mask_inodes=()",
                "  preserved_package_aliases=()",
                "  preserved_package_alias_inodes=()",
                "  preserved_package_alias_targets=()",
                "  preserved_package_alias_canonical_units=()",
                "  policy_guarded_canonical_units=()",
                "  policy_guarded_absent_units=()",
                "  recovery_guard_files=()",
                "  recovery_guard_created_directories=()",
                "  recovery_guard_file_inodes=()",
                "  recovery_guard_contents=()",
                "  recovery_guard_condition_paths=()",
                "  recovery_guard_directory_inodes=()",
                "  recovery_guard_paths_by_unit=()",
                "  recovery_guard_path_owners=()",
                "  recovery_guard_paths_retained=()",
                "  recovery_guard_retained_states=()",
                "  recovery_guards_cleanup_complete=false",
                "  denied_units_finalized=false",
                "}",
                'root="$1"',
                'if command -v cygpath >/dev/null 2>&1; then root="$(cygpath -u -- "$root")"; fi',
                'mkdir -p -- "$root"',
                'target="$root/no-package-root"',
                'mask_root="$target/etc/systemd/system"',
                "",
                "# Newly absent units remain absent so package preset bookkeeping works.",
                'absent="$root/absent/iscsi.service"',
                'mkdir -p -- "$(dirname -- "$absent")"',
                "reset_tracking",
                'prepare_temporary_unit_mask "$absent" iscsi.service',
                '[[ ! -e "$absent" && ! -L "$absent" ]]',
                '[[ "${policy_guarded_absent_units[iscsi.service]}" == "$absent" ]]',
                "cleanup_temporary_masks",
                '[[ ! -e "$absent" && ! -L "$absent" ]]',
                'later="$root/later-failure/iscsi.service"',
                'mkdir -p -- "$(dirname -- "$later")"',
                "later_status=0",
                "(",
                "  reset_tracking",
                "  trap cleanup_temporary_masks EXIT",
                '  prepare_temporary_unit_mask "$later" iscsi.service',
                "  exit 79",
                ") || later_status=$?",
                '[[ "$later_status" -eq 79 && ! -e "$later" && ! -L "$later" ]]',
                "",
                "# A pre-existing exact absolute mask is never tracked or recreated.",
                'safe="$root/safe/iscsi.service"',
                'mkdir -p -- "$(dirname -- "$safe")"',
                'ln -s -- /dev/null "$safe"',
                'safe_inode="$(stat -c %i -- "$safe")"',
                "reset_tracking",
                'prepare_temporary_unit_mask "$safe" iscsi.service',
                '[[ "${#temporary_masks[@]}" -eq 0 ]]',
                '[[ "${preserved_unit_masks[iscsi.service]}" == "$safe" ]]',
                "cleanup_temporary_masks",
                '[[ -L "$safe" && "$(readlink -- "$safe")" == /dev/null ]]',
                '[[ "$(stat -c %i -- "$safe")" == "$safe_inode" ]]',
                'safe_failure="$root/safe-failure/iscsi.service"',
                'mkdir -p -- "$(dirname -- "$safe_failure")"',
                'ln -s -- /dev/null "$safe_failure"',
                'safe_failure_inode="$(stat -c %i -- "$safe_failure")"',
                "safe_status=0",
                "(",
                "  reset_tracking",
                "  trap cleanup_temporary_masks EXIT",
                '  prepare_temporary_unit_mask "$safe_failure" iscsi.service',
                "  exit 81",
                ") || safe_status=$?",
                '[[ "$safe_status" -eq 81 ]]',
                '[[ -L "$safe_failure" && "$(readlink -- "$safe_failure")" == /dev/null ]]',
                '[[ "$(stat -c %i -- "$safe_failure")" == "$safe_failure_inode" ]]',
                "",
                "# Every other pre-existing object is rejected without modification.",
                'regular="$root/reject-regular/iscsi.service"',
                'mkdir -p -- "$(dirname -- "$regular")"',
                'printf preserved >"$regular"',
                'regular_sha="$(sha256sum "$regular")"',
                'if prepare_temporary_unit_mask "$regular" iscsi.service; then exit 91; fi',
                '[[ "$(sha256sum "$regular")" == "$regular_sha" ]]',
                'directory="$root/reject-directory/iscsi.service"',
                'mkdir -p -- "$directory"',
                'printf marker >"$directory/preserved"',
                'if prepare_temporary_unit_mask "$directory" iscsi.service; then exit 92; fi',
                '[[ "$(cat "$directory/preserved")" == marker ]]',
                'target="$root/ordinary-target"',
                'printf target >"$target"',
                'ordinary="$root/reject-symlink/iscsi.service"',
                'mkdir -p -- "$(dirname -- "$ordinary")"',
                'ln -s -- "$target" "$ordinary"',
                'ordinary_inode="$(stat -c %i -- "$ordinary")"',
                'if prepare_temporary_unit_mask "$ordinary" iscsi.service; then exit 93; fi',
                '[[ "$(readlink -- "$ordinary")" == "$target" ]]',
                '[[ "$(stat -c %i -- "$ordinary")" == "$ordinary_inode" ]]',
                'dangling="$root/reject-dangling/iscsi.service"',
                'mkdir -p -- "$(dirname -- "$dangling")"',
                'ln -s -- /does/not/exist "$dangling"',
                'if prepare_temporary_unit_mask "$dangling" iscsi.service; then exit 94; fi',
                '[[ "$(readlink -- "$dangling")" == /does/not/exist ]]',
                'relative="$root/reject-relative/iscsi.service"',
                'mkdir -p -- "$(dirname -- "$relative")"',
                'ln -s -- ../../dev/null "$relative"',
                'if prepare_temporary_unit_mask "$relative" iscsi.service; then exit 95; fi',
                '[[ "$(readlink -- "$relative")" == ../../dev/null ]]',
                "",
                "# Mixed ownership cleanup preserves existing and leaves absent units absent.",
                'mixed_safe="$root/mixed/iscsi.service"',
                'mixed_new="$root/mixed/iscsid.service"',
                'mkdir -p -- "$(dirname -- "$mixed_safe")"',
                'ln -s -- /dev/null "$mixed_safe"',
                'mixed_inode="$(stat -c %i -- "$mixed_safe")"',
                "reset_tracking",
                'prepare_temporary_unit_mask "$mixed_safe" iscsi.service',
                'prepare_temporary_unit_mask "$mixed_new" iscsid.service',
                '[[ "${#temporary_masks[@]}" -eq 0 ]]',
                '[[ "${policy_guarded_absent_units[iscsid.service]}" == "$mixed_new" ]]',
                "cleanup_temporary_masks",
                '[[ -L "$mixed_safe" && "$(readlink -- "$mixed_safe")" == /dev/null ]]',
                '[[ "$(stat -c %i -- "$mixed_safe")" == "$mixed_inode" ]]',
                '[[ ! -e "$mixed_new" && ! -L "$mixed_new" ]]',
                "",
                "# Final disable preserves the accepted mask and validates the absent unit.",
                'lifecycle_safe="$root/lifecycle/iscsi.service"',
                'lifecycle_new="$root/lifecycle/iscsid.service"',
                'mkdir -p -- "$(dirname -- "$lifecycle_safe")"',
                'ln -s -- /dev/null "$lifecycle_safe"',
                'lifecycle_inode="$(stat -c %i -- "$lifecycle_safe")"',
                'disable_log="$root/disable.log"',
                "reset_tracking",
                'prepare_temporary_unit_mask "$lifecycle_safe" iscsi.service',
                'prepare_temporary_unit_mask "$lifecycle_new" iscsid.service',
                "denied_units=(iscsi.service iscsid.service)",
                'target="$root/target"',
                'state_root="$root/state"',
                'mkdir -p -- "$state_root"',
                "systemctl() {",
                '  printf \'%s\\n\' "$*" >>"$disable_log"',
                '  if [[ "$*" == *" is-enabled "* ]]; then',
                "    if [[ \"${*: -1}\" == iscsi.service ]]; then printf '%s\\n' masked; else printf '%s\\n' not-found; fi",
                "    return 1",
                "  fi",
                "  return 0",
                "}",
                "chroot() {",
                "  printf 'unexpected offline manager query: %s\\n' \"$*\" >&2",
                "  return 220",
                "}",
                "disable_unmasked_units",
                '[[ -L "$lifecycle_safe" && "$(readlink -- "$lifecycle_safe")" == /dev/null ]]',
                '[[ "$(stat -c %i -- "$lifecycle_safe")" == "$lifecycle_inode" ]]',
                '[[ ! -e "$lifecycle_new" && ! -L "$lifecycle_new" ]]',
                'grep -Fq -- "--root=$target disable iscsid.service" "$disable_log"',
                '! grep -Fq -- "is-active" "$disable_log"',
                '[[ "$(wc -l <"$state_root/service-policy-readback.tsv")" -eq 2 ]]',
                "awk -F '\\t' '",
                '  NF != 6 || $4 != "not-queried-offline" || $5 != -1 { exit 1 }',
                '\' "$state_root/service-policy-readback.tsv"',
                '[[ "$denied_units_finalized" == true ]]',
                "",
                "# A cleanup success cannot replace the mandatory fresh readback.",
                "reset_tracking",
                "denied_units=(iscsid.service)",
                'target="$root/still-enabled-target"',
                'state_root="$root/still-enabled-state"',
                'mkdir -p -- "$state_root"',
                "remove_denied_unit_enablement_links() { return 0; }",
                "systemctl() {",
                '  if [[ "$*" == *" is-enabled "* ]]; then printf \'%s\\n\' enabled; return 0; fi',
                "  return 1",
                "}",
                "if disable_unmasked_units; then exit 96; fi",
                '[[ ! -e "$state_root/service-policy-readback.tsv" ]]',
            )
        )
        with tempfile.TemporaryDirectory() as temporary:
            script_path = pathlib.Path(temporary) / "mask-regression.sh"
            script_path.write_text(script, encoding="utf-8", newline="\n")
            result = subprocess.run(
                [bash, str(script_path), temporary],
                capture_output=True,
                check=False,
                env={**os.environ, "MSYS": "winsymlinks:sys"},
                text=True,
            )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("command not found", result.stderr)

    @unittest.skipUnless(
        sys.platform.startswith("linux") and shutil.which("bash"),
        "requires Linux symlink and directory permission semantics",
    )
    def test_denied_unit_enablement_cleanup_is_exact_and_fail_closed(self) -> None:
        payload = (
            ROOT / "packaging" / "appliance" / "install-offline-payload.sh"
        ).read_text(encoding="utf-8")
        match = re.search(
            r"^remove_denied_unit_enablement_links\(\) \{\n.*?^\}\n",
            payload,
            flags=re.MULTILINE | re.DOTALL,
        )
        self.assertIsNotNone(match)
        assert match is not None
        cleanup_function = match.group(0)
        self.assertNotIn("find ", cleanup_function)
        self.assertNotIn("rm ", cleanup_function)
        self.assertNotIn("glob", cleanup_function)

        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary).resolve()
            script = root / "cleanup.sh"
            script.write_text(
                "set -euo pipefail\n"
                + cleanup_function
                + '\ntarget="$1"\nremove_denied_unit_enablement_links "$2"\n',
                encoding="utf-8",
                newline="\n",
            )
            bash = shutil.which("bash") or "bash"
            syntax = subprocess.run(
                [bash, "-n", str(script)], capture_output=True, text=True, check=False
            )
            self.assertEqual(syntax.returncode, 0, syntax.stderr)

            def prepare(label: str) -> tuple[pathlib.Path, pathlib.Path]:
                target = root / label / "target"
                vendor = target / "usr/lib/systemd/system"
                wants = target / "etc/systemd/system/multi-user.target.wants"
                vendor.mkdir(parents=True)
                wants.mkdir(parents=True)
                (vendor / "fixture.service").write_text(
                    "[Unit]\nDescription=fixture\n", encoding="ascii"
                )
                return target, wants / "fixture.service"

            def invoke(target: pathlib.Path) -> subprocess.CompletedProcess[str]:
                return subprocess.run(
                    [bash, str(script), str(target), "fixture.service"],
                    capture_output=True,
                    text=True,
                    check=False,
                )

            target, candidate = prepare("exact")
            candidate.symlink_to("/usr/lib/systemd/system/fixture.service")
            result = invoke(target)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(os.path.lexists(candidate))

            target, candidate = prepare("validated-alias")
            vendor = target / "usr/lib/systemd/system"
            canonical = vendor / "canonical.service"
            canonical.write_text("[Unit]\nDescription=canonical\n", encoding="ascii")
            (vendor / "fixture.service").unlink()
            (vendor / "fixture.service").symlink_to("canonical.service")
            candidate.symlink_to("/usr/lib/systemd/system/fixture.service")
            result = invoke(target)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(os.path.lexists(candidate))
            self.assertTrue((vendor / "fixture.service").is_symlink())

            target, candidate = prepare("wrong-target")
            wrong = target / "usr/lib/systemd/system/wrong.service"
            wrong.write_text("[Unit]\nDescription=wrong\n", encoding="ascii")
            candidate.symlink_to("/usr/lib/systemd/system/wrong.service")
            result = invoke(target)
            self.assertNotEqual(result.returncode, 0)
            self.assertTrue(candidate.is_symlink())

            target, candidate = prepare("non-link")
            candidate.write_text("preserved\n", encoding="ascii")
            before = candidate.read_bytes()
            result = invoke(target)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(candidate.read_bytes(), before)

            target, candidate = prepare("alias-mismatch")
            vendor = target / "usr/lib/systemd/system"
            wrong = vendor / "wrong.service"
            wrong.write_text("[Unit]\nDescription=wrong\n", encoding="ascii")
            alias = vendor / "wrong-alias.service"
            alias.symlink_to("wrong.service")
            candidate.symlink_to("/usr/lib/systemd/system/wrong-alias.service")
            result = invoke(target)
            self.assertNotEqual(result.returncode, 0)
            self.assertTrue(candidate.is_symlink())

            target, candidate = prepare("outside-root")
            wants = candidate.parent
            wants.rmdir()
            outside = root / "outside-enablement-parent"
            outside.mkdir()
            outside_candidate = outside / "fixture.service"
            outside_candidate.symlink_to("/usr/lib/systemd/system/fixture.service")
            wants.symlink_to(outside, target_is_directory=True)
            result = invoke(target)
            self.assertNotEqual(result.returncode, 0)
            self.assertTrue(outside_candidate.is_symlink())

            target = root / "symlink-configuration-root" / "target"
            vendor = target / "usr/lib/systemd/system"
            redirected = target / "redirected-systemd"
            wants = redirected / "multi-user.target.wants"
            vendor.mkdir(parents=True)
            wants.mkdir(parents=True)
            (vendor / "fixture.service").write_text(
                "[Unit]\nDescription=fixture\n", encoding="ascii"
            )
            redirected_candidate = wants / "fixture.service"
            redirected_candidate.symlink_to("/usr/lib/systemd/system/fixture.service")
            configuration_root = target / "etc/systemd/system"
            configuration_root.parent.mkdir(parents=True)
            configuration_root.symlink_to(redirected, target_is_directory=True)
            redirected_before = redirected_candidate.lstat()
            redirected_target = os.readlink(redirected_candidate)
            result = invoke(target)
            self.assertNotEqual(result.returncode, 0)
            redirected_after = redirected_candidate.lstat()
            self.assertEqual(
                (redirected_after.st_dev, redirected_after.st_ino),
                (redirected_before.st_dev, redirected_before.st_ino),
            )
            self.assertEqual(os.readlink(redirected_candidate), redirected_target)

            target, first_candidate = prepare("prevalidate-all-candidates")
            first_candidate.symlink_to("/usr/lib/systemd/system/fixture.service")
            later_parent = target / "etc/systemd/system/z-last.target.wants"
            later_parent.mkdir()
            later_candidate = later_parent / "fixture.service"
            later_candidate.write_text("preserved\n", encoding="ascii")
            first_before = first_candidate.lstat()
            first_target = os.readlink(first_candidate)
            later_before = later_candidate.lstat()
            later_bytes = later_candidate.read_bytes()
            result = invoke(target)
            self.assertNotEqual(result.returncode, 0)
            first_after = first_candidate.lstat()
            later_after = later_candidate.lstat()
            self.assertEqual(
                (first_after.st_dev, first_after.st_ino),
                (first_before.st_dev, first_before.st_ino),
            )
            self.assertEqual(os.readlink(first_candidate), first_target)
            self.assertEqual(
                (later_after.st_dev, later_after.st_ino),
                (later_before.st_dev, later_before.st_ino),
            )
            self.assertEqual(later_candidate.read_bytes(), later_bytes)

            if os.geteuid() != 0:
                target, candidate = prepare("failed-removal")
                candidate.symlink_to("/usr/lib/systemd/system/fixture.service")
                candidate.parent.chmod(0o500)
                try:
                    result = invoke(target)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertTrue(candidate.is_symlink())
                finally:
                    candidate.parent.chmod(0o700)

    def test_package_backed_iscsi_alias_lifecycle_is_fail_closed(self) -> None:
        payload = (
            ROOT / "packaging" / "appliance" / "install-offline-payload.sh"
        ).read_text(encoding="utf-8")

        def shell_function(name: str) -> str:
            match = re.search(
                rf"^{re.escape(name)}\(\) \{{\n.*?^\}}\n",
                payload,
                flags=re.MULTILINE | re.DOTALL,
            )
            self.assertIsNotNone(match, f"missing production function {name}")
            assert match is not None
            return match.group(0)

        if sys.platform == "win32":
            candidates = (
                pathlib.Path(r"C:\Program Files\Git\bin\bash.exe"),
                pathlib.Path(r"C:\msys64\usr\bin\bash.exe"),
            )
            bash = next((str(path) for path in candidates if path.is_file()), None)
        else:
            bash = shutil.which("bash")
        self.assertIsNotNone(bash, "Bash is required for executable alias regression")
        assert bash is not None

        script = "\n".join(
            (
                "set -euo pipefail",
                "temporary_masks=()",
                "declare -A temporary_mask_inodes=()",
                "temporary_masks_cleanup_complete=false",
                "policy_cleanup_complete=false",
                "service_guard_cleanup_complete=false",
                "package_transaction_started=false",
                "denied_units_finalized=false",
                "service_readback_complete=false",
                "created_runtime_mounts=()",
                "declare -A runtime_mount_ids=()",
                "declare -A runtime_mount_records=()",
                "declare -A preserved_unit_masks=()",
                "declare -A preserved_unit_mask_inodes=()",
                "declare -A preserved_package_aliases=()",
                "declare -A preserved_package_alias_inodes=()",
                "declare -A preserved_package_alias_targets=()",
                "declare -A preserved_package_alias_canonical_units=()",
                "declare -A policy_guarded_canonical_units=()",
                "declare -A policy_guarded_absent_units=()",
                "recovery_guard_files=()",
                "recovery_guard_created_directories=()",
                "declare -A recovery_guard_file_inodes=()",
                "declare -A recovery_guard_contents=()",
                "declare -A recovery_guard_condition_paths=()",
                "declare -A recovery_guard_directory_inodes=()",
                "declare -A recovery_guard_paths_by_unit=()",
                "declare -A recovery_guard_path_owners=()",
                "declare -A recovery_guard_paths_retained=()",
                "declare -A recovery_guard_retained_states=()",
                "recovery_guards_cleanup_complete=false",
                "recovery_guard_authorization_root=",
                shell_function("install_service_start_guard"),
                shell_function("entry_is_root_owned"),
                shell_function("exact_iscsi_alias_parents_are_safe"),
                shell_function("unit_declares_exact_alias"),
                shell_function("is_exact_package_backed_iscsi_alias"),
                shell_function("record_package_backed_iscsi_alias"),
                shell_function("validate_preserved_unit_objects"),
                shell_function("prepare_recovery_unit_guard"),
                shell_function("validate_recovery_unit_guards"),
                shell_function("retain_recovery_unit_guards"),
                shell_function("remove_recovery_unit_guards"),
                shell_function("prepare_temporary_unit_mask"),
                shell_function("cleanup_temporary_masks"),
                shell_function("cleanup_runtime_mounts"),
                shell_function("cleanup_service_guards"),
                shell_function("cleanup_guard"),
                shell_function("exit_cleanup"),
                shell_function("signal_exit"),
                shell_function("remove_denied_unit_enablement_links"),
                shell_function("disable_unmasked_units"),
                r"""
root="$1"
if command -v cygpath >/dev/null 2>&1; then root="$(cygpath -u -- "$root")"; fi
mkdir -p -- "$root"
real_stat="$(command -v stat)"
non_root_path=
fail_inode_path=
package_metadata_mode=ok
stat() {
    local format="${2:-}"
    local path="${*: -1}"
    if [[ "${1:-}" == -c && "$format" == %u:%g ]]; then
        if [[ -n "$non_root_path" && "$path" == "$non_root_path" ]]; then
            printf '%s\n' 1000:1000
        else
            printf '%s\n' 0:0
        fi
        return 0
    fi
    if [[ "${1:-}" == -c && "$format" == %i && -n "$fail_inode_path" && \
        "$path" == "$fail_inode_path" ]]; then
        return 1
    fi
    "$real_stat" "$@"
}
dpkg-query() {
    if [[ " $* " == *" -W "* ]]; then
        case "$package_metadata_mode" in
            ok|wrong-owner) printf 'installed\topen-iscsi\n' ;;
            wrong-package) printf 'installed\tother-package\n' ;;
            malformed) printf 'not-a-valid-status\n' ;;
            missing) return 1 ;;
        esac
        return 0
    fi
    if [[ " $* " == *" -S "* ]]; then
        case "$package_metadata_mode" in
            ok|wrong-package) printf 'open-iscsi: /usr/lib/systemd/system/open-iscsi.service\n' ;;
            wrong-owner) printf 'other-package: /usr/lib/systemd/system/open-iscsi.service\n' ;;
            malformed) printf 'ambiguous\nopen-iscsi: /usr/lib/systemd/system/open-iscsi.service\n' ;;
            missing) return 1 ;;
        esac
        return 0
    fi
    return 1
}
reset_tracking() {
    temporary_masks=()
    temporary_mask_inodes=()
    temporary_masks_cleanup_complete=false
    policy_cleanup_complete=false
    service_guard_cleanup_complete=false
    package_transaction_started=false
    denied_units_finalized=false
    service_readback_complete=false
    created_runtime_mounts=()
    runtime_mount_ids=()
    runtime_mount_records=()
    preserved_unit_masks=()
    preserved_unit_mask_inodes=()
    preserved_package_aliases=()
    preserved_package_alias_inodes=()
    preserved_package_alias_targets=()
    preserved_package_alias_canonical_units=()
    policy_guarded_canonical_units=()
    policy_guarded_absent_units=()
    recovery_guard_files=()
    recovery_guard_created_directories=()
    recovery_guard_file_inodes=()
    recovery_guard_contents=()
    recovery_guard_condition_paths=()
    recovery_guard_directory_inodes=()
    recovery_guard_paths_by_unit=()
    recovery_guard_path_owners=()
    recovery_guard_paths_retained=()
    recovery_guard_retained_states=()
    recovery_guards_cleanup_complete=false
}
refresh_md5() {
    local canonical="$target/usr/lib/systemd/system/open-iscsi.service"
    local digest
    digest="$(md5sum -- "$canonical" | awk '{print $1}')"
    printf '%s  %s\n' "$digest" usr/lib/systemd/system/open-iscsi.service \
        >"$target/var/lib/dpkg/info/open-iscsi.md5sums"
}
make_fixture() {
    target="$root/$1"
    mask_root="$target/etc/systemd/system"
    recovery_guard_authorization_root="$mask_root/.hoardarr-service-start-authorized"
    mkdir -p -- \
        "$mask_root" \
        "$target/usr/lib/systemd/system" \
        "$target/var/lib/dpkg/info" \
        "$target/usr/sbin" \
        "$target/opt/hoardarr-install"
    printf '%s\n' \
        '[Unit]' \
        'Description=Open-iSCSI' \
        '[Install]' \
        'WantedBy=sysinit.target' \
        'Alias=iscsi.service' \
        >"$target/usr/lib/systemd/system/open-iscsi.service"
    printf '%s\n' 'Package: open-iscsi' 'Status: install ok installed' \
        >"$target/var/lib/dpkg/status"
    printf '%s\n' /usr/lib/systemd/system/open-iscsi.service \
        >"$target/var/lib/dpkg/info/open-iscsi.list"
    refresh_md5
    ln -s -- /usr/lib/systemd/system/open-iscsi.service "$mask_root/iscsi.service"
    policy="$target/usr/sbin/policy-rc.d"
    policy_backup="$target/opt/hoardarr-install/policy-rc.d.original"
    state_root="$target/opt/hoardarr-install/state"
    mkdir -p -- "$state_root"
    policy_state=absent
    package_metadata_mode=ok
    non_root_path=
    fail_inode_path=
    reset_tracking
}
expect_alias_rejected_unchanged() {
    local unit="${1:-iscsi.service}"
    local alias="$mask_root/iscsi.service"
    local inode target_before status=0
    inode="$(stat -c %i -- "$alias")"
    target_before="$(readlink -- "$alias")"
    prepare_temporary_unit_mask "$alias" "$unit" >/dev/null 2>&1 || status=$?
    [[ "$status" -ne 0 ]]
    [[ -L "$alias" && "$(readlink -- "$alias")" == "$target_before" ]]
    [[ "$(stat -c %i -- "$alias")" == "$inode" ]]
}

# Exact retained tuple: no replacement, exact inode survives the entire
# pre-finalization lifecycle, and policy-rc.d denies the retained postinst start.
make_fixture exact
alias="$mask_root/iscsi.service"
canonical_override="$mask_root/open-iscsi.service"
wants="$mask_root/sysinit.target.wants/open-iscsi.service"
alias_inode="$(stat -c %i -- "$alias")"
install_service_start_guard
prepare_temporary_unit_mask "$alias" iscsi.service
prepare_temporary_unit_mask "$canonical_override" open-iscsi.service
[[ "${preserved_package_aliases[iscsi.service]}" == "$alias" ]]
[[ "${policy_guarded_canonical_units[open-iscsi.service]}" == "$canonical_override" ]]
[[ ! -e "$canonical_override" && ! -L "$canonical_override" ]]

# Retained open-iscsi.postinst semantics: unmask, enable, then invoke start.
rm -f -- "$canonical_override"
mkdir -p -- "$(dirname -- "$wants")"
ln -s -- /usr/lib/systemd/system/open-iscsi.service "$wants"
postinst_start_status=0
invoke_start() {
    local status=0
    "$policy" open-iscsi.service start || status=$?
    if (( status == 0 )); then
        mkdir -p -- "$target/run"
        : >"$target/run/open-iscsi.started"
    fi
    return "$status"
}
invoke_start || postinst_start_status=$?
[[ "$postinst_start_status" -eq 101 ]]
[[ ! -e "$target/run/open-iscsi.started" ]]
[[ -L "$alias" && "$(readlink -- "$alias")" == /usr/lib/systemd/system/open-iscsi.service ]]
[[ "$(stat -c %i -- "$alias")" == "$alias_inode" ]]
cleanup_temporary_masks
[[ "$(stat -c %i -- "$alias")" == "$alias_inode" ]]

# A later payload failure preserves the original status and alias identity.
failure_status=0
cleanup_guard 79 || failure_status=$?
[[ "$failure_status" -eq 79 ]]
[[ -L "$alias" && "$(stat -c %i -- "$alias")" == "$alias_inode" ]]

# A clean success finalization acts only on the canonical unit, removes the
# vendor alias and wants link, and requires a disabled canonical readback.
make_fixture final
alias="$mask_root/iscsi.service"
canonical_override="$mask_root/open-iscsi.service"
wants="$mask_root/sysinit.target.wants/open-iscsi.service"
mkdir -p -- "$(dirname -- "$wants")"
ln -s -- /usr/lib/systemd/system/open-iscsi.service "$wants"
install_service_start_guard
prepare_temporary_unit_mask "$alias" iscsi.service
prepare_temporary_unit_mask "$canonical_override" open-iscsi.service
cleanup_temporary_masks
disable_log="$target/disable.log"
systemctl() {
    [[ "$1" == "--root=$target" ]]
    if [[ "$2" == disable && "$3" == open-iscsi.service ]]; then
        printf '%s\n' disable-open-iscsi >>"$disable_log"
        rm -f -- "$alias" "$wants"
        return 0
    fi
    if [[ "$2" == is-enabled && "$3" == open-iscsi.service ]]; then
        printf '%s\n' disabled
        return 1
    fi
    printf 'unexpected systemctl argv: %s\n' "$*" >&2
    return 97
}
chroot() { printf 'unexpected offline manager query: %s\n' "$*" >&2; return 220; }
denied_units=(iscsi.service open-iscsi.service)
disable_unmasked_units
[[ ! -e "$alias" && ! -L "$alias" ]]
[[ ! -e "$wants" && ! -L "$wants" ]]
[[ "$(cat "$disable_log")" == disable-open-iscsi ]]

# Canonical disable failure is not ignored and does not remove either link.
make_fixture disable-failure
alias="$mask_root/iscsi.service"
wants="$mask_root/sysinit.target.wants/open-iscsi.service"
mkdir -p -- "$(dirname -- "$wants")"
ln -s -- /usr/lib/systemd/system/open-iscsi.service "$wants"
prepare_temporary_unit_mask "$alias" iscsi.service
systemctl() {
    [[ "$2" == disable && "$3" == open-iscsi.service ]]
    return 1
}
chroot() { printf 'unexpected offline manager query: %s\n' "$*" >&2; return 220; }
denied_units=(iscsi.service open-iscsi.service)
disable_failure_status=0
disable_unmasked_units >/dev/null 2>&1 || disable_failure_status=$?
[[ "$disable_failure_status" -ne 0 ]]
[[ -L "$alias" && -L "$wants" ]]

# A nominal disable that leaves either generated link is rejected.
make_fixture disable-incomplete
alias="$mask_root/iscsi.service"
wants="$mask_root/sysinit.target.wants/open-iscsi.service"
mkdir -p -- "$(dirname -- "$wants")"
ln -s -- /usr/lib/systemd/system/open-iscsi.service "$wants"
prepare_temporary_unit_mask "$alias" iscsi.service
systemctl() {
    if [[ "$2" == disable ]]; then return 0; fi
    printf '%s\n' disabled
    return 1
}
chroot() { printf 'unexpected offline manager query: %s\n' "$*" >&2; return 220; }
denied_units=(iscsi.service open-iscsi.service)
disable_incomplete_status=0
disable_unmasked_units >/dev/null 2>&1 || disable_incomplete_status=$?
[[ "$disable_incomplete_status" -ne 0 ]]
[[ -L "$alias" && -L "$wants" ]]

# Exact tuple negative cases all reject without changing the alias object.
make_fixture wrong-unit
expect_alias_rejected_unchanged other.service

make_fixture relative-target
rm -f -- "$mask_root/iscsi.service"
ln -s -- ../../usr/lib/systemd/system/open-iscsi.service "$mask_root/iscsi.service"
expect_alias_rejected_unchanged

make_fixture alternate-target
rm -f -- "$mask_root/iscsi.service"
ln -s -- /usr/lib/systemd/system/alternate.service "$mask_root/iscsi.service"
expect_alias_rejected_unchanged

make_fixture missing-canonical
rm -f -- "$target/usr/lib/systemd/system/open-iscsi.service"
expect_alias_rejected_unchanged

make_fixture canonical-symlink
rm -f -- "$target/usr/lib/systemd/system/open-iscsi.service"
ln -s -- /dev/null "$target/usr/lib/systemd/system/open-iscsi.service"
expect_alias_rejected_unchanged

make_fixture canonical-directory
rm -f -- "$target/usr/lib/systemd/system/open-iscsi.service"
mkdir -- "$target/usr/lib/systemd/system/open-iscsi.service"
expect_alias_rejected_unchanged

make_fixture non-root-alias
non_root_path="$mask_root/iscsi.service"
expect_alias_rejected_unchanged

make_fixture non-root-canonical
non_root_path="$target/usr/lib/systemd/system/open-iscsi.service"
expect_alias_rejected_unchanged

make_fixture wrong-owner
package_metadata_mode=wrong-owner
expect_alias_rejected_unchanged

make_fixture wrong-package
package_metadata_mode=wrong-package
expect_alias_rejected_unchanged

make_fixture missing-package
package_metadata_mode=missing
expect_alias_rejected_unchanged

make_fixture malformed-package
package_metadata_mode=malformed
expect_alias_rejected_unchanged

make_fixture missing-alias-metadata
sed -i '/^Alias=/d' "$target/usr/lib/systemd/system/open-iscsi.service"
refresh_md5
expect_alias_rejected_unchanged

make_fixture wrong-alias-metadata
sed -i 's/^Alias=.*/Alias=other.service/' "$target/usr/lib/systemd/system/open-iscsi.service"
refresh_md5
expect_alias_rejected_unchanged

make_fixture extra-alias-metadata
sed -i 's/^Alias=.*/Alias=iscsi.service other.service/' \
    "$target/usr/lib/systemd/system/open-iscsi.service"
refresh_md5
expect_alias_rejected_unchanged

make_fixture status-symlink
mv -- "$target/var/lib/dpkg/status" "$target/status-retained"
ln -s -- "$target/status-retained" "$target/var/lib/dpkg/status"
expect_alias_rejected_unchanged

make_fixture package-list-symlink
mv -- "$target/var/lib/dpkg/info/open-iscsi.list" "$target/list-retained"
ln -s -- "$target/list-retained" "$target/var/lib/dpkg/info/open-iscsi.list"
expect_alias_rejected_unchanged

make_fixture missing-md5
rm -f -- "$target/var/lib/dpkg/info/open-iscsi.md5sums"
expect_alias_rejected_unchanged

make_fixture malformed-md5
printf '%s\n' malformed >"$target/var/lib/dpkg/info/open-iscsi.md5sums"
expect_alias_rejected_unchanged

make_fixture parent-symlink
external="$root/parent-symlink-systemd"
mv -- "$target/etc/systemd" "$external"
ln -s -- "$external" "$target/etc/systemd"
expect_alias_rejected_unchanged

# Recorded alias drift is detected before finalization and never overwritten.
make_fixture alias-drift
alias="$mask_root/iscsi.service"
prepare_temporary_unit_mask "$alias" iscsi.service
original_inode="$(stat -c %i -- "$alias")"
mv -- "$alias" "$target/original-alias-retained"
ln -s -- /usr/lib/systemd/system/drifted.service "$alias"
drift_inode="$(stat -c %i -- "$alias")"
drift_status=0
cleanup_temporary_masks >/dev/null 2>&1 || drift_status=$?
[[ "$drift_status" -ne 0 ]]
[[ "$(readlink -- "$alias")" == /usr/lib/systemd/system/drifted.service ]]
[[ "$(stat -c %i -- "$alias")" == "$drift_inode" ]]
[[ "$(stat -c %i -- "$target/original-alias-retained")" == "$original_inode" ]]

# Drift between alias classification and canonical policy-guard registration
# is rejected before the canonical path is accepted.
make_fixture canonical-registration-drift
alias="$mask_root/iscsi.service"
canonical_override="$mask_root/open-iscsi.service"
prepare_temporary_unit_mask "$alias" iscsi.service
mv -- "$alias" "$target/original-alias-retained"
ln -s -- /usr/lib/systemd/system/drifted.service "$alias"
canonical_registration_status=0
prepare_temporary_unit_mask "$canonical_override" open-iscsi.service \
    >/dev/null 2>&1 || canonical_registration_status=$?
[[ "$canonical_registration_status" -ne 0 ]]
[[ ! -e "$canonical_override" && ! -L "$canonical_override" ]]
[[ "$(readlink -- "$alias")" == /usr/lib/systemd/system/drifted.service ]]

# A pre-existing exact mask whose inode cannot be recorded is rejected intact.
make_fixture safe-stat-failure
safe_stat_failure="$mask_root/safe-stat-failure.service"
ln -s -- /dev/null "$safe_stat_failure"
safe_stat_failure_inode="$(stat -c %i -- "$safe_stat_failure")"
fail_inode_path="$safe_stat_failure"
safe_stat_failure_status=0
prepare_temporary_unit_mask "$safe_stat_failure" safe-stat-failure.service \
    >/dev/null 2>&1 || safe_stat_failure_status=$?
fail_inode_path=
[[ "$safe_stat_failure_status" -ne 0 ]]
[[ -L "$safe_stat_failure" && "$(readlink -- "$safe_stat_failure")" == /dev/null ]]
[[ "$(stat -c %i -- "$safe_stat_failure")" == "$safe_stat_failure_inode" ]]
[[ "${#preserved_unit_masks[@]}" -eq 0 ]]

# Cleanup failure is aggregated; an existing payload failure remains exact,
# while an otherwise successful invocation becomes failure.
make_fixture incomplete-transaction
install_service_start_guard
prepare_temporary_unit_mask "$mask_root/iscsi.service" iscsi.service
prepare_recovery_unit_guard iscsi.service
mkdir -p -- "$mask_root/sysinit.target.wants"
ln -s -- /usr/lib/systemd/system/open-iscsi.service \
    "$mask_root/sysinit.target.wants/open-iscsi.service"
package_transaction_started=true
incomplete_status=0
cleanup_guard 73 >/dev/null 2>&1 || incomplete_status=$?
[[ "$incomplete_status" -eq 73 ]]
guard_status=0
"$policy" pmcd.service start || guard_status=$?
[[ -x "$policy" && "$guard_status" -eq 101 ]]
grep -Fq 'finalization=false readback=false' "$state_root/service-guard-recovery.txt"
recovery_path="${recovery_guard_paths_by_unit[iscsi.service]}"
[[ -f "$recovery_path" && ! -L "$recovery_path" ]]
grep -Fxq 'ConditionPathExists=/dev/null/hoardarr-offline-service-guard/open-iscsi.service' "$recovery_path"
[[ -L "$mask_root/sysinit.target.wants/open-iscsi.service" ]]

set +e
cleanup_guard 0 >/dev/null 2>&1
set_plus_e_status=$?
set -e
[[ "$set_plus_e_status" -ne 0 && -x "$policy" ]]

denied_units_finalized=true
cleanup_guard 0 >/dev/null 2>&1 || readback_incomplete_status=$?
[[ "${readback_incomplete_status:-0}" -ne 0 && -x "$policy" ]]
denied_units_finalized=false
systemctl() {
    if [[ "$2" == disable && "$3" == open-iscsi.service ]]; then
        rm -f -- "$mask_root/iscsi.service" \
            "$mask_root/sysinit.target.wants/open-iscsi.service"
        return 0
    fi
    if [[ "$2" == is-enabled && "$3" == open-iscsi.service ]]; then
        printf '%s\n' disabled
        return 1
    fi
    return 97
}
chroot() { printf 'unexpected offline manager query: %s\n' "$*" >&2; return 220; }
denied_units=(iscsi.service open-iscsi.service)
disable_unmasked_units
cleanup_guard 0
[[ ! -e "$policy" && ! -L "$policy" ]]

for signal_case in 'HUP 129' 'INT 130' 'TERM 143'; do
    read -r signal_name signal_status <<<"$signal_case"
    make_fixture "signal-$signal_name"
    install_service_start_guard
    prepare_temporary_unit_mask "$mask_root/iscsi.service" iscsi.service
    prepare_recovery_unit_guard iscsi.service
    package_transaction_started=true
    observed_status=0
    (
        trap 'exit_cleanup $?' EXIT
        trap 'signal_exit 129' HUP
        trap 'signal_exit 130' INT
        trap 'signal_exit 143' TERM
        kill -s "$signal_name" "$BASHPID"
    ) || observed_status=$?
    [[ "$observed_status" -eq "$signal_status" && -x "$policy" ]]
    grep -Fq 'finalization=false readback=false' "$state_root/service-guard-recovery.txt"
done

make_fixture cleanup-original-status
reset_tracking
rm -f -- "$policy"
mkdir -- "$policy"
original_status=0
cleanup_guard 73 >/dev/null 2>&1 || original_status=$?
[[ "$original_status" -eq 73 ]]

make_fixture cleanup-success-status
reset_tracking
rm -f -- "$policy"
mkdir -- "$policy"
success_status=0
cleanup_guard 0 >/dev/null 2>&1 || success_status=$?
[[ "$success_status" -ne 0 ]]
""",
            )
        )
        with tempfile.TemporaryDirectory() as temporary:
            script_path = pathlib.Path(temporary) / "alias-regression.sh"
            script_path.write_text(script, encoding="utf-8", newline="\n")
            result = subprocess.run(
                [bash, str(script_path), temporary],
                capture_output=True,
                check=False,
                env={**os.environ, "MSYS": "winsymlinks:sys"},
                text=True,
            )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("command not found", result.stderr)

    def test_pcp_trace_contract_rejects_untrusted_or_incomplete_evidence(
        self,
    ) -> None:
        def phase_record(kind: str, index: int) -> str:
            phase, label = PCP_TRACE_PHASES[index]
            return f"HPCP|1|{kind}|{phase}|status=-|line=-|function=-|label={label}"

        valid_lines = [
            record
            for index in range(len(PCP_TRACE_PHASES))
            for record in (phase_record("BEGIN", index), phase_record("PASS", index))
        ]
        final_phase, final_label = PCP_TRACE_PHASES[-1]
        valid_lines.append(
            f"HPCP|1|EXIT|{final_phase}|status=0|line=321|function=main|"
            f"label={final_label}"
        )

        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary).resolve()
            namespace = root / "namespace"
            namespace.mkdir()

            def rejected(name: str, lines: list[str], *, outside: bool = False) -> None:
                if outside:
                    with tempfile.TemporaryDirectory() as other:
                        trace = pathlib.Path(other) / f"{name}.trace"
                        trace.write_text("\n".join(lines) + "\n", encoding="ascii")
                        with self.assertRaises(AssertionError):
                            _validate_pcp_trace(trace, root, namespace)
                    return
                trace = root / f"{name}.trace"
                trace.write_text("\n".join(lines) + "\n", encoding="ascii")
                with self.assertRaises(AssertionError):
                    _validate_pcp_trace(trace, root, namespace)

            success_trace = root / "success.trace"
            success_trace.write_text("\n".join(valid_lines) + "\n", encoding="ascii")
            _, status = _validate_pcp_trace(success_trace, root, namespace)
            self.assertEqual(status, 0)

            missing = valid_lines[:4] + valid_lines[6:]
            duplicate = valid_lines[:1] + [valid_lines[0]] + valid_lines[1:]
            out_of_order = valid_lines.copy()
            out_of_order[2:6] = valid_lines[4:6] + valid_lines[2:4]
            unknown = valid_lines.copy()
            unknown[0] = unknown[0].replace("01-fixture-creation", "01-unknown-phase")
            multiple_terminal = valid_lines + [valid_lines[-1]]
            malformed_status = valid_lines.copy()
            malformed_status[-1] = malformed_status[-1].replace(
                "status=0", "status=999"
            )
            malformed_line = valid_lines.copy()
            malformed_line[-1] = malformed_line[-1].replace("line=321", "line=0")
            unbounded = valid_lines.copy()
            unbounded[0] += "x" * PCP_TRACE_MAX_LINE_BYTES
            environment_like = valid_lines.copy()
            environment_like[0] += "|PASSWORD=do-not-record"

            cases = {
                "missing": missing,
                "duplicate": duplicate,
                "out-of-order": out_of_order,
                "unknown": unknown,
                "multiple-terminal": multiple_terminal,
                "malformed-status": malformed_status,
                "malformed-line": malformed_line,
                "unbounded": unbounded,
                "environment-like": environment_like,
            }
            for name, lines in cases.items():
                with self.subTest(name=name):
                    rejected(name, lines)
            rejected("outside-root", valid_lines, outside=True)

    def test_manager_root_receipt_parser_is_bounded_and_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            namespace = pathlib.Path(temporary).resolve() / "namespace"
            manager = namespace / "run-systemd"
            manager.mkdir(parents=True)
            before = namespace / "manager-root-before.tsv"
            after = namespace / "manager-root-after.tsv"
            before.write_text(
                "HMROOT|1|before|status=-\n", encoding="utf-8", newline="\n"
            )
            before_text, before_status, before_entries = _validate_manager_root_receipt(
                before, namespace, manager, "before"
            )
            self.assertEqual(before_text, "HMROOT|1|before|status=-\n")
            self.assertIsNone(before_status)
            self.assertEqual(before_entries, ())
            after.write_text(
                "HMROOT|1|after|status=1\n"
                "ENTRY\tprivate\tdirectory\t755\t0\t0\n"
                "ENTRY\tprivate/socket\tsocket\t660\t100\t101\n",
                encoding="utf-8",
                newline="\n",
            )
            _, after_status, after_entries = _validate_manager_root_receipt(
                after, namespace, manager, "after"
            )
            self.assertEqual(after_status, 1)
            self.assertEqual(len(after_entries), 2)

            before.unlink()
            after.unlink()

            def rejected(
                name: str,
                text: str | bytes | None,
                *,
                stage: str = "after",
                path: pathlib.Path | None = None,
            ) -> None:
                receipt = path or namespace / f"manager-root-{stage}.tsv"
                if receipt.exists() or receipt.is_symlink():
                    receipt.unlink()
                if isinstance(text, bytes):
                    receipt.write_bytes(text)
                elif text is not None:
                    receipt.write_text(text, encoding="utf-8", newline="\n")
                with self.assertRaises(AssertionError, msg=name):
                    _validate_manager_root_receipt(receipt, namespace, manager, stage)
                if receipt.exists() or receipt.is_symlink():
                    receipt.unlink()

            header = "HMROOT|1|after|status=1\n"
            valid = "ENTRY\tentry\tregular\t600\t0\t0\n"
            cases: dict[str, str | bytes | None] = {
                "missing-file": None,
                "missing-header": valid,
                "wrong-version": "HMROOT|2|after|status=1\n",
                "wrong-stage": "HMROOT|1|before|status=-\n",
                "wrong-status": "HMROOT|1|after|status=-\n",
                "overlong-path": header + f"ENTRY\t{'a' * 193}\tregular\t600\t0\t0\n",
                "absolute-path": header + "ENTRY\t/absolute\tregular\t600\t0\t0\n",
                "traversal-path": header
                + "ENTRY\tsafe/../escape\tregular\t600\t0\t0\n",
                "control-path": (header + "ENTRY\tbad\x01path\tregular\t600\t0\t0\n"),
                "excess-depth": header + "ENTRY\ta/b/c/d/e/f\tregular\t600\t0\t0\n",
                "unknown-type": header + "ENTRY\tentry\tunknown\t600\t0\t0\n",
                "invalid-mode": header + "ENTRY\tentry\tregular\t888\t0\t0\n",
                "invalid-uid": header + "ENTRY\tentry\tregular\t600\troot\t0\n",
                "invalid-gid": header + "ENTRY\tentry\tregular\t600\t0\t-1\n",
                "duplicate": header + valid + valid,
                "out-of-order": header
                + "ENTRY\tz\tregular\t600\t0\t0\n"
                + "ENTRY\ta\tregular\t600\t0\t0\n",
                "excess-entries": header
                + "".join(
                    f"ENTRY\tp{index:03d}\tregular\t600\t0\t0\n" for index in range(129)
                ),
                "oversized": b"x" * (PCP_MANAGER_ROOT_RECEIPT_MAX_BYTES + 1),
                "appended-text": header + valid + "TRAILING\n",
            }
            for name, text in cases.items():
                with self.subTest(name=name):
                    rejected(name, text)
            rejected(
                "outside-exact-path",
                header,
                path=namespace / "unexpected-receipt.tsv",
            )
            rejected(
                "before-has-status",
                "HMROOT|1|before|status=1\n",
                stage="before",
            )

    def test_pcp_generated_nonactivation_proof_is_structural_and_managerless(
        self,
    ) -> None:
        _assert_pcp_offline_nonactivation_contract(PCP_OFFLINE_NONACTIVATION_PROOF)
        with self.assertRaises(AssertionError):
            _assert_pcp_offline_nonactivation_contract(
                PCP_OFFLINE_NONACTIVATION_PROOF + "\nsystemctl is-active pmcd.service\n"
            )
        with self.assertRaises(AssertionError):
            _assert_pcp_offline_nonactivation_contract(
                PCP_OFFLINE_NONACTIVATION_PROOF.replace(
                    '[[ "$post_configure_start_status" -eq 101 ]]', "", 1
                )
            )

    @unittest.skipUnless(
        sys.platform.startswith("linux") and shutil.which("bash"),
        "requires Linux Bash",
    )
    def test_systemd_receipt_production_writers_emit_real_tab_bytes(self) -> None:
        source_writer = _extract_systemd_receipt_writer(
            PCP_SYSTEMD_SOURCE_RECEIPT, "systemd_source_partial"
        )
        causal_writer = _extract_systemd_receipt_writer(
            PCP_SYSTEMD_CAUSAL_PROOF, "systemd_causal_partial"
        )
        self.assertNotIn("printf '%s\\n' \\", source_writer)
        self.assertNotIn("printf '%s\\n' \\", causal_writer)

        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary).resolve()
            script = root / "emit-systemd-receipts.sh"
            script.write_text(
                "set -Eeuo pipefail\n"
                'work="$1"\n'
                'systemd_source_receipt="$work/systemd-source.tsv"\n'
                'systemd_source_partial="${systemd_source_receipt}.partial"\n'
                'systemd_package_version="255.4-1ubuntu8.17"\n'
                'systemd_package_arch="amd64"\n'
                'systemd_version_first="systemd 255 (255.4-1ubuntu8.17)"\n'
                f'systemd_version_sha256="{"b" * 64}"\n'
                f'systemd_executable_sha256="{"a" * 64}"\n'
                'systemd_executable_mode="755"\n'
                'systemd_executable_size="123456"\n'
                'systemd_executable_links="1"\n'
                + source_writer
                + "\n"
                + '/usr/bin/mv -- "$systemd_source_partial" '
                '"$systemd_source_receipt"\n'
                + 'systemd_causal_receipt="$work/systemd-causal.tsv"\n'
                'systemd_causal_partial="${systemd_causal_receipt}.partial"\n'
                'systemd_marker_mode="444"\n'
                'systemd_marker_uid="0"\n'
                'systemd_marker_gid="0"\n'
                'systemd_marker_size="0"\n'
                'systemd_marker_links="1"\n'
                + causal_writer
                + "\n"
                + '/usr/bin/mv -- "$systemd_causal_partial" '
                '"$systemd_causal_receipt"\n',
                encoding="ascii",
                newline="\n",
            )
            result = subprocess.run(
                [shutil.which("bash") or "bash", str(script), str(root)],
                capture_output=True,
                check=False,
                text=True,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

            source = root / "systemd-source.tsv"
            causal = root / "systemd-causal.tsv"
            for receipt, expected_lines in ((source, 9), (causal, 4)):
                raw = receipt.read_bytes()
                with self.subTest(receipt=receipt.name):
                    self.assertTrue(raw.isascii())
                    self.assertTrue(raw.endswith(b"\n"))
                    self.assertNotIn(b"\r", raw)
                    self.assertEqual(len(raw.splitlines()), expected_lines)
                    self.assertIn(b"\t", raw)
                    self.assertNotIn(b"\\t", b"\n".join(raw.splitlines()[1:]))
                    self.assertTrue(all(b"\t" in row for row in raw.splitlines()[1:]))

            source_text, version, executable_hash = _validate_systemd_source_receipt(
                source, root
            )
            self.assertEqual(version, "255.4-1ubuntu8.17")
            self.assertEqual(executable_hash, "a" * 64)
            self.assertEqual(source_text.encode("ascii"), source.read_bytes())
            causal_text = _validate_systemd_causal_receipt(causal, root)
            self.assertEqual(causal_text.encode("ascii"), causal.read_bytes())

            source.write_bytes(source.read_bytes().replace(b"\t", b"\\t"))
            causal.write_bytes(causal.read_bytes().replace(b"\t", b"\\t"))
            with self.assertRaises(AssertionError):
                _validate_systemd_source_receipt(source, root)
            with self.assertRaises(AssertionError):
                _validate_systemd_causal_receipt(causal, root)

    def test_systemd_source_and_causal_receipts_are_bounded_and_fail_closed(
        self,
    ) -> None:
        package_version = "255.4-1ubuntu8.17"
        executable_hash = "a" * 64
        version_hash = "b" * 64
        source_receipt = (
            "HSOURCE|1\n"
            f"PACKAGE\tsystemd\t{package_version}\tamd64\n"
            f"VERSION\tsystemd 255 ({package_version})\t{version_hash}\n"
            "EXECUTABLE\t/usr/bin/systemd-analyze\t"
            f"{executable_hash}\t755\t0\t0\t123456\t1\tsystemd\n"
            f"UPSTREAM\t{PCP_SYSTEMD_UPSTREAM_REPOSITORY}\t"
            f"{PCP_SYSTEMD_UPSTREAM_TAG}\t{PCP_SYSTEMD_UPSTREAM_REVISION}\n"
            f"SOURCE\t{PCP_SYSTEMD_ANALYZE_SOURCE_PATH}\t"
            f"{PCP_SYSTEMD_ANALYZE_SOURCE_FUNCTION}\t"
            f"{PCP_SYSTEMD_ANALYZE_SOURCE_SHA256}\n"
            f"SOURCE\t{PCP_SYSTEMD_MANAGER_SOURCE_PATH}\t"
            f"{PCP_SYSTEMD_MANAGER_SOURCE_FUNCTION}\t"
            f"{PCP_SYSTEMD_MANAGER_SOURCE_SHA256}\n"
            "CHAIN\tverb_condition>verify_conditions>manager_startup>"
            "manager_ready>touch_file\n"
            f"MARKER\t{PCP_SYSTEMD_MARKER_PATH}\tregular\t0444\t"
            "zero-length\tmanager-ready\n"
        )
        causal_receipt = (
            "HCAUSE|1\n"
            "CONTROL\tnegative\tcommand=none\tstatus=-\tbefore=0\tafter=0\t"
            "manager_endpoints_before=0\tmanager_endpoints_after=0\t"
            "cleanup=removed\n"
            "CONTROL\tpositive\tcommand=systemd-analyze-condition\tstatus=1\t"
            "before=0\tafter=1\tmanager_endpoints_before=0\t"
            "manager_endpoints_after=0\tcleanup=removed\n"
            "MARKER\tsystemd-units-load\tregular\t444\t0\t0\t0\t1\t"
            "same-filesystem\n"
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary).resolve()
            source = root / "systemd-source.tsv"
            causal = root / "systemd-causal.tsv"
            source.write_text(source_receipt, encoding="ascii", newline="\n")
            causal.write_text(causal_receipt, encoding="ascii", newline="\n")
            source_text, actual_version, actual_hash = _validate_systemd_source_receipt(
                source, root
            )
            self.assertEqual(source_text, source_receipt)
            self.assertEqual(actual_version, package_version)
            self.assertEqual(actual_hash, executable_hash)
            self.assertEqual(
                _validate_systemd_causal_receipt(causal, root), causal_receipt
            )

            def source_rejected(name: str, mutated: str | bytes | None) -> None:
                if source.exists() or source.is_symlink():
                    source.unlink()
                if isinstance(mutated, bytes):
                    source.write_bytes(mutated)
                elif mutated is not None:
                    source.write_text(mutated, encoding="ascii", newline="\n")
                with self.assertRaises(AssertionError, msg=name):
                    _validate_systemd_source_receipt(source, root)

            source_cases: dict[str, str | bytes | None] = {
                "missing": None,
                "wrong-package": source_receipt.replace(
                    "PACKAGE\tsystemd", "PACKAGE\tlibsystemd0", 1
                ),
                "wrong-package-version": source_receipt.replace(
                    package_version, "256.1-1", 1
                ),
                "version-divergence": source_receipt.replace(
                    f"systemd 255 ({package_version})", "systemd 255 (255.4-other)", 1
                ),
                "wrong-executable": source_receipt.replace(
                    "/usr/bin/systemd-analyze", "/tmp/systemd-analyze", 1
                ),
                "wrong-executable-hash": source_receipt.replace(
                    executable_hash, "z" * 64, 1
                ),
                "wrong-revision": source_receipt.replace(
                    PCP_SYSTEMD_UPSTREAM_REVISION, "0" * 40, 1
                ),
                "wrong-source": source_receipt.replace(
                    PCP_SYSTEMD_MANAGER_SOURCE_PATH, "src/core/not-manager.c", 1
                ),
                "wrong-source-function": source_receipt.replace(
                    PCP_SYSTEMD_MANAGER_SOURCE_FUNCTION, "manager_ready:1-2", 1
                ),
                "wrong-source-hash": source_receipt.replace(
                    PCP_SYSTEMD_MANAGER_SOURCE_SHA256, "0" * 64, 1
                ),
                "unknown-field": source_receipt + "ENV\tSECRET=value\n",
                "control": source_receipt.replace("systemd\t", "systemd\x01", 1),
                "oversized": b"x" * (PCP_SYSTEMD_SOURCE_RECEIPT_MAX_BYTES + 1),
            }
            for name, mutation in source_cases.items():
                with self.subTest(source=name):
                    source_rejected(name, mutation)

            source.write_text(source_receipt, encoding="ascii", newline="\n")

            def causal_rejected(name: str, mutated: str | bytes | None) -> None:
                if causal.exists() or causal.is_symlink():
                    causal.unlink()
                if isinstance(mutated, bytes):
                    causal.write_bytes(mutated)
                elif mutated is not None:
                    causal.write_text(mutated, encoding="ascii", newline="\n")
                with self.assertRaises(AssertionError, msg=name):
                    _validate_systemd_causal_receipt(causal, root)

            causal_cases: dict[str, str | bytes | None] = {
                "missing": None,
                "preexisting-marker": causal_receipt.replace("before=0", "before=1", 1),
                "symlink": causal_receipt.replace("\tregular\t", "\tsymlink\t", 1),
                "directory": causal_receipt.replace("\tregular\t", "\tdirectory\t", 1),
                "nonzero-size": causal_receipt.replace("\t0\t1\t", "\t1\t1\t", 1),
                "wrong-mode": causal_receipt.replace("\t444\t", "\t644\t", 1),
                "wrong-owner": causal_receipt.replace(
                    "\t444\t0\t0\t", "\t444\t1\t0\t", 1
                ),
                "wrong-link-count": causal_receipt.replace("\t0\t1\t", "\t0\t2\t", 1),
                "wrong-filesystem": causal_receipt.replace(
                    "same-filesystem", "different-filesystem", 1
                ),
                "extra-entry": causal_receipt + "ENTRY\tprivate\n",
                "manager-before": causal_receipt.replace(
                    "manager_endpoints_before=0", "manager_endpoints_before=1", 1
                ),
                "manager-after": causal_receipt.replace(
                    "manager_endpoints_after=0", "manager_endpoints_after=1", 1
                ),
                "wrong-command": causal_receipt.replace(
                    "command=systemd-analyze-condition", "command=systemctl", 1
                ),
                "wrong-status": causal_receipt.replace("status=1", "status=0", 1),
                "negative-nonempty": causal_receipt.replace(
                    "command=none\tstatus=-\tbefore=0\tafter=0",
                    "command=none\tstatus=-\tbefore=0\tafter=1",
                    1,
                ),
                "cleanup-drift": causal_receipt.replace(
                    "cleanup=removed", "cleanup=present", 1
                ),
                "unknown-field": causal_receipt.replace(
                    "HCAUSE|1", "HCAUSE|1\nENV\tTOKEN=value", 1
                ),
                "control": causal_receipt.replace("positive", "pos\x01itive", 1),
                "oversized": b"x" * (PCP_SYSTEMD_CAUSAL_RECEIPT_MAX_BYTES + 1),
            }
            for name, mutation in causal_cases.items():
                with self.subTest(causal=name):
                    causal_rejected(name, mutation)
            outside = root.parent / "systemd-causal.tsv"
            outside.write_text(causal_receipt, encoding="ascii", newline="\n")
            try:
                with self.assertRaises(AssertionError):
                    _validate_systemd_causal_receipt(outside, root)
            finally:
                outside.unlink()

    def test_systemd_causal_control_preserves_real_phase_ten_sequence(self) -> None:
        phase = _pcp_phase_ten_with_causal_proof()
        real_sequence = (
            'manager_root_snapshot before - "$work/manager-root-before.tsv"\n'
            'systemd-analyze condition "ConditionPathExists=$expected_pmcd_condition" \\\n'
            "    >/dev/null 2>&1 && exit 100\n"
            "condition_status=$?\n"
            'manager_root_snapshot after "$condition_status" '
            '"$work/manager-root-after.tsv"\n'
            "validate_and_remove_local_systemd_marker "
            '"$work/manager-root-after.tsv"\n'
            '[[ "$local_systemd_marker_cleanup_count" -eq 1 ]]\n'
            '[[ -z "$(find "$work/run-systemd" -mindepth 1 -print -quit)" ]]'
        )
        self.assertEqual(phase.count(real_sequence), 1)
        self.assertEqual(
            phase.count(
                "/usr/bin/systemd-analyze condition \\\n"
                f'    "{PCP_SYSTEMD_FALSE_CONDITION}" >/dev/null 2>&1'
            ),
            1,
        )
        self.assertEqual(phase.count("systemd_causal_cleanup_root"), 4)
        self.assertNotIn("apt-get", PCP_SYSTEMD_CAUSAL_PROOF)
        self.assertNotIn("systemctl", PCP_SYSTEMD_CAUSAL_PROOF)
        self.assertNotIn("curl", PCP_SYSTEMD_CAUSAL_PROOF)
        self.assertNotIn("wget", PCP_SYSTEMD_CAUSAL_PROOF)
        self.assertNotIn("strace", PCP_SYSTEMD_CAUSAL_PROOF)
        self.assertEqual(
            PCP_LOCAL_SYSTEMD_MARKER_ORACLE.count('/usr/bin/rm -- "$marker"'), 1
        )
        self.assertNotIn("rm -f", PCP_LOCAL_SYSTEMD_MARKER_ORACLE)
        self.assertNotIn("rm -r", PCP_LOCAL_SYSTEMD_MARKER_ORACLE)
        self.assertIsNone(
            re.search(
                r"(?m)^\s*/usr/bin/rm[^\n]*[\*\?\[]",
                PCP_LOCAL_SYSTEMD_MARKER_ORACLE,
            )
        )
        self.assertNotIn("systemctl", PCP_LOCAL_SYSTEMD_MARKER_ORACLE)
        self.assertNotIn("systemd-analyze", PCP_LOCAL_SYSTEMD_MARKER_ORACLE)
        self.assertIn(
            'rm -f -- "$expected_entry"',
            PCP_SYSTEMD_CAUSAL_PROOF,
        )
        _assert_pcp_offline_nonactivation_contract(phase)
        with self.assertRaises(AssertionError):
            _assert_pcp_offline_nonactivation_contract(
                phase.replace(
                    'manager_root_snapshot after "$condition_status" '
                    '"$work/manager-root-after.tsv"\n'
                    "validate_and_remove_local_systemd_marker",
                    "validate_and_remove_local_systemd_marker "
                    '"$work/manager-root-after.tsv"\n'
                    'manager_root_snapshot after "$condition_status"',
                    1,
                )
            )
        with self.assertRaises(AssertionError):
            _assert_pcp_offline_nonactivation_contract(
                phase.replace(
                    "validate_and_remove_local_systemd_marker "
                    '"$work/manager-root-after.tsv"\n'
                    '[[ "$local_systemd_marker_cleanup_count" -eq 1 ]]\n'
                    '[[ -z "$(find "$work/run-systemd" -mindepth 1 -print -quit)" ]]',
                    '[[ -z "$(find "$work/run-systemd" -mindepth 1 -print -quit)" ]]\n'
                    "validate_and_remove_local_systemd_marker "
                    '"$work/manager-root-after.tsv"\n'
                    '[[ "$local_systemd_marker_cleanup_count" -eq 1 ]]',
                    1,
                )
            )

    @unittest.skipUnless(sys.platform.startswith("linux"), "requires Linux mounts")
    def test_local_systemd_marker_oracle_is_exact_and_fail_closed(self) -> None:
        required = ("bash", "mount", "sudo", "umount", "unshare")
        missing = [command for command in required if shutil.which(command) is None]
        self.assertEqual(missing, [], f"missing marker-oracle tools: {missing}")
        sudo = subprocess.run(
            ["sudo", "-n", "true"], text=True, capture_output=True, check=False
        )
        self.assertEqual(sudo.returncode, 0, sudo.stderr)

        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary).resolve()
            script = root / "local-systemd-marker-oracle.sh"
            script.write_text(
                "set -Eeuo pipefail\n"
                'fixture_root="$1"\n'
                "mounted=false\n"
                "nested_marker_mount=false\n"
                "private_marker_mount=false\n"
                "sync_wrapper_mount=false\n"
                'wrong_device_source="/dev/shm/hoardarr-f16-marker-$$"\n'
                "cleanup_case() {\n"
                '    if [[ "$sync_wrapper_mount" == true ]]; then\n'
                "        /usr/bin/umount -- /usr/bin/sync || return 201\n"
                "        sync_wrapper_mount=false\n"
                "    fi\n"
                '    if [[ "$nested_marker_mount" == true ]]; then\n'
                "        /usr/bin/umount -- /run/systemd/systemd-units-load || return 202\n"
                "        nested_marker_mount=false\n"
                "    fi\n"
                '    if [[ "$private_marker_mount" == true ]]; then\n'
                '        /usr/bin/umount -- "$work/run-systemd/systemd-units-load" || return 202\n'
                "        private_marker_mount=false\n"
                "    fi\n"
                '    if [[ "$mounted" == true ]]; then\n'
                "        /usr/bin/umount -- /run/systemd || return 203\n"
                "        mounted=false\n"
                "    fi\n"
                "}\n"
                "cleanup_all() {\n"
                '    local status="$?"\n'
                "    trap - EXIT\n"
                "    cleanup_case || status=$?\n"
                '    /usr/bin/rm -f -- "$wrong_device_source" || status=204\n'
                '    exit "$status"\n'
                "}\n"
                "trap cleanup_all EXIT\n"
                "systemd_mount_id() {\n"
                "    /usr/bin/awk '$5 == \"/run/systemd\" { id=$1 } END { print id }' "
                "/proc/self/mountinfo\n"
                "}\n"
                "start_case() {\n"
                "    cleanup_case\n"
                '    work="$fixture_root/$1"\n'
                '    /usr/bin/mkdir -p -- "$work/run-systemd"\n'
                '    /usr/bin/mount --bind "$work/run-systemd" /run/systemd\n'
                "    mounted=true\n"
                "    /usr/bin/mount --make-private /run/systemd\n"
                '    systemd_underlay_mount_id="$(systemd_mount_id)"\n'
                '    [[ "$systemd_underlay_mount_id" =~ ^[1-9][0-9]*$ ]]\n'
                "    condition_status=1\n"
                "    local_systemd_marker_cleanup_count=0\n"
                "}\n"
                "write_receipt() {\n"
                "    printf 'HMROOT|1|after|status=1\\nENTRY\\tsystemd-units-load\\tregular\\t444\\t0\\t0\\n' >\"$work/manager-root-after.tsv\"\n"
                "}\n"
                "write_marker() {\n"
                '    : >"$work/run-systemd/systemd-units-load"\n'
                '    /usr/bin/chown 0:0 -- "$work/run-systemd/systemd-units-load"\n'
                '    /usr/bin/chmod 0444 -- "$work/run-systemd/systemd-units-load"\n'
                "}\n"
                "expect_rejected() {\n"
                '    local label="$1" expected_status="$2" '
                'before="$local_systemd_marker_cleanup_count" actual_status=0\n'
                "    if validate_and_remove_local_systemd_marker "
                '"$work/manager-root-after.tsv"; then\n'
                "        printf 'unexpected oracle acceptance: %s\\n' \"$label\" >&2\n"
                "        exit 205\n"
                "    else\n"
                "        actual_status=$?\n"
                "    fi\n"
                '    [[ "$actual_status" -eq "$expected_status" ]] || {\n'
                "        printf 'unexpected oracle rejection: %s expected=%s actual=%s\\n' "
                '"$label" "$expected_status" "$actual_status" >&2\n'
                "        exit 206\n"
                "    }\n"
                '    [[ "$local_systemd_marker_cleanup_count" -eq "$before" ]] || '
                '[[ "$label" == residual && "$local_systemd_marker_cleanup_count" -eq 1 ]]\n'
                "}\n"
                + PCP_LOCAL_SYSTEMD_MARKER_ORACLE
                + "\n"
                + r"""
negative_count=0
start_case valid
write_receipt
write_marker
receipt_hash="$(/usr/bin/sha256sum -- "$work/manager-root-after.tsv")"
validate_and_remove_local_systemd_marker "$work/manager-root-after.tsv"
[[ "$local_systemd_marker_cleanup_count" -eq 1 ]]
[[ -z "$(/usr/bin/find "$work/run-systemd" -mindepth 1 -print -quit)" ]]
[[ "$(/usr/bin/sha256sum -- "$work/manager-root-after.tsv")" == "$receipt_hash" ]]

start_case missing
write_receipt
expect_rejected missing 169
negative_count=$((negative_count + 1))

start_case wrong-name
write_receipt
: >"$work/run-systemd/not-systemd-units-load"
expect_rejected wrong-name 169
negative_count=$((negative_count + 1))

start_case extra
write_receipt
write_marker
: >"$work/run-systemd/extra"
expect_rejected extra 169
negative_count=$((negative_count + 1))

start_case deeper
write_receipt
/usr/bin/mkdir -- "$work/run-systemd/systemd-units-load"
: >"$work/run-systemd/systemd-units-load/deeper"
expect_rejected deeper 170
negative_count=$((negative_count + 1))

start_case directory
write_receipt
/usr/bin/mkdir -- "$work/run-systemd/systemd-units-load"
expect_rejected directory 172
negative_count=$((negative_count + 1))

start_case symlink
write_receipt
/usr/bin/ln -s -- /dev/null "$work/run-systemd/systemd-units-load"
expect_rejected symlink 172
negative_count=$((negative_count + 1))

start_case socket
write_receipt
/usr/bin/python3 - "$work/run-systemd/systemd-units-load" <<'PY'
import socket, sys
s = socket.socket(socket.AF_UNIX)
s.bind(sys.argv[1])
s.close()
PY
expect_rejected socket 171
negative_count=$((negative_count + 1))

start_case fifo
write_receipt
/usr/bin/mkfifo -- "$work/run-systemd/systemd-units-load"
expect_rejected fifo 172
negative_count=$((negative_count + 1))

start_case nonzero
write_receipt
printf x >"$work/run-systemd/systemd-units-load"
/usr/bin/chmod 0444 -- "$work/run-systemd/systemd-units-load"
expect_rejected nonzero 173
negative_count=$((negative_count + 1))

start_case wrong-mode
write_receipt
write_marker
/usr/bin/chmod 0644 -- "$work/run-systemd/systemd-units-load"
expect_rejected wrong-mode 173
negative_count=$((negative_count + 1))

start_case wrong-owner
write_receipt
write_marker
/usr/bin/chown 1:0 -- "$work/run-systemd/systemd-units-load"
expect_rejected wrong-owner 173
negative_count=$((negative_count + 1))

start_case wrong-group
write_receipt
write_marker
/usr/bin/chown 0:1 -- "$work/run-systemd/systemd-units-load"
expect_rejected wrong-group 173
negative_count=$((negative_count + 1))

start_case wrong-links
write_receipt
write_marker
/usr/bin/ln -- "$work/run-systemd/systemd-units-load" "$work/marker-peer"
expect_rejected wrong-links 173
negative_count=$((negative_count + 1))

start_case wrong-device
write_receipt
write_marker
: >"$wrong_device_source"
/usr/bin/chown 0:0 -- "$wrong_device_source"
/usr/bin/chmod 0444 -- "$wrong_device_source"
[[ "$(/usr/bin/stat -c %d -- "$wrong_device_source")" != \
    "$(/usr/bin/stat -c %d -- "$work/run-systemd")" ]]
/usr/bin/mount --bind "$wrong_device_source" /run/systemd/systemd-units-load
nested_marker_mount=true
/usr/bin/mount --bind "$wrong_device_source" "$work/run-systemd/systemd-units-load"
private_marker_mount=true
expect_rejected wrong-device 173
negative_count=$((negative_count + 1))

start_case manager-endpoint
write_receipt
write_marker
/usr/bin/python3 - "$work/run-systemd/private" <<'PY'
import socket, sys
s = socket.socket(socket.AF_UNIX)
s.bind(sys.argv[1])
s.close()
PY
expect_rejected manager-endpoint 169
negative_count=$((negative_count + 1))

start_case binding-mismatch
write_receipt
write_marker
/usr/bin/umount -- /run/systemd
mounted=false
/usr/bin/mkdir -p -- "$fixture_root/binding-mismatch-mounted"
/usr/bin/mount --bind "$fixture_root/binding-mismatch-mounted" /run/systemd
mounted=true
/usr/bin/mount --make-private /run/systemd
systemd_underlay_mount_id="$(systemd_mount_id)"
: >/run/systemd/systemd-units-load
/usr/bin/chown 0:0 -- /run/systemd/systemd-units-load
/usr/bin/chmod 0444 -- /run/systemd/systemd-units-load
expect_rejected binding-mismatch 167
negative_count=$((negative_count + 1))

start_case cleanup-failure
write_receipt
write_marker
cleanup_source="$fixture_root/cleanup-source"
: >"$cleanup_source"
/usr/bin/chown 0:0 -- "$cleanup_source"
/usr/bin/chmod 0444 -- "$cleanup_source"
[[ "$(/usr/bin/stat -c %d -- "$cleanup_source")" == \
    "$(/usr/bin/stat -c %d -- "$work/run-systemd")" ]]
/usr/bin/mount --bind "$cleanup_source" /run/systemd/systemd-units-load
nested_marker_mount=true
/usr/bin/mount --bind "$cleanup_source" "$work/run-systemd/systemd-units-load"
private_marker_mount=true
expect_rejected cleanup-failure 178
[[ "$local_systemd_marker_cleanup_count" -eq 0 ]]
negative_count=$((negative_count + 1))

start_case receipt-drift
write_receipt
write_marker
/usr/bin/sed -i 's/regular\t444/regular\t644/' "$work/manager-root-after.tsv"
expect_rejected receipt-drift 163
negative_count=$((negative_count + 1))

start_case residual
write_receipt
write_marker
/usr/bin/cp -- /usr/bin/sync "$fixture_root/real-sync"
cat >"$fixture_root/sync-wrapper" <<EOF
#!/bin/sh
count_file='$fixture_root/sync-count'
count=0
if [ -f "\$count_file" ]; then count=\$(cat -- "\$count_file"); fi
count=\$((count + 1))
printf '%s\n' "\$count" >"\$count_file"
if [ "\$count" -eq 2 ]; then : >/run/systemd/residual; fi
exec '$fixture_root/real-sync' "\$@"
EOF
/usr/bin/chmod 0755 -- "$fixture_root/sync-wrapper"
/usr/bin/mount --bind "$fixture_root/sync-wrapper" /usr/bin/sync
sync_wrapper_mount=true
expect_rejected residual 180
[[ "$local_systemd_marker_cleanup_count" -eq 1 && -f /run/systemd/residual ]]
negative_count=$((negative_count + 1))

start_case one-removal
write_receipt
write_marker
validate_and_remove_local_systemd_marker "$work/manager-root-after.tsv"
write_marker
expect_rejected second-removal 175
[[ "$local_systemd_marker_cleanup_count" -eq 1 && \
    -f "$work/run-systemd/systemd-units-load" ]]
negative_count=$((negative_count + 1))

[[ "$negative_count" -eq 20 ]]
printf 'local_systemd_marker_oracle_valid=1 negatives=%s cleanup_count=1\n' \
    "$negative_count"
""",
                encoding="utf-8",
                newline="\n",
            )
            syntax = subprocess.run(
                [shutil.which("bash") or "bash", "-n", str(script)],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(syntax.returncode, 0, syntax.stdout + syntax.stderr)
            result: subprocess.CompletedProcess[str] | None = None
            ownership: subprocess.CompletedProcess[str] | None = None
            try:
                result = subprocess.run(
                    [
                        "sudo",
                        "-n",
                        "unshare",
                        "--mount",
                        "--fork",
                        shutil.which("bash") or "bash",
                        str(script),
                        str(root),
                    ],
                    text=True,
                    capture_output=True,
                    check=False,
                    timeout=120,
                )
            finally:
                ownership = subprocess.run(
                    [
                        "sudo",
                        "-n",
                        "chown",
                        "-R",
                        f"{os.getuid()}:{os.getgid()}",
                        "--",
                        str(root),
                    ],
                    text=True,
                    capture_output=True,
                    check=False,
                    timeout=30,
                )
            assert ownership is not None
            self.assertEqual(ownership.returncode, 0, ownership.stderr)
            assert result is not None
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(
                result.stdout,
                "local_systemd_marker_oracle_valid=1 negatives=20 cleanup_count=1\n",
            )

    def test_recovery_guard_condition_lookups_require_path_keys(self) -> None:
        harness = f"""{PCP_PHASE11_WATCHDOG_GUARD_LOOKUP}
systemd-analyze condition "ConditionPathExists=$watchdog_condition"
{PCP_PHASE14_PEER_GUARD_LOOKUP}
systemd-analyze condition "ConditionPathExists=$peer_condition"
"""
        _assert_recovery_guard_path_key_contract(harness)
        for resolved, direct in (
            (
                '"ConditionPathExists=$watchdog_condition"',
                '"ConditionPathExists=${recovery_guard_condition_paths[watchdog.service]}"',
            ),
            (
                '"ConditionPathExists=$peer_condition"',
                '"ConditionPathExists=${recovery_guard_condition_paths[zfs.target]}"',
            ),
        ):
            with self.subTest(direct=direct), self.assertRaises(AssertionError):
                _assert_recovery_guard_path_key_contract(
                    harness.replace(resolved, direct, 1)
                )

    @unittest.skipUnless(
        sys.platform.startswith("linux") and shutil.which("bash"),
        "requires Linux Bash",
    )
    def test_recovery_guard_wrong_domain_fails_before_condition(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary).resolve()
            guard = root / "90-hoardarr-offline-recovery.conf"
            guard.write_text("guard\n", encoding="ascii")
            script = root / "guard-key-domain.sh"
            script.write_text(
                "set -Eeuo pipefail\n"
                'guard="$1"\n'
                'unit="$2"\n'
                'mutation="$3"\n'
                "declare -A recovery_guard_paths_by_unit=()\n"
                "declare -A recovery_guard_path_owners=()\n"
                "declare -A recovery_guard_file_inodes=()\n"
                "declare -A recovery_guard_condition_paths=()\n"
                'if [[ "$mutation" != missing-unit ]]; then\n'
                '    recovery_guard_paths_by_unit["$unit"]="$guard"\n'
                "fi\n"
                'recovery_guard_path_owners["$guard"]="$unit"\n'
                'recovery_guard_file_inodes["$guard"]="$(stat -c %i -- "$guard")"\n'
                'case "$mutation" in\n'
                '    unit-domain) recovery_guard_condition_paths["$unit"]=/dev/null/wrong-domain ;;\n'
                "    missing-condition) ;;\n"
                '    empty-condition) recovery_guard_condition_paths["$guard"]="" ;;\n'
                '    wrong-owner) recovery_guard_path_owners["$guard"]=other.service ;;\n'
                '    wrong-path) recovery_guard_paths_by_unit["$unit"]="$guard.other" ;;\n'
                '    wrong-inode) recovery_guard_file_inodes["$guard"]=1 ;;\n'
                "    valid|missing-unit) "
                'recovery_guard_condition_paths["$guard"]=/dev/null/exact ;;\n'
                "    *) exit 210 ;;\n"
                "esac\n"
                'if [[ "$unit" == watchdog.service ]]; then\n'
                + PCP_PHASE11_WATCHDOG_GUARD_LOOKUP
                + "\n"
                '    [[ "$watchdog_condition" == /dev/null/exact ]]\n'
                "else\n"
                '    peer_guard="${recovery_guard_paths_by_unit[zfs.target]-}"\n'
                + PCP_PHASE14_PEER_GUARD_LOOKUP
                + "\n"
                '    [[ "$peer_condition" == /dev/null/exact ]]\n'
                "fi\n"
                "printf 'condition-command-reached:%s\\n' \"$unit\"\n",
                encoding="utf-8",
                newline="\n",
            )
            syntax = subprocess.run(
                [shutil.which("bash") or "bash", "-n", str(script)],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(syntax.returncode, 0, syntax.stdout + syntax.stderr)
            for unit in ("watchdog.service", "zfs.target"):
                valid = subprocess.run(
                    [
                        shutil.which("bash") or "bash",
                        str(script),
                        str(guard),
                        unit,
                        "valid",
                    ],
                    text=True,
                    capture_output=True,
                    check=False,
                )
                self.assertEqual(valid.returncode, 0, valid.stdout + valid.stderr)
                self.assertEqual(valid.stdout, f"condition-command-reached:{unit}\n")
                for mutation in (
                    "unit-domain",
                    "missing-unit",
                    "missing-condition",
                    "empty-condition",
                    "wrong-owner",
                    "wrong-path",
                    "wrong-inode",
                ):
                    with self.subTest(unit=unit, mutation=mutation):
                        rejected = subprocess.run(
                            [
                                shutil.which("bash") or "bash",
                                str(script),
                                str(guard),
                                unit,
                                mutation,
                            ],
                            text=True,
                            capture_output=True,
                            check=False,
                        )
                        self.assertNotEqual(rejected.returncode, 0)
                        self.assertEqual(rejected.stdout, "")

    @unittest.skipUnless(
        sys.platform.startswith("linux") and shutil.which("bash"),
        "requires Linux Bash",
    )
    def test_pcp_trace_trap_preserves_original_exit_status(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary).resolve()
            namespace = root / "namespace"
            namespace.mkdir()
            trace = root / "exit-preservation.trace"
            for index in range(4):
                _append_pcp_trace_phase(trace, index, "BEGIN")
                _append_pcp_trace_phase(trace, index, "PASS")
            script = root / "trace-exit.sh"
            script.write_text(
                "set -Eeuo pipefail\n"
                + _pcp_trace_shell_prelude()
                + "\ntrace_begin 05-mount-namespace mount-namespace\nexit 73\n",
                encoding="utf-8",
                newline="\n",
            )
            result = subprocess.run(
                [
                    shutil.which("bash") or "bash",
                    str(script),
                    "unused-1",
                    "unused-2",
                    "unused-3",
                    "unused-4",
                    str(trace),
                ],
                capture_output=True,
                check=False,
                env={**os.environ, "MSYS": "winsymlinks:sys"},
                text=True,
            )
            try:
                trace_text, trace_status = _validate_pcp_trace(trace, root, namespace)
            except AssertionError as exc:
                self.fail(result.stdout + result.stderr + str(exc))
            self.assertEqual(result.returncode, 73, trace_text)
            self.assertEqual(trace_status, result.returncode, trace_text)

    @unittest.skipUnless(sys.platform.startswith("linux"), "requires Linux mounts")
    def test_real_noble_pcp_postinst_presets_with_production_service_guard(
        self,
    ) -> None:
        payload_path = ROOT / "packaging" / "appliance" / "install-offline-payload.sh"
        verifier_path = ROOT / "packaging" / "appliance" / "verify-offline-appliance.sh"
        self.assertEqual(
            hashlib.sha256(payload_path.read_bytes()).hexdigest(),
            "3116215f4f2dde376f591b06cb192b3cc725e4261885c5a0bc88e23b8867005b",
        )
        self.assertEqual(
            hashlib.sha256(verifier_path.read_bytes()).hexdigest(),
            "f188d76e7c19ba38472a5125c68d53e428bcf095d36878ac688e56a93fc627ad",
        )
        payload = payload_path.read_text(encoding="utf-8")

        def shell_function(name: str) -> str:
            if name == "write_retained_recovery_guard_manifest":
                start = payload.index(f"{name}() {{\n")
                end = payload.index("\nprepare_temporary_unit_mask() {", start)
                return payload[start : end + 1]
            match = re.search(
                rf"^{re.escape(name)}\(\) \{{\n.*?^\}}\n",
                payload,
                flags=re.MULTILINE | re.DOTALL,
            )
            self.assertIsNotNone(match, f"missing production function {name}")
            assert match is not None
            return match.group(0)

        required = (
            "apt-get",
            "bash",
            "deb-systemd-helper",
            "dpkg-deb",
            "dpkg-query",
            "sudo",
            "systemd-analyze",
            "systemctl",
            "unshare",
        )
        missing = [command for command in required if shutil.which(command) is None]
        self.assertEqual(missing, [], f"missing Noble service-guard tools: {missing}")
        sudo = subprocess.run(
            ["sudo", "-n", "true"], text=True, capture_output=True, check=False
        )
        self.assertEqual(sudo.returncode, 0, sudo.stderr)

        expected_version = "6.2.0-1.1build4"
        expected_deb_sha256 = (
            "5941a5aabb5e873883b1f4ac8e5e577a3617a8c9b7cb1918a3baea6e1d1b89a9"
        )
        expected_postinst_sha256 = (
            "a964a5c5a17ad154eec1068fe984c37fa9cc1642d85fe5dc393f6022afe6440c"
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            trace_path = root / "pcp-harness.trace"
            namespace_path = (root / "namespace").resolve()
            self.assertEqual(namespace_path.parent, root.resolve())
            self.assertEqual(trace_path.resolve(strict=False).parent, root.resolve())
            self.assertNotEqual(trace_path.resolve(strict=False), namespace_path)
            _append_pcp_trace_phase(trace_path, 0, "BEGIN")
            _append_pcp_trace_phase(trace_path, 0, "PASS")
            _append_pcp_trace_phase(trace_path, 1, "BEGIN")
            download = subprocess.run(
                ["apt-get", "download", f"pcp={expected_version}"],
                cwd=root,
                text=True,
                capture_output=True,
                check=False,
                timeout=240,
            )
            self.assertEqual(download.returncode, 0, download.stdout + download.stderr)
            _append_pcp_trace_phase(trace_path, 1, "PASS")
            debs = list(root.glob("pcp_*.deb"))
            self.assertEqual(len(debs), 1, [path.name for path in debs])
            _append_pcp_trace_phase(trace_path, 2, "BEGIN")
            self.assertEqual(
                hashlib.sha256(debs[0].read_bytes()).hexdigest(), expected_deb_sha256
            )
            _append_pcp_trace_phase(trace_path, 2, "PASS")
            _append_pcp_trace_phase(trace_path, 3, "BEGIN")
            control = root / "control"
            data = root / "data"
            subprocess.run(["dpkg-deb", "-e", str(debs[0]), str(control)], check=True)
            subprocess.run(["dpkg-deb", "-x", str(debs[0]), str(data)], check=True)
            postinst = control / "postinst"
            self.assertEqual(
                hashlib.sha256(postinst.read_bytes()).hexdigest(),
                expected_postinst_sha256,
            )
            _append_pcp_trace_phase(trace_path, 3, "PASS")
            policy = json.loads(
                (ROOT / "packaging" / "offline" / "package-policy.json").read_text(
                    encoding="utf-8"
                )
            )
            denied_units = policy["denied_units"]
            denied_path = root / "denied-units.txt"
            denied_path.write_text(
                "".join(f"{unit}\n" for unit in denied_units), encoding="utf-8"
            )
            readback_validator = root / "service-policy-readback-validator.py"
            readback_validator.write_text(
                _service_policy_readback_validator(payload),
                encoding="utf-8",
                newline="\n",
            )
            readback_matrix = root / "compatibility-matrix.json"
            readback_matrix.write_text(
                json.dumps({"denied_units": denied_units}) + "\n",
                encoding="utf-8",
                newline="\n",
            )
            finalizer_source = root / "disable-unmasked-units.sh"
            finalizer_source.write_text(
                shell_function("disable_unmasked_units"),
                encoding="utf-8",
                newline="\n",
            )
            finalizer_sha256 = hashlib.sha256(finalizer_source.read_bytes()).hexdigest()
            f19_snapshot = root / "f19-snapshot.py"
            f19_snapshot.write_text(
                F19_SNAPSHOT_SCRIPT,
                encoding="utf-8",
                newline="\n",
            )
            f20_snapshot = root / "f20-snapshot.py"
            f20_snapshot.write_text(
                F20_SNAPSHOT_SCRIPT,
                encoding="utf-8",
                newline="\n",
            )
            f21_capture_error = root / "f21-capture-error.py"
            f21_capture_error.write_text(
                F21_CAPTURE_ERROR_SCRIPT,
                encoding="utf-8",
                newline="\n",
            )
            f29_f21_runner = root / "f29-f21-runner.py"
            f29_f21_runner.write_text(
                F29_F21_RUNNER_SCRIPT,
                encoding="utf-8",
                newline="\n",
            )
            f29_outer_runner = root / "f29-outer-runner.py"
            f29_outer_runner.write_text(
                F29_OUTER_RUNNER_SCRIPT,
                encoding="utf-8",
                newline="\n",
            )
            f21_capture_error_sha256 = hashlib.sha256(
                f21_capture_error.read_bytes()
            ).hexdigest()
            f29_f21_runner_sha256 = hashlib.sha256(
                f29_f21_runner.read_bytes()
            ).hexdigest()
            host_sysv_before, host_sysv_before_sha256 = _capture_f20_host_manifest()

            harness = root / "pcp-service-guard.sh"
            harness.write_text(
                "\n".join(
                    (
                        "set -Eeuo pipefail",
                        _pcp_trace_shell_prelude(),
                        PCP_MANAGER_ROOT_SNAPSHOT_FUNCTION,
                        shell_function("install_service_start_guard"),
                        shell_function("entry_is_root_owned"),
                        shell_function("validate_preserved_unit_objects"),
                        shell_function("prepare_recovery_unit_guard"),
                        shell_function("validate_recovery_unit_guards"),
                        shell_function("retain_recovery_unit_guards"),
                        shell_function("remove_recovery_unit_guards"),
                        shell_function("write_retained_recovery_guard_manifest"),
                        shell_function("prepare_temporary_unit_mask"),
                        shell_function("cleanup_temporary_masks"),
                        shell_function("cleanup_service_guards"),
                        shell_function("remove_denied_unit_enablement_links"),
                        _instrument_f23_disable_unmasked_units(
                            shell_function("disable_unmasked_units")
                        ),
                        r"""
postinst="$1"
data="$2"
work="$3"
denied_file="$4"
readback_validator="$6"
readback_matrix="$7"
f19_snapshot="$8"
f19_finalizer_source="$9"
f20_snapshot="${10}"
f21_capture_error="${11}"
f29_f21_runner="${12}"
f21_capture_error_sha256="${13}"
f29_outer_runner="${14}"
f29_f21_runner_sha256="${15}"
trace_begin 05-mount-namespace mount-namespace
mount --make-rprivate /
mkdir -p "$work"/{etc-systemd,systemd-state,run-systemd,usr-sbin,wrappers,state,install,f20-sysv/init.d}
cp -a "$(command -v chroot)" "$work/usr-sbin/chroot"
cp -a "$data/usr/lib/systemd/system/." "$work/vendor-units/" 2>/dev/null || {
    mkdir -p "$work/vendor-units"
    cp -a "$data/usr/lib/systemd/system/." "$work/vendor-units/"
}
while IFS= read -r unit; do
    [[ "$unit" =~ ^[A-Za-z0-9@_.:-]+\.(service|socket|timer|target)$ ]]
    unit_path="$work/vendor-units/$unit"
    [[ -e "$unit_path" ]] && continue
    case "$unit" in
        *.service) body=$'[Service]\nType=oneshot\nExecStart=/bin/true' ;;
        *.socket) body=$'[Socket]\nListenStream=/run/hoardarr-test-'"${unit//[^A-Za-z0-9]/-}" ;;
        *.timer) body=$'[Timer]\nOnBootSec=1h' ;;
        *.target) body= ;;
    esac
    printf '[Unit]\nDescription=Hoardarr denied-unit preset regression\n%s\n[Install]\nWantedBy=multi-user.target\n' \
        "$body" >"$unit_path"
done <"$denied_file"
# Guarantee one supported static-style unit so intentional retained-guard
# behavior is exercised independently of the host package set.
printf '%s\n' \
    '[Unit]' \
    'Description=Hoardarr static denied-unit regression' \
    '[Service]' \
    'Type=oneshot' \
    'ExecStart=/bin/true' \
    >"$work/vendor-units/watchdog.service"
printf '%s\n' \
    '[Unit]' \
    'Description=Hoardarr static peer denied-unit regression' \
    >"$work/vendor-units/zfs.target"
mount --bind "$work/vendor-units" /usr/lib/systemd/system
mount --bind "$work/etc-systemd" /etc/systemd/system
mount --bind "$work/systemd-state" /var/lib/systemd
mount --bind "$work/run-systemd" /run/systemd
mount --bind "$work/usr-sbin" /usr/sbin

# Preserve the exact observed SysV objects while containing every possible
# helper mutation in this private mount namespace.
if [[ -e /etc/init.d/iscsid || -L /etc/init.d/iscsid ]]; then
    cp -a -- /etc/init.d/iscsid "$work/f20-sysv/init.d/iscsid"
fi
for level in 0 1 2 3 4 5 6 S; do
    private_rc="$work/f20-sysv/rc${level}.d"
    source_rc="/etc/rc${level}.d"
    mkdir -- "$private_rc"
    if [[ -d "$source_rc" && ! -L "$source_rc" ]]; then
        while IFS= read -r -d '' source_entry; do
            cp -a -- "$source_entry" "$private_rc/"
        done < <(find "$source_rc" -xdev -mindepth 1 -maxdepth 1 \
            \( -name '*iscsid*' -o -name '*open-iscsi*' \) -print0)
    fi
done
cp --dereference --preserve=mode,ownership,timestamps -- \
    /usr/lib/systemd/systemd-sysv-install "$work/f20-helper-real"
sha256sum -- "$work/f20-helper-real" | awk '{print $1}' \
    >"$work/f20-helper-source.sha256"
chmod 0600 -- "$work/f20-helper-source.sha256"
cat >"$work/f20-helper-wrapper.body" <<'EOF'
/usr/bin/python3 - __F25_EVIDENCE_ROOT__ "$0" __F25_REAL_HELPER__ \
    __F25_SOURCE_HASH__ __F25_EXPECTED_PATH__ "$@" <<'PY'
import hashlib, json, os, pathlib, re, stat, sys

root=pathlib.Path(sys.argv[1])
wrapper=pathlib.Path(sys.argv[2])
real_helper=pathlib.Path(sys.argv[3])
source_hash_path=pathlib.Path(sys.argv[4])
expected_path=sys.argv[5]
args=sys.argv[6:]
partial=root/"f25-helper-entry.json.partial"
destination=root/"f25-helper-entry.json"
allowlist={"--root=/","disable","iscsid"}

def identity_mode(path,expected_mode):
    try: metadata=path.lstat()
    except OSError: return False
    return stat.S_ISREG(metadata.st_mode) and not path.is_symlink() and metadata.st_uid==0 and metadata.st_gid==0 and stat.S_IMODE(metadata.st_mode)==expected_mode and metadata.st_nlink==1

def digest(path):
    value=hashlib.sha256()
    try:
        with path.open("rb") as stream:
            for block in iter(lambda:stream.read(1024*1024),b""): value.update(block)
    except OSError: return None
    return value.hexdigest()

try:
    source_hash=source_hash_path.read_text("ascii").strip()
except (OSError,UnicodeError):
    source_hash=""
wrapper_source=root/"f20-helper-wrapper"
wrapper_identity=identity_mode(wrapper,0o755) and identity_mode(wrapper_source,0o755)
if wrapper_identity:
    wrapper_metadata=wrapper.stat(); source_metadata=wrapper_source.stat()
    wrapper_identity=wrapper_metadata.st_dev==source_metadata.st_dev and wrapper_metadata.st_ino==source_metadata.st_ino
real_identity=identity_mode(real_helper,0o755) and re.fullmatch(r"[0-9a-f]{64}",source_hash) is not None and digest(real_helper)==source_hash
classified=[]
for position,arg in enumerate(args[:16]):
    raw=os.fsencode(arg)
    if arg in allowlist:
        classified.append({"position":position,"classification":"ALLOWLISTED","value":arg})
    else:
        classified.append({"position":position,"classification":"UNEXPECTED","byte_length":len(raw),"sha256":hashlib.sha256(raw).hexdigest()})
predicates={
    "expected_argc":len(args)==3,
    "exact_vector":args==["--root=/","disable","iscsid"],
    "systemd_offline":os.environ.get("SYSTEMD_OFFLINE")=="1",
    "dpkg_maintscripts_package":os.environ.get("DPKG_MAINTSCRIPT_PACKAGE")=="pcp",
    "dpkg_maintscripts_name":os.environ.get("DPKG_MAINTSCRIPT_NAME")=="postinst",
    "exact_private_path":os.environ.get("PATH")==expected_path,
    "wrapper_identity_mode":wrapper_identity,
    "real_helper_identity_mode":real_identity,
}
receipt={"schema_version":1,"entry_reached":True,"argc":len(args),"argv":classified,"predicates":predicates,"guard_outcome":"ACCEPTED" if all(predicates.values()) else "REJECTED"}
encoded=(json.dumps(receipt,separators=(",",":"))+"\n").encode("ascii")
if len(encoded)>8192: raise SystemExit(127)
if os.path.lexists(destination) or os.path.lexists(partial): raise SystemExit(127)
flags=os.O_WRONLY|os.O_CREAT|os.O_EXCL|getattr(os,"O_NOFOLLOW",0)|getattr(os,"O_CLOEXEC",0)
fd=os.open(partial,flags,0o600)
try:
    with os.fdopen(fd,"wb",closefd=True) as stream:
        stream.write(encoded); stream.flush(); os.fsync(stream.fileno())
    os.link(partial,destination,follow_symlinks=False)
    os.unlink(partial)
    directory_fd=os.open(root,os.O_RDONLY|getattr(os,"O_DIRECTORY",0)|getattr(os,"O_CLOEXEC",0))
    try: os.fsync(directory_fd)
    finally: os.close(directory_fd)
except BaseException:
    raise
metadata=destination.lstat()
if not stat.S_ISREG(metadata.st_mode) or destination.is_symlink() or metadata.st_uid!=0 or metadata.st_gid!=0 or stat.S_IMODE(metadata.st_mode)!=0o600 or metadata.st_nlink!=1 or destination.read_bytes()!=encoded: raise SystemExit(127)
PY
set -u
umask 077
if [[ "$#" -ne 3 || "$1" != --root=/ || "$2" != disable || "$3" != iscsid ]]; then exit 125; fi
[[ "${SYSTEMD_OFFLINE-}" == 1 && "${DPKG_MAINTSCRIPT_PACKAGE-}" == pcp && \
    "${DPKG_MAINTSCRIPT_NAME-}" == postinst && "$PATH" == "__F20_PATH__" ]] || exit 125
for evidence in "$evidence_root"/f20-helper-{invocation.tsv,stdout.bin,stderr.bin,status.txt}; do
    if [[ -e "$evidence" || -L "$evidence" ]]; then
        printf 'repeat\n' >"$evidence_root/f20-helper-repeat.txt"
        chmod 0600 -- "$evidence_root/f20-helper-repeat.txt"
        exit 124
    fi
done
printf 'F20HELPER\t1\nARGC\t3\nARGV0\t--root=/\nARGV1\tdisable\nARGV2\tiscsid\nENV\tSYSTEMD_OFFLINE=1\n' \
    >"$evidence_root/f20-helper-invocation.tsv" || exit 126
chmod 0600 -- "$evidence_root/f20-helper-invocation.tsv" || exit 126
"$real_helper" "$@" >"$evidence_root/f20-helper-stdout.bin.partial" \
    2>"$evidence_root/f20-helper-stderr.bin.partial"
helper_status=$?
evidence_status=0
for partial in "$evidence_root"/f20-helper-{stdout,stderr}.bin.partial; do
    [[ -f "$partial" && ! -L "$partial" && "$(stat -c %s -- "$partial")" -le 32768 ]] || evidence_status=126
    (( evidence_status != 0 )) || chmod 0600 -- "$partial" || evidence_status=126
done
(( evidence_status != 0 )) || mv -- "$evidence_root/f20-helper-stdout.bin.partial" "$evidence_root/f20-helper-stdout.bin" || evidence_status=126
(( evidence_status != 0 )) || mv -- "$evidence_root/f20-helper-stderr.bin.partial" "$evidence_root/f20-helper-stderr.bin" || evidence_status=126
(( evidence_status != 0 )) || printf '%s\n' "$helper_status" >"$evidence_root/f20-helper-status.txt" || evidence_status=126
(( evidence_status != 0 )) || chmod 0600 -- "$evidence_root/f20-helper-status.txt" || evidence_status=126
(( evidence_status != 0 )) || sync -f "$evidence_root/f20-helper-invocation.tsv" "$evidence_root/f20-helper-stdout.bin" \
    "$evidence_root/f20-helper-stderr.bin" "$evidence_root/f20-helper-status.txt" || evidence_status=126
if (( evidence_status != 0 )); then
    rm -f -- "$evidence_root/f20-helper-status.txt"
fi
exit "$helper_status"
EOF
python3 - "$work/f20-helper-wrapper.body" "$work/f20-helper-wrapper" "$work" <<'PY'
import pathlib, shlex, sys
source=pathlib.Path(sys.argv[1]); destination=pathlib.Path(sys.argv[2]); work=pathlib.Path(sys.argv[3])
body=source.read_text(encoding="utf-8")
expected_path=str(work/"wrappers")+":/usr/sbin:/usr/bin:/bin"
header="#!/bin/bash\n"
for marker,value in (
    ("__F25_EVIDENCE_ROOT__",str(work)),
    ("__F25_REAL_HELPER__",str(work/"f20-helper-real")),
    ("__F25_SOURCE_HASH__",str(work/"f20-helper-source.sha256")),
    ("__F25_EXPECTED_PATH__",expected_path),
): body=body.replace(marker,shlex.quote(value))
body=body.replace("__F20_PATH__",expected_path)
body=body.replace("set -u\n","readonly evidence_root="+shlex.quote(str(work))+"\nreadonly real_helper="+shlex.quote(str(work/"f20-helper-real"))+"\nset -u\n",1)
destination.write_text(header+body,encoding="utf-8")
PY
chmod 0755 -- "$work/f20-helper-wrapper"
mount --bind "$work/f20-sysv/init.d" /etc/init.d
for level in 0 1 2 3 4 5 6 S; do
    mount --bind "$work/f20-sysv/rc${level}.d" "/etc/rc${level}.d"
done
mount --bind "$work/f20-helper-wrapper" /usr/lib/systemd/systemd-sysv-install
for command in dpkg-maintscript-helper touch chown groupadd useradd; do
    cat >"$work/wrappers/$command" <<'EOF'
#!/bin/sh
case "$(basename "$0"):$1" in
    dpkg-maintscript-helper:supports) exit 0 ;;
esac
exit 0
EOF
    chmod 0755 "$work/wrappers/$command"
done
cat >"$work/wrappers/chmod" <<'EOF'
#!/bin/sh
# Package-maintainer chmod calls remain isolated.  Delegate only the exact
# recovery-guard temporary-file operation performed by the extracted
# production helper, inside this fixture's private systemd bind mount.
if [ "$#" -ne 3 ] || [ "$1" != 0644 ] || [ "$2" != -- ]; then
    exit 0
fi
candidate=$3
case "$candidate" in
    "$HOARDARR_TEST_RECOVERY_ROOT"/*) ;;
    *) exit 0 ;;
esac
case "$candidate" in
    *//*|*/../*|*/./*) exit 0 ;;
esac
parent=${candidate%/*}
name=${candidate##*/}
case "$name" in
    .hoardarr-recovery.??????) ;;
    *) exit 0 ;;
esac
suffix=${name#.hoardarr-recovery.}
case "$suffix" in
    *[!A-Za-z0-9]*) exit 0 ;;
esac
parent_name=${parent##*/}
case "$parent_name" in
    *.d) unit=${parent_name%.d} ;;
    *) exit 0 ;;
esac
case "$unit" in
    ''|*[!A-Za-z0-9@_.:-]*) exit 0 ;;
esac
unit_count=0
while IFS= read -r denied_unit; do
    if [ "$denied_unit" = "$unit" ]; then
        unit_count=$((unit_count + 1))
    fi
done <"$HOARDARR_TEST_DENIED_UNITS"
[ "$unit_count" -eq 1 ] || exit 0
[ -f "$candidate" ] && [ ! -L "$candidate" ] || exit 0
[ "$(/usr/bin/stat -c %h -- "$candidate" 2>/dev/null)" = 1 ] || exit 0
canonical_root=$(/usr/bin/readlink -e -- "$HOARDARR_TEST_RECOVERY_ROOT") || exit 0
canonical_parent=$(/usr/bin/readlink -e -- "$parent") || exit 0
canonical_target=$(/usr/bin/readlink -e -- "$candidate") || exit 0
[ "$canonical_parent" = "$canonical_root/$unit.d" ] || exit 0
[ "$canonical_target" = "$canonical_parent/$name" ] || exit 0
/usr/bin/chmod 0644 -- "$candidate" || exit $?
[ "$(/usr/bin/stat -c %a -- "$candidate" 2>/dev/null)" = 644 ] || exit 1
printf '%s\t%s\t0644\n' "$unit" "$name" >>"$HOARDARR_TEST_CHMOD_RECEIPT" || exit 1
exit 0
EOF
chmod 0755 "$work/wrappers/chmod"
cat >"$work/wrappers/getent" <<'EOF'
#!/bin/sh
exit 0
EOF
chmod 0755 "$work/wrappers/getent"
export HOARDARR_TEST_RECOVERY_ROOT=/etc/systemd/system
export HOARDARR_TEST_DENIED_UNITS="$denied_file"
export HOARDARR_TEST_CHMOD_RECEIPT="$work/chmod-delegated.tsv"
: >"$HOARDARR_TEST_CHMOD_RECEIPT"
export PATH="$work/wrappers:/usr/sbin:/usr/bin:/bin"
export DPKG_MAINTSCRIPT_PACKAGE=pcp
export DPKG_MAINTSCRIPT_NAME=postinst
export SYSTEMD_OFFLINE=1
pcp_units=(pcp-reboot-init.service pmcd.service pmlogger.service pmie.service pmproxy.service)
mapfile -t all_denied_units <"$denied_file"

# Prove malformed and unrelated requests remain isolated no-ops.  These
# fixtures are wholly inside the disposable private mount namespace.
negative_unit=${all_denied_units[0]}
negative_dir="$HOARDARR_TEST_RECOVERY_ROOT/$negative_unit.d"
wrong_dir="$HOARDARR_TEST_RECOVERY_ROOT/not-denied.service.d"
mkdir -- "$negative_dir" "$wrong_dir"
negative_target="$negative_dir/.hoardarr-recovery.NEG001"
wrong_name="$negative_dir/not-a-recovery-temporary"
wrong_directory="$wrong_dir/.hoardarr-recovery.DIR001"
outside_target="$work/.hoardarr-recovery.OUT001"
unrelated_target="$work/package-mode-target"
for path in "$negative_target" "$wrong_name" "$wrong_directory" \
    "$outside_target" "$unrelated_target"; do
    : >"$path"
    /usr/bin/chmod 0600 -- "$path"
done
symlink_target="$negative_dir/.hoardarr-recovery.SYM001"
hardlink_source="$HOARDARR_TEST_RECOVERY_ROOT/.hoardarr-hardlink-negative-source"
hardlink_target="$negative_dir/.hoardarr-recovery.LNK001"
ln -s -- "$outside_target" "$symlink_target"
: >"$hardlink_source"
/usr/bin/chmod 0600 -- "$hardlink_source"
ln -- "$hardlink_source" "$hardlink_target"
[[ "$hardlink_source" == "$HOARDARR_TEST_RECOVERY_ROOT/"* ]]
[[ "${hardlink_source%/*}" == "$HOARDARR_TEST_RECOVERY_ROOT" ]]
[[ "$hardlink_source" != *.d/* ]]
[[ "$hardlink_source" != */../* ]]
[[ -f "$hardlink_source" && ! -L "$hardlink_source" ]]
[[ "$(/usr/bin/stat -c %d -- "$hardlink_source")" == \
    "$(/usr/bin/stat -c %d -- "$hardlink_target")" ]]
[[ "$(/usr/bin/stat -c %i -- "$hardlink_source")" == \
    "$(/usr/bin/stat -c %i -- "$hardlink_target")" ]]
[[ "$(/usr/bin/stat -c %h -- "$hardlink_source")" == 2 ]]
[[ "$(/usr/bin/stat -c %h -- "$hardlink_target")" == 2 ]]
"$work/wrappers/chmod" 0600 -- "$negative_target"
"$work/wrappers/chmod" 0644 "$negative_target"
"$work/wrappers/chmod" 0644 -- "$negative_target" extra
"$work/wrappers/chmod" 0644 -- "$negative_dir/../$negative_unit.d/.hoardarr-recovery.NEG001"
"$work/wrappers/chmod" 0644 -- "$outside_target"
"$work/wrappers/chmod" 0644 -- "$wrong_name"
"$work/wrappers/chmod" 0644 -- "$wrong_directory"
"$work/wrappers/chmod" 0644 -- "$symlink_target"
"$work/wrappers/chmod" 0644 -- "$hardlink_target"
"$work/wrappers/chmod" 0644 -- "$unrelated_target"
for path in "$negative_target" "$wrong_name" "$wrong_directory" \
    "$outside_target" "$unrelated_target" "$hardlink_source" "$hardlink_target"; do
    [[ "$(/usr/bin/stat -c %a -- "$path")" == 600 ]]
done
[[ ! -s "$HOARDARR_TEST_CHMOD_RECEIPT" ]]
rm -f -- "$symlink_target" "$hardlink_target" "$hardlink_source" \
    "$negative_target" "$wrong_name" "$wrong_directory" "$outside_target" \
    "$unrelated_target"
[[ ! -e "$hardlink_target" && ! -L "$hardlink_target" ]]
[[ ! -e "$hardlink_source" && ! -L "$hardlink_source" ]]
rmdir -- "$negative_dir" "$wrong_dir"
trace_pass

# Reproduce the accepted F7A defect using the exact package script.
trace_begin 06-old-failure old-failure
for unit in "${pcp_units[@]}"; do ln -s /dev/null "/etc/systemd/system/$unit"; done
old_status=0
"$postinst" configure >"$work/old.log" 2>&1 || old_status=$?
(( old_status != 0 ))
grep -Fq 'Failed to preset unit' "$work/old.log"
find "$work/etc-systemd" -mindepth 1 -maxdepth 1 -delete
find "$work/systemd-state" -mindepth 1 -delete
trace_pass

# Exercise the production classification and exact start guard.
trace_begin 07-guard-preparation guard-preparation
target="/"
mask_root=/etc/systemd/system
install_root="$work/install"
state_root="$work/state"
policy=/usr/sbin/policy-rc.d
policy_backup="$install_root/policy-rc.d.original"
policy_state=absent
temporary_masks=()
declare -A temporary_mask_inodes=()
temporary_masks_cleanup_complete=false
policy_cleanup_complete=false
service_guard_cleanup_complete=false
declare -A preserved_unit_masks=()
declare -A preserved_unit_mask_inodes=()
declare -A preserved_package_aliases=()
declare -A preserved_package_alias_inodes=()
declare -A preserved_package_alias_targets=()
declare -A preserved_package_alias_canonical_units=()
declare -A policy_guarded_canonical_units=()
declare -A policy_guarded_absent_units=()
recovery_guard_files=()
recovery_guard_created_directories=()
declare -A recovery_guard_file_inodes=()
declare -A recovery_guard_contents=()
declare -A recovery_guard_condition_paths=()
declare -A recovery_guard_directory_inodes=()
declare -A recovery_guard_paths_by_unit=()
declare -A recovery_guard_path_owners=()
declare -A recovery_guard_paths_retained=()
declare -A recovery_guard_retained_states=()
recovery_guards_cleanup_complete=false
recovery_guard_authorization_root=/etc/systemd/system/.hoardarr-service-start-authorized
denied_units=("${all_denied_units[@]}")
denied_units_finalized=false
service_readback_complete=false
package_transaction_started=false
install_service_start_guard
# A pre-existing authorization namespace is never trusted during guard setup.
mkdir -- "$recovery_guard_authorization_root"
if prepare_recovery_unit_guard corosync.service >/dev/null 2>&1; then exit 95; fi
rmdir -- "$recovery_guard_authorization_root"
for unit in "${denied_units[@]}"; do
    prepare_temporary_unit_mask "$mask_root/$unit" "$unit"
    [[ ! -e "$mask_root/$unit" && ! -L "$mask_root/$unit" ]]
    prepare_recovery_unit_guard "$unit"
done
[[ "$(wc -l <"$HOARDARR_TEST_CHMOD_RECEIPT")" -eq "${#denied_units[@]}" ]]
declare -A delegated_chmod_units=()
while IFS=$'\t' read -r unit temporary_name delegated_mode extra; do
    [[ -z "$extra" && "$delegated_mode" == 0644 ]]
    [[ "$temporary_name" == .hoardarr-recovery.?????? ]]
    [[ "$temporary_name" != *[!A-Za-z0-9.\-]* ]]
    [[ -z "${delegated_chmod_units[$unit]+present}" ]]
    delegated_chmod_units[$unit]=$temporary_name
done <"$HOARDARR_TEST_CHMOD_RECEIPT"
for unit in "${denied_units[@]}"; do
    [[ -n "${delegated_chmod_units[$unit]+present}" ]]
done
chmod_receipt_hash="$(sha256sum -- "$HOARDARR_TEST_CHMOD_RECEIPT" | awk '{print $1}')"
start_status=0
"$policy" pmcd.service start || start_status=$?
[[ "$start_status" -eq 101 ]]
trace_pass

trace_begin 08-pcp-configure pcp-configure
"$postinst" configure >"$work/corrected.log" 2>&1
! grep -Fq 'Failed to preset unit' "$work/corrected.log"
[[ "$(sha256sum -- "$HOARDARR_TEST_CHMOD_RECEIPT" | awk '{print $1}')" == \
    "$chmod_receipt_hash" ]]
trace_pass
trace_begin 09-all-denied-presets all-denied-presets
phase09_outcomes="$work/f19-phase09.tsv"
: >"$phase09_outcomes"
exec 18>"$phase09_outcomes"
for unit in "${denied_units[@]}"; do
    SYSTEMD_OFFLINE=1 systemctl preset "$unit"
    case "$unit" in
        corosync.service|iscsid.service|iscsid.socket|iscsi.service|open-iscsi.service)
            printf '%s\t0\n' "$unit" >&18
            ;;
    esac
done >"$work/all-denied-presets.log" 2>&1
exec 18>&-
[[ "$(wc -l <"$phase09_outcomes")" -eq 5 ]]
! grep -Fq 'Failed to preset unit' "$work/all-denied-presets.log"
[[ "$(sha256sum -- "$HOARDARR_TEST_CHMOD_RECEIPT" | awk '{print $1}')" == \
    "$chmod_receipt_hash" ]]
trace_pass
""",
                        _pcp_phase_ten_with_causal_proof(),
                        r"""
trace_begin 11-interrupted-retention interrupted-retention
package_transaction_started=true
interrupted_status=0
# The old marker namespace cannot authorize the structurally false condition,
# and its appearance makes interrupted recovery evidence fail closed.
mkdir -- "$recovery_guard_authorization_root"
: >"$recovery_guard_authorization_root/watchdog.service"
rm -f -- "$state_root/service-guard-recovery.txt"
cleanup_service_guards >/dev/null 2>&1 || interrupted_status=$?
[[ "$interrupted_status" -ne 0 ]]
[[ ! -e "$state_root/service-guard-recovery.txt" ]]
""",
                        PCP_PHASE11_WATCHDOG_GUARD_LOOKUP,
                        r"""
systemd-analyze condition \
    "ConditionPathExists=$watchdog_condition" \
    >/dev/null 2>&1 && exit 98
rm -f -- "$recovery_guard_authorization_root/watchdog.service"
rmdir -- "$recovery_guard_authorization_root"
interrupted_status=0
cleanup_service_guards >/dev/null 2>&1 || interrupted_status=$?
[[ "$interrupted_status" -ne 0 ]]
validate_recovery_unit_guards
grep -Fq 'finalization=false readback=false' "$state_root/service-guard-recovery.txt"
for path in "${recovery_guard_files[@]}"; do
    systemd-analyze condition "ConditionPathExists=${recovery_guard_condition_paths[$path]}" \
        >/dev/null 2>&1 && exit 96
done
trace_pass
trace_begin 12-final-disable-readback final-disable-readback
f19_before="$work/f19-before.json"
f19_after="$work/f19-after.json"
f19_command_trace="$work/f19-command.trace"
f19_capture_status_file="$work/f19-capture-status.txt"
f20_before="$work/f20-before.json"
f20_after="$work/f20-after.json"
f20_capture_status_file="$work/f20-capture-status.txt"
f21_capture_error_receipt="$work/f21-capture-error.json"
f29_f21_attempt_receipt="$work/f29-f21-attempt.json"
f29_outer_f19_receipt="$work/f29-outer-f19-after.json"
f29_outer_f20_receipt="$work/f29-outer-f20-after.json"
capture_f29_outer_invocation() {
    local stage="$1"
    local snapshot_status="$2"
    local snapshot_stderr="$3"
    local receipt="$4"
    local capture_prefix="$work/f29-direct-$stage"
    local capture_stdout="$capture_prefix.stdout"
    local capture_stderr="$capture_prefix.stderr"
    local capture_status="$capture_prefix.status"
    local outer_status=0
    [[ ! -e "$capture_stdout" && ! -L "$capture_stdout" ]]
    [[ ! -e "$capture_stderr" && ! -L "$capture_stderr" ]]
    [[ ! -e "$capture_status" && ! -L "$capture_status" ]]
    install -m 0600 -- /dev/null "$capture_stdout"
    install -m 0600 -- /dev/null "$capture_stderr"
    install -m 0600 -- /dev/null "$capture_status"
    (
        ulimit -f 16
        python3 "$f29_outer_runner" "$stage" "$snapshot_status" \
            "$snapshot_stderr" "$f21_capture_error" "$f21_capture_error_receipt" \
            "$f29_f21_attempt_receipt" "$work" "$f21_capture_error_sha256" \
            "$f29_f21_runner" "$f29_f21_runner_sha256" "$receipt" \
            >"$capture_stdout" 2>"$capture_stderr"
    ) || outer_status=$?
    printf '%s\n' "$outer_status" >"$capture_status"
    chmod 0600 -- "$capture_stdout" "$capture_stderr" "$capture_status"
    return 0
}
python3 "$f19_snapshot" before "$f19_before" "$f19_finalizer_source" \
    "$phase09_outcomes" "$f19_command_trace" 0 0 none none
python3 "$f20_snapshot" before "$f20_before" "$work" "$work/f20-sysv"
f19_capture_after_failure() {
    local original_status="$1"
    local original_line="$2"
    local original_function="$3"
    local original_command="$4"
    local capture_status=0
    set +x
    exec 19>&-
    local snapshot_status=0
    local snapshot_stderr=
    snapshot_stderr="$work/f19-after.stderr"
    python3 "$f19_snapshot" after "$f19_after" "$f19_finalizer_source" \
        "$phase09_outcomes" "$f19_command_trace" "$original_status" \
        "$original_line" "$original_function" "$original_command" \
        >/dev/null 2>"$snapshot_stderr" || snapshot_status=$?
    /usr/bin/chmod 0600 -- "$snapshot_stderr" || {
        (( snapshot_status != 0 )) || snapshot_status=126
    }
    if (( snapshot_status == 0 )); then
        if [[ -s "$snapshot_stderr" ]]; then
            snapshot_status=126
        else
            rm -f -- "$snapshot_stderr" || snapshot_status=126
        fi
    fi
    if (( snapshot_status != 0 )); then
        capture_f29_outer_invocation f19-after "$snapshot_status" \
            "$snapshot_stderr" "$f29_outer_f19_receipt"
        capture_status="$snapshot_status"
    fi
    snapshot_status=0
    snapshot_stderr="$work/f20-after.stderr"
    python3 "$f20_snapshot" after "$f20_after" "$work" "$work/f20-sysv" \
        >/dev/null 2>"$snapshot_stderr" || snapshot_status=$?
    /usr/bin/chmod 0600 -- "$snapshot_stderr" || {
        (( snapshot_status != 0 )) || snapshot_status=126
    }
    if (( snapshot_status == 0 )); then
        if [[ -s "$snapshot_stderr" ]]; then
            snapshot_status=126
        else
            rm -f -- "$snapshot_stderr" || snapshot_status=126
        fi
    fi
    if (( snapshot_status != 0 )); then
        if [[ ! -e "$f21_capture_error_receipt" && ! -L "$f21_capture_error_receipt" ]]; then
            capture_f29_outer_invocation f20-after "$snapshot_status" \
                "$snapshot_stderr" "$f29_outer_f20_receipt"
        fi
        (( capture_status != 0 )) || capture_status="$snapshot_status"
    fi
    printf '%s\n' "$capture_status" >"$f19_capture_status_file" || :
    printf '%s\n' "$capture_status" >"$f20_capture_status_file" || :
    return 0
}
trap 'f19_status=$?; f19_line=$LINENO; f19_function=${FUNCNAME[0]:-main}; f19_command=$BASH_COMMAND; f19_capture_after_failure "$f19_status" "$f19_line" "$f19_function" "$f19_command"; trace_failure "$f19_status" "$f19_line"' ERR
f23_systemctl_stdout="$work/f23-systemctl-stdout.bin"
f23_systemctl_stderr="$work/f23-systemctl-stderr.bin"
[[ ! -e "$f23_systemctl_stdout" && ! -L "$f23_systemctl_stdout" ]]
[[ ! -e "$f23_systemctl_stderr" && ! -L "$f23_systemctl_stderr" ]]
install -m 0600 -- /dev/null "$f23_systemctl_stdout"
install -m 0600 -- /dev/null "$f23_systemctl_stderr"
declare -A f23_systemctl_stdout_by_unit=(
    [iscsid.service]="$f23_systemctl_stdout"
)
declare -A f23_systemctl_stderr_by_unit=(
    [iscsid.service]="$f23_systemctl_stderr"
)
: >"$f19_command_trace"
chmod 0600 -- "$f19_command_trace"
exec 19>"$f19_command_trace"
export BASH_XTRACEFD=19
PS4='+F19X|${LINENO}|${FUNCNAME[0]:-main}|'
set -x
disable_unmasked_units
set +x
exec 19>&-
printf '0\n' >"$f19_capture_status_file"
[[ ! -e "$work/etc-systemd/multi-user.target.wants/iscsid.service" && \
    ! -L "$work/etc-systemd/multi-user.target.wants/iscsid.service" ]]
python3 "$f20_snapshot" after "$f20_after" "$work" "$work/f20-sysv"
printf '0\n' >"$f20_capture_status_file"
[[ ! -e "$f21_capture_error_receipt" && ! -L "$f21_capture_error_receipt" ]]
trap 'trace_failure "$?" "$LINENO"' ERR
[[ "$denied_units_finalized" == true ]]
[[ "$(wc -l <"$state_root/service-policy-readback.tsv")" -eq "${#denied_units[@]}" ]]
: >"$work/f19-validator-reached"
python3 "$readback_validator" / "$readback_matrix" \
    "$state_root/service-policy-readback.tsv" "$state_root/service-policy-readback.json"
python3 - "$state_root/service-policy-readback.json" "$readback_matrix" <<'PY'
import json, pathlib, sys
receipt=json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
matrix=json.loads(pathlib.Path(sys.argv[2]).read_text(encoding="utf-8"))
assert receipt["activity_verification"] == "deferred-to-first-boot"
assert [row["unit"] for row in receipt["units"]] == matrix["denied_units"]
assert all(row["active_state"] == "not-queried-offline" for row in receipt["units"])
assert all(row["active_status"] == -1 for row in receipt["units"])
PY
python3 - "$state_root/service-policy-readback.tsv" "$work" <<'PY'
import pathlib, sys
source=pathlib.Path(sys.argv[1]).read_text(encoding="utf-8").splitlines()
root=pathlib.Path(sys.argv[2])
def fields(row): return row.split("\t")
mutations={}
rows=[fields(row) for row in source]
mutations["old-inactive"]=[row[:3]+["inactive","3"]+row[5:] for row in rows]
mutations["active"]=[row[:3]+["active","0"]+row[5:] for row in rows]
mutations["missing"]=rows[:-1]
mixed=[row[:] for row in rows]; mixed[0][3:5]=["inactive","3"]
mutations["mixed"]=mixed
arbitrary=[row[:] for row in rows]; arbitrary[0][3:5]=["unknown-runtime","42"]
mutations["arbitrary"]=arbitrary
for label, content in mutations.items():
    (root/f"readback-{label}.tsv").write_text(
        "".join("\t".join(row)+"\n" for row in content), encoding="utf-8"
    )
PY
for label in old-inactive active missing mixed arbitrary; do
    rm -f -- "$work/readback-$label.json"
    if python3 "$readback_validator" / "$readback_matrix" \
        "$work/readback-$label.tsv" "$work/readback-$label.json" \
        >"$work/readback-$label.log" 2>&1; then
        exit 94
    fi
    [[ ! -e "$work/readback-$label.json" ]]
done
cleanup_service_guards
[[ "$service_guard_cleanup_complete" == true ]]
trace_pass
trace_begin 13-retained-manifest retained-manifest
: >"$work/f19-phase13-reached"
write_retained_recovery_guard_manifest
retained_count=0
while IFS=$'\t' read -r unit enabled enabled_status active active_status boundary; do
    path="${recovery_guard_paths_by_unit[$unit]}"
    if [[ "$boundary" == condition-drop-in ]]; then
        [[ -n "${recovery_guard_paths_retained[$path]+present}" && -f "$path" ]]
        systemd-analyze condition "ConditionPathExists=${recovery_guard_condition_paths[$path]}" \
            >/dev/null 2>&1 && exit 97
        retained_count=$((retained_count + 1))
    else
        [[ ! -e "$path" && ! -L "$path" ]]
    fi
done <"$state_root/service-policy-readback.tsv"
(( retained_count > 0 ))
python3 - "$state_root/service-retained-guards.json" <<'PY'
import json, pathlib, sys
document=json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
assert document["schema_version"] == 1
assert document["supported_activation_action"] == "remove-exact-verified-guard-only"
assert document["removal_requirement"] == "later-authorized-selection-must-verify-unit-path-inode-and-sha256-before-removal"
assert document["guards"]
for guard in document["guards"]:
    assert guard["reason"] == "unit-file-state-requires-persistent-start-boundary"
    assert guard["enabled_state"] in {"static","indirect","generated","transient"}
    assert guard["canonical_path"].startswith("/etc/systemd/system/")
    assert guard["condition_path"] == f"/dev/null/hoardarr-offline-service-guard/{guard['unit']}"
    assert guard["inode"] > 0 and len(guard["sha256"]) == 64
PY
sha256sum "$state_root/service-policy-readback.json" \
    "$state_root/service-retained-guards.json" >"$state_root/SHA256SUMS"
(cd "$state_root" && sha256sum --check --strict SHA256SUMS)
trace_pass
# Removing one exact verified guard in this disposable fixture cannot release
# its retained static peer.  Product activation remains out of scope.
trace_begin 14-peer-isolation peer-isolation
: >"$work/f19-phase14-reached"
watchdog_guard="${recovery_guard_paths_by_unit[watchdog.service]}"
peer_guard="${recovery_guard_paths_by_unit[zfs.target]}"
[[ -f "$watchdog_guard" && -f "$peer_guard" ]]
""",
                        PCP_PHASE14_PEER_GUARD_LOOKUP,
                        r"""
watchdog_inode="$(stat -c %i -- "$watchdog_guard")"
watchdog_hash="$(sha256sum -- "$watchdog_guard" | awk '{print $1}')"
python3 - "$state_root/service-retained-guards.json" "$watchdog_inode" \
    "$watchdog_hash" <<'PY'
import json, pathlib, sys
document=json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
matches=[guard for guard in document["guards"] if guard["unit"] == "watchdog.service"]
assert len(matches) == 1
assert matches[0]["inode"] == int(sys.argv[2])
assert matches[0]["sha256"] == sys.argv[3]
PY
rm -f -- "$watchdog_guard"
[[ ! -e "$watchdog_guard" && -f "$peer_guard" ]]
systemd-analyze condition \
    "ConditionPathExists=$peer_condition" \
    >/dev/null 2>&1 && exit 99
for unit in "${denied_units[@]}"; do
    state_status=0
    state="$(SYSTEMD_OFFLINE=1 systemctl --root=/ is-enabled "$unit" 2>&1)" || state_status=$?
    [[ "$state" != enabled ]]
done
trace_pass
trace_begin 15-fixture-cleanup fixture-cleanup
: >"$work/f19-phase15-reached"
printf '%s\n' \
    real_pcp_old_preset_failure=reproduced \
    real_pcp_corrected_preset_errors=0 \
    policy_rc_d_start_status=101 \
    host_manager_contacts=0 \
    final_denied_units="${#denied_units[@]}"
trace_pass
trace_terminal=true
trace_write "HPCP|1|EXIT|$current_phase|status=0|line=$LINENO|function=main|label=$current_label"
trap - ERR EXIT
exit 0
""",
                    )
                ),
                encoding="utf-8",
                newline="\n",
            )
            _assert_pcp_offline_nonactivation_contract(
                harness.read_text(encoding="utf-8")
            )
            _assert_recovery_guard_path_key_contract(
                harness.read_text(encoding="utf-8")
            )
            result: subprocess.CompletedProcess[str] | None = None
            run_error: OSError | subprocess.TimeoutExpired | None = None
            ownership: subprocess.CompletedProcess[str] | None = None
            ownership_error: OSError | subprocess.TimeoutExpired | None = None
            precleanup_capture_error: tuple[dict[str, object], str] | None = None
            precleanup_capture_error_failure: AssertionError | None = None
            precleanup_f29_attempt: tuple[dict[str, object], str] | None = None
            precleanup_f29_attempt_failure: AssertionError | None = None
            precleanup_f29_outer: list[tuple[dict[str, object], str]] = []
            precleanup_f29_outer_failure: AssertionError | None = None
            precleanup_f29_direct: list[dict[str, object]] = []
            precleanup_f29_direct_failure: AssertionError | None = None
            f23_outputs: dict[str, dict[str, object]] | None = None
            f23_output_failure: AssertionError | None = None
            manager_receipt_diagnostic = ""
            systemd_receipt_diagnostic = ""
            f19_diagnostic = ""
            try:
                try:
                    result = subprocess.run(
                        [
                            "sudo",
                            "-n",
                            "unshare",
                            "--mount",
                            "--fork",
                            "bash",
                            str(harness),
                            str(postinst),
                            str(data),
                            str(namespace_path),
                            str(denied_path),
                            str(trace_path),
                            str(readback_validator),
                            str(readback_matrix),
                            str(f19_snapshot),
                            str(finalizer_source),
                            str(f20_snapshot),
                            str(f21_capture_error),
                            str(f29_f21_runner),
                            f21_capture_error_sha256,
                            str(f29_outer_runner),
                            f29_f21_runner_sha256,
                        ],
                        text=True,
                        capture_output=True,
                        check=False,
                        timeout=240,
                    )
                except (OSError, subprocess.TimeoutExpired) as exc:
                    run_error = exc
            finally:
                if namespace_path.exists():
                    capture_error_path = namespace_path / "f21-capture-error.json"
                    if capture_error_path.exists() or capture_error_path.is_symlink():
                        try:
                            precleanup_capture_error = _validate_f21_capture_error(
                                capture_error_path,
                                namespace_path,
                            )
                        except AssertionError as exc:
                            precleanup_capture_error_failure = exc
                    f29_attempt_path = namespace_path / "f29-f21-attempt.json"
                    if f29_attempt_path.exists() or f29_attempt_path.is_symlink():
                        try:
                            precleanup_f29_attempt = _validate_f29_f21_attempt(
                                f29_attempt_path,
                                namespace_path,
                                f21_capture_error_sha256,
                            )
                        except AssertionError as exc:
                            precleanup_f29_attempt_failure = exc
                    for outer_stage in ("f19-after", "f20-after"):
                        outer_path = namespace_path / f"f29-outer-{outer_stage}.json"
                        if outer_path.exists() or outer_path.is_symlink():
                            try:
                                precleanup_f29_outer.append(
                                    _validate_f29_outer_receipt(
                                        outer_path,
                                        namespace_path,
                                        outer_stage,
                                        f29_f21_runner_sha256,
                                    )
                                )
                            except AssertionError as exc:
                                precleanup_f29_outer_failure = exc
                    for outer_stage in ("f19-after", "f20-after"):
                        direct_status_path = (
                            namespace_path / f"f29-direct-{outer_stage}.status"
                        )
                        if (
                            direct_status_path.exists()
                            or direct_status_path.is_symlink()
                        ):
                            try:
                                precleanup_f29_direct.append(
                                    _validate_f29_direct_capture(
                                        namespace_path,
                                        outer_stage,
                                        required_paths={
                                            "F20_SNAPSHOT_STDERR": (
                                                namespace_path / "f20-after.stderr",
                                                0o600,
                                                (0, 0),
                                            ),
                                            "F21_CAPTURE_SOURCE": (
                                                f21_capture_error,
                                                0o644,
                                                None,
                                            ),
                                            "F29_RUNNER_SOURCE": (
                                                f29_f21_runner,
                                                0o644,
                                                None,
                                            ),
                                        }
                                        if outer_stage == "f20-after"
                                        else None,
                                    )
                                )
                            except AssertionError as exc:
                                precleanup_f29_direct_failure = exc
                    try:
                        f23_outputs = {
                            "stdout": _validate_f23_systemctl_output(
                                namespace_path / "f23-systemctl-stdout.bin",
                                namespace_path,
                                "f23-systemctl-stdout.bin",
                            ),
                            "stderr": _validate_f23_systemctl_output(
                                namespace_path / "f23-systemctl-stderr.bin",
                                namespace_path,
                                "f23-systemctl-stderr.bin",
                            ),
                        }
                    except AssertionError as exc:
                        f23_output_failure = exc
                    try:
                        ownership = subprocess.run(
                            [
                                "sudo",
                                "-n",
                                "chown",
                                "-R",
                                f"{os.getuid()}:{os.getgid()}",
                                "--",
                                str(namespace_path),
                            ],
                            text=True,
                            capture_output=True,
                            check=False,
                            timeout=30,
                        )
                    except (OSError, subprocess.TimeoutExpired) as exc:
                        ownership_error = exc
            trace_text, trace_status = _validate_pcp_trace(
                trace_path, root, namespace_path
            )
            host_sysv_after, host_sysv_after_sha256 = _capture_f20_host_manifest()
            self.assertEqual(host_sysv_after, host_sysv_before)
            self.assertEqual(host_sysv_after_sha256, host_sysv_before_sha256)
            if ownership_error is not None:
                self.fail(
                    f"namespace ownership cleanup failed: {ownership_error}\n{trace_text}"
                )
            if ownership is not None:
                self.assertEqual(
                    ownership.returncode,
                    0,
                    ownership.stdout + ownership.stderr + trace_text,
                )
            if run_error is not None:
                self.fail(f"PCP harness execution failed: {run_error}\n{trace_text}")
            assert result is not None
            self.assertEqual(trace_status, result.returncode, trace_text)
            try:
                before_text, before_status, _ = _validate_manager_root_receipt(
                    namespace_path / "manager-root-before.tsv",
                    namespace_path,
                    namespace_path / "run-systemd",
                    "before",
                )
                after_text, after_status, _ = _validate_manager_root_receipt(
                    namespace_path / "manager-root-after.tsv",
                    namespace_path,
                    namespace_path / "run-systemd",
                    "after",
                )
                source_text, package_version, executable_hash = (
                    _validate_systemd_source_receipt(
                        namespace_path / "systemd-source.tsv",
                        namespace_path,
                    )
                )
                causal_text = _validate_systemd_causal_receipt(
                    namespace_path / "systemd-causal.tsv",
                    namespace_path,
                )
            except AssertionError as exc:
                self.fail(
                    f"systemd/manager receipt validation failed: {exc}\n{trace_text}"
                )
            self.assertIsNone(before_status)
            self.assertIsNotNone(after_status)
            manager_receipt_diagnostic = (
                "\nVALIDATED MANAGER-ROOT BEFORE RECEIPT\n"
                + before_text
                + "VALIDATED MANAGER-ROOT AFTER RECEIPT\n"
                + after_text
            )
            systemd_receipt_diagnostic = (
                "VALIDATED SYSTEMD SOURCE RECEIPT\n"
                + source_text
                + "VALIDATED SYSTEMD CAUSAL RECEIPT\n"
                + causal_text
            )
            self.assertTrue(package_version.startswith("255.4-"))
            self.assertRegex(executable_hash, r"^[0-9a-f]{64}$")
            capture_status_path = namespace_path / "f19-capture-status.txt"
            self.assertTrue(capture_status_path.is_file())
            f20_capture_status = namespace_path / "f20-capture-status.txt"
            self.assertTrue(f20_capture_status.is_file())
            capture_statuses = (
                capture_status_path.read_text(encoding="ascii"),
                f20_capture_status.read_text(encoding="ascii"),
            )
            self.assertEqual(capture_statuses[0], capture_statuses[1])
            if capture_statuses[0] != "0\n":
                if precleanup_f29_outer_failure is not None:
                    self.fail(
                        "F29 outer runner failed without a valid root-owned receipt: "
                        f"{precleanup_f29_outer_failure}\n{trace_text}"
                    )
                correlated_outer_success = False
                if precleanup_f29_outer:
                    nonzero_outer = [
                        item
                        for item in precleanup_f29_outer
                        if item[0]["runner_status"] != 0
                    ]
                    if nonzero_outer:
                        outer, outer_sha256 = nonzero_outer[-1]
                        self.fail(
                            "F29 validated outer invocation: "
                            f"stage={outer['stage']} "
                            f"runner_status={outer['runner_status']} "
                            f"timed_out={outer['timed_out']} "
                            f"stdout_size={outer['stdout_size']} "
                            f"stderr_size={outer['stderr_size']} "
                            f"stderr_class={outer['stderr_class']} "
                            f"attempt_exists={outer['attempt_exists']} "
                            f"output_exists={outer['output_exists']} "
                            f"receipt_sha256={outer_sha256}\n{trace_text}"
                        )
                    if precleanup_f29_direct_failure is not None:
                        self.fail(
                            "F29 direct outer invocation capture is invalid: "
                            f"{precleanup_f29_direct_failure}\n{trace_text}"
                        )
                    if precleanup_capture_error_failure is not None:
                        self.fail(
                            "F21 snapshot capture failed without a valid sanitized "
                            f"record: {precleanup_capture_error_failure}\n{trace_text}"
                        )
                    try:
                        precleanup_capture_error = (
                            _require_f29_outer_success_correlation(
                                precleanup_f29_outer,
                                precleanup_f29_direct,
                                precleanup_capture_error,
                            )
                        )
                    except AssertionError as exc:
                        self.fail(
                            "F29 outer/direct success correlation is invalid: "
                            f"{exc}\n{trace_text}"
                        )
                    correlated_outer_success = True
                if not correlated_outer_success:
                    if precleanup_f29_direct_failure is not None:
                        self.fail(
                            "F29 direct outer invocation capture is invalid: "
                            f"{precleanup_f29_direct_failure}\n{trace_text}"
                        )
                    if precleanup_f29_direct:
                        self.fail(
                            f"{_format_f29_direct_captures(precleanup_f29_direct)}\n"
                            f"{trace_text}"
                        )
                if precleanup_capture_error_failure is not None:
                    self.fail(
                        "F21 snapshot capture failed without a valid sanitized "
                        f"record: {precleanup_capture_error_failure}\n{trace_text}"
                    )
                if precleanup_capture_error is not None:
                    capture_error, capture_error_sha256 = precleanup_capture_error
                    self.fail(
                        "F21 validated capture error: "
                        f"stage={capture_error['stage']} "
                        f"status={capture_error['status']} "
                        f"stderr_class={capture_error['stderr_class']} "
                        f"stderr_size={capture_error['stderr_size']} "
                        f"stderr_sha256={capture_error['stderr_sha256']} "
                        f"receipt_sha256={capture_error_sha256}\n{trace_text}"
                    )
                if precleanup_f29_attempt_failure is not None:
                    self.fail(
                        "F29 runner failed without a valid root-owned receipt: "
                        f"{precleanup_f29_attempt_failure}\n{trace_text}"
                    )
                if precleanup_f29_attempt is None:
                    self.fail(
                        "F21 snapshot capture failed without an F29 root-owned receipt\n"
                        + trace_text
                    )
                attempt, attempt_sha256 = precleanup_f29_attempt
                self.fail(
                    "F29 validated sanitizer attempt: "
                    f"stage={attempt['stage']} "
                    f"snapshot_status={attempt['snapshot_status']} "
                    f"child_status={attempt['child_status']} "
                    f"timed_out={attempt['timed_out']} "
                    f"stdout_size={attempt['stdout_size']} "
                    f"stdout_sha256={attempt['stdout_sha256']} "
                    f"stderr_size={attempt['stderr_size']} "
                    f"stderr_sha256={attempt['stderr_sha256']} "
                    f"stderr_class={attempt['stderr_class']} "
                    f"receipt_sha256={attempt_sha256}\n{trace_text}"
                )
            self.assertFalse(
                (namespace_path / "f21-capture-error.json").exists()
                or (namespace_path / "f21-capture-error.json").is_symlink()
            )
            self.assertFalse(
                (namespace_path / "f29-f21-attempt.json").exists()
                or (namespace_path / "f29-f21-attempt.json").is_symlink()
            )
            f20_before_receipt, f20_before_sha256 = _validate_f20_snapshot(
                namespace_path / "f20-before.json", namespace_path, "before"
            )
            f20_after_receipt, f20_after_sha256 = _validate_f20_snapshot(
                namespace_path / "f20-after.json", namespace_path, "after"
            )
            self.assertEqual(f20_before_receipt["mounts"], f20_after_receipt["mounts"])
            self.assertEqual(
                f20_before_receipt["helper"]["real_helper"],
                f20_after_receipt["helper"]["real_helper"],
            )
            identity_keys = {
                "path",
                "type",
                "uid",
                "gid",
                "mode",
                "size",
                "link_target",
                "sha256",
            }

            def identity(item: dict[str, object]) -> dict[str, object]:
                return {key: item[key] for key in identity_keys if key in item}

            host_objects = {item["path"]: item for item in host_sysv_before["objects"]}
            private_objects = {
                item["path"]: item for item in f20_before_receipt["objects"]
            }
            self.assertEqual(
                identity(private_objects["/etc/init.d/iscsid"]),
                identity(host_objects["/etc/init.d/iscsid"]),
            )
            self.assertEqual(
                f20_before_receipt["helper"]["real_helper"]["sha256"],
                host_objects["/usr/lib/systemd/systemd-sysv-install"].get(
                    "sha256",
                    host_objects["/usr/lib/systemd/systemd-sysv-install"].get(
                        "resolved_sha256"
                    ),
                ),
            )
            host_rc = {
                row["path"]: [identity(item) for item in row["entries"]]
                for row in host_sysv_before["rc_directories"]
            }
            private_rc = {
                row["path"]: [identity(item) for item in row["entries"]]
                for row in f20_before_receipt["rc_directories"]
            }
            self.assertEqual(private_rc, host_rc)
            before_receipt, before_sha256 = _validate_f19_snapshot(
                namespace_path / "f19-before.json",
                namespace_path,
                "before",
                finalizer_sha256,
            )
            after_receipt, after_sha256 = _validate_f19_snapshot(
                namespace_path / "f19-after.json",
                namespace_path,
                "after",
                finalizer_sha256,
            )
            command_trace = after_receipt["command_trace"]
            assert isinstance(command_trace, dict)
            command_diagnostic, command_checks = _validate_f19_command_trace(
                namespace_path / "f19-command.trace",
                namespace_path,
                str(command_trace["sha256"]),
            )
            if f23_output_failure is not None:
                self.fail(
                    f"F23 systemctl output validation failed: {f23_output_failure}\n"
                    + trace_text
                )
            self.assertIsNotNone(f23_outputs, trace_text)
            assert f23_outputs is not None
            self.assertGreater(
                sum(int(output["size"]) for output in f23_outputs.values()),
                0,
                "the exact systemctl rejection produced no bounded diagnostic output",
            )
            self.assertEqual(
                command_diagnostic.count("systemctl --root=/ disable iscsid.service"),
                1,
                command_diagnostic,
            )
            disable_statuses = [
                int(value)
                for value in re.findall(
                    r"disable_status=([0-9]{1,3})", command_diagnostic
                )
            ]
            self.assertTrue(disable_statuses, command_diagnostic)
            self.assertEqual(disable_statuses[-1], 1, command_diagnostic)
            f23_systemctl_evidence = {
                "argv": ["systemctl", "--root=/", "disable", "iscsid.service"],
                "environment": {"SYSTEMD_OFFLINE": "1"},
                "call_count": 1,
                "status": disable_statuses[-1],
                "outputs": f23_outputs,
            }
            f25_classification = _classify_f25_entry(
                f23_outputs["stderr"], f20_after_receipt["helper"]
            )
            self.assertEqual(before_receipt["systemd"], after_receipt["systemd"])
            self.assertEqual(before_receipt["mounts"], after_receipt["mounts"])
            self.assertEqual(
                before_receipt["effective_preset_rules"],
                after_receipt["effective_preset_rules"],
            )
            self.assertEqual(
                before_receipt["phase09_outcomes"],
                after_receipt["phase09_outcomes"],
            )
            self.assertNotIn(
                "offline install denied unit remains enabled: iscsid.service=enabled",
                result.stderr,
            )
            sanitized = {
                "schema_version": F19_DIAGNOSTIC_SCHEMA,
                "before_sha256": before_sha256,
                "after_sha256": after_sha256,
                "inputs": after_receipt["inputs"],
                "systemd": after_receipt["systemd"],
                "mounts": after_receipt["mounts"],
                "before_enabled_states": before_receipt["enabled_states"],
                "after_enabled_states": after_receipt["enabled_states"],
                "before_etc_entries": before_receipt["etc_entries"],
                "after_etc_entries": after_receipt["etc_entries"],
                "effective_preset_rules": after_receipt["effective_preset_rules"],
                "preset_identities": [
                    {
                        "name": item["name"],
                        "path": item["path"],
                        "size": item["size"],
                        "sha256": item["sha256"],
                    }
                    for item in after_receipt["presets"]
                ],
                "vendor_unit_identities": [
                    {
                        key: value
                        for key, value in item.items()
                        if key != "content_base64"
                    }
                    for item in after_receipt["vendor_units"]
                ],
                "phase09_outcomes": after_receipt["phase09_outcomes"],
                "phase_boundaries": after_receipt["phase_boundaries"],
                "failure": after_receipt["failure"],
                "command_trace": after_receipt["command_trace"],
                "command_checks": command_checks,
                "systemctl_disable": f23_systemctl_evidence,
                "helper_entry_classification": f25_classification,
                "sysv_compatibility_cause": (
                    "systemctl-sysv-delegation-returned-nonzero-before-"
                    "native-enablement-link-removal"
                ),
                "enablement_link_after_finalization": "absent",
            }
            f20_helper = f20_after_receipt["helper"]
            assert isinstance(f20_helper, dict)
            f20_outputs = f20_helper.get("outputs")
            if f20_helper["invoked"] is True:
                assert isinstance(f20_outputs, dict)
                sanitized_helper = {
                    key: value for key, value in f20_helper.items() if key != "outputs"
                } | {
                    "outputs": {
                        label: {
                            key: value
                            for key, value in output.items()
                            if key != "content_base64"
                        }
                        for label, output in f20_outputs.items()
                    }
                }
            else:
                self.assertIsNone(f20_outputs)
                sanitized_helper = f20_helper
            f20_sanitized = {
                "schema_version": F20_DIAGNOSTIC_SCHEMA,
                "host_manifest_sha256": host_sysv_after_sha256,
                "before_sha256": f20_before_sha256,
                "after_sha256": f20_after_sha256,
                "objects": [
                    {
                        key: value
                        for key, value in item.items()
                        if key != "content_base64"
                    }
                    for item in f20_after_receipt["objects"]
                ],
                "mounts": f20_after_receipt["mounts"],
                "rc_directories": [
                    {
                        "path": row["path"],
                        "identity": row["identity"],
                        "entries": [
                            {
                                key: value
                                for key, value in item.items()
                                if key != "content_base64"
                            }
                            for item in row["entries"]
                        ],
                    }
                    for row in f20_after_receipt["rc_directories"]
                ],
                "generators": f20_after_receipt["generators"],
                "helper": sanitized_helper,
                "helper_entry_classification": f25_classification,
            }
            f19_diagnostic = (
                "\nVALIDATED F19 SANITIZED DIAGNOSTIC\n"
                + json.dumps(sanitized, indent=2, sort_keys=True)
                + "\nVALIDATED F19 COMMAND TRACE\n"
                + command_diagnostic
                + "\nVALIDATED F20 SANITIZED DIAGNOSTIC\n"
                + json.dumps(f20_sanitized, indent=2, sort_keys=True)
                + "\nVALIDATED F23 SYSTEMCTL OUTPUT\n"
                + json.dumps(f23_systemctl_evidence, indent=2, sort_keys=True)
            )
        self.assertEqual(
            result.returncode,
            0,
            result.stdout
            + result.stderr
            + trace_text
            + manager_receipt_diagnostic
            + systemd_receipt_diagnostic
            + f19_diagnostic,
        )
        self.assertIn("real_pcp_old_preset_failure=reproduced", result.stdout)
        self.assertIn("real_pcp_corrected_preset_errors=0", result.stdout)
        self.assertIn("policy_rc_d_start_status=101", result.stdout)
        self.assertIn("host_manager_contacts=0", result.stdout)
        self.assertIn(f"final_denied_units={len(denied_units)}", result.stdout)

    def test_debian_control_metadata_is_parsed_by_field_name(self) -> None:
        control = """Package: snapraid
Version: 12.3-1
Architecture: amd64
Source: snapraid
Depends: libc6 (>= 2.34), libgcc-s1 (>= 3.0)
Homepage: https://www.snapraid.it/
Description: Backup program for disk arrays
"""
        completed = mock.Mock(stdout=control)
        with mock.patch.object(offline_repo, "_run", return_value=completed) as run:
            fields = offline_repo._deb_fields(pathlib.Path("snapraid.deb"))

        run.assert_called_once_with(["dpkg-deb", "-f", "snapraid.deb"])
        self.assertEqual(fields["Package"], "snapraid")
        self.assertEqual(fields["Version"], "12.3-1")
        self.assertEqual(fields["Architecture"], "amd64")
        self.assertEqual(fields["Source"], "snapraid")
        self.assertEqual(fields["Depends"], "libc6 (>= 2.34), libgcc-s1 (>= 3.0)")

    def test_reconciled_plan_covers_profiles_providers_and_all_dispositions(
        self,
    ) -> None:
        plan = offline_repo.build_plan()
        candidates = plan.matrix["candidates"]
        self.assertEqual(len(plan.roots), 109)
        self.assertEqual(len(candidates), 129)
        self.assertEqual(
            {item["disposition"] for item in candidates},
            {
                "included-and-installed",
                "included-but-feature-disabled",
                "sidecar-manual-offline-import",
                "not-supported",
            },
        )
        selected = {item["package"] for item in candidates if item["package"]}
        self.assertEqual(selected, set(plan.roots))
        self.assertIn("snapraid", selected)
        self.assertIn("b3sum", selected)
        self.assertIn("xxhash", selected)
        self.assertIn("lm-sensors", selected)
        self.assertIn("sysstat", selected)
        self.assertIn("pcp", selected)
        self.assertNotIn("dstat", selected)

    def test_compatibility_families_are_explicit_without_changing_product_roots(
        self,
    ) -> None:
        plan = offline_repo.build_plan()
        systemd_members = {
            "systemd",
            "systemd-sysv",
            "systemd-timesyncd",
            "systemd-resolved",
            "udev",
            "libudev1",
            "libsystemd0",
            "libsystemd-shared",
            "libpam-systemd",
            "libnss-systemd",
            "systemd-dev",
        }
        linux_members = {
            "linux-generic",
            "linux-image-generic",
            "linux-headers-generic",
        }
        self.assertEqual(len(plan.roots), 109)
        self.assertEqual(len(plan.compatibility_families), 2)
        families = {family["id"]: family for family in plan.compatibility_families}
        self.assertEqual(set(families), {"systemd-noble", "linux-meta-noble"})
        self.assertEqual(set(families["systemd-noble"]["members"]), systemd_members)
        self.assertEqual(families["systemd-noble"]["exact_dependencies"], {})
        linux_family = families["linux-meta-noble"]
        self.assertEqual(set(linux_family["members"]), linux_members)
        self.assertEqual(
            linux_family["exact_dependencies"],
            {
                "linux-generic": (
                    "linux-image-generic",
                    "linux-headers-generic",
                )
            },
        )
        self.assertTrue(
            all(
                family["version_policy"] == "single-candidate-version"
                for family in families.values()
            )
        )
        self.assertEqual(
            set(plan.roots),
            {item["package"] for item in plan.matrix["candidates"] if item["package"]},
        )
        self.assertEqual(set(plan.roots) & linux_members, {"linux-image-generic"})
        self.assertTrue(systemd_members - set(plan.roots))

    def test_compatibility_family_schema_rejects_unsafe_and_duplicate_values(
        self,
    ) -> None:
        valid = [
            {
                "id": "systemd-noble",
                "members": ["systemd", "systemd-sysv"],
                "version_policy": "single-candidate-version",
            }
        ]
        self.assertEqual(
            offline_repo._compatibility_families(valid)[0]["id"], "systemd-noble"
        )
        invalid_values = (
            [{**valid[0], "members": ["systemd", "../unsafe"]}],
            [valid[0], {**valid[0], "members": ["udev"]}],
            [valid[0], {**valid[0], "id": "udev-noble"}],
            [{**valid[0], "version_policy": "runner-installed"}],
            [{**valid[0], "extra": True}],
            [{**valid[0], "members": []}],
            [
                {
                    **valid[0],
                    "exact_dependencies": {"not-a-member": ["systemd"]},
                }
            ],
            [
                {
                    **valid[0],
                    "exact_dependencies": {"systemd": ["systemd"]},
                }
            ],
            [
                {
                    **valid[0],
                    "exact_dependencies": {"systemd": ["missing-member"]},
                }
            ],
        )
        for value in invalid_values:
            with (
                self.subTest(value=value),
                self.assertRaises(offline_repo.OfflineRepositoryError),
            ):
                offline_repo._compatibility_families(value)

    def test_exact_dependency_parser_is_whitespace_and_alternative_safe(self) -> None:
        version = "6.8.0-138.138"
        self.assertEqual(
            offline_repo._exact_dependency_versions(
                " linux-image-generic   (= 6.8.0-138.138) , "
                "linux-headers-generic (= 6.8.0-138.138), "
                "unrelated:any (>= 1) "
            ),
            {
                "linux-image-generic": version,
                "linux-headers-generic": version,
            },
        )
        self.assertEqual(
            offline_repo._exact_dependency_versions(
                "linux-image-generic (= 6.8.0-138.138) | linux-image-virtual "
                "(= 6.8.0-138.138), linux-headers-generic (>= 6.8.0-138.138)"
            ),
            {},
        )
        with self.assertRaisesRegex(
            offline_repo.OfflineRepositoryError, "unsupported clause"
        ):
            offline_repo._exact_dependency_versions("linux-image-generic (6.8.0)")

    def test_linux_meta_dependency_validation_requires_exact_sibling_versions(
        self,
    ) -> None:
        version = "6.8.0-138.138"
        family = {
            "id": "linux-meta-noble",
            "members": (
                "linux-generic",
                "linux-image-generic",
                "linux-headers-generic",
            ),
            "version_policy": "single-candidate-version",
            "exact_dependencies": {
                "linux-generic": (
                    "linux-image-generic",
                    "linux-headers-generic",
                )
            },
        }
        valid = [
            {
                "name": "linux-generic",
                "version": version,
                "depends": (
                    f"linux-image-generic (= {version}), "
                    f"linux-headers-generic (= {version})"
                ),
            }
        ]
        offline_repo._validate_family_dependencies((family,), valid)
        invalid_depends = (
            f"linux-image-generic (= {version})",
            (
                f"linux-image-generic (= {version}), "
                "linux-headers-generic (= 6.8.0-137.137)"
            ),
            (
                f"linux-image-generic (= {version}), "
                f"linux-headers-generic (>= {version})"
            ),
            (
                f"linux-image-generic (= {version}), "
                f"linux-headers-generic (= {version}) | linux-headers-virtual"
            ),
        )
        for depends in invalid_depends:
            with (
                self.subTest(depends=depends),
                self.assertRaisesRegex(
                    offline_repo.OfflineRepositoryError, "depend exactly"
                ),
            ):
                offline_repo._validate_family_dependencies(
                    (family,), [{**valid[0], "depends": depends}]
                )

    def test_download_closure_pins_roots_and_complete_family_at_one_version(
        self,
    ) -> None:
        plan = offline_repo.PackagePlan(
            roots=("root-package",),
            compatibility_families=(
                {
                    "id": "systemd-noble",
                    "members": ("systemd", "systemd-sysv"),
                    "version_policy": "single-candidate-version",
                },
            ),
            matrix={},
            policy={},
        )
        candidates = {
            "root-package": "1.0",
            "systemd": "255.4-1ubuntu8.17",
            "systemd-sysv": "255.4-1ubuntu8.17",
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)

            def run(command: list[str], **_: object) -> mock.Mock:
                archives_arg = next(
                    item for item in command if item.startswith("Dir::Cache::archives=")
                )
                archives = pathlib.Path(archives_arg.split("=", 1)[1])
                for package in candidates:
                    (archives / f"{package}.deb").write_bytes(package.encode())
                return mock.Mock(stdout="", stderr="", returncode=0)

            def fields(path: pathlib.Path) -> dict[str, str]:
                package = path.stem
                return {
                    "Package": package,
                    "Version": candidates[package],
                    "Architecture": "amd64",
                }

            with (
                mock.patch.object(
                    offline_repo,
                    "_candidate",
                    side_effect=lambda name: candidates[name],
                ),
                mock.patch.object(offline_repo, "_run", side_effect=run) as apt_run,
                mock.patch.object(offline_repo, "_deb_fields", side_effect=fields),
            ):
                roots, families, debs = offline_repo._download_closure(plan, root)

        argv = apt_run.call_args.args[0]
        self.assertEqual(roots, {"root-package": "1.0"})
        self.assertEqual(
            families["systemd-noble"],
            {
                "systemd": "255.4-1ubuntu8.17",
                "systemd-sysv": "255.4-1ubuntu8.17",
            },
        )
        self.assertEqual(len(debs), 3)
        self.assertEqual(
            argv[-4:],
            [
                "install",
                "root-package=1.0",
                "systemd=255.4-1ubuntu8.17",
                "systemd-sysv=255.4-1ubuntu8.17",
            ],
        )

    def test_download_closure_rejects_family_version_mismatch_and_omission(
        self,
    ) -> None:
        plan = offline_repo.PackagePlan(
            roots=("root-package",),
            compatibility_families=(
                {
                    "id": "systemd-noble",
                    "members": ("systemd", "systemd-sysv"),
                    "version_policy": "single-candidate-version",
                },
            ),
            matrix={},
            policy={},
        )
        with (
            mock.patch.object(
                offline_repo,
                "_candidate",
                side_effect=lambda name: {
                    "root-package": "1.0",
                    "systemd": "8.17",
                    "systemd-sysv": "8.12",
                }[name],
            ),
            tempfile.TemporaryDirectory() as temporary,
            self.assertRaisesRegex(
                offline_repo.OfflineRepositoryError, "candidate versions differ"
            ),
        ):
            offline_repo._download_closure(plan, pathlib.Path(temporary))

        candidates = {
            "root-package": "1.0",
            "systemd": "8.17",
            "systemd-sysv": "8.17",
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)

            def run(command: list[str], **_: object) -> mock.Mock:
                archives = pathlib.Path(
                    next(
                        item
                        for item in command
                        if item.startswith("Dir::Cache::archives=")
                    ).split("=", 1)[1]
                )
                for package in ("root-package", "systemd"):
                    (archives / f"{package}.deb").write_bytes(package.encode())
                return mock.Mock(stdout="", stderr="", returncode=0)

            with (
                mock.patch.object(
                    offline_repo,
                    "_candidate",
                    side_effect=lambda name: candidates[name],
                ),
                mock.patch.object(offline_repo, "_run", side_effect=run),
                mock.patch.object(
                    offline_repo,
                    "_deb_fields",
                    side_effect=lambda path: {
                        "Package": path.stem,
                        "Version": candidates[path.stem],
                        "Architecture": "amd64",
                    },
                ),
                self.assertRaisesRegex(
                    offline_repo.OfflineRepositoryError,
                    "omitted required exact inputs: systemd-sysv",
                ),
            ):
                offline_repo._download_closure(plan, root)

    def test_download_closure_rejects_duplicate_binary_identity(self) -> None:
        plan = offline_repo.PackagePlan(
            roots=("systemd",),
            compatibility_families=(),
            matrix={},
            policy={},
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)

            def run(command: list[str], **_: object) -> mock.Mock:
                archives = pathlib.Path(
                    next(
                        item
                        for item in command
                        if item.startswith("Dir::Cache::archives=")
                    ).split("=", 1)[1]
                )
                (archives / "one.deb").write_bytes(b"one")
                (archives / "two.deb").write_bytes(b"two")
                return mock.Mock(stdout="", stderr="", returncode=0)

            with (
                mock.patch.object(offline_repo, "_candidate", return_value="8.17"),
                mock.patch.object(offline_repo, "_run", side_effect=run),
                mock.patch.object(
                    offline_repo,
                    "_deb_fields",
                    return_value={
                        "Package": "systemd",
                        "Version": "8.17",
                        "Architecture": "amd64",
                    },
                ),
                self.assertRaisesRegex(
                    offline_repo.OfflineRepositoryError, "duplicate binary identity"
                ),
            ):
                offline_repo._download_closure(plan, root)

    def test_owner_workbook_aliases_and_superseded_container_rows_are_explicit(
        self,
    ) -> None:
        matrix = offline_repo.build_plan().matrix
        self.assertEqual(
            matrix["owner_workbook"]["sha256"],
            "438991f1a7def5de709beea6337780baf50e2fd5e50f3a9229ef858d8186ed4c",
        )
        self.assertEqual(matrix["command_aliases"]["dd"], "coreutils")
        self.assertEqual(matrix["command_aliases"]["shred"], "coreutils")
        self.assertEqual(matrix["command_aliases"]["wipefs"], "util-linux")
        self.assertEqual(matrix["command_aliases"]["dstat"], "pcp")
        intake = json.loads(
            (ROOT / "packaging" / "offline" / "owner-workbook-intake.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual([item["row"] for item in intake["rows"]], list(range(4, 51)))
        dispositions = {item["candidate"]: item for item in matrix["candidates"]}
        for candidate in (
            "docker-ce",
            "docker-ce-cli",
            "containerd.io",
            "docker-compose-plugin",
        ):
            self.assertEqual(dispositions[candidate]["disposition"], "not-supported")
            self.assertTrue(dispositions[candidate]["reason"])
        self.assertEqual(dispositions["nwipe"]["disposition"], "not-supported")

    def test_every_vendor_tool_is_manual_sidecar_not_silently_downloaded(self) -> None:
        matrix = offline_repo.build_plan().matrix
        sidecars = {
            item["candidate"]
            for item in matrix["candidates"]
            if item["disposition"] == "sidecar-manual-offline-import"
        }
        catalog = json.loads(
            (ROOT / "packaging" / "hardware" / "vendor-tools.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(sidecars, {item["id"] for item in catalog["tools"]})

    def test_autoinstall_is_explicitly_offline_and_installs_payload_before_release(
        self,
    ) -> None:
        user_data = (ROOT / "packaging" / "appliance" / "user-data").read_text(
            encoding="utf-8"
        )
        self.assertIn("fallback: offline-install", user_data)
        self.assertIn("geoip: false", user_data)
        self.assertNotRegex(user_data, r"(?m)^\s+packages:\s*$")
        payload = user_data.index("install-offline-payload.sh /target")
        release = user_data.index("hoardarr-release.tar.gz")
        self.assertLess(payload, release)
        self.assertNotIn("http://", user_data)
        self.assertNotIn("https://", user_data)

    def test_offline_installer_has_independent_service_and_storage_guards(self) -> None:
        installer = (
            ROOT / "packaging" / "appliance" / "install-offline-payload.sh"
        ).read_text(encoding="utf-8")
        self.assertIn("policy-rc.d", installer)
        self.assertIn(
            'condition_path="/dev/null/hoardarr-offline-service-guard/$guarded_unit"',
            installer,
        )
        self.assertNotIn('ln -s -- /dev/null "$destination"', installer)
        self.assertIn("AUTO -all", installer)
        self.assertIn('global_filter = [ "r|.*|" ]', installer)
        self.assertIn('devnode ".*"', installer)
        self.assertIn("--simulate --no-install-recommends", installer)
        self._assert_actual_install_contract(installer)
        self.assertIn("package-readback.json", installer)
        self.assertIn("service-policy-readback.json", installer)
        self.assertIn("service-retained-guards.json", installer)
        self.assertIn(
            "later-authorized-selection-must-verify-unit-path-inode-and-sha256",
            installer,
        )
        self.assertIn("sha256sum --check --strict SHA256SUMS", installer)
        self.assertNotIn("curl ", installer)
        self.assertNotIn("wget ", installer)
        verifier = (
            ROOT / "packaging" / "appliance" / "verify-offline-appliance.sh"
        ).read_text(encoding="utf-8")
        self.assertIn("HOARDARR_OFFLINE_EVIDENCE_BEGIN", verifier)
        self.assertIn("list-unit-files", verifier)
        self.assertIn("127.0.0.1:7877/health/ready", verifier)

    def test_offline_activity_is_strictly_deferred_to_first_boot(self) -> None:
        payload = (
            ROOT / "packaging" / "appliance" / "install-offline-payload.sh"
        ).read_text(encoding="utf-8")
        match = re.search(
            r"^disable_unmasked_units\(\) \{\n.*?^\}\n",
            payload,
            flags=re.MULTILINE | re.DOTALL,
        )
        self.assertIsNotNone(match)
        assert match is not None
        finalizer = match.group(0)
        self.assertNotIn("is-active", finalizer)
        self.assertNotRegex(finalizer, r"chroot\s+.*systemctl")
        self.assertEqual(finalizer.count("not-queried-offline"), 1)
        self.assertEqual(finalizer.count("activity_status=-1"), 1)
        self.assertIn('systemctl --root="$target" disable', finalizer)
        self.assertIn(
            'systemctl --root="$target" \\\n                is-enabled', finalizer
        )

        validator = _service_policy_readback_validator(payload)
        self.assertIn('active != "not-queried-offline"', validator)
        self.assertIn('row["active_status"] != -1', validator)
        self.assertIn('"activity_verification":"deferred-to-first-boot"', validator)
        self.assertNotIn('active != "inactive"', validator)

        units = ["masked.service", "guarded.target"]
        valid_rows = [
            [
                "masked.service",
                "masked",
                "1",
                "not-queried-offline",
                "-1",
                "pre-existing-mask",
            ],
            [
                "guarded.target",
                "static",
                "0",
                "not-queried-offline",
                "-1",
                "condition-drop-in",
            ],
        ]
        mutations = {
            "old-inactive": [
                row[:3] + ["inactive", "3"] + row[5:] for row in valid_rows
            ],
            "active": [row[:3] + ["active", "0"] + row[5:] for row in valid_rows],
            "missing": valid_rows[:-1],
            "mixed": [
                valid_rows[0][:3] + ["inactive", "3"] + valid_rows[0][5:],
                valid_rows[1],
            ],
            "arbitrary": [
                valid_rows[0][:3] + ["unknown-runtime", "42"] + valid_rows[0][5:],
                valid_rows[1],
            ],
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            validator_path = root / "validator.py"
            validator_path.write_text(validator, encoding="utf-8", newline="\n")
            matrix_path = root / "matrix.json"
            matrix_path.write_text(
                json.dumps({"denied_units": units}) + "\n", encoding="utf-8"
            )

            def write_rows(path: pathlib.Path, rows: list[list[str]]) -> None:
                path.write_text(
                    "".join("\t".join(row) + "\n" for row in rows),
                    encoding="utf-8",
                    newline="\n",
                )

            valid_path = root / "valid.tsv"
            valid_json = root / "valid.json"
            write_rows(valid_path, valid_rows)
            accepted = subprocess.run(
                [
                    sys.executable,
                    str(validator_path),
                    "/",
                    str(matrix_path),
                    str(valid_path),
                    str(valid_json),
                ],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(accepted.returncode, 0, accepted.stdout + accepted.stderr)
            receipt = json.loads(valid_json.read_text(encoding="utf-8"))
            self.assertEqual(receipt["activity_verification"], "deferred-to-first-boot")
            self.assertEqual([row["unit"] for row in receipt["units"]], units)
            for label, rows in mutations.items():
                with self.subTest(label=label):
                    input_path = root / f"{label}.tsv"
                    output_path = root / f"{label}.json"
                    write_rows(input_path, rows)
                    rejected = subprocess.run(
                        [
                            sys.executable,
                            str(validator_path),
                            "/",
                            str(matrix_path),
                            str(input_path),
                            str(output_path),
                        ],
                        text=True,
                        capture_output=True,
                        check=False,
                    )
                    self.assertNotEqual(rejected.returncode, 0)
                    self.assertFalse(output_path.exists())

        verifier_path = ROOT / "packaging" / "appliance" / "verify-offline-appliance.sh"
        verifier = verifier_path.read_text(encoding="utf-8")
        self.assertEqual(
            hashlib.sha256(verifier_path.read_bytes()).hexdigest(),
            "f188d76e7c19ba38472a5125c68d53e428bcf095d36878ac688e56a93fc627ad",
        )
        self.assertIn('["systemctl","is-active",unit]', verifier)
        self.assertIn('if active_state == "active":', verifier)

    def test_f19_diagnostic_scope_and_command_trace_are_bounded(self) -> None:
        payload_path = ROOT / "packaging" / "appliance" / "install-offline-payload.sh"
        verifier_path = ROOT / "packaging" / "appliance" / "verify-offline-appliance.sh"
        self.assertEqual(
            hashlib.sha256(payload_path.read_bytes()).hexdigest(),
            "3116215f4f2dde376f591b06cb192b3cc725e4261885c5a0bc88e23b8867005b",
        )
        self.assertEqual(
            hashlib.sha256(verifier_path.read_bytes()).hexdigest(),
            "f188d76e7c19ba38472a5125c68d53e428bcf095d36878ac688e56a93fc627ad",
        )
        compile(F19_SNAPSHOT_SCRIPT, "f19-snapshot.py", "exec")
        source = pathlib.Path(__file__).read_text(encoding="utf-8")
        phase12 = source.split(
            "trace_begin 12-final-disable-readback final-disable-readback\n", 1
        )[1].split('[[ "$denied_units_finalized" == true ]]', 1)[0]
        self.assertEqual(phase12.count("\ndisable_unmasked_units\n"), 1)
        self.assertIn("set -x\ndisable_unmasked_units\nset +x", phase12)
        self.assertNotRegex(
            phase12,
            r"(?:if|until|while)\s+disable_unmasked_units|disable_unmasked_units\s*(?:\|\||&&)",
        )
        self.assertNotIn("systemctl is-active", F19_SNAPSHOT_SCRIPT)

        valid = (
            "+F19X|1200|disable_unmasked_units|disable_status=0\n"
            "+F19X|1201|disable_unmasked_units|SYSTEMD_OFFLINE=1 systemctl --root=/ disable iscsid.service\n"
            "+F19X|1202|disable_unmasked_units|enabled_status=0\n"
            "++F19X|1203|disable_unmasked_units|SYSTEMD_OFFLINE=1 systemctl --root=/ is-enabled iscsid.service\n"
            "+F19X|1204|disable_unmasked_units|enabled_state=enabled\n"
            "+F19X|1205|disable_unmasked_units|return 1\n"
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            trace = root / "f19-command.trace"
            trace.write_text(valid, encoding="ascii", newline="\n")
            digest = hashlib.sha256(trace.read_bytes()).hexdigest()
            _, checks = _validate_f19_command_trace(trace, root, digest)
            self.assertTrue(checks["disable_iscsid"])
            self.assertFalse(checks["disable_fallback_executed"])
            self.assertTrue(checks["is_enabled_iscsid"])
            for label, content, expected_hash in (
                ("bad-prefix", valid.replace("F19X|", "BAD|", 1), None),
                ("oversize", "+F19X|1|main|" + "x" * 800 + "\n", None),
                ("hash-mismatch", valid, "0" * 64),
            ):
                with self.subTest(label=label):
                    trace.write_text(content, encoding="ascii", newline="\n")
                    candidate_hash = (
                        expected_hash or hashlib.sha256(trace.read_bytes()).hexdigest()
                    )
                    with self.assertRaises(AssertionError):
                        _validate_f19_command_trace(trace, root, candidate_hash)

    def test_f20_sysv_diagnostic_scope_and_receipt_are_fail_closed(self) -> None:
        payload = ROOT / "packaging" / "appliance" / "install-offline-payload.sh"
        verifier = ROOT / "packaging" / "appliance" / "verify-offline-appliance.sh"
        self.assertEqual(
            hashlib.sha256(payload.read_bytes()).hexdigest(),
            "3116215f4f2dde376f591b06cb192b3cc725e4261885c5a0bc88e23b8867005b",
        )
        self.assertEqual(
            hashlib.sha256(verifier.read_bytes()).hexdigest(),
            "f188d76e7c19ba38472a5125c68d53e428bcf095d36878ac688e56a93fc627ad",
        )
        compile(F20_SNAPSHOT_SCRIPT, "f20-snapshot.py", "exec")
        compile(F21_CAPTURE_ERROR_SCRIPT, "f21-capture-error.py", "exec")
        source = pathlib.Path(__file__).read_text(encoding="utf-8")
        phase12 = source.split(
            "trace_begin 12-final-disable-readback final-disable-readback\n", 1
        )[1].split('[[ "$denied_units_finalized" == true ]]', 1)[0]
        self.assertEqual(phase12.count("\ndisable_unmasked_units\n"), 1)
        self.assertIn("set -x\ndisable_unmasked_units\nset +x", phase12)
        self.assertNotRegex(
            phase12,
            r"(?:if|until|while)\s+disable_unmasked_units|disable_unmasked_units\s*(?:\|\||&&)",
        )
        wrapper = source.split("cat >\"$work/f20-helper-wrapper.body\" <<'EOF'\n", 1)[
            1
        ].split("\nEOF\n", 1)[0]
        for exact in (
            '[[ "$#" -ne 3 || "$1" != --root=/ || "$2" != disable || "$3" != iscsid ]]',
            "ARGC\\t3",
            "ARGV0\\t--root=/",
            "ARGV1\\tdisable",
            "ARGV2\\tiscsid",
            '"$real_helper" "$@"',
            "helper_status=$?",
            'exit "$helper_status"',
            "SYSTEMD_OFFLINE=1",
        ):
            self.assertIn(exact, wrapper)
        self.assertEqual(wrapper.count('"$real_helper" "$@"'), 1)
        self.assertNotIn("systemctl", wrapper)
        self.assertNotIn("eval", wrapper)

        absent_objects = [
            {"path": path, "type": "absent", "package": None} for path in F20_SYSV_PATHS
        ]
        absent_rc = [
            {
                "path": path,
                "identity": {"path": path, "type": "absent", "package": None},
                "entries": [],
            }
            for path in F20_RC_DIRS
        ]
        absent_generators = [
            {
                "path": path,
                "identity": {"path": path, "type": "absent", "package": None},
                "entries": [],
            }
            for path in F20_GENERATOR_ROOTS
        ]
        mounts = [
            {
                "mountpoint": path,
                "mount_id": index + 1,
                "root": "/",
                "filesystem_type": "tmpfs",
                "source": "tmpfs",
                "fixture_source": f"/fixture/{index}",
                "fixture_identity": f"1:{index + 1}",
                "mountpoint_identity": f"1:{index + 1}",
                "bind_identity_matches": True,
            }
            for index, path in enumerate(
                [
                    "/etc/init.d",
                    *F20_RC_DIRS,
                    "/usr/lib/systemd/systemd-sysv-install",
                ]
            )
        ]
        empty_hash = hashlib.sha256(b"").hexdigest()
        receipt = {
            "schema_version": 1,
            "stage": "after",
            "objects": absent_objects,
            "rc_directories": absent_rc,
            "generators": absent_generators,
            "mounts": mounts,
            "helper": {
                "invoked": True,
                "real_helper": {
                    "path": "/fixture/helper",
                    "size": 1,
                    "mode": "0755",
                    "sha256": "1" * 64,
                },
                "entry_guard": {
                    "schema_version": F25_ENTRY_SCHEMA,
                    "entry_reached": True,
                    "argc": 3,
                    "argv": [
                        {
                            "position": 0,
                            "classification": "ALLOWLISTED",
                            "value": "--root=/",
                        },
                        {
                            "position": 1,
                            "classification": "ALLOWLISTED",
                            "value": "disable",
                        },
                        {
                            "position": 2,
                            "classification": "ALLOWLISTED",
                            "value": "iscsid",
                        },
                    ],
                    "predicates": {
                        predicate: True for predicate in F25_ENTRY_PREDICATES
                    },
                    "guard_outcome": "ACCEPTED",
                },
                "argv": ["--root=/", "disable", "iscsid"],
                "environment": {"SYSTEMD_OFFLINE": "1"},
                "status": 1,
                "invocation_sha256": "2" * 64,
                "outputs": {
                    label: {
                        "size": 0,
                        "sha256": empty_hash,
                        "safe_first_line": "",
                        "content_base64": "",
                    }
                    for label in ("stdout", "stderr")
                },
            },
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            candidate = root / "f20-after.json"

            def write(document: dict[str, object]) -> None:
                candidate.write_text(
                    json.dumps(document, sort_keys=True) + "\n", encoding="utf-8"
                )

            write(receipt)
            _validate_f20_snapshot(candidate, root, "after")
            not_invoked = {
                **receipt,
                "helper": {
                    "invoked": False,
                    "real_helper": receipt["helper"]["real_helper"],
                    "entry_guard": {"entry_reached": False},
                },
            }
            write(not_invoked)
            validated_not_invoked, _ = _validate_f20_snapshot(candidate, root, "after")
            self.assertIs(validated_not_invoked["helper"]["invoked"], False)
            mutations = {
                "schema": {**receipt, "schema_version": 2},
                "wrong-path": {
                    **receipt,
                    "objects": [
                        {**absent_objects[0], "path": "/etc/init.d/other"},
                        *absent_objects[1:],
                    ],
                },
                "mount-drift": {
                    **receipt,
                    "mounts": [
                        {**mounts[0], "bind_identity_matches": False},
                        *mounts[1:],
                    ],
                },
                "argv": {
                    **receipt,
                    "helper": {**receipt["helper"], "argv": ["enable", "iscsid"]},
                },
                "unbounded": {
                    **receipt,
                    "helper": {
                        **receipt["helper"],
                        "outputs": {
                            **receipt["helper"]["outputs"],
                            "stderr": {
                                **receipt["helper"]["outputs"]["stderr"],
                                "size": F20_OUTPUT_MAX_BYTES + 1,
                            },
                        },
                    },
                },
                "false-with-status": {
                    **not_invoked,
                    "helper": {**not_invoked["helper"], "status": 1},
                },
                "true-missing-output": {
                    **receipt,
                    "helper": {
                        key: value
                        for key, value in receipt["helper"].items()
                        if key != "outputs"
                    },
                },
                "true-duplicate-key-shape": {
                    **receipt,
                    "helper": {**receipt["helper"], "unexpected": True},
                },
                "true-malformed-status": {
                    **receipt,
                    "helper": {**receipt["helper"], "status": "1"},
                },
                "true-inconsistent-output": {
                    **receipt,
                    "helper": {
                        **receipt["helper"],
                        "outputs": {
                            **receipt["helper"]["outputs"],
                            "stdout": {
                                **receipt["helper"]["outputs"]["stdout"],
                                "sha256": "9" * 64,
                            },
                        },
                    },
                },
            }
            for label, mutation in mutations.items():
                with self.subTest(label=label):
                    write(mutation)
                    with self.assertRaises(AssertionError):
                        _validate_f20_snapshot(candidate, root, "after")

            helper_namespace: dict[str, object] = {}
            exec(  # noqa: S102 - execute the fixed generated helper for regression proof
                F20_SNAPSHOT_SCRIPT.rsplit("raise SystemExit(main())", 1)[0],
                helper_namespace,
            )
            real_helper = root / "f20-helper-real"
            real_helper.write_bytes(b"helper")
            (root / "f20-helper-source.sha256").write_text(
                hashlib.sha256(b"helper").hexdigest(), encoding="ascii"
            )
            helper_function = helper_namespace["helper"]
            self.assertTrue(callable(helper_function))
            self.assertIn(
                r're.fullmatch(rb"[0-9]{1,3}\n",status_bytes)',
                F20_SNAPSHOT_SCRIPT,
            )
            self.assertNotIn(
                'status_text=paths[3].read_text("ascii")',
                F20_SNAPSHOT_SCRIPT,
            )
            status_patterns = [
                value
                for value in helper_function.__code__.co_consts
                if isinstance(value, bytes) and value.startswith(b"[0-9]{1,3}")
            ]
            self.assertEqual(status_patterns, [rb"[0-9]{1,3}\n"])

            def status_is_valid(status: bytes) -> bool:
                return (
                    re.fullmatch(status_patterns[0], status) is not None
                    and int(status) <= 255
                )

            valid_statuses = (b"0\n", b"1\n", b"124\n", b"255\n")
            invalid_statuses = {
                "empty": b"",
                "sign": b"-1\n",
                "leading-space": b" 1\n",
                "trailing-space": b"1 \n",
                "crlf": b"1\r\n",
                "missing-lf": b"1",
                "extra-line": b"1\n2\n",
                "literal-backslash-n": rb"0\n",
                "too-many-digits": b"0000\n",
                "non-ascii-digit": "\N{ARABIC-INDIC DIGIT ZERO}\n".encode(),
                "above-255": b"256\n",
            }
            for status in valid_statuses:
                self.assertTrue(status_is_valid(status), status)
            for label, status in invalid_statuses.items():
                with self.subTest(invalid_regex_status=label):
                    self.assertFalse(status_is_valid(status))

            absent = helper_function("after", root)
            self.assertEqual(set(absent), {"invoked", "real_helper", "entry_guard"})
            self.assertIs(absent["invoked"], False)
            self.assertEqual(absent["entry_guard"], {"entry_reached": False})
            for evidence_name in (
                "f20-helper-invocation.tsv",
                "f20-helper-stdout.bin",
                "f20-helper-stderr.bin",
                "f20-helper-status.txt",
                "f20-helper-stdout.bin.partial",
                "f20-helper-stderr.bin.partial",
                "f20-helper-repeat.txt",
            ):
                with self.subTest(evidence=evidence_name):
                    evidence = root / evidence_name
                    evidence.write_bytes(b"x")
                    with self.assertRaises(SystemExit):
                        helper_function("after", root)
                    evidence.unlink()

            if os.name != "posix":
                return

            invocation = (
                b"F20HELPER\t1\n"
                b"ARGC\t3\n"
                b"ARGV0\t--root=/\n"
                b"ARGV1\tdisable\n"
                b"ARGV2\tiscsid\n"
                b"ENV\tSYSTEMD_OFFLINE=1\n"
            )
            evidence_paths = {
                "invocation": root / "f20-helper-invocation.tsv",
                "stdout": root / "f20-helper-stdout.bin",
                "stderr": root / "f20-helper-stderr.bin",
                "status": root / "f20-helper-status.txt",
            }

            def write_helper_evidence(status: bytes) -> None:
                evidence_paths["invocation"].write_bytes(invocation)
                evidence_paths["stdout"].write_bytes(b"")
                evidence_paths["stderr"].write_bytes(b"")
                evidence_paths["status"].write_bytes(status)
                for path in evidence_paths.values():
                    path.chmod(0o600)

            def remove_helper_evidence() -> None:
                for path in evidence_paths.values():
                    path.unlink(missing_ok=True)

            write_helper_evidence(b"0\n")
            evidence_paths["status"].chmod(0o640)
            with self.assertRaisesRegex(
                SystemExit, "F20 helper evidence metadata is invalid"
            ):
                helper_function("after", root)
            remove_helper_evidence()

            original_path_stat = pathlib.Path.stat
            projected_paths = frozenset(evidence_paths.values())

            class ExactStatProxy:
                __slots__ = ("_observed",)

                def __init__(self, observed: os.stat_result) -> None:
                    object.__setattr__(self, "_observed", observed)

                @property
                def st_mode(self) -> int:
                    return stat.S_IFREG | 0o600

                @property
                def st_nlink(self) -> int:
                    return 1

                @property
                def st_uid(self) -> int:
                    return 0

                @property
                def st_gid(self) -> int:
                    return 0

                def __getattr__(self, name: str) -> object:
                    return getattr(self._observed, name)

                def __getitem__(self, index: object) -> object:
                    return self._observed[index]

                def __iter__(self) -> object:
                    return iter(self._observed)

                def __len__(self) -> int:
                    return len(self._observed)

                def __contains__(self, value: object) -> bool:
                    return value in self._observed

                def __reversed__(self) -> object:
                    return reversed(self._observed)

                def count(self, value: object) -> int:
                    return self._observed.count(value)

                def index(self, value: object, *args: int) -> int:
                    return self._observed.index(value, *args)

                def __setattr__(self, name: str, value: object) -> None:
                    raise AttributeError("exact stat proxy is immutable")

                def __delattr__(self, name: str) -> None:
                    raise AttributeError("exact stat proxy is immutable")

            def projected_helper_stat(
                path: pathlib.Path, *args: object, **kwargs: object
            ) -> object:
                observed = original_path_stat(path, *args, **kwargs)
                if path not in projected_paths:
                    return observed
                return ExactStatProxy(observed)

            with mock.patch.object(pathlib.Path, "stat", new=projected_helper_stat):
                for status in valid_statuses:
                    with self.subTest(valid_status=status):
                        try:
                            write_helper_evidence(status)
                            if status == b"0\n":
                                actual = original_path_stat(evidence_paths["status"])
                                projected = evidence_paths["status"].stat()
                                self.assertEqual(stat.S_IMODE(projected.st_mode), 0o600)
                                self.assertEqual(
                                    (
                                        projected.st_uid,
                                        projected.st_gid,
                                        projected.st_nlink,
                                    ),
                                    (0, 0, 1),
                                )
                                preserved_attributes = sorted(
                                    attribute
                                    for attribute in dir(actual)
                                    if attribute.startswith("st_")
                                    and attribute
                                    not in {"st_mode", "st_nlink", "st_uid", "st_gid"}
                                )
                                for attribute in preserved_attributes:
                                    self.assertEqual(
                                        getattr(projected, attribute),
                                        getattr(actual, attribute),
                                    )
                                self.assertEqual(tuple(projected), tuple(actual))
                                self.assertEqual(len(projected), len(actual))
                                self.assertEqual(
                                    list(reversed(projected)), list(reversed(actual))
                                )
                                with self.assertRaisesRegex(
                                    AttributeError, "exact stat proxy is immutable"
                                ):
                                    projected.st_uid = 1
                                self.assertEqual(
                                    real_helper.stat(), original_path_stat(real_helper)
                                )
                            observed = helper_function("after", root)
                            self.assertIs(observed["invoked"], True)
                            self.assertEqual(observed["status"], int(status))
                        finally:
                            remove_helper_evidence()

                for label, status in invalid_statuses.items():
                    with self.subTest(invalid_status=label):
                        try:
                            write_helper_evidence(status)
                            with self.assertRaises(SystemExit):
                                helper_function("after", root)
                        finally:
                            remove_helper_evidence()

    def test_f21_capture_error_portable_contract_is_fail_closed(self) -> None:
        compile(F21_CAPTURE_ERROR_SCRIPT, "f21-capture-error.py", "exec")
        script_prefix = F21_CAPTURE_ERROR_SCRIPT.rsplit("raise SystemExit(main())", 1)[
            0
        ]
        namespace: dict[str, object] = {}
        exec(  # noqa: S102 - execute only the fixed generated diagnostic helpers
            script_prefix,
            namespace,
        )
        classify_error = namespace["classify_error"]
        self.assertTrue(callable(classify_error))
        valid_error = b"F20 helper evidence is incomplete\n"
        self.assertEqual(
            classify_error("f20-after", valid_error),
            "f20-helper-evidence-incomplete",
        )
        for label, stage, content in (
            (
                "oversized",
                "f20-after",
                b"F20 " + b"x" * F21_CAPTURE_ERROR_MAX_STDERR_BYTES,
            ),
            (
                "secret",
                "f20-after",
                b"F20 password=not-a-real-test-value\n",
            ),
            ("malformed", "f20-after", b"arbitrary failure\n"),
            ("multiline", "f20-after", b"F20 first\nF20 second\n"),
        ):
            with self.subTest(label=label), self.assertRaises(SystemExit):
                classify_error(stage, content)

        strict_metadata = (
            "not stat.S_ISREG(metadata.st_mode) or stderr_path.is_symlink() "
            "or metadata.st_uid!=0 or metadata.st_gid!=0 or "
            "stat.S_IMODE(metadata.st_mode)!=0o600 or metadata.st_nlink!=1"
        )
        self.assertIn(strict_metadata, F21_CAPTURE_ERROR_SCRIPT)
        self.assertIn('output.name!="f21-capture-error.json"', F21_CAPTURE_ERROR_SCRIPT)
        self.assertIn("F21 capture record already exists", F21_CAPTURE_ERROR_SCRIPT)
        self.assertIn("F21 capture path escapes fixture", F21_CAPTURE_ERROR_SCRIPT)
        source = pathlib.Path(__file__).read_text(encoding="utf-8")
        phase12 = source.split(
            "trace_begin 12-final-disable-readback final-disable-readback\n", 1
        )[1].split('[[ "$denied_units_finalized" == true ]]', 1)[0]
        self.assertEqual(phase12.count("\ndisable_unmasked_units\n"), 1)
        self.assertNotRegex(
            phase12,
            r"(?:if|until|while)\s+disable_unmasked_units|disable_unmasked_units\s*(?:\|\||&&)",
        )
        self.assertIn(
            '[[ ! -e "$f21_capture_error_receipt" && ! -L "$f21_capture_error_receipt" ]]',
            phase12,
        )

        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            script = root / "f21-capture-error.py"
            script.write_text(F21_CAPTURE_ERROR_SCRIPT, encoding="utf-8", newline="\n")
            stderr_path = root / "f20-after.stderr"
            stderr_path.write_bytes(valid_error)
            output = root / "f21-capture-error.json"
            outside = root.parent / f"{root.name}-outside.stderr"
            outside.write_bytes(valid_error)
            command = [sys.executable, str(script)]
            for label, args, expected in (
                (
                    "unknown-stage",
                    ["unknown", "1", str(stderr_path), str(output), str(root)],
                    "F21 capture stage invalid",
                ),
                (
                    "status-zero",
                    ["f20-after", "0", str(stderr_path), str(output), str(root)],
                    "F21 capture status invalid",
                ),
                (
                    "traversal",
                    ["f20-after", "1", str(outside), str(output), str(root)],
                    "F21 capture path escapes fixture",
                ),
            ):
                with self.subTest(label=label):
                    rejected = subprocess.run(
                        [*command, *args],
                        text=True,
                        capture_output=True,
                        check=False,
                    )
                    self.assertNotEqual(rejected.returncode, 0)
                    self.assertEqual(rejected.stderr, expected + "\n")
                    self.assertFalse(output.exists())
                    self.assertFalse(
                        output.with_suffix(output.suffix + ".partial").exists()
                    )
            output.write_text("preserved\n", encoding="ascii")
            duplicate = subprocess.run(
                [
                    *command,
                    "f20-after",
                    "1",
                    str(stderr_path),
                    str(output),
                    str(root),
                ],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertNotEqual(duplicate.returncode, 0)
            self.assertEqual(duplicate.stderr, "F21 capture record already exists\n")
            self.assertEqual(output.read_text(encoding="ascii"), "preserved\n")
            outside.unlink()

    def test_f29_runner_contract_and_validator_are_fail_closed(self) -> None:
        compile(F29_F21_RUNNER_SCRIPT, "f29-f21-runner.py", "exec")
        compile(F29_OUTER_RUNNER_SCRIPT, "f29-outer-runner.py", "exec")
        prefix = F29_F21_RUNNER_SCRIPT.rsplit("raise SystemExit(main())", 1)[0]
        namespace: dict[str, object] = {}
        exec(  # noqa: S102 - execute only the fixed generated diagnostic helper
            prefix, namespace
        )
        classify = namespace["classify"]
        self.assertTrue(callable(classify))
        classification_cases = {
            b"": "EMPTY",
            b"F21 capture stderr unsafe or truncated\n": "UNSAFE_OR_TRUNCATED",
            b"F21 capture stderr encoding invalid\n": "ENCODING_INVALID",
            b"F21 capture stderr framing invalid\n": "FRAMING_INVALID",
            b"F21 capture argv invalid\n": "ARGV_INVALID",
            b"F21 capture path identity invalid\n": "PATH_IDENTITY_INVALID",
            b"F21 capture stderr metadata invalid\n": "METADATA_INVALID",
            b"F21 capture record already exists\n": "OUTPUT_EXISTS",
            b"Permission denied\n": "PERMISSION_DENIED",
            b"Traceback fixture exception\n": "PYTHON_EXCEPTION_SANITIZED",
            b"unclassified bounded fixture\n": "UNCLASSIFIED_BOUNDED",
        }
        for raw, expected in classification_cases.items():
            with self.subTest(expected=expected):
                self.assertEqual(classify(raw), expected)
        for raw, expected in (
            (b"\xff", "ENCODING_INVALID"),
            (b"line-one\nline-two\n", "FRAMING_INVALID"),
            (b"password=not-a-real-test-value\n", "UNSAFE_OR_TRUNCATED"),
            (b"x" * (F29_F21_OUTPUT_MAX_BYTES + 1), "UNSAFE_OR_TRUNCATED"),
        ):
            with self.subTest(adversarial=expected):
                self.assertEqual(classify(raw), expected)

        expected_source = "a" * 64
        valid = {
            "schema_version": 1,
            "stage": "f20-after",
            "snapshot_status": 1,
            "child_status": 7,
            "timed_out": False,
            "stdout_size": 0,
            "stdout_sha256": hashlib.sha256(b"").hexdigest(),
            "stderr_size": 1,
            "stderr_sha256": hashlib.sha256(b"x").hexdigest(),
            "stderr_class": "UNCLASSIFIED_BOUNDED",
            "source_sha256": expected_source,
        }
        raw = (json.dumps(valid, sort_keys=True, separators=(",", ":")) + "\n").encode(
            "ascii"
        )
        with mock.patch(f"{__name__}._read_strict_root_file", return_value=raw):
            receipt, receipt_hash = _validate_f29_f21_attempt(
                pathlib.Path("fixture/f29-f21-attempt.json"),
                pathlib.Path("fixture"),
                expected_source,
            )
        self.assertEqual(receipt, valid)
        self.assertEqual(receipt_hash, hashlib.sha256(raw).hexdigest())
        for label, mutation in {
            "missing": {key: value for key, value in valid.items() if key != "stage"},
            "stage": {**valid, "stage": "other"},
            "status": {**valid, "snapshot_status": 0},
            "timeout": {**valid, "timed_out": True},
            "output-size": {**valid, "stderr_size": F29_F21_OUTPUT_MAX_BYTES + 1},
            "digest": {**valid, "stdout_sha256": "x" * 64},
            "class": {**valid, "stderr_class": "arbitrary"},
            "source": {**valid, "source_sha256": "b" * 64},
        }.items():
            candidate = (
                json.dumps(mutation, sort_keys=True, separators=(",", ":")) + "\n"
            ).encode("ascii")
            with (
                self.subTest(mutation=label),
                mock.patch(
                    f"{__name__}._read_strict_root_file", return_value=candidate
                ),
                self.assertRaises(AssertionError),
            ):
                _validate_f29_f21_attempt(
                    pathlib.Path("fixture/f29-f21-attempt.json"),
                    pathlib.Path("fixture"),
                    expected_source,
                )

        outer_valid = {
            "schema_version": 1,
            "stage": "f19-after",
            "runner_invoked": True,
            "runner_status": 1,
            "timed_out": False,
            "stdout_size": 0,
            "stdout_sha256": hashlib.sha256(b"").hexdigest(),
            "stderr_size": 1,
            "stderr_sha256": hashlib.sha256(b"x").hexdigest(),
            "stderr_class": "UNCLASSIFIED_BOUNDED",
            "attempt_exists": False,
            "output_exists": False,
            "source_sha256": expected_source,
        }
        outer_raw = (
            json.dumps(outer_valid, sort_keys=True, separators=(",", ":")) + "\n"
        ).encode("ascii")
        with mock.patch(f"{__name__}._read_strict_root_file", return_value=outer_raw):
            outer_receipt, _ = _validate_f29_outer_receipt(
                pathlib.Path("fixture/f29-outer-f19-after.json"),
                pathlib.Path("fixture"),
                "f19-after",
                expected_source,
            )
        self.assertEqual(outer_receipt, outer_valid)
        for key, value in (
            ("runner_invoked", False),
            ("stage", "other"),
            ("stderr_class", "other"),
        ):
            invalid = {**outer_valid, key: value}
            invalid_raw = (
                json.dumps(invalid, sort_keys=True, separators=(",", ":")) + "\n"
            ).encode("ascii")
            with (
                self.subTest(outer_mutation=key),
                mock.patch(
                    f"{__name__}._read_strict_root_file", return_value=invalid_raw
                ),
                self.assertRaises(AssertionError),
            ):
                _validate_f29_outer_receipt(
                    pathlib.Path("fixture/f29-outer-f19-after.json"),
                    pathlib.Path("fixture"),
                    "f19-after",
                    expected_source,
                )

        empty_sha256 = hashlib.sha256(b"").hexdigest()
        outer_success = {
            **outer_valid,
            "runner_status": 0,
            "stdout_size": 0,
            "stdout_sha256": empty_sha256,
            "stderr_size": 0,
            "stderr_sha256": empty_sha256,
            "stderr_class": "EMPTY",
            "attempt_exists": False,
            "output_exists": True,
        }
        outer_success_raw = (
            json.dumps(outer_success, sort_keys=True, separators=(",", ":")) + "\n"
        ).encode("ascii")
        with mock.patch(
            f"{__name__}._read_strict_root_file", return_value=outer_success_raw
        ):
            success_receipt, _ = _validate_f29_outer_receipt(
                pathlib.Path("fixture/f29-outer-f19-after.json"),
                pathlib.Path("fixture"),
                "f19-after",
                expected_source,
            )
        self.assertEqual(success_receipt, outer_success)
        for label, mutation in {
            "timed-out": {**outer_success, "timed_out": True},
            "stdout-size": {**outer_success, "stdout_size": 1},
            "stdout-digest": {**outer_success, "stdout_sha256": "b" * 64},
            "stderr-size": {**outer_success, "stderr_size": 1},
            "stderr-digest": {**outer_success, "stderr_sha256": "b" * 64},
            "stderr-class": {
                **outer_success,
                "stderr_class": "UNCLASSIFIED_BOUNDED",
            },
            "attempt-present": {**outer_success, "attempt_exists": True},
            "output-absent": {**outer_success, "output_exists": False},
        }.items():
            invalid_raw = (
                json.dumps(mutation, sort_keys=True, separators=(",", ":")) + "\n"
            ).encode("ascii")
            with (
                self.subTest(outer_success_mutation=label),
                mock.patch(
                    f"{__name__}._read_strict_root_file", return_value=invalid_raw
                ),
                self.assertRaisesRegex(
                    AssertionError, "F29 outer success envelope is invalid"
                ),
            ):
                _validate_f29_outer_receipt(
                    pathlib.Path("fixture/f29-outer-f19-after.json"),
                    pathlib.Path("fixture"),
                    "f19-after",
                    expected_source,
                )
        outer_timeout = {**outer_valid, "runner_status": 124, "timed_out": True}
        outer_timeout_raw = (
            json.dumps(outer_timeout, sort_keys=True, separators=(",", ":")) + "\n"
        ).encode("ascii")
        with mock.patch(
            f"{__name__}._read_strict_root_file", return_value=outer_timeout_raw
        ):
            timeout_receipt, _ = _validate_f29_outer_receipt(
                pathlib.Path("fixture/f29-outer-f19-after.json"),
                pathlib.Path("fixture"),
                "f19-after",
                expected_source,
            )
        self.assertEqual(timeout_receipt, outer_timeout)

        valid_direct = (b"9\n", b"", b"F29 outer path identity is invalid\n")
        with mock.patch(f"{__name__}._read_strict_root_file", side_effect=valid_direct):
            direct = _validate_f29_direct_capture(pathlib.Path("fixture"), "f19-after")
        self.assertEqual(direct["stage"], "f19-after")
        self.assertIs(direct["attempted"], True)
        self.assertEqual(direct["exit_status"], 9)
        self.assertIs(direct["completed_status_record"], True)
        self.assertFalse(direct["timed_out"])
        self.assertEqual(direct["streams"]["stdout"]["classification"], "EMPTY")
        self.assertEqual(direct["streams"]["stderr"]["classification"], "OUTER_GUARD")
        direct_success = {
            **direct,
            "exit_status": 0,
            "streams": {
                "stdout": {
                    "size": 0,
                    "sha256": empty_sha256,
                    "classification": "EMPTY",
                },
                "stderr": {
                    "size": 0,
                    "sha256": empty_sha256,
                    "classification": "EMPTY",
                },
            },
        }
        validated_capture_error = ({"status": 1}, "b" * 64)
        self.assertIs(
            _require_f29_outer_success_correlation(
                [(outer_success, "a" * 64)],
                [direct_success],
                validated_capture_error,
            ),
            validated_capture_error,
        )
        for label, mutation in {
            "nonzero-status": {**direct_success, "exit_status": 1},
            "timeout": {**direct_success, "timed_out": True},
            "incomplete-status": {
                **direct_success,
                "completed_status_record": False,
            },
            "stdout-size": {
                **direct_success,
                "streams": {
                    **direct_success["streams"],
                    "stdout": {
                        **direct_success["streams"]["stdout"],
                        "size": 1,
                    },
                },
            },
            "stderr-class": {
                **direct_success,
                "streams": {
                    **direct_success["streams"],
                    "stderr": {
                        **direct_success["streams"]["stderr"],
                        "classification": "UNCLASSIFIED_BOUNDED",
                    },
                },
            },
        }.items():
            with (
                self.subTest(direct_success_mutation=label),
                self.assertRaises(AssertionError),
            ):
                _require_f29_outer_success_correlation(
                    [(outer_success, "a" * 64)],
                    [mutation],
                    validated_capture_error,
                )
        with self.assertRaisesRegex(
            AssertionError, "F29 outer/direct success stages do not match"
        ):
            _require_f29_outer_success_correlation(
                [(outer_success, "a" * 64)],
                [{**direct_success, "stage": "f20-after"}],
                validated_capture_error,
            )
        with self.assertRaisesRegex(
            AssertionError, "F29 outer success has no validated F21 capture error"
        ):
            _require_f29_outer_success_correlation(
                [(outer_success, "a" * 64)], [direct_success], None
            )
        for fixed_class in sorted(F29_REQUIRED_PATH_CLASSES):
            fixed_direct = {
                **direct,
                "streams": {
                    **direct["streams"],
                    "stderr": {
                        **direct["streams"]["stderr"],
                        "classification": fixed_class,
                    },
                },
            }
            with self.subTest(fixed_required_path_class=fixed_class):
                formatted_fixed = _format_f29_direct_captures([fixed_direct])
                self.assertIn(f"stderr_class={fixed_class}", formatted_fixed)
        for invalid_class in (
            "F20_SNAPSHOT_STDERR",
            "F20_SNAPSHOT_STDERR_EXPECTED_MODE_EXTRA",
            "EXPECTED_MODE_F20_SNAPSHOT_STDERR",
            "F20_SNAPSHOT_STDERR_EXPECTED_MODE F21_CAPTURE_SOURCE_EXPECTED_MODE",
            "f20_snapshot_stderr_expected_mode",
        ):
            invalid_direct = {
                **direct,
                "streams": {
                    **direct["streams"],
                    "stderr": {
                        **direct["streams"]["stderr"],
                        "classification": invalid_class,
                    },
                },
            }
            with (
                self.subTest(invalid_required_path_class=invalid_class),
                self.assertRaises(AssertionError),
            ):
                _format_f29_direct_captures([invalid_direct])
        for label, values in {"status": (b"256\n", b"", b"")}.items():
            with (
                self.subTest(direct_capture=label),
                mock.patch(f"{__name__}._read_strict_root_file", side_effect=values),
                self.assertRaises(AssertionError),
            ):
                _validate_f29_direct_capture(pathlib.Path("fixture"), "f19-after")
        for label, raw in {
            "secret": b"token=not-a-real-test-value\n",
            "multiline": b"one\ntwo\n",
            "encoding": b"\xff",
            "oversize": b"x" * (F29_DIRECT_OUTPUT_MAX_BYTES + 1),
        }.items():
            with self.subTest(direct_stream=label):
                self.assertEqual(
                    _classify_f29_direct_stream(raw),
                    "UNSAFE_OR_TRUNCATED"
                    if label in {"secret", "oversize"}
                    else "FRAMING_INVALID"
                    if label == "multiline"
                    else "ENCODING_INVALID",
                )
        self.assertEqual(
            _classify_f29_direct_stream(b"/private/fixture/path\n"),
            "UNSAFE_OR_TRUNCATED",
        )
        self.assertEqual(
            _classify_f29_direct_stream(
                b"F29 outer required path metadata is invalid\n"
            ),
            "REQUIRED_PATH_METADATA",
        )
        for malformed_required_path in (
            b"prefix F29 outer required path metadata is invalid\n",
            b"F29 outer required path metadata is invalid suffix\n",
            b"F29 outer required path metadata is invalid\nF29 outer required path metadata is invalid\n",
            b"F29 outer required path metadata is invalid\x00\n",
        ):
            with self.subTest(malformed_required_path=malformed_required_path):
                self.assertNotEqual(
                    _classify_f29_direct_stream(malformed_required_path),
                    "REQUIRED_PATH_METADATA",
                )
        with self.assertRaises(AssertionError):
            _validate_f29_direct_capture(pathlib.Path("fixture"), "../escape")
        required_message_capture = (
            b"1\n",
            b"",
            b"F29 outer required path metadata is invalid\n",
        )
        with (
            mock.patch(
                f"{__name__}._read_strict_root_file",
                side_effect=required_message_capture,
            ),
            self.assertRaises(AssertionError),
        ):
            _validate_f29_direct_capture(pathlib.Path("fixture"), "f20-after")
        dummy_required_paths = {
            name: (pathlib.Path(f"fixture-{name}"), mode, None)
            for name, mode in F29_REQUIRED_OBJECT_MODES.items()
        }
        with (
            mock.patch(
                f"{__name__}._read_strict_root_file",
                side_effect=required_message_capture,
            ),
            mock.patch(
                f"{__name__}._classify_f29_required_path_predicate",
                return_value="F20_SNAPSHOT_STDERR_EXPECTED_MODE",
            ),
        ):
            classified_required = _validate_f29_direct_capture(
                pathlib.Path("fixture"),
                "f20-after",
                required_paths=dummy_required_paths,
            )
        self.assertEqual(
            classified_required["streams"]["stderr"]["classification"],
            "F20_SNAPSHOT_STDERR_EXPECTED_MODE",
        )
        f20_direct = {**direct, "stage": "f20-after"}
        for captures, expected_count in (
            ([direct], 1),
            ([f20_direct], 1),
            ([direct, f20_direct], 2),
        ):
            with self.subTest(canonical=[item["stage"] for item in captures]):
                formatted = _format_f29_direct_captures(captures)
                self.assertTrue(
                    formatted.startswith(
                        f"F29 direct outer invocations: count={expected_count} "
                    )
                )
                self.assertNotIn("fixture", formatted)
        formatted = _format_f29_direct_captures([direct, f20_direct])
        self.assertLess(
            formatted.index("stage=f19-after"), formatted.index("stage=f20-after")
        )
        for malformed in (
            [],
            [direct, direct],
            [f20_direct, direct],
            [{**direct, "stage": "unknown"}],
            [{key: value for key, value in direct.items() if key != "streams"}],
            [{**direct, "completed_status_record": False}],
            [{**direct, "timed_out": True}],
        ):
            with self.assertRaises(AssertionError):
                _format_f29_direct_captures(malformed)

        source = pathlib.Path(__file__).read_text(encoding="utf-8")
        phase12 = source.split(
            "trace_begin 12-final-disable-readback final-disable-readback\n", 1
        )[1].split('[[ "$denied_units_finalized" == true ]]', 1)[0]
        self.assertEqual(phase12.count('python3 "$f29_outer_runner"'), 1)
        self.assertEqual(phase12.count("capture_f29_outer_invocation"), 3)
        self.assertEqual(phase12.count('python3 "$f29_f21_runner"'), 0)
        self.assertNotIn('python3 "$f21_capture_error" f19-after', phase12)
        self.assertNotIn('python3 "$f21_capture_error" f20-after', phase12)
        self.assertEqual(phase12.count("disable_unmasked_units\n"), 1)
        self.assertIn("timeout=15", F29_F21_RUNNER_SCRIPT)
        self.assertIn("os.O_EXCL|os.O_NOFOLLOW", F29_F21_RUNNER_SCRIPT)
        self.assertIn("return child_status", F29_F21_RUNNER_SCRIPT)
        self.assertEqual(
            F29_OUTER_RUNNER_SCRIPT.count("subprocess.run([sys.executable,str(f29)"), 1
        )
        self.assertNotIn("str(f21),stage", F29_OUTER_RUNNER_SCRIPT)
        expected_script_hashes = {
            "F19": "c156b0938b0af78a5c2e04504fb195e0f3f841dfcfe2c603c2096e764774cc73",
            "F20": "ea2e8f6b08b56e834c4d093b794f4196f4be481d0b292a0a0d84924c3e5a5709",
            "F21": "13366ef44f4065b716d42a3294d297bb8c77be7a46e61ae1093e1885a9ffdccd",
            "F29": "6d0c55cca01e2512a1234a42ebbfaba258e54edc88e55b47295f411a1faffc24",
            "F29_OUTER": "9c38bfc1da2751d99bc4a9785bbcce18a09f54210f903c29e23fc863a004e41a",
        }
        for label, script in (
            ("F19", F19_SNAPSHOT_SCRIPT),
            ("F20", F20_SNAPSHOT_SCRIPT),
            ("F21", F21_CAPTURE_ERROR_SCRIPT),
            ("F29", F29_F21_RUNNER_SCRIPT),
            ("F29_OUTER", F29_OUTER_RUNNER_SCRIPT),
        ):
            self.assertEqual(
                hashlib.sha256(script.encode()).hexdigest(),
                expected_script_hashes[label],
            )
        for function_name, expected_hash in (
            (
                "capture_f29_outer_invocation",
                "6bda7b0bf00c07a2f5aac130a5bc58b4cb3bd6354c73649b92a8af0cc11bc3d3",
            ),
            (
                "f19_capture_after_failure",
                "8633f3d90485b4624d88154ac5c0694f91fa95179378ba09983fe75c6e0dd006",
            ),
        ):
            start = source.index(f"{function_name}() {{\n")
            end = source.index("\n}\n", start) + 3
            self.assertEqual(
                hashlib.sha256(source[start:end].encode()).hexdigest(), expected_hash
            )

    def test_f30_absolute_snapshot_stderr_mode_contract_is_exact(self) -> None:
        source = pathlib.Path(__file__).read_text(encoding="utf-8")

        def exact_shell_function(name: str) -> str:
            start = source.index(f"{name}() {{\n")
            end = source.index("\n}\n", start) + 3
            return source[start:end]

        capture_function = exact_shell_function("f19_capture_after_failure")
        absolute_snapshot_chmod = '/usr/bin/chmod 0600 -- "$snapshot_stderr"'
        self.assertEqual(capture_function.count(absolute_snapshot_chmod), 2)
        self.assertNotIn('\n    chmod 0600 -- "$snapshot_stderr"', capture_function)
        for stage_start, stage_end in (
            (
                'snapshot_stderr="$work/f19-after.stderr"',
                'snapshot_stderr="$work/f20-after.stderr"',
            ),
            (
                'snapshot_stderr="$work/f20-after.stderr"',
                '    printf \'%s\\n\' "$capture_status" >"$f19_capture_status_file"',
            ),
        ):
            stage = capture_function.split(stage_start, 1)[1].split(stage_end, 1)[0]
            self.assertLess(
                stage.index("python3"), stage.index(absolute_snapshot_chmod)
            )
            self.assertLess(
                stage.index(absolute_snapshot_chmod),
                stage.index("if (( snapshot_status == 0 ))"),
            )
            self.assertLess(
                stage.index("if (( snapshot_status == 0 ))"),
                stage.index("if (( snapshot_status != 0 ))"),
            )
            self.assertLess(
                stage.index("if (( snapshot_status != 0 ))"),
                stage.index("capture_f29_outer_invocation"),
            )

        wrapper_start = source.index("cat >\"$work/wrappers/chmod\" <<'EOF'\n")
        wrapper_end = source.index(
            '\nEOF\nchmod 0755 "$work/wrappers/chmod"\n', wrapper_start
        ) + len('\nEOF\nchmod 0755 "$work/wrappers/chmod"\n')
        private_chmod_wrapper = source[wrapper_start:wrapper_end]
        self.assertEqual(len(private_chmod_wrapper.encode()), 1781)
        self.assertEqual(
            hashlib.sha256(private_chmod_wrapper.encode()).hexdigest(),
            "13f6849a0cca4e251331ab8c55e9ce51d06609d0ddf233614b3131e1e9ca03ec",
        )
        self.assertEqual(
            hashlib.sha256(
                exact_shell_function("capture_f29_outer_invocation").encode()
            ).hexdigest(),
            "6bda7b0bf00c07a2f5aac130a5bc58b4cb3bd6354c73649b92a8af0cc11bc3d3",
        )
        self.assertEqual(
            hashlib.sha256(capture_function.encode()).hexdigest(),
            "8633f3d90485b4624d88154ac5c0694f91fa95179378ba09983fe75c6e0dd006",
        )
        self.assertEqual(
            hashlib.sha256(
                (
                    ROOT / "packaging" / "appliance" / "install-offline-payload.sh"
                ).read_bytes()
            ).hexdigest(),
            "3116215f4f2dde376f591b06cb192b3cc725e4261885c5a0bc88e23b8867005b",
        )
        self.assertEqual(
            hashlib.sha256(
                (
                    ROOT / "packaging" / "appliance" / "verify-offline-appliance.sh"
                ).read_bytes()
            ).hexdigest(),
            "f188d76e7c19ba38472a5125c68d53e428bcf095d36878ac688e56a93fc627ad",
        )

    def test_f31_correlated_success_precedes_f21_diagnostic(self) -> None:
        source = pathlib.Path(__file__).read_text(encoding="utf-8")
        start = source.index('            if capture_statuses[0] != "0\\n":\n')
        end = source.index(
            "            self.assertFalse(\n"
            '                (namespace_path / "f21-capture-error.json").exists()',
            start,
        )
        diagnostic = source[start:end]
        self.assertEqual(diagnostic.count("_require_f29_outer_success_correlation("), 1)
        self.assertLess(
            diagnostic.index("if precleanup_f29_outer_failure is not None:"),
            diagnostic.index("nonzero_outer = ["),
        )
        self.assertLess(
            diagnostic.index("if nonzero_outer:"),
            diagnostic.index("_require_f29_outer_success_correlation("),
        )
        self.assertLess(
            diagnostic.index("if precleanup_capture_error_failure is not None:"),
            diagnostic.index("_require_f29_outer_success_correlation("),
        )
        self.assertLess(
            diagnostic.index("_require_f29_outer_success_correlation("),
            diagnostic.index("if precleanup_capture_error is not None:"),
        )

    @unittest.skipIf(
        sys.platform == "win32",
        "requires POSIX file metadata and root-owner fixture transitions",
    )
    def test_f29_required_path_predicates_are_exact_and_causal(self) -> None:
        for expected_class in sorted(F29_REQUIRED_PATH_CLASSES):
            object_name = next(
                name
                for name in F29_REQUIRED_OBJECT_MODES
                if expected_class.startswith(f"{name}_")
            )
            predicate = expected_class.removeprefix(f"{object_name}_")
            with (
                self.subTest(required_class=expected_class),
                tempfile.TemporaryDirectory() as temporary,
            ):
                root = pathlib.Path(temporary)
                paths = {
                    "F20_SNAPSHOT_STDERR": root / "f20-after.stderr",
                    "F21_CAPTURE_SOURCE": root / "f21-capture-error.py",
                    "F29_RUNNER_SOURCE": root / "f29-f21-runner.py",
                }
                for name, path in paths.items():
                    path.write_bytes(b"fixture\n")
                    path.chmod(F29_REQUIRED_OBJECT_MODES[name])
                path = paths[object_name]
                if predicate == "REGULAR_FILE":
                    path.unlink()
                    path.mkdir()
                elif predicate == "NON_SYMLINK":
                    path.unlink()
                    target = root / f"{object_name.lower()}-target"
                    target.write_bytes(b"fixture\n")
                    path.symlink_to(target)
                elif predicate == "EXPECTED_MODE":
                    path.chmod(0o640)
                elif predicate == "LINK_COUNT_ONE":
                    os.link(path, root / f"{object_name.lower()}-peer")
                elif predicate != "EXPECTED_OWNER":
                    self.fail(f"unhandled fixed predicate: {predicate}")
                f20_path = paths["F20_SNAPSHOT_STDERR"]
                if (
                    not (
                        object_name == "F20_SNAPSHOT_STDERR"
                        and predicate == "EXPECTED_OWNER"
                    )
                    and f20_path.is_file()
                    and not f20_path.is_symlink()
                ):
                    subprocess.run(
                        ["sudo", "-n", "chown", "0:0", "--", str(f20_path)],
                        check=True,
                    )
                required_paths = {
                    "F20_SNAPSHOT_STDERR": (
                        paths["F20_SNAPSHOT_STDERR"],
                        0o600,
                        (0, 0),
                    ),
                    "F21_CAPTURE_SOURCE": (
                        paths["F21_CAPTURE_SOURCE"],
                        0o644,
                        None,
                    ),
                    "F29_RUNNER_SOURCE": (
                        paths["F29_RUNNER_SOURCE"],
                        0o644,
                        None,
                    ),
                }
                self.assertEqual(
                    _classify_f29_required_path_predicate(required_paths),
                    expected_class,
                )
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            paths = {
                "F20_SNAPSHOT_STDERR": root / "f20-after.stderr",
                "F21_CAPTURE_SOURCE": root / "f21-capture-error.py",
                "F29_RUNNER_SOURCE": root / "f29-f21-runner.py",
            }
            for name, path in paths.items():
                path.write_bytes(b"fixture\n")
                path.chmod(0o640)
            ambiguous = {
                "F20_SNAPSHOT_STDERR": (paths["F20_SNAPSHOT_STDERR"], 0o600, None),
                "F21_CAPTURE_SOURCE": (paths["F21_CAPTURE_SOURCE"], 0o644, None),
                "F29_RUNNER_SOURCE": (paths["F29_RUNNER_SOURCE"], 0o644, None),
            }
            with self.assertRaises(AssertionError):
                _classify_f29_required_path_predicate(ambiguous)

    @unittest.skipIf(
        sys.platform == "win32",
        "requires root-owned private receipt metadata and a POSIX subprocess",
    )
    def test_f29_runner_retains_one_root_owned_failed_child_attempt(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            work = root / "namespace"
            work.mkdir()
            source = root / "f21-capture-error.py"
            source.write_text(
                "import sys\nsys.stderr.write('F21 capture path identity invalid\\n')\nsys.exit(7)\n",
                encoding="utf-8",
                newline="\n",
            )
            source.chmod(0o644)
            runner = root / "f29-f21-runner.py"
            runner.write_text(F29_F21_RUNNER_SCRIPT, encoding="utf-8", newline="\n")
            snapshot_stderr = work / "f20-after.stderr"
            snapshot_stderr.write_bytes(b"F20 bounded fixture failure\n")
            snapshot_stderr.chmod(0o600)
            subprocess.run(
                ["sudo", "-n", "chown", "0:0", "--", str(snapshot_stderr)],
                check=True,
            )
            output = work / "f21-capture-error.json"
            attempt = work / "f29-f21-attempt.json"
            baseline_source = source.read_bytes()
            source_sha256 = hashlib.sha256(baseline_source).hexdigest()
            command = [
                "sudo",
                "-n",
                sys.executable,
                str(runner),
                "f20-after",
                "1",
                str(snapshot_stderr),
                str(source),
                str(output),
                str(attempt),
                str(work),
                source_sha256,
            ]
            completed = subprocess.run(
                command, text=True, capture_output=True, check=False
            )
            self.assertEqual(completed.returncode, 7, completed.stderr)
            self.assertEqual(completed.stdout, "")
            receipt, _ = _validate_f29_f21_attempt(attempt, work, source_sha256)
            self.assertEqual(receipt["child_status"], 7)
            self.assertEqual(receipt["stderr_class"], "PATH_IDENTITY_INVALID")
            before = _read_strict_root_file(
                attempt,
                work,
                expected_name="f29-f21-attempt.json",
                max_bytes=F29_F21_ATTEMPT_MAX_BYTES,
            )
            duplicate = subprocess.run(
                command, text=True, capture_output=True, check=False
            )
            self.assertNotEqual(duplicate.returncode, 0)
            self.assertEqual(
                _read_strict_root_file(
                    attempt,
                    work,
                    expected_name="f29-f21-attempt.json",
                    max_bytes=F29_F21_ATTEMPT_MAX_BYTES,
                ),
                before,
            )
            try:
                for label in ("source-drift", "output-collision"):
                    with self.subTest(mutation=label):
                        if label == "source-drift":
                            source.write_bytes(b"x\n")
                            self.assertNotEqual(
                                hashlib.sha256(source.read_bytes()).hexdigest(),
                                source_sha256,
                            )
                        else:
                            source.write_bytes(baseline_source)
                            self.assertEqual(
                                hashlib.sha256(source.read_bytes()).hexdigest(),
                                source_sha256,
                            )
                        case = root / label
                        case.mkdir()
                        stderr_path = case / "f20-after.stderr"
                        stderr_path.write_bytes(b"F20 bounded fixture failure\n")
                        stderr_path.chmod(0o600)
                        subprocess.run(
                            ["sudo", "-n", "chown", "0:0", "--", str(stderr_path)],
                            check=True,
                        )
                        candidate_output = case / "f21-capture-error.json"
                        candidate_attempt = case / "f29-f21-attempt.json"
                        if label == "output-collision":
                            candidate_output.write_bytes(b"preserved\n")
                        altered = [
                            "sudo",
                            "-n",
                            sys.executable,
                            str(runner),
                            "f20-after",
                            "1",
                            str(stderr_path),
                            str(source),
                            str(candidate_output),
                            str(candidate_attempt),
                            str(case),
                            source_sha256,
                        ]
                        rejected = subprocess.run(
                            altered, text=True, capture_output=True, check=False
                        )
                        self.assertNotEqual(rejected.returncode, 0)
                        self.assertFalse(candidate_attempt.exists())
            finally:
                source.write_bytes(baseline_source)
                self.assertEqual(
                    hashlib.sha256(source.read_bytes()).hexdigest(), source_sha256
                )

    @unittest.skipIf(
        sys.platform == "win32",
        "requires root-owned private receipt metadata and a POSIX subprocess",
    )
    def test_f29_outer_runner_retains_early_f29_rejection(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            work = root / "namespace"
            work.mkdir()
            stderr_path = work / "f19-after.stderr"
            stderr_path.write_bytes(b"fixture snapshot failure\n")
            stderr_path.chmod(0o600)
            subprocess.run(
                ["sudo", "-n", "chown", "0:0", "--", str(stderr_path)],
                check=True,
            )
            f21 = root / "f21-capture-error.py"
            f21.write_text("raise SystemExit(0)\n", encoding="ascii", newline="\n")
            f29 = root / "f29-f21-runner.py"
            f29.write_text(
                "import sys\nsys.stderr.write('F29 runner path identity is invalid\\n')\nsys.exit(9)\n",
                encoding="ascii",
                newline="\n",
            )
            outer = root / "f29-outer-runner.py"
            outer.write_text(F29_OUTER_RUNNER_SCRIPT, encoding="utf-8", newline="\n")
            output = work / "f21-capture-error.json"
            attempt = work / "f29-f21-attempt.json"
            receipt_path = work / "f29-outer-f19-after.json"
            f21_sha256 = hashlib.sha256(f21.read_bytes()).hexdigest()
            f29_sha256 = hashlib.sha256(f29.read_bytes()).hexdigest()
            completed = subprocess.run(
                [
                    "sudo",
                    "-n",
                    sys.executable,
                    str(outer),
                    "f19-after",
                    "1",
                    str(stderr_path),
                    str(f21),
                    str(output),
                    str(attempt),
                    str(work),
                    f21_sha256,
                    str(f29),
                    f29_sha256,
                    str(receipt_path),
                ],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 9, completed.stderr)
            receipt, _ = _validate_f29_outer_receipt(
                receipt_path, work, "f19-after", f29_sha256
            )
            self.assertEqual(receipt["runner_status"], 9)
            self.assertEqual(receipt["stderr_class"], "PATH_IDENTITY_INVALID")
            self.assertFalse(receipt["attempt_exists"])
            self.assertFalse(receipt["output_exists"])

    @unittest.skipIf(sys.platform == "win32", "requires Bash subprocess coverage")
    def test_f29_capture_status_tracks_snapshots_not_original_phase(self) -> None:
        source = pathlib.Path(__file__).read_text(encoding="utf-8")

        def exact_shell_function(name: str) -> str:
            start = source.index(f"{name}() {{\n")
            end = source.index("\n}\n", start) + 3
            return source[start:end]

        outer_function = exact_shell_function("capture_f29_outer_invocation")
        capture_function = exact_shell_function("f19_capture_after_failure")
        function = outer_function + "\n" + capture_function
        forbidden_before_calls = (
            'python3 "$f19_snapshot" before',
            'python3 "$f20_snapshot" before',
        )
        for extracted in (outer_function, capture_function, function):
            for forbidden in forbidden_before_calls:
                self.assertNotIn(forbidden, extracted)
        self.assertEqual(function.count("capture_f29_outer_invocation() {"), 1)
        self.assertEqual(function.count("f19_capture_after_failure() {"), 1)

        def run_case(
            f19_status: int,
            f20_status: int,
            *,
            f19_stderr: str = "",
            f20_stderr: str = "",
            chmod_fails: bool = False,
            absolute_chmod_fails: bool = False,
            rm_fails: bool = False,
            outer_status: int = 0,
            create_f19_receipt: bool = True,
        ) -> tuple[str, str, list[str], list[str], list[str], str]:
            with tempfile.TemporaryDirectory() as temporary:
                root = pathlib.Path(temporary)
                work = root / "work"
                work.mkdir()
                f19 = root / "f19.py"
                f20 = root / "f20.py"
                outer = root / "outer.py"
                f19.write_text(
                    "import os, pathlib, sys\n"
                    "pathlib.Path(os.environ['ARGS']).write_text('|'.join(sys.argv[1:]))\n"
                    "pathlib.Path(os.environ['SNAPSHOTS']).open('a').write('f19\\n')\n"
                    "sys.stderr.write(os.environ['F19_STDERR'])\n"
                    "if os.environ['ABSOLUTE_CHMOD_FAILS'] == '1': pathlib.Path(os.environ['F19_STDERR_PATH']).unlink()\n"
                    "sys.exit(int(os.environ['F19']))\n",
                    encoding="ascii",
                    newline="\n",
                )
                f20.write_text(
                    "import os, pathlib, sys\n"
                    "pathlib.Path(os.environ['SNAPSHOTS']).open('a').write('f20\\n')\n"
                    "sys.stderr.write(os.environ['F20_STDERR'])\n"
                    "if os.environ['ABSOLUTE_CHMOD_FAILS'] == '1': pathlib.Path(os.environ['F20_STDERR_PATH']).unlink()\n"
                    "sys.exit(int(os.environ['F20']))\n",
                    encoding="ascii",
                    newline="\n",
                )
                outer.write_text(
                    "import os, pathlib, sys\n"
                    "pathlib.Path(os.environ['OUTER']).open('a').write(sys.argv[1]+'|'+sys.argv[2]+'\\n')\n"
                    "if sys.argv[1] == 'f19-after' and os.environ['CREATE_F19_RECEIPT'] == '1': pathlib.Path(sys.argv[5]).write_text('receipt')\n"
                    "sys.exit(int(os.environ['OUTER_STATUS']))\n",
                    encoding="ascii",
                    newline="\n",
                )
                script = root / "case.sh"
                script.write_text(
                    "\n".join(
                        (
                            "#!/usr/bin/env bash",
                            "set -u",
                            f"work={work}",
                            f"f19_snapshot={f19}",
                            f"f20_snapshot={f20}",
                            f"f29_outer_runner={outer}",
                            "f19_after=$work/f19-after.json",
                            "f20_after=$work/f20-after.json",
                            "f19_finalizer_source=$work/finalizer",
                            "phase09_outcomes=$work/outcomes",
                            "f19_command_trace=$work/trace",
                            "f21_capture_error=$work/f21.py",
                            "f21_capture_error_receipt=$work/f21.json",
                            "f29_f21_attempt_receipt=$work/f29.json",
                            "f21_capture_error_sha256=$(printf '%064d' 0)",
                            "f29_f21_runner=$work/f29.py",
                            "f29_f21_runner_sha256=$(printf '%064d' 0)",
                            "f29_outer_f19_receipt=$work/outer-f19.json",
                            "f29_outer_f20_receipt=$work/outer-f20.json",
                            "f19_capture_status_file=$work/f19.status",
                            "f20_capture_status_file=$work/f20.status",
                            'chmod() { if [[ ${CHMOD_FAILS:-0} == 1 ]]; then return 1; fi; command chmod "$@"; }',
                            'rm() { if [[ ${RM_FAILS:-0} == 1 ]]; then return 1; fi; command rm "$@"; }',
                            function,
                            "f19_capture_after_failure 1 99 main phase12-command",
                        )
                    )
                    + "\n",
                    encoding="utf-8",
                    newline="\n",
                )
                args = root / "args.txt"
                outer_log = root / "outer.log"
                snapshots = root / "snapshots.log"
                completed = subprocess.run(
                    ["bash", str(script)],
                    text=True,
                    capture_output=True,
                    check=False,
                    env={
                        **os.environ,
                        "F19": str(f19_status),
                        "F20": str(f20_status),
                        "F19_STDERR": f19_stderr,
                        "F20_STDERR": f20_stderr,
                        "CHMOD_FAILS": "1" if chmod_fails else "0",
                        "ABSOLUTE_CHMOD_FAILS": ("1" if absolute_chmod_fails else "0"),
                        "F19_STDERR_PATH": str(work / "f19-after.stderr"),
                        "F20_STDERR_PATH": str(work / "f20-after.stderr"),
                        "RM_FAILS": "1" if rm_fails else "0",
                        "OUTER_STATUS": str(outer_status),
                        "CREATE_F19_RECEIPT": "1" if create_f19_receipt else "0",
                        "ARGS": str(args),
                        "OUTER": str(outer_log),
                        "SNAPSHOTS": str(snapshots),
                    },
                )
                self.assertEqual(completed.returncode, 0, completed.stderr)
                return (
                    (work / "f19.status").read_text(encoding="ascii"),
                    (work / "f20.status").read_text(encoding="ascii"),
                    outer_log.read_text(encoding="ascii").splitlines()
                    if outer_log.exists()
                    else [],
                    snapshots.read_text(encoding="ascii").splitlines(),
                    sorted(
                        path.name
                        for path in work.glob("f29-direct-*")
                        if path.is_file()
                    ),
                    args.read_text(encoding="utf-8"),
                )

        with self.subTest(case="both-success"):
            f19_capture, f20_capture, outer_calls, snapshots, direct_files, args = (
                run_case(0, 0)
            )
            self.assertEqual((f19_capture, f20_capture), ("0\n", "0\n"))
            self.assertEqual(outer_calls, [])
            self.assertEqual(direct_files, [])
            self.assertEqual(snapshots, ["f19", "f20"])
            self.assertIn("|1|99|main|phase12-command", f"|{args}")
        with self.subTest(case="nonempty-stderr"):
            f19_capture, f20_capture, outer_calls, snapshots, direct_files, _ = (
                run_case(0, 0, f19_stderr="unexpected\n")
            )
            self.assertEqual((f19_capture, f20_capture), ("126\n", "126\n"))
            self.assertEqual(outer_calls, ["f19-after|126"])
            self.assertEqual(len(direct_files), 3)
            self.assertEqual(snapshots, ["f19", "f20"])
        with self.subTest(case="f19-nonzero"):
            f19_capture, f20_capture, outer_calls, snapshots, direct_files, _ = (
                run_case(41, 0)
            )
            self.assertEqual((f19_capture, f20_capture), ("41\n", "41\n"))
            self.assertEqual(outer_calls, ["f19-after|41"])
            self.assertEqual(len(direct_files), 3)
            self.assertEqual(snapshots, ["f19", "f20"])
        with self.subTest(case="f20-nonzero"):
            f19_capture, f20_capture, outer_calls, snapshots, direct_files, _ = (
                run_case(0, 42)
            )
            self.assertEqual((f19_capture, f20_capture), ("42\n", "42\n"))
            self.assertEqual(outer_calls, ["f20-after|42"])
            self.assertEqual(len(direct_files), 3)
            self.assertEqual(snapshots, ["f19", "f20"])
        with self.subTest(case="private-chmod-wrapper-does-not-intercept"):
            f19_capture, f20_capture, outer_calls, snapshots, direct_files, _ = (
                run_case(0, 0, chmod_fails=True)
            )
            self.assertEqual((f19_capture, f20_capture), ("0\n", "0\n"))
            self.assertEqual(outer_calls, [])
            self.assertEqual(direct_files, [])
            self.assertEqual(snapshots, ["f19", "f20"])
        with self.subTest(case="absolute-chmod-failure-after-zero"):
            f19_capture, f20_capture, outer_calls, snapshots, direct_files, _ = (
                run_case(0, 0, absolute_chmod_fails=True)
            )
            self.assertEqual((f19_capture, f20_capture), ("126\n", "126\n"))
            self.assertEqual(outer_calls, ["f19-after|126"])
            self.assertEqual(len(direct_files), 3)
            self.assertEqual(snapshots, ["f19", "f20"])
        with self.subTest(case="owned-removal-failure-after-empty-zero"):
            f19_capture, f20_capture, outer_calls, snapshots, direct_files, _ = (
                run_case(0, 0, rm_fails=True)
            )
            self.assertEqual((f19_capture, f20_capture), ("126\n", "126\n"))
            self.assertEqual(outer_calls, ["f19-after|126"])
            self.assertEqual(len(direct_files), 3)
            self.assertEqual(snapshots, ["f19", "f20"])
        with self.subTest(case="f19-and-f20-fail-first-status-wins"):
            f19_capture, f20_capture, outer_calls, snapshots, direct_files, _ = (
                run_case(41, 42)
            )
            self.assertEqual((f19_capture, f20_capture), ("41\n", "41\n"))
            self.assertEqual(outer_calls, ["f19-after|41"])
            self.assertEqual(len(direct_files), 3)
            self.assertEqual(snapshots, ["f19", "f20"])
        with self.subTest(case="outer-failure-preserves-capture-status"):
            f19_capture, f20_capture, outer_calls, snapshots, direct_files, _ = (
                run_case(41, 0, outer_status=9)
            )
            self.assertEqual((f19_capture, f20_capture), ("41\n", "41\n"))
            self.assertEqual(outer_calls, ["f19-after|41"])
            self.assertEqual(len(direct_files), 3)
            self.assertEqual(snapshots, ["f19", "f20"])
        with self.subTest(case="dual-failure-without-f19-receipt-captures-both"):
            f19_capture, f20_capture, outer_calls, snapshots, direct_files, _ = (
                run_case(41, 42, create_f19_receipt=False)
            )
            self.assertEqual((f19_capture, f20_capture), ("41\n", "41\n"))
            self.assertEqual(outer_calls, ["f19-after|41", "f20-after|42"])
            self.assertEqual(snapshots, ["f19", "f20"])
            self.assertEqual(len(direct_files), 6)

    def test_f23_instrumentation_preserves_the_single_systemctl_call(self) -> None:
        payload = (
            ROOT / "packaging" / "appliance" / "install-offline-payload.sh"
        ).read_text(encoding="utf-8")
        match = re.search(
            r"^disable_unmasked_units\(\) \{\n.*?^\}\n",
            payload,
            flags=re.MULTILINE | re.DOTALL,
        )
        self.assertIsNotNone(match)
        assert match is not None
        original = match.group(0)
        instrumented = _instrument_f23_disable_unmasked_units(original)
        replacement = (
            '            >"${f23_systemctl_stdout_by_unit[$unit]:-/dev/null}" '
            '2>"${f23_systemctl_stderr_by_unit[$unit]:-/dev/null}" || '
            "disable_status=$?"
        )
        self.assertEqual(
            original.count(
                'SYSTEMD_OFFLINE=1 systemctl --root="$target" disable "$unit"'
            ),
            instrumented.count(
                'SYSTEMD_OFFLINE=1 systemctl --root="$target" disable "$unit"'
            ),
        )
        self.assertEqual(instrumented.count(replacement), 1)
        self.assertEqual(
            instrumented.replace(
                replacement,
                "            >/dev/null 2>&1 || disable_status=$?",
                1,
            ),
            original,
        )
        source = pathlib.Path(__file__).read_text(encoding="utf-8")
        phase12 = source.split(
            "trace_begin 12-final-disable-readback final-disable-readback\n", 1
        )[1].split('[[ "$denied_units_finalized" == true ]]', 1)[0]
        self.assertEqual(phase12.count("\ndisable_unmasked_units\n"), 1)
        self.assertIn(
            '[iscsid.service]="$f23_systemctl_stdout"',
            phase12,
        )
        self.assertIn(
            '[iscsid.service]="$f23_systemctl_stderr"',
            phase12,
        )
        self.assertNotIn("systemctl is-active", phase12)

    def test_f24_complete_output_sanitizer_is_bounded_and_fail_closed(self) -> None:
        root = pathlib.Path("fixture")
        path = root / "f23-systemctl-stderr.bin"
        valid = (
            b"Synchronizing state of fixture.service.\n"
            b"Executing a bounded fixture helper.\n"
            b"Failed to disable unit: fixture rejection.\n"
        )
        with mock.patch(f"{__name__}._read_strict_root_file", return_value=valid):
            receipt = _validate_f23_systemctl_output(
                path, root, "f23-systemctl-stderr.bin"
            )
        self.assertEqual(receipt["size"], len(valid))
        self.assertEqual(receipt["sha256"], hashlib.sha256(valid).hexdigest())
        self.assertEqual(
            receipt["lines"],
            [
                "Synchronizing state of fixture.service.",
                "Executing a bounded fixture helper.",
                "Failed to disable unit: fixture rejection.",
            ],
        )
        self.assertIs(receipt["trailing_lf"], True)
        reconstructed = "\n".join(receipt["lines"]) + "\n"
        self.assertEqual(reconstructed.encode("utf-8"), valid)
        for label, content in (
            ("total-overflow", b"x" * (F23_SYSTEMCTL_OUTPUT_MAX_BYTES + 1)),
            ("invalid-utf8", b"\xff"),
            ("control", b"bad\rline\n"),
            ("secret", b"password=not-a-real-test-value\n"),
            ("long-line", b"x" * (F24_SYSTEMCTL_OUTPUT_MAX_LINE_BYTES + 1)),
            (
                "too-many-lines",
                b"x\n" * (F24_SYSTEMCTL_OUTPUT_MAX_LINES + 1),
            ),
        ):
            with (
                self.subTest(label=label),
                mock.patch(f"{__name__}._read_strict_root_file", return_value=content),
                self.assertRaises(AssertionError),
            ):
                _validate_f23_systemctl_output(path, root, "f23-systemctl-stderr.bin")

    def test_f25_entry_guard_receipt_and_correlation_are_fail_closed(self) -> None:
        def allowlisted(values: list[str]) -> list[dict[str, object]]:
            return [
                {
                    "position": index,
                    "classification": "ALLOWLISTED",
                    "value": value,
                }
                for index, value in enumerate(values)
            ]

        rejected_predicates = {
            predicate: predicate != "systemd_offline"
            for predicate in F25_ENTRY_PREDICATES
        }
        rejected = {
            "schema_version": F25_ENTRY_SCHEMA,
            "entry_reached": True,
            "argc": 3,
            "argv": allowlisted(["--root=/", "disable", "iscsid"]),
            "predicates": rejected_predicates,
            "guard_outcome": "REJECTED",
        }
        stderr = {
            "lines": [
                "Synchronizing state of iscsid.service with SysV service script.",
                "Executing: /usr/lib/systemd/systemd-sysv-install --root=/ disable iscsid",
            ]
        }
        self.assertEqual(_validate_f25_entry_guard(rejected), rejected)
        self.assertEqual(
            _classify_f25_entry(
                stderr,
                {
                    "invoked": False,
                    "entry_guard": rejected,
                },
            ),
            "WRAPPER_ENTRY_GUARD_REJECTION",
        )
        self.assertEqual(
            _classify_f25_entry(
                stderr,
                {"invoked": False, "entry_guard": {"entry_reached": False}},
            ),
            "HELPER_EXEC_NOT_REACHED",
        )

        accepted = {
            **rejected,
            "predicates": {predicate: True for predicate in F25_ENTRY_PREDICATES},
            "guard_outcome": "ACCEPTED",
        }
        accepted_stderr = {
            "lines": [
                "Executing: /usr/lib/systemd/systemd-sysv-install --root=/ disable iscsid"
            ]
        }
        self.assertEqual(
            _classify_f25_entry(
                accepted_stderr, {"invoked": True, "entry_guard": accepted}
            ),
            "WRAPPER_ENTRY_ACCEPTED",
        )
        with self.assertRaises(AssertionError):
            _classify_f25_entry(
                accepted_stderr, {"invoked": False, "entry_guard": accepted}
            )
        with self.assertRaises(AssertionError):
            _classify_f25_entry(stderr, {"invoked": True, "entry_guard": rejected})
        with self.assertRaises(AssertionError):
            _classify_f25_entry(
                {
                    "lines": [
                        "Executing: /usr/lib/systemd/systemd-sysv-install disable iscsid"
                    ]
                },
                {"invoked": False, "entry_guard": rejected},
            )

        unexpected_raw = b"unexpected-value"
        unexpected = {
            **rejected,
            "argc": 1,
            "argv": [
                {
                    "position": 0,
                    "classification": "UNEXPECTED",
                    "byte_length": len(unexpected_raw),
                    "sha256": hashlib.sha256(unexpected_raw).hexdigest(),
                }
            ],
            "predicates": {
                predicate: predicate not in {"expected_argc", "exact_vector"}
                for predicate in F25_ENTRY_PREDICATES
            },
        }
        self.assertEqual(_validate_f25_entry_guard(unexpected), unexpected)
        self.assertNotIn("unexpected-value", json.dumps(unexpected))
        mutations = {
            "missing-key": {
                key: value for key, value in rejected.items() if key != "argc"
            },
            "extra-key": {**rejected, "extra": True},
            "argc-type": {**rejected, "argc": True},
            "argc-cap": {**rejected, "argc": F25_ENTRY_MAX_ARGC + 1},
            "argv-count": {**rejected, "argv": rejected["argv"][:-1]},
            "argv-order": {
                **rejected,
                "argv": [{**rejected["argv"][0], "position": 1}, *rejected["argv"][1:]],
            },
            "raw-unexpected": {
                **unexpected,
                "argv": [{**unexpected["argv"][0], "value": "unexpected-value"}],
            },
            "predicate-extra": {
                **rejected,
                "predicates": {**rejected_predicates, "unexpected": False},
            },
            "predicate-type": {
                **rejected,
                "predicates": {**rejected_predicates, "systemd_offline": 1},
            },
            "predicate-inconsistent": {
                **rejected,
                "predicates": {**rejected_predicates, "expected_argc": False},
            },
            "outcome-inconsistent": {**rejected, "guard_outcome": "ACCEPTED"},
        }
        for label, mutation in mutations.items():
            with self.subTest(label=label), self.assertRaises(AssertionError):
                _validate_f25_entry_guard(mutation)

    def test_f25_wrapper_entry_is_first_and_preserves_the_existing_guard(self) -> None:
        source = pathlib.Path(__file__).read_text(encoding="utf-8")
        body = source.split("cat >\"$work/f20-helper-wrapper.body\" <<'EOF'\n", 1)[
            1
        ].split("\nEOF\n", 1)[0]
        self.assertTrue(body.startswith("/usr/bin/python3 - __F25_EVIDENCE_ROOT__"))
        self.assertLess(body.index("f25-helper-entry.json"), body.index("set -u\n"))
        existing_guard = (
            'if [[ "$#" -ne 3 || "$1" != --root=/ || "$2" != disable || "$3" != iscsid ]]; then exit 125; fi\n'
            '[[ "${SYSTEMD_OFFLINE-}" == 1 && "${DPKG_MAINTSCRIPT_PACKAGE-}" == pcp && \\\n'
            '    "${DPKG_MAINTSCRIPT_NAME-}" == postinst && "$PATH" == "__F20_PATH__" ]] || exit 125'
        )
        self.assertIn(existing_guard, body)
        writer = body.split("<<'PY'\n", 1)[1].split("\nPY\nset -u", 1)[0]
        self.assertEqual(writer.count('"expected_argc":len(args)==3'), 1)
        self.assertEqual(
            writer.count('"exact_vector":args==["--root=/","disable","iscsid"]'),
            1,
        )
        self.assertNotIn('"expected_argc":len(args)==2', writer)
        self.assertNotIn('"exact_vector":args==["disable","iscsid"]', writer)
        self.assertEqual(body.count('"$real_helper" "$@"'), 1)
        self.assertIn("os.O_EXCL", body)
        self.assertIn('getattr(os,"O_NOFOLLOW",0)', body)
        self.assertIn("os.fsync(stream.fileno())", body)
        self.assertIn("os.link(partial,destination,follow_symlinks=False)", body)
        self.assertNotIn("eval", body)
        phase12 = source.split(
            "trace_begin 12-final-disable-readback final-disable-readback\n", 1
        )[1].split('[[ "$denied_units_finalized" == true ]]', 1)[0]
        self.assertEqual(phase12.count("\ndisable_unmasked_units\n"), 1)

    @unittest.skipIf(
        sys.platform == "win32",
        "requires real POSIX root ownership, mode, symlink, and hard-link semantics",
    )
    def test_f25_entry_writer_is_exclusive_atomic_and_predicate_complete(self) -> None:
        source = pathlib.Path(__file__).read_text(encoding="utf-8")
        body = source.split("cat >\"$work/f20-helper-wrapper.body\" <<'EOF'\n", 1)[
            1
        ].split("\nEOF\n", 1)[0]
        writer = body.split("<<'PY'\n", 1)[1].split("\nPY\nset -u", 1)[0]
        with contextlib.ExitStack() as stack:
            temporary = stack.enter_context(tempfile.TemporaryDirectory())
            root = pathlib.Path(temporary)
            stack.callback(
                subprocess.run,
                [
                    "sudo",
                    "-n",
                    "chown",
                    "-R",
                    f"{os.getuid()}:{os.getgid()}",
                    "--",
                    str(root),
                ],
                check=False,
                capture_output=True,
            )
            writer_path = root / "f25-entry-writer.py"
            writer_path.write_text(writer, encoding="utf-8", newline="\n")
            expected_path = f"{root / 'wrappers'}:/usr/sbin:/usr/bin:/bin"

            def prepare(label: str) -> tuple[pathlib.Path, pathlib.Path, pathlib.Path]:
                case = root / label
                case.mkdir()
                wrapper = case / "f20-helper-wrapper"
                helper = case / "f20-helper-real"
                source_hash = case / "f20-helper-source.sha256"
                wrapper.write_bytes(b"wrapper\n")
                helper.write_bytes(b"helper\n")
                wrapper.chmod(0o755)
                helper.chmod(0o755)
                source_hash.write_text(
                    hashlib.sha256(helper.read_bytes()).hexdigest() + "\n",
                    encoding="ascii",
                )
                subprocess.run(
                    [
                        "sudo",
                        "-n",
                        "chown",
                        "0:0",
                        "--",
                        str(wrapper),
                        str(helper),
                        str(source_hash),
                    ],
                    check=True,
                )
                return case, wrapper, helper

            def invoke(
                case: pathlib.Path,
                wrapper: pathlib.Path,
                helper: pathlib.Path,
                args: list[str],
                *,
                environment: dict[str, str] | None = None,
                script: pathlib.Path | None = None,
            ) -> subprocess.CompletedProcess[str]:
                env = os.environ.copy()
                env.update(
                    {
                        "SYSTEMD_OFFLINE": "1",
                        "DPKG_MAINTSCRIPT_PACKAGE": "pcp",
                        "DPKG_MAINTSCRIPT_NAME": "postinst",
                        "PATH": expected_path,
                    }
                )
                if environment:
                    env.update(environment)
                return subprocess.run(
                    [
                        "/usr/bin/sudo",
                        "-n",
                        "/usr/bin/env",
                        f"SYSTEMD_OFFLINE={env['SYSTEMD_OFFLINE']}",
                        f"DPKG_MAINTSCRIPT_PACKAGE={env['DPKG_MAINTSCRIPT_PACKAGE']}",
                        f"DPKG_MAINTSCRIPT_NAME={env['DPKG_MAINTSCRIPT_NAME']}",
                        f"PATH={env['PATH']}",
                        "/usr/bin/python3",
                        str(script or writer_path),
                        str(case),
                        str(wrapper),
                        str(helper),
                        str(case / "f20-helper-source.sha256"),
                        expected_path,
                        *args,
                    ],
                    text=True,
                    capture_output=True,
                    check=False,
                    env=env,
                )

            def read_entry(case: pathlib.Path) -> bytes:
                return _read_strict_root_file(
                    case / "f25-helper-entry.json",
                    case,
                    expected_name="f25-helper-entry.json",
                    max_bytes=F25_ENTRY_MAX_BYTES,
                )

            case, wrapper, helper = prepare("valid-rooted")
            completed = invoke(case, wrapper, helper, list(F25_ENTRY_ALLOWLIST))
            self.assertEqual(completed.returncode, 0, completed.stderr)
            receipt = json.loads(read_entry(case).decode("ascii"))
            validated = _validate_f25_entry_guard(receipt)
            self.assertEqual(validated["argc"], 3)
            self.assertTrue(all(validated["predicates"].values()))
            self.assertEqual(validated["guard_outcome"], "ACCEPTED")
            original = read_entry(case)
            repeated = invoke(case, wrapper, helper, list(F25_ENTRY_ALLOWLIST))
            self.assertNotEqual(repeated.returncode, 0)
            self.assertEqual(read_entry(case), original)

            predicate_cases = {
                "old-two-argument": (["disable", "iscsid"], {}, "expected_argc"),
                "missing-root": (["disable", "iscsid"], {}, "exact_vector"),
                "other-root": (
                    ["--root=/tmp", "disable", "iscsid"],
                    {},
                    "exact_vector",
                ),
                "root-without-equals": (
                    ["--root", "disable", "iscsid"],
                    {},
                    "exact_vector",
                ),
                "reordered": (["--root=/", "iscsid", "disable"], {}, "exact_vector"),
                "duplicate": (
                    ["--root=/", "--root=/", "disable", "iscsid"],
                    {},
                    "expected_argc",
                ),
                "extra": (
                    ["--root=/", "disable", "iscsid", "extra"],
                    {},
                    "expected_argc",
                ),
                "offline": (
                    list(F25_ENTRY_ALLOWLIST),
                    {"SYSTEMD_OFFLINE": "0"},
                    "systemd_offline",
                ),
                "package": (
                    list(F25_ENTRY_ALLOWLIST),
                    {"DPKG_MAINTSCRIPT_PACKAGE": "other"},
                    "dpkg_maintscripts_package",
                ),
                "name": (
                    list(F25_ENTRY_ALLOWLIST),
                    {"DPKG_MAINTSCRIPT_NAME": "prerm"},
                    "dpkg_maintscripts_name",
                ),
                "path": (
                    list(F25_ENTRY_ALLOWLIST),
                    {"PATH": "/usr/sbin:/usr/bin:/bin"},
                    "exact_private_path",
                ),
            }
            for label, (args, environment, predicate) in predicate_cases.items():
                with self.subTest(predicate=label):
                    case, wrapper, helper = prepare(f"predicate-{label}")
                    result = invoke(
                        case, wrapper, helper, args, environment=environment
                    )
                    self.assertEqual(result.returncode, 0, result.stderr)
                    candidate = json.loads(read_entry(case).decode("ascii"))
                    self.assertIs(candidate["predicates"][predicate], False)
                    self.assertEqual(candidate["guard_outcome"], "REJECTED")

            identity_predicates = {
                "wrapper": "wrapper_identity_mode",
                "helper": "real_helper_identity_mode",
            }
            self.assertEqual(
                identity_predicates,
                {
                    "wrapper": "wrapper_identity_mode",
                    "helper": "real_helper_identity_mode",
                },
            )
            for label, target in (("wrapper", "wrapper"), ("helper", "helper")):
                with self.subTest(identity=label):
                    case, wrapper, helper = prepare(f"identity-{label}")
                    subprocess.run(
                        [
                            "sudo",
                            "-n",
                            "chmod",
                            "0700",
                            "--",
                            str(wrapper if target == "wrapper" else helper),
                        ],
                        check=True,
                    )
                    result = invoke(case, wrapper, helper, list(F25_ENTRY_ALLOWLIST))
                    self.assertEqual(result.returncode, 0, result.stderr)
                    candidate = json.loads(read_entry(case).decode("ascii"))
                    self.assertIs(
                        candidate["predicates"][identity_predicates[label]], False
                    )

            case, wrapper, helper = prepare("unexpected")
            unexpected = "outside-allowlist"
            result = invoke(case, wrapper, helper, [unexpected])
            self.assertEqual(result.returncode, 0, result.stderr)
            raw_receipt = read_entry(case)
            self.assertNotIn(unexpected.encode(), raw_receipt)
            candidate = json.loads(raw_receipt)
            self.assertEqual(candidate["argv"][0]["classification"], "UNEXPECTED")
            self.assertEqual(candidate["argv"][0]["byte_length"], len(unexpected))
            self.assertEqual(
                candidate["argv"][0]["sha256"],
                hashlib.sha256(unexpected.encode()).hexdigest(),
            )

            for label in ("regular", "symlink", "hardlink", "unsafe-mode", "partial"):
                with self.subTest(preexisting=label):
                    case, wrapper, helper = prepare(f"preexisting-{label}")
                    destination = case / "f25-helper-entry.json"
                    partial = case / "f25-helper-entry.json.partial"
                    sentinel = case / "sentinel"
                    sentinel.write_bytes(b"preserved\n")
                    if label == "regular":
                        destination.write_bytes(b"preserved\n")
                    elif label == "symlink":
                        destination.symlink_to(sentinel)
                    elif label == "hardlink":
                        os.link(sentinel, destination)
                        self.assertEqual(sentinel.stat().st_nlink, 2)
                    elif label == "unsafe-mode":
                        destination.write_bytes(b"preserved\n")
                        destination.chmod(0o644)
                    else:
                        partial.write_bytes(b"partial\n")
                    before = sentinel.read_bytes()
                    result = invoke(case, wrapper, helper, list(F25_ENTRY_ALLOWLIST))
                    self.assertNotEqual(result.returncode, 0)
                    self.assertEqual(sentinel.read_bytes(), before)
                    if label == "partial":
                        self.assertFalse(destination.exists())
                        self.assertEqual(partial.read_bytes(), b"partial\n")

            case, wrapper, helper = prepare("partial-write")
            injected_path = root / "f25-entry-writer-partial.py"
            injected_path.write_text(
                writer.replace(
                    "stream.write(encoded); stream.flush(); os.fsync(stream.fileno())",
                    'stream.write(encoded[:1]); stream.flush(); raise OSError("injected")',
                    1,
                ),
                encoding="utf-8",
                newline="\n",
            )
            result = invoke(
                case,
                wrapper,
                helper,
                list(F25_ENTRY_ALLOWLIST),
                script=injected_path,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse((case / "f25-helper-entry.json").exists())
            self.assertEqual(
                _read_strict_root_file(
                    case / "f25-helper-entry.json.partial",
                    case,
                    expected_name="f25-helper-entry.json.partial",
                    max_bytes=F25_ENTRY_MAX_BYTES,
                ),
                b"{",
            )

    @unittest.skipIf(
        sys.platform == "win32",
        "requires real POSIX root ownership, mode, link, and sudo reader semantics",
    )
    def test_f23_root_receipt_reader_preserves_strict_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            receipt = root / "f21-capture-error.json"
            raw = b'{"schema_version":1}\n'
            receipt.write_bytes(raw)
            receipt.chmod(0o600)
            subprocess.run(
                ["sudo", "-n", "chown", "0:0", "--", str(receipt)], check=True
            )
            self.assertEqual(
                _read_strict_root_file(
                    receipt,
                    root,
                    expected_name="f21-capture-error.json",
                    max_bytes=1024,
                ),
                raw,
            )
            for label, completed in (
                ("reader-nonzero", subprocess.CompletedProcess([], 73, b"", b"")),
                (
                    "reader-oversized",
                    subprocess.CompletedProcess([], 0, b"x" * 1025, b""),
                ),
                (
                    "reader-stderr",
                    subprocess.CompletedProcess([], 0, raw, b"unexpected\n"),
                ),
            ):
                with (
                    self.subTest(label=label),
                    mock.patch("subprocess.run", return_value=completed),
                    self.assertRaises(AssertionError),
                ):
                    _read_strict_root_file(
                        receipt,
                        root,
                        expected_name="f21-capture-error.json",
                        max_bytes=1024,
                    )

            hardlink = root / "receipt-hardlink"
            subprocess.run(
                ["sudo", "-n", "ln", "--", str(receipt), str(hardlink)], check=True
            )
            with self.assertRaises(AssertionError):
                _read_strict_root_file(
                    receipt,
                    root,
                    expected_name="f21-capture-error.json",
                    max_bytes=1024,
                )
            subprocess.run(["sudo", "-n", "rm", "--", str(hardlink)], check=True)

            for label, command in (
                ("wrong-mode", ["chmod", "0640", "--", str(receipt)]),
                (
                    "wrong-owner",
                    ["chown", f"{os.getuid()}:{os.getgid()}", "--", str(receipt)],
                ),
            ):
                with self.subTest(label=label):
                    subprocess.run(["sudo", "-n", *command], check=True)
                    with self.assertRaises(AssertionError):
                        _read_strict_root_file(
                            receipt,
                            root,
                            expected_name="f21-capture-error.json",
                            max_bytes=1024,
                        )
                    subprocess.run(
                        ["sudo", "-n", "chown", "0:0", "--", str(receipt)],
                        check=True,
                    )
                    subprocess.run(
                        ["sudo", "-n", "chmod", "0600", "--", str(receipt)],
                        check=True,
                    )

            moved = root / "receipt-original"
            subprocess.run(
                ["sudo", "-n", "mv", "--", str(receipt), str(moved)], check=True
            )
            subprocess.run(
                ["sudo", "-n", "ln", "-s", "--", str(moved), str(receipt)], check=True
            )
            with self.assertRaises(AssertionError):
                _read_strict_root_file(
                    receipt,
                    root,
                    expected_name="f21-capture-error.json",
                    max_bytes=1024,
                )
            subprocess.run(["sudo", "-n", "rm", "--", str(receipt)], check=True)
            subprocess.run(["sudo", "-n", "mkdir", "--", str(receipt)], check=True)
            with self.assertRaises(AssertionError):
                _read_strict_root_file(
                    receipt,
                    root,
                    expected_name="f21-capture-error.json",
                    max_bytes=1024,
                )
            subprocess.run(["sudo", "-n", "rmdir", "--", str(receipt)], check=True)
            subprocess.run(
                ["sudo", "-n", "mv", "--", str(moved), str(receipt)], check=True
            )
            wrong_path = root / "wrong-name.json"
            wrong_path.write_bytes(raw)
            with self.assertRaises(AssertionError):
                _read_strict_root_file(
                    wrong_path,
                    root,
                    expected_name="f21-capture-error.json",
                    max_bytes=1024,
                )
            nested = root / "nested"
            nested.mkdir()
            nested_receipt = nested / "f21-capture-error.json"
            nested_receipt.write_bytes(raw)
            nested_receipt.chmod(0o600)
            subprocess.run(
                ["sudo", "-n", "chown", "0:0", "--", str(nested_receipt)],
                check=True,
            )
            with self.assertRaises(AssertionError):
                _read_strict_root_file(
                    nested_receipt,
                    root,
                    expected_name="f21-capture-error.json",
                    max_bytes=1024,
                )
            subprocess.run(
                [
                    "sudo",
                    "-n",
                    "chown",
                    "-R",
                    f"{os.getuid()}:{os.getgid()}",
                    "--",
                    str(root),
                ],
                check=True,
            )

    @unittest.skipUnless(
        sys.platform == "win32",
        "Windows-specific 0666 metadata rejection boundary",
    )
    def test_f21_windows_0666_fixture_is_rejected_without_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            script = root / "f21-capture-error.py"
            script.write_text(F21_CAPTURE_ERROR_SCRIPT, encoding="utf-8", newline="\n")
            stderr_path = root / "f20-after.stderr"
            stderr_path.write_bytes(b"F20 helper evidence is incomplete\n")
            stderr_path.chmod(0o600)
            self.assertEqual(stat.S_IMODE(stderr_path.stat().st_mode), 0o666)
            output = root / "f21-capture-error.json"
            completed = subprocess.run(
                [
                    sys.executable,
                    str(script),
                    "f20-after",
                    "1",
                    str(stderr_path),
                    str(output),
                    str(root),
                ],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertNotEqual(completed.returncode, 0)
            self.assertEqual(
                completed.stderr,
                "F21 capture stderr metadata invalid\n",
            )
            self.assertFalse(output.exists())
            self.assertFalse(output.with_suffix(output.suffix + ".partial").exists())

    @unittest.skipIf(
        sys.platform == "win32",
        "requires real POSIX root ownership, mode 0600, and link-count semantics",
    )
    def test_f21_posix_capture_error_receipt_is_bounded_and_fail_closed(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            script = root / "f21-capture-error.py"
            script.write_text(F21_CAPTURE_ERROR_SCRIPT, encoding="utf-8", newline="\n")
            stderr_path = root / "f20-after.stderr"
            output = root / "f21-capture-error.json"
            valid_error = b"F20 helper evidence is incomplete\n"
            stderr_path.write_bytes(valid_error)
            stderr_path.chmod(0o600)

            if sys.platform == "win32":
                command_prefix = [sys.executable]
            else:
                command_prefix = ["sudo", "-n", "/usr/bin/python3"]
                subprocess.run(
                    ["sudo", "-n", "chown", "0:0", "--", str(stderr_path)],
                    check=True,
                )
            completed = subprocess.run(
                [
                    *command_prefix,
                    str(script),
                    "f20-after",
                    "1",
                    str(stderr_path),
                    str(output),
                    str(root),
                ],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            receipt, receipt_hash = _validate_f21_capture_error(output, root)
            self.assertEqual(receipt["stage"], "f20-after")
            self.assertEqual(receipt["status"], 1)
            self.assertEqual(receipt["stderr_size"], len(valid_error))
            self.assertEqual(
                receipt["stderr_sha256"], hashlib.sha256(valid_error).hexdigest()
            )
            self.assertRegex(receipt_hash, r"^[0-9a-f]{64}$")

            duplicate = subprocess.run(
                [
                    *command_prefix,
                    str(script),
                    "f20-after",
                    "1",
                    str(stderr_path),
                    str(output),
                    str(root),
                ],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertNotEqual(duplicate.returncode, 0)

            if sys.platform != "win32":
                ownership = subprocess.run(
                    [
                        "sudo",
                        "-n",
                        "chown",
                        "-R",
                        f"{os.getuid()}:{os.getgid()}",
                        "--",
                        str(root),
                    ],
                    text=True,
                    capture_output=True,
                    check=False,
                )
                self.assertEqual(ownership.returncode, 0, ownership.stderr)

            output.unlink()
            negative_cases = {
                "unknown-stage": ("unknown", "1", valid_error),
                "status-zero": ("f20-after", "0", valid_error),
                "status-overflow": ("f20-after", "256", valid_error),
                "oversized": (
                    "f20-after",
                    "1",
                    b"F20 " + b"x" * F21_CAPTURE_ERROR_MAX_STDERR_BYTES,
                ),
                "secret": (
                    "f20-after",
                    "1",
                    b"F20 password=not-a-real-test-value\n",
                ),
                "malformed": ("f20-after", "1", b"arbitrary failure\n"),
            }
            for label, (stage, status, content) in negative_cases.items():
                with self.subTest(label=label):
                    stderr_path.write_bytes(content)
                    stderr_path.chmod(0o600)
                    if sys.platform != "win32":
                        subprocess.run(
                            [
                                "sudo",
                                "-n",
                                "chown",
                                "0:0",
                                "--",
                                str(stderr_path),
                            ],
                            check=True,
                        )
                    rejected = subprocess.run(
                        [
                            *command_prefix,
                            str(script),
                            stage,
                            status,
                            str(stderr_path),
                            str(output),
                            str(root),
                        ],
                        text=True,
                        capture_output=True,
                        check=False,
                    )
                    self.assertNotEqual(rejected.returncode, 0)
                    self.assertFalse(output.exists())
                    if sys.platform != "win32":
                        subprocess.run(
                            [
                                "sudo",
                                "-n",
                                "chown",
                                f"{os.getuid()}:{os.getgid()}",
                                "--",
                                str(stderr_path),
                            ],
                            check=True,
                        )

            metadata_cases = ("wrong-mode", "wrong-owner", "directory", "symlink")
            for label in metadata_cases:
                with self.subTest(label=label):
                    if stderr_path.exists() or stderr_path.is_symlink():
                        if stderr_path.is_dir() and not stderr_path.is_symlink():
                            stderr_path.rmdir()
                        else:
                            stderr_path.unlink()
                    if label == "directory":
                        stderr_path.mkdir()
                    elif label == "symlink":
                        target = root / "symlink-target"
                        target.write_bytes(valid_error)
                        stderr_path.symlink_to(target)
                    else:
                        stderr_path.write_bytes(valid_error)
                        stderr_path.chmod(0o644 if label == "wrong-mode" else 0o600)
                        if sys.platform != "win32" and label == "wrong-mode":
                            subprocess.run(
                                [
                                    "sudo",
                                    "-n",
                                    "chown",
                                    "0:0",
                                    "--",
                                    str(stderr_path),
                                ],
                                check=True,
                            )
                    rejected = subprocess.run(
                        [
                            *command_prefix,
                            str(script),
                            "f20-after",
                            "1",
                            str(stderr_path),
                            str(output),
                            str(root),
                        ],
                        text=True,
                        capture_output=True,
                        check=False,
                    )
                    self.assertNotEqual(rejected.returncode, 0)
                    self.assertFalse(output.exists())
                    if sys.platform != "win32":
                        subprocess.run(
                            [
                                "sudo",
                                "-n",
                                "chown",
                                "-h",
                                f"{os.getuid()}:{os.getgid()}",
                                "--",
                                str(stderr_path),
                            ],
                            check=True,
                        )

            if stderr_path.is_symlink():
                stderr_path.unlink()
            outside = root.parent / f"{root.name}-outside.stderr"
            outside.write_bytes(valid_error)
            outside.chmod(0o600)
            if sys.platform != "win32":
                subprocess.run(
                    ["sudo", "-n", "chown", "0:0", "--", str(outside)],
                    check=True,
                )
            traversal = subprocess.run(
                [
                    *command_prefix,
                    str(script),
                    "f20-after",
                    "1",
                    str(outside),
                    str(output),
                    str(root),
                ],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertNotEqual(traversal.returncode, 0)
            self.assertFalse(output.exists())
            if sys.platform != "win32":
                subprocess.run(
                    [
                        "sudo",
                        "-n",
                        "chown",
                        f"{os.getuid()}:{os.getgid()}",
                        "--",
                        str(outside),
                    ],
                    check=True,
                )
            outside.unlink()

            with self.assertRaises(AssertionError):
                _validate_f21_capture_error(output, root)

    def test_actual_install_argv_executes_exact_production_fragment(self) -> None:
        payload = (
            ROOT / "packaging" / "appliance" / "install-offline-payload.sh"
        ).read_text(encoding="utf-8")
        fragment = self._actual_install_fragment(payload)
        if sys.platform == "win32":
            candidates = (
                pathlib.Path(r"C:\Program Files\Git\bin\bash.exe"),
                pathlib.Path(r"C:\msys64\usr\bin\bash.exe"),
            )
            bash = next((str(path) for path in candidates if path.is_file()), None)
        else:
            bash = shutil.which("bash")
        self.assertIsNotNone(
            bash, "Bash is required for actual-install argv regression"
        )
        assert bash is not None
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            fragment_path = root / "production-fragment.sh"
            log_path = root / "argv.bin"
            fragment_path.write_text(fragment + "\n", encoding="utf-8")
            script = "\n".join(
                (
                    "set -euo pipefail",
                    'target="/target"',
                    "apt_options=(",
                    '  -o "Dir::Etc::sourcelist=/etc/apt/sources.list.d/hoardarr-offline.list"',
                    '  -o "Dir::Etc::sourceparts=-"',
                    '  -o "Acquire::Languages=none"',
                    '  -o "Acquire::Retries=0"',
                    '  -o "Acquire::http::Proxy=false"',
                    '  -o "Acquire::https::Proxy=false"',
                    ")",
                    'exact_roots=("alpha=1" "beta=2")',
                    'log="$1"',
                    'chroot() { printf \'%s\\0\' "$@" >"$log"; }',
                    f'source "{fragment_path.as_posix()}"',
                )
            )
            result = subprocess.run(
                [bash, "-c", script, "bash", log_path.as_posix()],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            argv = log_path.read_bytes().split(b"\0")[:-1]
            self.assertEqual(
                [value.decode() for value in argv],
                [
                    "/target",
                    "apt-get",
                    "-o",
                    "Dir::Etc::sourcelist=/etc/apt/sources.list.d/hoardarr-offline.list",
                    "-o",
                    "Dir::Etc::sourceparts=-",
                    "-o",
                    "Acquire::Languages=none",
                    "-o",
                    "Acquire::Retries=0",
                    "-o",
                    "Acquire::http::Proxy=false",
                    "-o",
                    "Acquire::https::Proxy=false",
                    "--yes",
                    "--no-install-recommends",
                    "install",
                    "alpha=1",
                    "beta=2",
                ],
            )

    def test_actual_install_contract_rejects_safeguard_regressions(self) -> None:
        payload = (
            ROOT / "packaging" / "appliance" / "install-offline-payload.sh"
        ).read_text(encoding="utf-8")
        self._assert_actual_install_contract(payload)
        mutations = {
            "network source": payload.replace(
                "file:/opt/hoardarr/offline-repository",
                "https://example.invalid/repository",
            ),
            "missing signed-by": payload.replace("signed-by=", "keyring="),
            "signature weakening": payload.replace(
                "noble main", "noble main trusted=yes", 1
            ),
            "missing retry guard": payload.replace(
                "Acquire::Retries=0", "Acquire::Retries=1"
            ),
            "missing HTTP proxy guard": payload.replace(
                "Acquire::http::Proxy=false", "Acquire::http::Proxy=direct"
            ),
            "missing HTTPS proxy guard": payload.replace(
                "Acquire::https::Proxy=false", "Acquire::https::Proxy=direct"
            ),
            "root loss": payload.replace(
                '"${exact_roots[@]}"', '"${exact_roots[0]}"', 1
            ),
            "service guard loss": payload.replace("policy-rc.d", "policy-start.d"),
            "direct systemctl offline guard loss": payload.replace(
                "export SYSTEMD_OFFLINE=1", "export SYSTEMD_OFFLINE=0"
            ),
            "md storage guard loss": payload.replace("AUTO -all", "AUTO +all"),
            "LVM storage guard loss": payload.replace(
                'global_filter = [ "r|.*|" ]', 'global_filter = [ "a|.*|" ]'
            ),
            "multipath storage guard loss": payload.replace(
                'devnode ".*"', 'devnode "^$"'
            ),
            "no-download reintroduced": payload.replace(
                "--yes --no-install-recommends install",
                "--yes --no-download --no-install-recommends install",
            ),
        }
        for name, mutation in mutations.items():
            with self.subTest(name=name), self.assertRaises(AssertionError):
                self._assert_actual_install_contract(mutation)

    @unittest.skipUnless(sys.platform.startswith("linux"), "requires Linux APT")
    def test_signed_local_file_repository_actual_install(self) -> None:
        payload = (
            ROOT / "packaging" / "appliance" / "install-offline-payload.sh"
        ).read_text(encoding="utf-8")
        fragment = self._actual_install_fragment(payload)
        required = ("apt-get", "dpkg-deb", "dpkg-query", "gpg", "gzip", "sudo")
        missing = [command for command in required if shutil.which(command) is None]
        self.assertEqual(missing, [], f"missing Linux integration tools: {missing}")
        sudo = subprocess.run(
            ["sudo", "-n", "true"], text=True, capture_output=True, check=False
        )
        self.assertEqual(sudo.returncode, 0, sudo.stderr)
        with tempfile.TemporaryDirectory() as temporary:
            fragment_path = pathlib.Path(temporary) / "production-fragment.sh"
            fragment_path.write_text(fragment + "\n", encoding="utf-8")
            result = subprocess.run(
                [
                    "sudo",
                    "-n",
                    "bash",
                    str(
                        ROOT / "tests" / "appliance" / "test-local-file-apt-install.sh"
                    ),
                    str(fragment_path),
                ],
                text=True,
                capture_output=True,
                check=False,
                timeout=180,
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertRegex(result.stdout, r"(?m)^old_no_download_status=[1-9][0-9]*$")
        self.assertIn("archive_cache_was_empty=true", result.stdout)
        self.assertIn("actual_install_file_acquisition=true", result.stdout)
        self.assertIn("network_sources=0", result.stdout)
        self.assertIn("package_readback=installed\t1.0\tall", result.stdout)

    def test_appliance_builder_embeds_verified_repository_and_emits_complete_tree_manifest(
        self,
    ) -> None:
        builder = (ROOT / "scripts" / "build-appliance.sh").read_text(encoding="utf-8")
        self.assertIn("build-offline-apt-repository.py verify", builder)
        self.assertIn("/hoardarr/offline-repository", builder)
        self.assertIn("hoardarr/install-offline-payload.sh", builder)
        self.assertIn("offline repository contains a symbolic link", builder)
        self.assertIn('>"${output}.tree-sha256"', builder)

    def test_two_clean_no_nic_install_passes_are_manual_release_gates(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "appliance.yml").read_text(
            encoding="utf-8"
        )
        harness = (ROOT / "tests" / "appliance" / "run-offline-iso-pass.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn("offline_validation_mode:", workflow)
        self.assertIn("default: two-pass", workflow)
        self.assertIn("- diagnostic-pass-1", workflow)
        self.assertIn(
            "inputs.offline_validation_mode == 'diagnostic-pass-1'"
            ' && \'["pass-1"]\' || \'["pass-1","pass-2"]\'',
            workflow,
        )
        self.assertIn("HOARDARR_OFFLINE_DIAGNOSTIC_MODE", workflow)
        self.assertIn(
            "'hoardarr-offline-diagnostic-pass-1'"
            " || format('hoardarr-offline-{0}', matrix.pass)",
            workflow,
        )
        retention = workflow.split(
            "- name: Retain offline install inputs for no-network validation", 1
        )[1].split("\n\n  offline-install:", 1)[0]
        self.assertNotIn("if:", retention)
        self.assertIn("name: hoardarr-offline-install-inputs", retention)
        self.assertIn("compression-level: 0", retention)
        self.assertIn("retention-days: 3", retention)
        self.assertEqual(
            [
                line.strip()
                for line in retention.splitlines()
                if line.strip().startswith("dist/")
            ],
            ["dist/hoardarr-release.tar.gz", "dist/offline-repository"],
        )
        offline_install = workflow.split("\n  offline-install:\n", 1)[1]
        self.assertTrue(
            offline_install.startswith(
                "    if: github.event_name == 'workflow_dispatch'\n"
            )
        )
        self.assertEqual(workflow.count("\n  offline-install:\n"), 1)
        self.assertEqual(
            offline_install.count(
                "inputs.offline_validation_mode == 'diagnostic-pass-1'"
            ),
            3,
        )
        self.assertIn("$RUNNER_TEMP/ci-signing-key", workflow)
        self.assertIn("$RUNNER_TEMP/ubuntu-vulnerability-status.json", workflow)
        self.assertIn("-nic none", harness)
        self.assertIn("readonly=on", harness)
        self.assertIn("protected-before.sha256", harness)
        self.assertIn("protected-after.sha256", harness)
        self.assertIn("HOARDARR_OFFLINE_READY", harness)
        self.assertIn("installer-monitor.log", harness)
        self.assertIn("installer-process.tsv", harness)
        self.assertIn("process-identities.txt", harness)
        self.assertIn("qemu-installer-stderr.log", harness)
        self.assertIn("qemu-img-info.json", harness)
        self.assertIn("installer_timeout", harness)
        self.assertIn("timeout --signal=TERM --kill-after=30s 2700s", harness)
        self.assertIn('"acceptance_eligible": False', harness)
        self.assertIn('"bounded_runner_exit_status"', harness)
        self.assertIn("protected-diff.txt", harness)
        self.assertIn("frames/SHA256SUMS", harness)
        self.assertIn("evidence-finalization.txt", harness)
        self.assertIn("diagnostic evidence finalization was incomplete", harness)
        diagnostic_body = harness.split("run_diagnostic_installer() {", 1)[1].split(
            "\n}\n\ninstall_start=", 1
        )[0]
        self.assertLess(
            diagnostic_body.index('wait "$runner_pid"'),
            diagnostic_body.index("finalize_diagnostic_evidence"),
        )
        self.assertIn("timeout --signal=TERM --kill-after=30s 45m", harness)
        success_path = harness.split('if [[ "$diagnostic_mode" == true ]]; then', 2)[2]
        self.assertLess(
            success_path.index("write_diagnostic_metadata installer_reboot_checkpoint"),
            success_path.index("finalize_diagnostic_evidence"),
        )
        self.assertNotIn('cat >"$output/run.json"', success_path.split("else", 1)[0])

    def test_ci_payload_wrapper_preserves_exact_argv_and_status(self) -> None:
        user_data = (ROOT / "tests" / "appliance" / "offline-user-data").read_text(
            encoding="utf-8"
        )
        exact_argv = (
            "/cdrom/hoardarr/install-offline-payload.sh "
            "/target /cdrom/hoardarr/offline-repository"
        )
        self.assertEqual(user_data.count(exact_argv), 1)
        self.assertIn('pipeline_status=("${PIPESTATUS[@]}")', user_data)
        self.assertIn('payload_status="${pipeline_status[0]}"', user_data)
        self.assertIn(
            '[[ "${pipeline_status[1]}" -eq 0 ]] || capture_ok=false', user_data
        )
        self.assertIn('exit "$payload_status"', user_data)
        payload_tail = user_data.split('pipeline_status=("${PIPESTATUS[@]}")', 1)[1]
        self.assertNotIn("set -e", payload_tail.split('exit "$payload_status"', 1)[0])
        self.assertIn("/target/var/log/hoardarr-offline-payload.log", user_data)
        self.assertIn("[[ -c /dev/ttyS0 && -w /dev/ttyS0 ]]", user_data)
        self.assertIn("stty -F /dev/ttyS0 -opost || exit 126", user_data)
        self.assertIn(
            "stty -F /dev/ttyS0 -a | grep -qw -- -opost || exit 127", user_data
        )
        self.assertNotIn("|| true", payload_tail.split('exit "$payload_status"', 1)[0])
        for required_operation in (
            "emit_both HOARDARR_OFFLINE_PAYLOAD_END || capture_ok=false",
            'emit_both "HOARDARR_OFFLINE_PAYLOAD_EXIT=$payload_status" || capture_ok=false',
            'sync "$target_log" || capture_ok=false',
            'target_size="$(wc -c <"$target_log")" || capture_ok=false',
            'target_sha256="$(sha256sum "$target_log" | cut -d" " -f1)" || capture_ok=false',
        ):
            self.assertIn(required_operation, user_data)
        complete_guard = user_data.rsplit(
            'printf "%s\\n" HOARDARR_OFFLINE_PAYLOAD_CAPTURE_COMPLETE', 1
        )[0].rsplit('if [[ "$capture_ok" == true ]]', 1)
        self.assertEqual(len(complete_guard), 2)
        for marker in (
            "HOARDARR_OFFLINE_PAYLOAD_BEGIN",
            "HOARDARR_OFFLINE_PAYLOAD_END",
            "HOARDARR_OFFLINE_PAYLOAD_EXIT=",
            "HOARDARR_OFFLINE_PAYLOAD_TARGET_LOG_SIZE=",
            "HOARDARR_OFFLINE_PAYLOAD_TARGET_LOG_SHA256=",
            "HOARDARR_OFFLINE_PAYLOAD_CAPTURE_COMPLETE",
        ):
            self.assertIn(marker, user_data)

    def test_payload_capture_parser_is_fail_closed(self) -> None:
        parser = ROOT / "tests" / "appliance" / "parse-offline-payload-capture.py"

        def run_capture(
            serial: bytes,
        ) -> tuple[subprocess.CompletedProcess[str], pathlib.Path]:
            temporary = tempfile.TemporaryDirectory()
            self.addCleanup(temporary.cleanup)
            root = pathlib.Path(temporary.name)
            serial_path = root / "serial.log"
            serial_path.write_bytes(serial)
            result = subprocess.run(
                [
                    sys.executable,
                    str(parser),
                    str(serial_path),
                    str(root / "console.log"),
                    str(root / "target.log"),
                    str(root / "capture.json"),
                ],
                capture_output=True,
                check=False,
                text=True,
            )
            return result, root

        def complete(status: int, payload: bytes = b"decisive failure\n") -> bytes:
            target = (
                b"HOARDARR_OFFLINE_PAYLOAD_BEGIN\n"
                + payload
                + b"HOARDARR_OFFLINE_PAYLOAD_END\n"
                + f"HOARDARR_OFFLINE_PAYLOAD_EXIT={status}\n".encode()
            )
            return (
                b"prefix\n"
                + target
                + f"HOARDARR_OFFLINE_PAYLOAD_TARGET_LOG_SIZE={len(target)}\n".encode()
                + b"HOARDARR_OFFLINE_PAYLOAD_TARGET_LOG_SHA256="
                + hashlib.sha256(target).hexdigest().encode()
                + b"\nHOARDARR_OFFLINE_PAYLOAD_CAPTURE_COMPLETE\n"
            )

        nonzero, nonzero_root = run_capture(complete(17))
        self.assertEqual(nonzero.returncode, 10)
        self.assertEqual(
            json.loads((nonzero_root / "capture.json").read_text())["payload_status"],
            17,
        )
        self.assertIn(b"decisive failure", (nonzero_root / "target.log").read_bytes())

        zero, _ = run_capture(complete(0))
        self.assertEqual(zero.returncode, 0)
        crlf_stream = complete(17).replace(b"\n", b"\r\n")
        crlf, crlf_root = run_capture(crlf_stream)
        self.assertEqual(crlf.returncode, 10)
        crlf_metadata = json.loads((crlf_root / "capture.json").read_text())
        self.assertEqual(crlf_metadata["serial_transform"], "onlcr_crlf")
        expected_target = (
            complete(17)
            .split(b"prefix\n", 1)[1]
            .split(b"HOARDARR_OFFLINE_PAYLOAD_TARGET_LOG_SIZE=", 1)[0]
        )
        self.assertEqual((crlf_root / "target.log").read_bytes(), expected_target)
        self.assertEqual(
            crlf_metadata["target_log_sha256"],
            hashlib.sha256(expected_target).hexdigest(),
        )
        absent, _ = run_capture(b"")
        self.assertEqual(absent.returncode, 20)
        partial, _ = run_capture(b"HOARDARR_OFFLINE_PAYLOAD_BEGIN\n")
        self.assertEqual(partial.returncode, 20)
        malformed_bytes = complete(1).replace(
            b"HOARDARR_OFFLINE_PAYLOAD_TARGET_LOG_SIZE=",
            b"HOARDARR_OFFLINE_PAYLOAD_TARGET_LOG_SIZE=999",
        )
        malformed, malformed_root = run_capture(malformed_bytes)
        self.assertEqual(malformed.returncode, 21)
        self.assertFalse((malformed_root / "capture.json").exists())
        duplicate, _ = run_capture(
            complete(1).replace(
                b"HOARDARR_OFFLINE_PAYLOAD_BEGIN\n",
                b"HOARDARR_OFFLINE_PAYLOAD_BEGIN\nHOARDARR_OFFLINE_PAYLOAD_BEGIN\n",
            )
        )
        self.assertEqual(duplicate.returncode, 21)
        duplicate_complete, _ = run_capture(
            complete(1) + b"HOARDARR_OFFLINE_PAYLOAD_CAPTURE_COMPLETE\n"
        )
        self.assertEqual(duplicate_complete.returncode, 21)
        malformed_then_complete, malformed_then_root = run_capture(
            malformed_bytes + complete(1)
        )
        self.assertEqual(malformed_then_complete.returncode, 21)
        self.assertFalse((malformed_then_root / "capture.json").exists())
        arbitrary_cr, arbitrary_cr_root = run_capture(
            complete(1).replace(b"decisive failure", b"decisive\rfailure")
        )
        self.assertEqual(arbitrary_cr.returncode, 21)
        self.assertFalse((arbitrary_cr_root / "capture.json").exists())

    def test_diagnostic_early_stop_requires_valid_nonzero_capture(self) -> None:
        harness = (ROOT / "tests" / "appliance" / "run-offline-iso-pass.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn("if (( payload_parser_status == 10 )); then", harness)
        self.assertIn("payload_failure_observed=true\n            sleep 5", harness)
        self.assertIn("for _ in {1..3}; do", harness)
        self.assertIn('if [[ "$payload_failure_observed" == true ]]', harness)
        self.assertIn("offline_payload_failure_observed", harness)
        self.assertIn("offline_payload_capture_invalid", harness)
        self.assertIn(
            'if [[ "$payload_failure_observed" == true && "$payload_capture_invalid" != true ]]',
            harness,
        )
        self.assertNotIn(
            "payload_parser_status == 0 )); then\n            payload_failure_observed",
            harness,
        )
        parser_guard = harness.split('if [[ "$diagnostic_mode" == true ]]; then', 1)[1]
        self.assertIn('payload_capture_parser="$script_root/', parser_guard)
        self.assertNotIn(
            "parse-offline-payload-capture.py", harness.split(parser_guard, 1)[0]
        )

    def test_two_compatibility_families_emit_deterministic_verifiable_evidence(
        self,
    ) -> None:
        required = (
            "dists/noble/InRelease",
            "dists/noble/Release",
            "dists/noble/Release.gpg",
            "dists/noble/main/binary-amd64/Packages.gz",
            "evidence/SBOM.cdx.json",
            "evidence/provenance.json",
            "evidence/root-package-versions.txt",
            "evidence/vulnerability-status.json",
            "hoardarr-offline-archive-keyring.gpg",
        )
        version = "candidate-version-from-apt"
        families = (
            {
                "id": "systemd-noble",
                "members": ("udev", "systemd-dev"),
                "version_policy": "single-candidate-version",
                "exact_dependencies": {},
            },
            {
                "id": "linux-meta-noble",
                "members": (
                    "linux-generic",
                    "linux-image-generic",
                    "linux-headers-generic",
                ),
                "version_policy": "single-candidate-version",
                "exact_dependencies": {
                    "linux-generic": (
                        "linux-image-generic",
                        "linux-headers-generic",
                    )
                },
            },
        )
        declarations = [
            {
                "id": "systemd-noble",
                "members": ["udev", "systemd-dev"],
                "version_policy": "single-candidate-version",
            },
            {
                "id": "linux-meta-noble",
                "members": [
                    "linux-generic",
                    "linux-image-generic",
                    "linux-headers-generic",
                ],
                "version_policy": "single-candidate-version",
                "exact_dependencies": {
                    "linux-generic": [
                        "linux-image-generic",
                        "linux-headers-generic",
                    ]
                },
            },
        ]
        plan = offline_repo.PackagePlan(
            roots=("linux-image-generic",),
            compatibility_families=families,
            matrix={"compatibility_families": declarations},
            policy={},
        )
        dependency = (
            f"linux-image-generic (= {version}), linux-headers-generic (= {version})"
        )
        records = [
            {"name": "udev", "version": version, "architecture": "amd64"},
            {"name": "systemd-dev", "version": version, "architecture": "all"},
            {
                "name": "linux-generic",
                "version": version,
                "architecture": "amd64",
                "declared_dependencies": {"depends": dependency},
            },
            {
                "name": "linux-image-generic",
                "version": version,
                "architecture": "amd64",
            },
            {
                "name": "linux-headers-generic",
                "version": version,
                "architecture": "amd64",
            },
        ]
        family_versions = {
            family["id"]: {member: version for member in family["members"]}
            for family in families
        }
        evidence = offline_repo._resolved_family_evidence(
            plan, family_versions, records
        )
        self.assertEqual(
            evidence,
            offline_repo._resolved_family_evidence(plan, family_versions, records),
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            for relative in required:
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(relative, encoding="utf-8")
            packages = "".join(
                f"Package: {record['name']}\nVersion: {version}\n"
                f"Architecture: {record['architecture']}\n\n"
                for record in records
            )
            (root / "dists/noble/main/binary-amd64/Packages").write_text(
                packages, encoding="utf-8"
            )
            (root / "evidence/package-manifest.json").write_text(
                json.dumps({"schema_version": 1, "packages": records}),
                encoding="utf-8",
            )
            (root / "evidence/compatibility-matrix.json").write_text(
                json.dumps(plan.matrix), encoding="utf-8"
            )
            (root / "evidence/compatibility-families.json").write_text(
                json.dumps(evidence), encoding="utf-8"
            )
            offline_repo._write_tree_manifest(root)
            with mock.patch.object(offline_repo.shutil, "which", return_value=None):
                offline_repo.verify_repository(root)
                records[2]["declared_dependencies"]["depends"] = (
                    f"linux-image-generic (= {version}) | linux-image-virtual, "
                    f"linux-headers-generic (= {version})"
                )
                (root / "evidence/package-manifest.json").write_text(
                    json.dumps({"schema_version": 1, "packages": records}),
                    encoding="utf-8",
                )
                offline_repo._write_tree_manifest(root)
                with self.assertRaisesRegex(
                    offline_repo.OfflineRepositoryError, "depend exactly"
                ):
                    offline_repo.verify_repository(root)

    def test_repository_tree_verification_rejects_tampering(self) -> None:
        required = (
            "dists/noble/InRelease",
            "dists/noble/Release",
            "dists/noble/Release.gpg",
            "dists/noble/main/binary-amd64/Packages",
            "dists/noble/main/binary-amd64/Packages.gz",
            "evidence/SBOM.cdx.json",
            "evidence/compatibility-matrix.json",
            "evidence/compatibility-families.json",
            "evidence/package-manifest.json",
            "evidence/provenance.json",
            "evidence/root-package-versions.txt",
            "evidence/vulnerability-status.json",
            "hoardarr-offline-archive-keyring.gpg",
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            for relative in required:
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(relative, encoding="utf-8")
            (root / "evidence" / "package-manifest.json").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "packages": [
                            {
                                "name": "udev",
                                "version": "8.17",
                                "architecture": "amd64",
                            },
                            {
                                "name": "systemd-dev",
                                "version": "8.17",
                                "architecture": "all",
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )
            nonalphabetical_plan = offline_repo.PackagePlan(
                roots=(),
                compatibility_families=(
                    {
                        "id": "systemd-noble",
                        "members": ("udev", "systemd-dev"),
                        "version_policy": "single-candidate-version",
                    },
                ),
                matrix={},
                policy={},
            )
            generated_family_evidence = offline_repo._resolved_family_evidence(
                nonalphabetical_plan,
                {"systemd-noble": {"udev": "8.17", "systemd-dev": "8.17"}},
                [
                    {"name": "udev", "version": "8.17", "architecture": "amd64"},
                    {
                        "name": "systemd-dev",
                        "version": "8.17",
                        "architecture": "all",
                    },
                ],
            )
            (root / "evidence" / "compatibility-families.json").write_text(
                json.dumps(generated_family_evidence),
                encoding="utf-8",
            )
            (root / "evidence" / "compatibility-matrix.json").write_text(
                json.dumps(
                    {
                        "compatibility_families": [
                            {
                                "id": "systemd-noble",
                                "members": ["udev", "systemd-dev"],
                                "version_policy": "single-candidate-version",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            (root / "dists/noble/main/binary-amd64/Packages").write_text(
                "Package: udev\nVersion: 8.17\nArchitecture: amd64\n\n"
                "Package: systemd-dev\nVersion: 8.17\nArchitecture: all\n\n",
                encoding="utf-8",
            )
            offline_repo._write_tree_manifest(root)
            with mock.patch.object(offline_repo.shutil, "which", return_value=None):
                offline_repo.verify_repository(root)
                family_path = root / "evidence" / "compatibility-families.json"
                family_document = json.loads(family_path.read_text(encoding="utf-8"))
                family_document["schema_version"] = 2
                family_path.write_text(json.dumps(family_document), encoding="utf-8")
                offline_repo._write_tree_manifest(root)
                with self.assertRaisesRegex(
                    offline_repo.OfflineRepositoryError, "evidence schema"
                ):
                    offline_repo.verify_repository(root)
                family_document["schema_version"] = 1
                family_path.write_text(json.dumps(family_document), encoding="utf-8")
                offline_repo._write_tree_manifest(root)
                offline_repo.verify_repository(root)
                family_document["families"][0]["members"][0] = "udev=8.17"
                family_path.write_text(json.dumps(family_document), encoding="utf-8")
                offline_repo._write_tree_manifest(root)
                with self.assertRaisesRegex(
                    offline_repo.OfflineRepositoryError,
                    "incomplete or incoherent",
                ):
                    offline_repo.verify_repository(root)
                family_document["families"][0]["members"][0] = {
                    "name": "udev",
                    "version": "8.17",
                }
                family_path.write_text(json.dumps(family_document), encoding="utf-8")
                offline_repo._write_tree_manifest(root)
                offline_repo.verify_repository(root)
                package_manifest_path = root / "evidence" / "package-manifest.json"
                package_document = json.loads(
                    package_manifest_path.read_text(encoding="utf-8")
                )
                package_document["packages"] = package_document["packages"][1:]
                package_manifest_path.write_text(
                    json.dumps(package_document), encoding="utf-8"
                )
                offline_repo._write_tree_manifest(root)
                with self.assertRaisesRegex(
                    offline_repo.OfflineRepositoryError, "incomplete or incoherent"
                ):
                    offline_repo.verify_repository(root)
                package_document["packages"].insert(
                    0,
                    {"name": "udev", "version": "8.17", "architecture": "amd64"},
                )
                package_manifest_path.write_text(
                    json.dumps(package_document), encoding="utf-8"
                )
                packages_path = root / "dists/noble/main/binary-amd64/Packages"
                packages_document = packages_path.read_text(encoding="utf-8")
                package_document["packages"].append(
                    {
                        "name": "systemd-dev",
                        "version": "8.17",
                        "architecture": "amd64",
                    }
                )
                package_manifest_path.write_text(
                    json.dumps(package_document), encoding="utf-8"
                )
                offline_repo._write_tree_manifest(root)
                with self.assertRaisesRegex(
                    offline_repo.OfflineRepositoryError, "incomplete or incoherent"
                ):
                    offline_repo.verify_repository(root)
                package_document["packages"].pop()
                package_document["packages"][1]["architecture"] = "i386"
                package_manifest_path.write_text(
                    json.dumps(package_document), encoding="utf-8"
                )
                packages_path.write_text(
                    packages_document.replace(
                        "Architecture: all", "Architecture: i386"
                    ),
                    encoding="utf-8",
                )
                offline_repo._write_tree_manifest(root)
                with self.assertRaisesRegex(
                    offline_repo.OfflineRepositoryError, "incomplete or incoherent"
                ):
                    offline_repo.verify_repository(root)
                package_document["packages"][1]["architecture"] = "all"
                package_document["packages"].append(
                    dict(package_document["packages"][1])
                )
                package_manifest_path.write_text(
                    json.dumps(package_document), encoding="utf-8"
                )
                packages_path.write_text(packages_document, encoding="utf-8")
                offline_repo._write_tree_manifest(root)
                with self.assertRaisesRegex(
                    offline_repo.OfflineRepositoryError, "invalid binary identities"
                ):
                    offline_repo.verify_repository(root)
                package_document["packages"].pop()
                package_manifest_path.write_text(
                    json.dumps(package_document), encoding="utf-8"
                )
                packages_path.write_text(
                    packages_document.split("\n\n", 1)[1], encoding="utf-8"
                )
                offline_repo._write_tree_manifest(root)
                with self.assertRaisesRegex(
                    offline_repo.OfflineRepositoryError, "incomplete or incoherent"
                ):
                    offline_repo.verify_repository(root)
                packages_path.write_text(packages_document, encoding="utf-8")
                offline_repo._write_tree_manifest(root)
                offline_repo.verify_repository(root)
                (root / "evidence" / "package-manifest.json").write_text(
                    "tampered", encoding="utf-8"
                )
                with self.assertRaisesRegex(
                    offline_repo.OfflineRepositoryError, "digest mismatch"
                ):
                    offline_repo.verify_repository(root)

    def test_deferred_release_install_does_not_enable_lldpd(self) -> None:
        installer = (ROOT / "scripts" / "install-release-bundle.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn("systemctl disable lldpd.service", installer)
        deferred = installer.split('if [[ "${DEFER_SERVICE_START}" == "true" ]]', 1)[1]
        self.assertNotIn("systemctl enable lldpd.service", deferred.split("else", 1)[0])

    def test_release_bundle_emits_dependency_sbom_license_and_provenance_evidence(
        self,
    ) -> None:
        builder = (ROOT / "scripts" / "build-release-bundle.py").read_text(
            encoding="utf-8"
        )
        installer = (ROOT / "scripts" / "install-release-bundle.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn('staging / "evidence" / "SBOM.cdx.json"', builder)
        self.assertIn('staging / "evidence" / "python-licenses.json"', builder)
        self.assertIn('staging / "evidence" / "npm-licenses.json"', builder)
        self.assertIn('staging / "evidence" / "provenance.json"', builder)
        self.assertIn('"evidence/SBOM.cdx.json"', installer)
        self.assertIn('"evidence/vulnerability-status.json"', installer)


if __name__ == "__main__":
    unittest.main()
