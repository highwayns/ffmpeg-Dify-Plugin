from collections.abc import Generator
from typing import Any
import tempfile
import os
import json
import subprocess
import requests

from dify_plugin import Tool
from dify_plugin.entities.tool import ToolInvokeMessage

def get_field(obj, field, default=None):
    """兼容 dict 和对象两种方式取字段"""
    if isinstance(obj, dict):
        return obj.get(field, default)
    return getattr(obj, field, default)

class VideoInfoTool(Tool):
    def _invoke(self, tool_parameters: dict[str, Any]) -> Generator[ToolInvokeMessage, None, None]:
        video_meta = tool_parameters.get("video", None)
        if video_meta is None:
            yield self.create_text_message("❌ 缺少 video 参数")
            yield self.create_json_message({
                "status": "error",
                "message": "Missing video parameter"
            })
            return

        # 兼容两种结构
        transfer_method = get_field(video_meta, "transfer_method", "local_file")
        filename = get_field(video_meta, "filename", "video.mp4")
        extension = get_field(video_meta, "extension", ".mp4")
        file_url = get_field(video_meta, "url", "")
        mime_type = get_field(video_meta, "mime_type", "video/mp4")

        # 检查本地文件方式
        if transfer_method == "local_file":
            if not file_url.startswith("/files/"):
                yield self.create_text_message("❌ 本地文件URL不合法，请检查。")
                yield self.create_json_message({
                    "status": "error",
                    "message": "Invalid local file URL."
                })
                return
            # Dify本地文件必须拼接base_url前缀，请自行替换此处
            base_url = "http://api:5001"  # ✅ 请替换为你的 Dify 部署地址
            full_url = base_url + file_url
            response = requests.get(full_url)
            if response.status_code != 200:
                yield self.create_text_message("❌ 无法下载 Dify 本地文件，请确认文件URL正确并已登录。")
                yield self.create_json_message({
                    "status": "error",
                    "message": f"Failed to fetch local file: {response.status_code}"
                })
                return
            file_bytes = response.content
        elif transfer_method == "remote_url":
            full_url = get_field(video_meta, "remote_url", "")
            if not full_url.startswith("http"):
                yield self.create_text_message("❌ 远程 URL 缺少协议前缀 http:// 或 https://")
                yield self.create_json_message({
                    "status": "error",
                    "message": "Invalid remote URL"
                })
                return
            response = requests.get(full_url)
            if response.status_code != 200:
                yield self.create_text_message("❌ 无法下载远程文件")
                yield self.create_json_message({
                    "status": "error",
                    "message": f"Failed to fetch remote file: {response.status_code}"
                })
                return
            file_bytes = response.content
        else:
            yield self.create_text_message("❌ 不支持的传输方式")
            yield self.create_json_message({
                "status": "error",
                "message": f"Unsupported transfer method: {transfer_method}"
            })
            return

        temp_file_path = None
        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix=extension) as temp_file:
                temp_file.write(file_bytes)
                temp_file_path = temp_file.name

            result = subprocess.run(
                [
                    'ffprobe', '-v', 'quiet', '-print_format', 'json',
                    '-show_format', '-show_streams', temp_file_path
                ],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
            )

            if result.returncode != 0:
                raise RuntimeError(f"ffprobe error: {result.stderr}")

            info = json.loads(result.stdout)
            formatted_info = {
                "status": "success",
                "filename": filename,
                "format": {
                    "format_name": info.get("format", {}).get("format_name", "unknown"),
                    "duration": float(info.get("format", {}).get("duration", 0)),
                    "size": int(info.get("format", {}).get("size", 0)),
                    "bit_rate": int(info.get("format", {}).get("bit_rate", 0))
                },
                "streams": []
            }

            for stream in info.get("streams", []):
                stream_info = {
                    "index": stream.get("index"),
                    "codec_type": stream.get("codec_type"),
                    "codec_name": stream.get("codec_name")
                }
                if stream.get("codec_type") == "video":
                    stream_info.update({
                        "width": stream.get("width"),
                        "height": stream.get("height"),
                        "r_frame_rate": stream.get("r_frame_rate"),
                        "display_aspect_ratio": stream.get("display_aspect_ratio", "unknown")
                    })
                elif stream.get("codec_type") == "audio":
                    stream_info.update({
                        "sample_rate": stream.get("sample_rate"),
                        "channels": stream.get("channels"),
                        "channel_layout": stream.get("channel_layout", "unknown")
                    })

                formatted_info["streams"].append(stream_info)

            # 构建文本摘要
            duration_sec = formatted_info["format"]["duration"]
            summary = f"""🎬 **{filename} 视频信息摘要**
格式: {formatted_info['format']['format_name']}
时长: {int(duration_sec // 60)}分 {int(duration_sec % 60)}秒
大小: {formatted_info['format']['size'] / (1024 * 1024):.2f} MB
平均码率: {formatted_info['format']['bit_rate'] / 1000:.2f} kbps
"""
            for s in formatted_info["streams"]:
                if s["codec_type"] == "video":
                    summary += f"📹 视频流: {s.get('width')}x{s.get('height')} / 编码: {s.get('codec_name')}\n"
                elif s["codec_type"] == "audio":
                    summary += f"🔊 音频流: 编码: {s.get('codec_name')} / 采样率: {s.get('sample_rate', '未知')}\n"

            yield self.create_text_message(summary.strip())
            yield self.create_json_message(formatted_info)

        except Exception as e:
            msg = f"❌ 处理视频失败: {str(e)}"
            yield self.create_text_message(msg)
            yield self.create_json_message({
                "status": "error",
                "message": msg
            })
        finally:
            if temp_file_path and os.path.exists(temp_file_path):
                os.unlink(temp_file_path)
