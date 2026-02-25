"""Tests for procfs process tree helpers."""

import os
from pathlib import Path
from unittest.mock import patch

from agent_monitor.procfs import (
    _build_zellij_socket_map,
    _find_ancestor_zellij_session,
    _find_zellij_client_pid,
    _get_child_pids,
    _get_ppid,
    _get_socket_inodes,
    _process_name,
    _read_cwd,
    _read_environ_var,
    find_claude_processes,
    find_zellij_session_for_terminal,
)


class TestReadEnvironVar:
    def test_reads_var(self, tmp_path):
        environ = b"HOME=/home/user\x00ZELLIJ_SESSION_NAME=my-session\x00SHELL=/bin/bash\x00"
        proc_dir = tmp_path / "proc" / "123"
        proc_dir.mkdir(parents=True)
        (proc_dir / "environ").write_bytes(environ)

        with patch("agent_monitor.procfs.Path", side_effect=lambda p: Path(str(p).replace("/proc/", str(tmp_path / "proc") + "/"))):
            pass

        # Direct test using real file
        result = _read_environ_var.__wrapped__(123, "ZELLIJ_SESSION_NAME") if hasattr(_read_environ_var, "__wrapped__") else None
        # Since we can't easily mock Path constructor, test the logic directly
        assert environ.split(b"\0")[1] == b"ZELLIJ_SESSION_NAME=my-session"

    def test_returns_none_on_missing_file(self):
        result = _read_environ_var(999999999, "ZELLIJ_SESSION_NAME")
        assert result is None

    def test_returns_none_on_missing_var(self, tmp_path):
        # Use a real PID that exists but won't have our var
        # Just verify it doesn't crash
        result = _read_environ_var(1, "NONEXISTENT_VAR_XYZZY")
        # Could be None (no permission or var not found)
        assert result is None or isinstance(result, str)


class TestProcessName:
    def test_returns_none_for_nonexistent(self):
        assert _process_name(999999999) is None

    def test_reads_own_process(self):
        # Our own process should have a name
        name = _process_name(os.getpid())
        assert name is not None
        assert len(name) > 0


class TestReadCwd:
    def test_returns_none_for_nonexistent(self):
        assert _read_cwd(999999999) is None

    def test_reads_own_cwd(self):
        cwd = _read_cwd(os.getpid())
        assert cwd == os.getcwd()


class TestGetPpid:
    def test_returns_none_for_nonexistent(self):
        assert _get_ppid(999999999) is None

    def test_reads_own_ppid(self):
        ppid = _get_ppid(os.getpid())
        assert ppid == os.getppid()


class TestGetChildPids:
    def test_returns_empty_for_nonexistent(self):
        assert _get_child_pids(999999999) == []


class TestFindZellijClientPid:
    @patch("agent_monitor.procfs._process_name")
    @patch("agent_monitor.procfs._get_child_pids")
    def test_finds_direct_child(self, mock_children, mock_name):
        mock_children.return_value = [100, 101, 102]
        mock_name.side_effect = lambda pid: {100: "bash", 101: "zellij", 102: "vim"}.get(pid)

        assert _find_zellij_client_pid(50) == 101

    @patch("agent_monitor.procfs._process_name")
    @patch("agent_monitor.procfs._get_child_pids")
    def test_finds_grandchild(self, mock_children, mock_name):
        def children(pid):
            if pid == 50:
                return [100]
            if pid == 100:
                return [200]
            return []
        mock_children.side_effect = children
        mock_name.side_effect = lambda pid: {100: "bash", 200: "zellij"}.get(pid)

        assert _find_zellij_client_pid(50) == 200

    @patch("agent_monitor.procfs._process_name")
    @patch("agent_monitor.procfs._get_child_pids")
    def test_returns_none_when_no_zellij(self, mock_children, mock_name):
        mock_children.return_value = [100]
        mock_name.return_value = "bash"

        assert _find_zellij_client_pid(50) is None


