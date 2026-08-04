#!/usr/bin/env python3
"""Validate and apply VVV Hysteria 2 UDP port hopping."""

import argparse
import json
import os
import re
import socket
import subprocess
import tempfile
from pathlib import Path

TABLE_FAMILY = "inet"
TABLE_NAME = "vvv_hy2_hop"


class PortSpecError(ValueError):
    pass


def parse_port(value):
    text = str(value).strip()
    if not re.fullmatch(r"[0-9]+", text):
        raise PortSpecError(f"端口 {text or '空值'} 不是纯数字。")
    number = int(text, 10)
    if not 1 <= number <= 65535:
        raise PortSpecError(f"端口 {number} 必须在 1–65535 范围内。")
    return number


def merge_intervals(intervals):
    merged = []
    for start, end in sorted(intervals):
        if merged and start <= merged[-1][1] + 1:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def parse_port_spec(spec, listen_port=None):
    raw = str(spec or "").strip()
    if not raw:
        raise PortSpecError("端口跳跃范围不能为空。")
    if any(ch in raw for ch in "，－—–"):
        raise PortSpecError("只能使用英文逗号 , 和英文连字符 -。")
    if not re.fullmatch(r"[0-9,\-\s]+", raw):
        raise PortSpecError("端口跳跃范围只能包含数字、英文逗号和英文连字符。")
    intervals = []
    for item in raw.split(","):
        item = item.strip()
        if not item:
            raise PortSpecError("端口跳跃范围不能包含空项目或连续逗号。")
        if item.count("-") == 0:
            port = parse_port(item)
            intervals.append((port, port))
        elif item.count("-") == 1:
            left, right = (part.strip() for part in item.split("-", 1))
            start, end = parse_port(left), parse_port(right)
            if start > end:
                raise PortSpecError(f"范围 {item} 的起始端口不能大于结束端口。")
            intervals.append((start, end))
        else:
            raise PortSpecError(f"范围 {item} 的连字符数量不正确。")
    merged = merge_intervals(intervals)
    if sum(end - start + 1 for start, end in merged) < 2:
        raise PortSpecError("端口跳跃范围必须至少包含两个不同的 UDP 端口。")
    if listen_port is not None:
        listener = parse_port(listen_port)
        if not any(start <= listener <= end for start, end in merged):
            raise PortSpecError(f"端口跳跃范围必须包含实际监听端口 {listener}。")
    return merged


def format_intervals(intervals):
    return ",".join(str(start) if start == end else f"{start}-{end}" for start, end in intervals)


def subtract_port(intervals, port):
    result = []
    for start, end in intervals:
        if not start <= port <= end:
            result.append((start, end))
            continue
        if start <= port - 1:
            result.append((start, port - 1))
        if port + 1 <= end:
            result.append((port + 1, end))
    return result


def contains(intervals, port):
    return any(start <= port <= end for start, end in intervals)


def udp_socket_rows():
    rows = []
    for filename, family in (("/proc/net/udp", socket.AF_INET), ("/proc/net/udp6", socket.AF_INET6)):
        path = Path(filename)
        if not path.exists():
            continue
        for line in path.read_text(encoding="ascii", errors="ignore").splitlines()[1:]:
            fields = line.split()
            if len(fields) < 10:
                continue
            local = fields[1]
            state = fields[3]
            inode = fields[9]
            try:
                port = int(local.rsplit(":", 1)[1], 16)
            except (ValueError, IndexError):
                continue
            # 07 is UDP UNCONN/listening. Connected UDP sockets may also reserve a local port,
            # so retain every non-zero local port instead of relying on one state only.
            if port:
                rows.append({"port": port, "inode": inode, "family": family, "state": state})
    return rows


def inode_owners(inodes):
    owners = {inode: [] for inode in inodes if inode and inode != "0"}
    if not owners:
        return owners
    proc = Path("/proc")
    for process in proc.iterdir():
        if not process.name.isdigit():
            continue
        fd_dir = process / "fd"
        try:
            links = list(fd_dir.iterdir())
        except (PermissionError, FileNotFoundError, ProcessLookupError):
            continue
        matched = set()
        for fd in links:
            try:
                target = os.readlink(fd)
            except (PermissionError, FileNotFoundError, ProcessLookupError, OSError):
                continue
            match = re.fullmatch(r"socket:\[(\d+)\]", target)
            if match and match.group(1) in owners:
                matched.add(match.group(1))
        if not matched:
            continue
        try:
            comm = (process / "comm").read_text(encoding="utf-8", errors="replace").strip()
        except (PermissionError, FileNotFoundError, ProcessLookupError):
            comm = "未知"
        try:
            cmdline = (process / "cmdline").read_bytes().replace(b"\0", b" ").decode("utf-8", "replace").strip()
        except (PermissionError, FileNotFoundError, ProcessLookupError):
            cmdline = ""
        for inode in matched:
            owners[inode].append({"pid": int(process.name), "process": comm, "command": cmdline})
    return owners


def find_udp_conflicts(intervals, allow_listen_port=None, allow_process=None):
    rows = udp_socket_rows()
    owners = inode_owners({row["inode"] for row in rows})
    allowed_re = re.compile(allow_process, re.I) if allow_process else None
    conflicts = {}
    for row in rows:
        port = row["port"]
        if not contains(intervals, port):
            continue
        owner_rows = owners.get(row["inode"], [])
        if allow_listen_port is not None and port == int(allow_listen_port) and allowed_re:
            haystack = "\n".join(f"{owner.get('process', '')} {owner.get('command', '')}" for owner in owner_rows)
            if allowed_re.search(haystack):
                continue
        entry = conflicts.setdefault(port, {"port": port, "owners": []})
        for owner in owner_rows:
            if owner not in entry["owners"]:
                entry["owners"].append(owner)
    return [conflicts[key] for key in sorted(conflicts)]


