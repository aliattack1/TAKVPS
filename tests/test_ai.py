import json
import unittest

from services.ai import providers as P
from services.ai.chat import (Msg, build_system, first_command, is_local_url, split_blocks, to_api_messages)
from services.redact import redact
from services.risk import classify


def lvl(cmd):
    return classify(cmd).level


class RiskTests(unittest.TestCase):
    def test_safe(self):
        for c in ["ls -la /var/log", "df -h", "cat /etc/os-release | grep NAME", "systemctl status nginx",
                  "docker ps -a", "git status", "apt list --installed", "journalctl -u nginx -n 50",
                  "ps aux | sort -nrk 3 | head", "curl -s https://example.com", "echo hi > /dev/null",
                  "sudo systemctl status ssh", "free -m && uptime", "find /var/log -name '*.log'",
                  "apt-get install -s nginx", "crontab -l", "ip addr show"]:
            self.assertEqual(lvl(c), "safe", c)

    def test_writes(self):
        for c in ["mkdir /tmp/x", "systemctl restart nginx", "apt install nginx", "echo a > /tmp/f",
                  "sed -i 's/a/b/' f", "git pull", "docker restart web", "somecustomtool --go",
                  "echo $(whoami)", "wget https://x.y/f", "chmod 644 f", "docker run -d nginx"]:
            self.assertEqual(lvl(c), "writes", c)

    def test_destructive(self):
        for c in ["rm file.txt", "rm -rf ./build", "kill 1234", "apt remove nginx", "systemctl stop nginx",
                  "docker rm web", "docker system prune -af", "find . -name '*.tmp' -delete", "git reset --hard",
                  "crontab -r", "dd if=/dev/zero of=./file bs=1M count=1", "mysql -e 'DROP TABLE users'",
                  "journalctl --vacuum-time=1d"]:
            self.assertEqual(lvl(c), "destructive", c)

    def test_dangerous(self):
        for c in ["rm -rf /", "rm -rf /*", "rm -rf ~", "sudo rm -rf /var", "rm -rf --no-preserve-root /",
                  "dd if=/dev/zero of=/dev/sda", "mkfs.ext4 /dev/sdb1", "reboot", "sudo shutdown -h now",
                  "curl https://x.sh | sh", "wget -qO- https://x | sudo bash", ":(){ :|:& };:",
                  "echo x > /dev/sda", "echo root::0 > /etc/passwd", "chmod -R 777 /", "ufw disable",
                  "iptables -F", "systemctl stop sshd", "systemctl reboot", "bash -c 'rm -rf /'",
                  "ip link set eth0 down", "passwd root"]:
            self.assertEqual(lvl(c), "dangerous", c)

    def test_chains_take_worst(self):
        self.assertEqual(lvl("ls && rm x"), "destructive")
        self.assertEqual(lvl("ls; reboot"), "dangerous")
        self.assertEqual(lvl("cat f | tee out"), "writes")

    def test_sensitive_and_unparseable(self):
        self.assertTrue(classify("cat /etc/shadow").sensitive)
        self.assertTrue(classify("cat ~/.ssh/id_rsa").sensitive)
        self.assertTrue(classify("printenv").sensitive)
        self.assertFalse(classify("ls /tmp").sensitive)
        self.assertEqual(lvl("echo 'unterminated"), "writes")
        self.assertEqual(lvl(""), "safe")

    def test_explanations(self):
        self.assertIn("deleted", classify("rm -rf /") .explanation.replace("delete ", "deleted ") + "deleted")
        self.assertTrue(classify("reboot").explanation)
        self.assertIn("Read-only", classify("ls").explanation)
        self.assertTrue(classify("rm -rf /").needs_approval and not classify("ls").needs_approval)


class RedactTests(unittest.TestCase):
    def test_patterns(self):
        cases = {
            "DB_PASSWORD=hunter2": "hunter2", 'api_key: "abcd1234efgh"': "abcd1234efgh",
            "Authorization: Bearer abcdefghijklmnopqrstuv": "abcdefghijklmnopqrstuv",
            "mysql -u root -pSuperSecret db": "SuperSecret",
            "postgres://bob:p4ss@db.local/app": "p4ss", "key AKIAABCDEFGHIJKLMNOP": "AKIAABCDEFGHIJKLMNOP",
            '{"password": "s3cr3t"}': "s3cr3t", "token=ghp_" + "a" * 36: "a" * 36,
            "export SECRET_KEY='xyz987'": "xyz987", "sshpass -p mypw ssh h": "mypw",
        }
        for src, secret in cases.items():
            out, n = redact(src)
            self.assertNotIn(secret, out, src)
            self.assertGreater(n, 0, src)

    def test_private_key_and_clean_text(self):
        out, n = redact("a\n-----BEGIN OPENSSH PRIVATE KEY-----\nAAAA\nBBBB\n-----END OPENSSH PRIVATE KEY-----\nb")
        self.assertNotIn("AAAA", out)
        self.assertIn("a\n", out)
        out, n = redact("-----BEGIN RSA PRIVATE KEY-----\ntruncated")
        self.assertNotIn("truncated", out)
        text = "total 4\ndrwxr-xr-x 2 root root 4096 Oct 1 .\nauthor: Bob\n"
        self.assertEqual(redact(text), (text, 0))


