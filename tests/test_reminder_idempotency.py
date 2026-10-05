"""提醒幂等性测试：重试不得产生重复副作用（Duplicate Side-effect Rate）。"""

from trippilot.state import ToolCall
from trippilot.tools.tools import ReminderTool


def setup_function():
    ReminderTool.reset()


def test_create_is_idempotent():
    tool = ReminderTool()
    call = ToolCall(tool="reminder.create",
                    args={"content": "面试", "time": "2026-10-06T13:00:00+08:00",
                          "session_id": "s1"})
    r1 = tool.run(call)
    r2 = tool.run(call)  # 模拟重试
    assert r1.ok and r2.ok
    assert r1.data["reminder_id"] == r2.data["reminder_id"]
    assert r2.data["duplicate_suppressed"] is True
    assert len(ReminderTool._store) == 1


def test_different_time_is_different_reminder():
    tool = ReminderTool()
    c1 = ToolCall(tool="reminder.create",
                  args={"content": "面试", "time": "2026-10-06T13:00:00+08:00",
                        "session_id": "s1"})
    c2 = ToolCall(tool="reminder.create",
                  args={"content": "面试", "time": "2026-10-06T14:00:00+08:00",
                        "session_id": "s1"})
    r1, r2 = tool.run(c1), tool.run(c2)
    assert r1.data["reminder_id"] != r2.data["reminder_id"]
    assert len(ReminderTool._store) == 2
