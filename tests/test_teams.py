import sys
import tempfile
import unittest
from pathlib import Path

WEB = Path(__file__).resolve().parents[1] / "web"
sys.path.insert(0, str(WEB))

import server


class TeamTests(unittest.TestCase):
    """租户与团队协作 MVP：创建、邀请加入、成员列表、权限、退出。"""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.mkdtemp()
        server.DB_FILE = Path(cls._tmp) / "test_teams.db"
        server.init_db()

    def _mkuser(self, username):
        with server._DB_LOCK:
            conn = server.db()
            cur = conn.execute(
                "INSERT INTO users (username, email, password_hash, role) VALUES (?,?,?, 'user')",
                (username, username + "@t.local", server.hash_password("pass123")),
            )
            conn.commit()
            uid = cur.lastrowid
            conn.close()
        return uid

    def setUp(self):
        conn = server.db()
        for t in ("team_members", "teams", "users"):
            conn.execute("DELETE FROM " + t)
        conn.commit()
        conn.close()
        self.a = self._mkuser("alice")
        self.b = self._mkuser("bob")
        self.c = self._mkuser("carol")

    def test_create_team_makes_owner_and_code(self):
        team = server.create_team(self.a, "内推小分队")
        self.assertEqual(team["owner_user_id"], self.a)
        self.assertTrue(team["invite_code"])
        self.assertEqual(server.team_member_count(team["id"]), 1)
        self.assertTrue(server.is_team_member(team["id"], self.a))

    def test_join_team_by_code(self):
        team = server.create_team(self.a, "内推小分队")
        joined, err = server.join_team(self.b, team["invite_code"])
        self.assertIsNone(err)
        self.assertEqual(joined["id"], team["id"])
        self.assertEqual(server.team_member_count(team["id"]), 2)
        members = server.list_team_members(team["id"])
        usernames = [m["username"] for m in members]
        self.assertIn("alice", usernames)
        self.assertIn("bob", usernames)

    def test_join_invalid_code_and_duplicate(self):
        team = server.create_team(self.a, "t")
        _, err = server.join_team(self.b, "WRONG")
        self.assertEqual(err, "邀请码无效")
        server.join_team(self.b, team["invite_code"])
        _, err2 = server.join_team(self.b, team["invite_code"])
        self.assertEqual(err2, "已在团队中")

    def test_list_my_teams_shows_role_and_count(self):
        team = server.create_team(self.a, "t")
        server.join_team(self.b, team["invite_code"])
        teams_a = server.list_my_teams(self.a)
        teams_b = server.list_my_teams(self.b)
        self.assertEqual(teams_a[0]["my_role"], "owner")
        self.assertEqual(teams_b[0]["my_role"], "member")
        self.assertEqual(teams_b[0]["member_count"], 2)

    def test_member_access_control(self):
        team = server.create_team(self.a, "t")
        # 非成员（carol）不能看成员列表
        self.assertFalse(server.is_team_member(team["id"], self.c))
        self.assertTrue(server.is_team_member(team["id"], self.a))

    def test_owner_cannot_leave(self):
        team = server.create_team(self.a, "t")
        server.join_team(self.b, team["invite_code"])
        ok, err = server.leave_team(self.a, team["id"])
        self.assertFalse(ok)
        self.assertEqual(err, "创建者不可退出团队")
        ok2, err2 = server.leave_team(self.b, team["id"])
        self.assertTrue(ok2)
        self.assertIsNone(err2)
        self.assertFalse(server.is_team_member(team["id"], self.b))


if __name__ == "__main__":
    unittest.main()
