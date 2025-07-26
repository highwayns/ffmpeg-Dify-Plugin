from collections.abc import Generator
from typing import Any
import tempfile
import os
import time
import ffmpeg
import requests

from dify_plugin import Tool
from dify_plugin.entities.tool import ToolInvokeMessage

def get_field(obj, field, default=None):
    """兼容 dict 和对象属性两种方式取字段"""
    if isinstance(obj, dict):
        return obj.get(field, default)
    return getattr(obj, field, default)

class ExtractAudioTool(Tool):
    def _invoke(self, tool_parameters: dict[str, Any]) -> Generator[ToolInvokeMessage, None, None]:
        video_meta = tool_parameters.get("video") or {}

        # 兼容 dict 和对象
        transfer_method = get_field(video_meta, "transfer_method", "local_file")
        filename = get_field(video_meta, "filename", "video.mp4")
        file_extension = get_field(video_meta, "extension", ".mp4")
        file_url = get_field(video_meta, "url", "")
        mime_type = get_field(video_meta, "mime_type", "video/mp4")
        audio_format = tool_parameters.get("audio_format", "mp3").lower()

        valid_formats = ['mp3', 'aac', 'wav', 'ogg', 'flac']
        if audio_format not in valid_formats:
            yield self.create_text_message(f"⚠️ 不支持的音频格式: {audio_format}，将自动转为 'mp3'")
            audio_format = 'mp3'

        mime_types = {
            'mp3': 'audio/mpeg',
            'aac': 'audio/aac',
            'wav': 'audio/wav',
            'ogg': 'audio/ogg',
            'flac': 'audio/flac'
        }

        try:
            if transfer_method == "remote_url":
                if not file_url or not file_url.startswith(("http://", "https://")):
                    raise ValueError("无效的远程 URL：缺少 http(s) 协议")
                response = requests.get(file_url)
                if response.status_code != 200:
                    raise ValueError(f"视频文件下载失败：{file_url}")
                file_bytes = response.content
                filename_from_url = os.path.basename(file_url.split("?")[0])
                file_extension = os.path.splitext(filename_from_url)[-1] or ".mp4"
                if not filename:
                    filename = filename_from_url

            elif transfer_method == "local_file":
                if not file_url or not file_url.startswith("/files/"):
                    raise ValueError("无效的本地文件 URL")
                base_url = os.environ.get("DIFY_BASE_URL", "http://api:5001")
                full_url = base_url + file_url
                response = requests.get(full_url)
                if response.status_code != 200:
                    raise ValueError(f"无法下载本地文件：{file_url}")
                file_bytes = response.content
                # filename, file_extension 已从 video_meta 获取，无需重复

            else:
                raise ValueError(f"不支持的 transfer_method: {transfer_method}")

            orig_filename = os.path.splitext(filename)[0]
            output_filename = f"{orig_filename}.{audio_format}"

            with tempfile.NamedTemporaryFile(delete=False, suffix=file_extension) as in_temp_file:
                in_temp_file.write(file_bytes)
                in_temp_path = in_temp_file.name

            out_temp_path = os.path.join(tempfile.gettempdir(), f"audio_{int(time.time())}.{audio_format}")

            try:
                yield self.create_text_message(f"🔄 正在提取音频为 `{audio_format}` 格式...")

                (
                    ffmpeg
                    .input(in_temp_path)
                    .output(out_temp_path, acodec=self._get_codec_for_format(audio_format))
                    .run(capture_stdout=True, capture_stderr=True, overwrite_output=True)
                )

                with open(out_temp_path, 'rb') as out_file:
                    audio_data = out_file.read()

                audio_size = os.path.getsize(out_temp_path)

                yield self.create_blob_message(
                    audio_data,
                    meta={
                        "filename": output_filename,
                        "mime_type": mime_types.get(audio_format, f"audio/{audio_format}")
                    }
                )

                yield self.create_json_message({
                    "status": "success",
                    "message": f"成功提取音频为 {audio_format}",
                    "original_filename": filename,
                    "audio_filename": output_filename,
                    "audio_format": audio_format,
                    "audio_size": audio_size
                })

                summary = f"✅ 已成功提取音频：{output_filename}\n格式: {audio_format}\n大小: {audio_size / (1024 * 1024):.2f} MB"
                yield self.create_text_message(summary)

            finally:
                if os.path.exists(in_temp_path):
                    os.unlink(in_temp_path)
                if os.path.exists(out_temp_path):
                    os.unlink(out_temp_path)

        except Exception as e:
            error_msg = f"❌ 处理视频文件时出错: {str(e)}"
            yield self.create_text_message(error_msg)
            yield self.create_json_message({
                "status": "error",
                "message": error_msg
            })

    def _get_codec_for_format(self, audio_format: str) -> str:
        codecs = {
            'mp3': 'libmp3lame',
            'aac': 'aac',
            'wav': 'pcm_s16le',
            'ogg': 'libvorbis',
            'flac': 'flac'
        }
        return codecs.get(audio_format, 'copy')