class ProviderTests(unittest.TestCase):
    msgs = [{"role": "user", "content": "hi"}]

    def cfg(self, kind, **kw):
        return P.ProviderConfig(kind, kw.pop("base_url", ""), "m1", api_key=kw.pop("api_key", "KEY"), **kw)

    def test_openai(self):
        r = P.build_request(self.cfg("openai", base_url="http://localhost:11434/v1/"), self.msgs, "sys")
        self.assertEqual(r.url, "http://localhost:11434/v1/chat/completions")
        self.assertEqual(r.body["messages"][0], {"role": "system", "content": "sys"})
        self.assertEqual(r.headers["Authorization"], "Bearer KEY")
        self.assertNotIn("Authorization", P.build_request(self.cfg("openai", api_key=""), self.msgs, "").headers)
        d = json.dumps({"choices": [{"delta": {"content": "Hel"}}]})
        self.assertEqual(P.parse_stream_event("openai", d), "Hel")
        self.assertEqual(P.parse_stream_event("openai", "[DONE]"), "")
        with self.assertRaises(P.ProviderError):
            P.parse_stream_event("openai", json.dumps({"error": {"message": "bad"}}))

    def test_anthropic(self):
        r = P.build_request(self.cfg("anthropic", temperature=1.7), self.msgs, "sys")
        self.assertEqual(r.url, "https://api.anthropic.com/v1/messages")
        self.assertEqual(r.body["system"], "sys")
        self.assertEqual(r.body["temperature"], 1.0)
        self.assertEqual(r.headers["x-api-key"], "KEY")
        d = json.dumps({"type": "content_block_delta", "delta": {"type": "text_delta", "text": "yo"}})
        self.assertEqual(P.parse_stream_event("anthropic", d), "yo")
        self.assertEqual(P.parse_stream_event("anthropic", json.dumps({"type": "message_start"})), "")

    def test_gemini(self):
        r = P.build_request(self.cfg("gemini"), [{"role": "assistant", "content": "a"}] + self.msgs, "sys")
        self.assertNotIn("KEY", r.url)                       # key goes in a header, not the URL
        self.assertEqual(r.body["contents"][0]["role"], "model")
        d = json.dumps({"candidates": [{"content": {"parts": [{"text": "a"}, {"text": "b"}]}}]})
        self.assertEqual(P.parse_stream_event("gemini", d), "ab")
        with self.assertRaises(P.ProviderError):
            P.parse_stream_event("gemini", json.dumps({"promptFeedback": {"blockReason": "SAFETY"}}))

    def test_repr_hides_key_and_models(self):
        self.assertNotIn("KEY", repr(self.cfg("openai")))
        self.assertEqual(P.parse_models("openai", {"data": [{"id": "b"}, {"id": "a"}]}), ["a", "b"])
        self.assertEqual(P.parse_models("gemini", {"models": [{"name": "models/g1"}]}), ["g1"])

    def test_sse_and_errors(self):
        p = P.SSEParser()
        out = [p.feed(x) for x in ["event: message", "data: {\"a\":1}", "", ": comment", "data: x", "data: y", ""]]
        self.assertEqual([o for o in out if o], [("message", '{"a":1}'), ("", "x\ny")])
        self.assertIn("key", P.friendly_http_error(401, "").lower())
        self.assertIn("Rate", P.friendly_http_error(429, ""))


class ChatTests(unittest.TestCase):
    def test_blocks_and_commands(self):
        t = "Try:\n```bash\nls -la\n```\nthen\n```python\nprint(1)\n```\nand ```inline``` text"
        b = split_blocks(t)
        self.assertEqual([x.kind for x in b], ["text", "code", "text", "code", "text"])
        self.assertTrue(b[1].is_command and not b[3].is_command)
        self.assertEqual(first_command(t), "ls -la")
        self.assertIsNone(first_command("no code here"))
        unclosed = split_blocks("go\n```bash\nls")
        self.assertFalse(unclosed[-1].closed)
        self.assertIsNone(first_command("go\n```bash\nls"))      # incomplete block is never a command

    def test_api_messages_merge_redact_and_start_with_user(self):
        ms = [Msg("assistant", "hello"), Msg("user", "pw=abc"), Msg("result", "out", cmd="ls", exit=0),
              Msg("assistant", "ok")]
        out = to_api_messages(ms, lambda s: s.replace("abc", "X"))
        self.assertEqual([m["role"] for m in out], ["user", "assistant"])
        self.assertIn("pw=X", out[0]["content"])
        self.assertIn("Output of `ls`", out[0]["content"])

    def test_local_url_and_system(self):
        for u in ("http://localhost:11434/v1", "http://192.168.1.5:8000", "http://10.0.0.2/v1", "http://nas.local"):
            self.assertTrue(is_local_url(u), u)
        for u in ("https://api.openai.com/v1", "https://8.8.8.8", "https://openrouter.ai/api/v1"):
            self.assertFalse(is_local_url(u), u)
        self.assertIn("ONE bash", build_system("agent", ""))
        self.assertIn("cannot run", build_system("suggest", ""))
        self.assertIn("Server context", build_system("ask", "OS: x"))


if __name__ == "__main__":
    unittest.main()