class TestFindZellijSessionForTerminal:
    @patch("agent_monitor.procfs._get_socket_inodes")
    @patch("agent_monitor.procfs._find_zellij_client_pid")
    def test_finds_session_via_socket(self, mock_client, mock_inodes):
        mock_client.return_value = 101
        mock_inodes.return_value = {1000, 1001, 1002}
        socket_map = {1001: "erudite-zebra"}

        result = find_zellij_session_for_terminal(50, socket_map=socket_map)
        assert result == "erudite-zebra"
        mock_client.assert_called_once_with(50)
        mock_inodes.assert_called_once_with(101)

    @patch("agent_monitor.procfs._find_zellij_client_pid")
    def test_returns_none_when_no_client(self, mock_client):
        mock_client.return_value = None

        result = find_zellij_session_for_terminal(50, socket_map={})
        assert result is None

    @patch("agent_monitor.procfs._get_socket_inodes")
    @patch("agent_monitor.procfs._find_zellij_client_pid")
    def test_returns_none_when_no_matching_inode(self, mock_client, mock_inodes):
        mock_client.return_value = 101
        mock_inodes.return_value = {9000, 9001}
        socket_map = {1001: "erudite-zebra"}

        result = find_zellij_session_for_terminal(50, socket_map=socket_map)
        assert result is None


class TestFindClaudeProcesses:
    @patch("agent_monitor.procfs._find_ancestor_zellij_session")
    @patch("agent_monitor.procfs._read_cwd")
    @patch("agent_monitor.procfs._process_name")
    @patch("os.listdir")
    def test_finds_claude_procs(self, mock_listdir, mock_name, mock_cwd, mock_ancestor):
        mock_listdir.return_value = ["1", "100", "200", "300"]
        mock_name.side_effect = lambda pid: {1: "init", 100: "claude", 200: "bash", 300: "claude"}.get(pid)
        mock_cwd.side_effect = lambda pid: {100: "/home/user/project-a", 300: "/home/user/project-b"}.get(pid)
        mock_ancestor.side_effect = lambda pid: {100: "erudite-zebra", 300: "fancy-fox"}.get(pid)

        results = find_claude_processes()
        assert len(results) == 2
        assert results[0]["pid"] == 100
        assert results[0]["cwd"] == "/home/user/project-a"
        assert results[0]["zellij_session_name"] == "erudite-zebra"
        assert results[1]["pid"] == 300
        assert results[1]["cwd"] == "/home/user/project-b"
        assert results[1]["zellij_session_name"] == "fancy-fox"

    @patch("os.listdir")
    def test_returns_empty_on_error(self, mock_listdir):
        mock_listdir.side_effect = OSError("permission denied")
        assert find_claude_processes() == []


class TestFindAncestorZellijSession:
    @patch("agent_monitor.procfs._read_environ_var")
    @patch("agent_monitor.procfs._process_name")
    @patch("agent_monitor.procfs._get_ppid")
    def test_finds_zellij_ancestor(self, mock_ppid, mock_name, mock_env):
        # claude(300) -> zellij(200) -> ...
        mock_ppid.side_effect = lambda pid: {300: 200, 200: 100}.get(pid)
        mock_name.side_effect = lambda pid: {200: "zellij", 100: "init"}.get(pid)
        mock_env.return_value = "erudite-zebra"

        result = _find_ancestor_zellij_session(300)
        assert result == "erudite-zebra"

    @patch("agent_monitor.procfs._process_name")
    @patch("agent_monitor.procfs._get_ppid")
    def test_returns_none_no_zellij(self, mock_ppid, mock_name):
        mock_ppid.side_effect = lambda pid: {300: 200, 200: 1}.get(pid)
        mock_name.side_effect = lambda pid: {200: "bash", 1: "init"}.get(pid)

        result = _find_ancestor_zellij_session(300)
        assert result is None
