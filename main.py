import os
import json
import re
import base64
import mimetypes
import aiofiles
import random
import shutil
from typing import Dict, Optional, List
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api import logger
from astrbot.core.message.components import Image
from astrbot.core.message.message_event_result import MessageChain
from astrbot.core.provider.entities import LLMResponse, ProviderRequest


@register(
    "astrbot_plugin_meme_manager_lite",
    "ctrlkk",
    "允许LLM在回答中使用表情包 轻量级！",
    "1.0.0",
)
class MyPlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        self.config = context.get_config()
        self.max_memes_per_message = self.config.get("max_memes_per_message", 1)

        self.PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
        self.DATA_DIR = os.path.normpath(
            os.path.join(
                self.PLUGIN_DIR, "..", "..", "plugins_data", "meme_manager_lite_data"
            )
        )
        self.MEMES_DIR = os.path.join(self.DATA_DIR, "memes")
        self.MEMES_DATA_FILE = os.path.join(self.DATA_DIR, "memes_data.json")
        # 表情包名称到描述的映射
        self.memes_data: Dict[str, str] = {}

    async def initialize(self):
        """可选择实现异步的插件初始化方法，当实例化该插件类之后会自动调用该方法。"""
        self._init_default_config()
        self._load_memes_data()
        logger.info("表情包管理器插件已初始化")

    async def terminate(self):
        """可选择实现异步的插件销毁方法，当插件被卸载/停用时会调用。"""
        logger.info("表情包管理器插件已停止")

    def _init_default_config(self):
        """初始化默认配置，如果配置文件不存在则复制默认配置"""
        try:
            os.makedirs(self.DATA_DIR, exist_ok=True)

            if not os.path.exists(self.MEMES_DATA_FILE):
                default_config_path = os.path.join(
                    self.PLUGIN_DIR, "default", "mees_data.json"
                )
                if os.path.exists(default_config_path):
                    shutil.copy2(default_config_path, self.MEMES_DATA_FILE)
                    logger.info(
                        f"已从默认配置复制表情包数据文件到 {self.MEMES_DATA_FILE}"
                    )
                else:
                    logger.error("默认配置文件也不存在，创建空配置文件")
                    # 创建空的配置文件
                    with open(self.MEMES_DATA_FILE, "w", encoding="utf-8") as f:
                        json.dump({}, f, ensure_ascii=False, indent=2)

            if not os.path.exists(self.MEMES_DIR):
                default_memes_dir = os.path.join(self.PLUGIN_DIR, "default", "memes")
                if os.path.exists(default_memes_dir):
                    os.makedirs(self.MEMES_DIR, exist_ok=True)
                    for meme_name in os.listdir(default_memes_dir):
                        default_meme_dir = os.path.join(default_memes_dir, meme_name)
                        target_meme_dir = os.path.join(self.MEMES_DIR, meme_name)

                        if os.path.isdir(default_meme_dir) and not os.path.exists(
                            target_meme_dir
                        ):
                            shutil.copytree(default_meme_dir, target_meme_dir)
                            logger.info(
                                f"已从默认配置复制表情包目录 {meme_name} 到 {target_meme_dir}"
                            )
                else:
                    logger.error("默认表情包目录也不存在")

        except Exception as e:
            logger.error(f"初始化默认配置失败: {e}")

    def _load_memes_data(self):
        """加载表情包数据"""
        try:
            if os.path.exists(self.MEMES_DATA_FILE):
                with open(self.MEMES_DATA_FILE, "r", encoding="utf-8") as f:
                    self.memes_data = json.load(f)
                logger.info(f"已加载 {len(self.memes_data)} 个表情包数据")
            else:
                logger.warning("表情包数据文件不存在，使用空配置")
                self.memes_data = {}
        except json.JSONDecodeError as e:
            logger.error(f"表情包数据文件格式错误: {e}")
            self.memes_data = {}
        except Exception as e:
            logger.error(f"加载表情包数据失败: {e}")
            self.memes_data = {}

    def _get_meme_image_path(self, meme_name: str) -> Optional[str]:
        """获取表情包图片路径，存在多张图片时随机选择"""
        meme_dir = os.path.join(self.MEMES_DIR, meme_name)
        if os.path.exists(meme_dir):
            try:
                image_files = []
                for file in os.listdir(meme_dir):
                    if file.lower().endswith(
                        (".png", ".jpg", ".jpeg", ".gif", ".webp")
                    ):
                        image_files.append(os.path.join(meme_dir, file))
                if image_files:
                    return random.choice(image_files)
            except Exception as e:
                logger.error(f"读取表情包目录失败: {e}")
        return None

    async def _image_to_dataurl(self, image_path: str) -> Optional[str]:
        """异步将图片文件转换为 dataurl 格式"""
        try:
            if not os.path.exists(image_path):
                logger.error(f"图片文件不存在: {image_path}")
                return None
            # 获取图片的 MIME 类型
            mime_type, _ = mimetypes.guess_type(image_path)
            if not mime_type:
                # 如果无法猜测 MIME 类型，使用默认值
                mime_type = "image/jpeg"
            async with aiofiles.open(image_path, "rb") as image_file:
                image_data = await image_file.read()
                base64_data = base64.b64encode(image_data).decode("utf-8")
            dataurl = f"data:{mime_type};base64,{base64_data}"
            return dataurl
        except Exception as e:
            logger.error(f"转换图片为 dataurl 失败: {e}")
            return None

    def _parse_emoji_tags(self, text: str) -> List[str]:
        """解析文本中的表情包标签"""
        pattern = r'<emoji\s+name="([^"]+)"/>'
        matches = re.findall(pattern, text)
        return matches

    def _remove_emoji_tags(self, text: str) -> str:
        """移除文本中的表情包标签"""
        pattern = r'<emoji\s+name="[^"]+"/>'
        return re.sub(pattern, "", text).strip()

    def _generate_meme_list(self) -> str:
        """生成表情包清单"""
        meme_list = []
        for name, description in self.memes_data.items():
            meme_list.append(f"- [{name}]：{description}")

        return "\n".join(meme_list)

    @filter.on_llm_request()
    async def on_llm_req(self, event: AstrMessageEvent, req: ProviderRequest):
        meme_list = self._generate_meme_list()
        instruction_prompt = f"""
在回答用户问题时，你可以在自然语言的基础上，使用表情包来增强表达效果。

「表情包清单」：
{meme_list}

使用规则：
1. 你只能使用清单中提供的表情包名字。
2. 当你需要使用表情时，请在回答中插入如下 XML 标签：
   <emoji name="表情包名字"/>
3. 你可以在回答中插入 0 个或多个 <emoji> 标签，但每条消息最多使用 {self.max_memes_per_message} 个表情包。
4. 回答应保持自然流畅，表情是辅助，不要过度使用。
5. 输出的 XML 标签会被解析为真正的表情图片。

示例：
（假设清单为：
- [smile]：开心、大笑
- [sad]：伤心、需要安慰
- [ok]：同意、没问题
）

- 用户说了一个好消息 → 输出：
  "太棒了！<emoji name="smile"/>"
- 用户说了坏消息 → 输出：
  "别灰心，我们一起想办法。<emoji name="sad"/>"
- 用户请求确认 → 输出：
  "好的，没问题。<emoji name="ok"/>"
"""
        req.system_prompt += f"\n\n{instruction_prompt}"

    @filter.on_llm_response()
    async def on_llm_resp(self, event: AstrMessageEvent, resp: LLMResponse):
        """处理 LLM 响应，解析表情包标签"""
        emoji_path_urls = []
        if resp.completion_text:
            emoji_tags = self._parse_emoji_tags(resp.completion_text)

            if emoji_tags:
                resp.completion_text = self._remove_emoji_tags(resp.completion_text)
                for meme_name in emoji_tags:
                    image_path = self._get_meme_image_path(meme_name)
                    if image_path:
                        emoji_path_urls.append(image_path)
                    else:
                        logger.warning(f"找不到表情包图片: {meme_name}")

        emoji_path_urls = emoji_path_urls[: self.max_memes_per_message]
        event.set_extra("emoji_path_urls", emoji_path_urls)

    @filter.on_decorating_result()
    async def on_decorating_result(self, event: AstrMessageEvent):
        pass

    @filter.after_message_sent()
    async def after_message_sent(self, event: AstrMessageEvent):
        emoji_path_urls = event.get_extra("emoji_path_urls") or []
        for emoji in emoji_path_urls:
            # emoji_dataurl = await self._image_to_dataurl(emoji)
            await self.context.send_message(
                event.unified_msg_origin,
                MessageChain([Image.fromFileSystem(emoji)]),
            )