def conflict_text(conflicts):
    lines = ["错误：Hysteria 2 端口跳跃范围与本机已占用的 UDP 端口冲突。", "", "冲突端口："]
    for item in conflicts:
        owners = item.get("owners") or []
        if owners:
            details = "; ".join(
                f"进程：{owner.get('process') or '未知'}，PID：{owner.get('pid') or '未知'}"
                for owner in owners
            )
        else:
            details = "进程：未知（内核未暴露进程信息）"
        lines.append(f"{item['port']}    {details}")
    lines += ["", "请修改端口范围，或停止占用端口的程序。"]
    return "\n".join(lines)


def nft_elements(intervals):
    return ", ".join(str(start) if start == end else f"{start}-{end}" for start, end in intervals)


def build_ruleset(spec, listen_port):
    listener = parse_port(listen_port)
    intervals = parse_port_spec(spec, listener)
    redirect_intervals = subtract_port(intervals, listener)
    lines = []
    if redirect_intervals:
        lines.extend([
            f"table {TABLE_FAMILY} {TABLE_NAME} {{",
            "  set ports {",
            "    type inet_service",
            "    flags interval",
            f"    elements = {{ {nft_elements(redirect_intervals)} }}",
            "  }",
            "  chain prerouting {",
            "    type nat hook prerouting priority dstnat; policy accept;",
            f"    udp dport @ports redirect to :{listener}",
            "  }",
            "}",
            "",
        ])
    return "\n".join(lines)


def nft_table_exists():
    return subprocess.run(
        ["nft", "list", "table", TABLE_FAMILY, TABLE_NAME],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    ).returncode == 0


def apply_nft(spec, listen_port):
    if not shutil_which("nft"):
        raise RuntimeError("系统缺少 nftables 命令。")
    rules = build_ruleset(spec, listen_port)
    commands = []
    if nft_table_exists():
        commands.append(f"delete table {TABLE_FAMILY} {TABLE_NAME}")
    if rules:
        commands.append(rules.rstrip())
    transaction = "\n".join(commands) + "\n"
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", prefix="vvv-hy2-hop.", suffix=".nft", delete=False) as handle:
        handle.write(transaction)
        path = handle.name
    try:
        subprocess.run(["nft", "-c", "-f", path], check=True)
        subprocess.run(["nft", "-f", path], check=True)
    finally:
        Path(path).unlink(missing_ok=True)


def remove_nft():
    if not shutil_which("nft") or not nft_table_exists():
        return
    subprocess.run(["nft", "delete", "table", TABLE_FAMILY, TABLE_NAME], check=True)


def shutil_which(command):
    for folder in os.environ.get("PATH", "").split(os.pathsep):
        candidate = Path(folder) / command
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None


def status_data():
    exists = bool(shutil_which("nft") and nft_table_exists())
    output = ""
    if exists:
        output = subprocess.run(
            ["nft", "-a", "list", "table", TABLE_FAMILY, TABLE_NAME],
            text=True,
            capture_output=True,
            check=False,
        ).stdout
    return {"active": exists, "family": TABLE_FAMILY, "table": TABLE_NAME, "ruleset": output}


def command_validate(args):
    try:
        intervals = parse_port_spec(args.spec, args.listen_port)
    except PortSpecError as exc:
        raise SystemExit(f"格式错误：{exc}")
    conflicts = []
    if args.check_udp:
        conflicts = find_udp_conflicts(
            intervals,
            allow_listen_port=args.allow_listen_port,
            allow_process=args.allow_process,
        )
    if conflicts:
        raise SystemExit(conflict_text(conflicts))
    print(json.dumps({
        "ok": True,
        "ports": format_intervals(intervals),
        "listen_port": int(args.listen_port),
        "hop_interval_seconds": int(args.hop_interval),
        "conflicts": [],
    }, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    validate = commands.add_parser("validate")
    validate.add_argument("--spec", required=True)
    validate.add_argument("--listen-port", required=True, type=int)
    validate.add_argument("--hop-interval", type=int, default=30)
    validate.add_argument("--check-udp", action="store_true")
    validate.add_argument("--allow-listen-port", type=int)
    validate.add_argument("--allow-process")
    apply_cmd = commands.add_parser("apply")
    apply_cmd.add_argument("--spec", required=True)
    apply_cmd.add_argument("--listen-port", required=True, type=int)
    commands.add_parser("remove")
    commands.add_parser("status")
    rules = commands.add_parser("render-rules")
    rules.add_argument("--spec", required=True)
    rules.add_argument("--listen-port", required=True, type=int)
    args = parser.parse_args()
    if args.command == "validate":
        command_validate(args)
    elif args.command == "apply":
        apply_nft(args.spec, args.listen_port)
    elif args.command == "remove":
        remove_nft()
    elif args.command == "status":
        print(json.dumps(status_data(), ensure_ascii=False, indent=2))
    else:
        print(build_ruleset(args.spec, args.listen_port), end="")


if __name__ == "__main__":
    main()
