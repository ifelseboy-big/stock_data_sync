import json
import os
import subprocess
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

from app.core.config import Settings


class LarkNotificationError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class DataSyncAlert:
    business_date: date
    checked_at: datetime
    scheduler_issues: tuple[str, ...]
    batch_statuses: dict[str, int]
    collection_statuses: dict[str, int]
    processing_statuses: dict[str, int]
    issue_details: tuple[str, ...]

    @property
    def successful(self) -> bool:
        return not (
            self.scheduler_issues
            or self.issue_details
            or _has_unsuccessful(self.batch_statuses, {"CLOSED"})
            or _has_unsuccessful(self.collection_statuses, {"SUCCESS", "EMPTY_VALID"})
            or _has_unsuccessful(self.processing_statuses, {"SUCCESS"})
        )

    def message(self, *, app_name: str) -> str:
        lines = [
            f"【{app_name} 数据同步报警】",
            f"日期：{self.business_date.isoformat()}",
            f"检查时间：{self.checked_at.strftime('%Y-%m-%d %H:%M:%S %Z')}",
            "结论：前一日数据同步未全部成功",
        ]
        if self.scheduler_issues:
            lines.append(f"调度异常：{len(self.scheduler_issues)} 项")
            lines.extend(f"- {item}" for item in self.scheduler_issues[:8])
        lines.extend(
            (
                f"采集批次：{_format_statuses(self.batch_statuses)}",
                f"采集任务：{_format_statuses(self.collection_statuses)}",
                f"加工任务：{_format_statuses(self.processing_statuses)}",
            )
        )
        if self.issue_details:
            lines.append("异常明细：")
            lines.extend(f"- {item}" for item in self.issue_details[:12])
        lines.append("请登录 Stock Sync 管理端查看并处理。")
        return "\n".join(lines)


class LarkCliNotifier:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def send_data_sync_alert(self, alert: DataSyncAlert) -> str:
        command = [
            self._settings.lark_cli_path,
            "im",
            "+messages-send",
            "--as",
            self._settings.lark_alert_send_as,
            "--user-id",
            self._settings.lark_alert_recipient_open_id,
            "--text",
            alert.message(app_name=self._settings.app_name),
            "--idempotency-key",
            f"stock-sync-alert-{alert.business_date:%Y%m%d}",
            "--json",
        ]
        environment = os.environ.copy()
        environment["LARKSUITE_CLI_NO_UPDATE_NOTIFIER"] = "1"
        environment["LARKSUITE_CLI_NO_SKILLS_NOTIFIER"] = "1"
        if self._settings.lark_cli_node_path:
            node_dir = str(Path(self._settings.lark_cli_node_path).parent)
            environment["PATH"] = f"{node_dir}:{environment.get('PATH', '')}"
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                check=False,
                env=environment,
                text=True,
                timeout=self._settings.lark_alert_timeout_seconds,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise LarkNotificationError(f"lark-cli invocation failed: {exc}") from exc

        payload = _parse_json(result.stdout if result.returncode == 0 else result.stderr)
        if result.returncode != 0 or payload.get("ok") is not True:
            raise LarkNotificationError(_error_message(payload, result.returncode))
        data = payload.get("data")
        if not isinstance(data, dict) or not data.get("message_id"):
            raise LarkNotificationError("lark-cli returned no message_id")
        return str(data["message_id"])


def _parse_json(value: str) -> dict[str, Any]:
    try:
        payload = json.loads(value)
    except json.JSONDecodeError as exc:
        raise LarkNotificationError("lark-cli returned invalid JSON") from exc
    if not isinstance(payload, dict):
        raise LarkNotificationError("lark-cli returned an invalid response envelope")
    return payload


def _error_message(payload: dict[str, Any], returncode: int) -> str:
    error = payload.get("error")
    if isinstance(error, dict) and error.get("message"):
        return f"lark-cli failed ({returncode}): {str(error['message'])[:500]}"
    return f"lark-cli failed with exit code {returncode}"


def _format_statuses(statuses: dict[str, int]) -> str:
    if not statuses:
        return "无记录"
    return "，".join(f"{status} {count}" for status, count in sorted(statuses.items()))


def _has_unsuccessful(statuses: dict[str, int], successful: set[str]) -> bool:
    return any(count > 0 and status not in successful for status, count in statuses.items())
