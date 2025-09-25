import os
import json
import base64
import mimetypes
import re
import aiofiles
import random
import shutil
from typing import Dict, Optional, List
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api import logger
from astrbot.core.message.components import Image, Plain
from astrbot.core.message.message_event_result import MessageChain
from astrbot.core.provider.entities import LLMResponse, ProviderRequest
from astrbot.core.star.star_tools import StarTools


@register(
    "astrbot_plugin_meme_manager_lite",
    "ctrlkk",
    "允许LLM在回答中使用表情包 轻量级！",
    "1.1",
)
class StickerManagerLitePlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        self.config = context.get_config()
        self.max_stickers_per_message = self.config.get("max_stickers_per_message", 1)
        self.sticker_score_threshold = self.config.get("sticker_score_threshold", 0.8)

        self.PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
        self.DATA_DIR = os.path.normpath(StarTools.get_data_dir())
        self.STICKERS_DIR = os.path.join(self.DATA_DIR, "memes")
        self.STICKERS_DATA_FILE = os.path.join(self.DATA_DIR, "memes_data.json")
        # 贴纸名称到描述的映射
        self.stickers_data: Dict[str, str] = {}

    async def initialize(self):
        """可选择实现异步的插件初始化方法，当实例化该插件类之后会自动调用该方法。"""
        self._init_default_config()
        self._load_stickers_data()
        logger.info("贴纸管理器插件已初始化")

    async def terminate(self):
        """可选择实现异步的插件销毁方法，当插件被卸载/停用时会调用。"""
        logger.info("贴纸管理器插件已停止")

    def _init_default_config(self):
        """初始化默认配置，如果配置文件不存在则复制默认配置"""
        try:
            os.makedirs(self.DATA_DIR, exist_ok=True)

            if not os.path.exists(self.STICKERS_DATA_FILE):
                default_config_path = os.path.join(
                    self.PLUGIN_DIR, "default", "memes_data.json"
                )
                if os.path.exists(default_config_path):
                    shutil.copy2(default_config_path, self.STICKERS_DATA_FILE)
                else:
                    logger.error("默认配置文件不存在，创建空配置文件")
                    # 创建空的配置文件
                    with open(self.STICKERS_DATA_FILE, "w", encoding="utf-8") as f:
                        json.dump({}, f, ensure_ascii=False, indent=2)

            if not os.path.exists(self.STICKERS_DIR):
                default_stickers_dir = os.path.join(self.PLUGIN_DIR, "default", "memes")
                if os.path.exists(default_stickers_dir):
                    os.makedirs(self.STICKERS_DIR, exist_ok=True)
                    for sticker_name in os.listdir(default_stickers_dir):
                        default_sticker_dir = os.path.join(
                            default_stickers_dir, sticker_name
                        )
                        target_sticker_dir = os.path.join(
                            self.STICKERS_DIR, sticker_name
                        )

                        if os.path.isdir(default_sticker_dir) and not os.path.exists(
                            target_sticker_dir
                        ):
                            shutil.copytree(default_sticker_dir, target_sticker_dir)
                else:
                    logger.error("默认贴纸目录不存在")

        except Exception as e:
            logger.error(f"初始化默认配置失败: {e}")

    def _load_stickers_data(self):
        """加载贴纸数据"""
        try:
            if os.path.exists(self.STICKERS_DATA_FILE):
                with open(self.STICKERS_DATA_FILE, "r", encoding="utf-8") as f:
                    self.stickers_data = json.load(f)
                logger.info(f"已加载 {len(self.stickers_data)} 个贴纸数据")
            else:
                logger.warning("贴纸数据文件不存在，使用空配置")
                self.stickers_data = {}
        except json.JSONDecodeError as e:
            logger.error(f"贴纸数据文件格式错误: {e}")
            self.stickers_data = {}
        except Exception as e:
            logger.error(f"加载贴纸数据失败: {e}")
            self.stickers_data = {}

    def _get_sticker_image_path(self, sticker_name: str) -> Optional[str]:
        """获取贴纸图片路径，存在多张图片时随机选择"""
        sticker_dir = os.path.join(self.STICKERS_DIR, sticker_name)
        if os.path.exists(sticker_dir):
            try:
                image_files = []
                for file in os.listdir(sticker_dir):
                    if file.lower().endswith(
                        (".png", ".jpg", ".jpeg", ".gif", ".webp")
                    ):
                        image_files.append(os.path.join(sticker_dir, file))
                if image_files:
                    return random.choice(image_files)
            except Exception as e:
                logger.error(f"读取贴纸目录失败: {e}")
        return None

    async def _image_to_data_url(self, image_path: str) -> Optional[str]:
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

    def _parse_sticker_tags(self, text: str) -> List[Dict[str, any]]:
        """解析文本中的贴纸标签，包含名称和分数"""
        pattern = r'<sticker\s+name="([^"]+)"\s+score="([^"]+)"/>'
        matches = re.findall(pattern, text)
        result = []
        for name, score_str in matches:
            try:
                score = float(score_str)
                # 确保分数在0-1范围内
                score = max(0.0, min(1.0, score))
                result.append({"name": name, "score": score})
            except ValueError:
                # 如果分数无法解析，使用默认分数0.5
                result.append({"name": name, "score": 0.5})
        return result

    def _remove_sticker_tags(self, text: str) -> str:
        """移除文本中的贴纸标签"""
        pattern = r'<sticker\s+name="[^"]+"\s+score="[^"]+"/>'
        return re.sub(pattern, "", text).strip()

    def _generate_sticker_list(self) -> str:
        """生成贴纸清单"""
        sticker_list = []
        for name, description in self.stickers_data.items():
            sticker_list.append(f"- [{name}]：{description}")

        return "\n".join(sticker_list)

    @filter.on_llm_request()
    async def on_llm_req(self, event: AstrMessageEvent, req: ProviderRequest):
        sticker_list = self._generate_sticker_list()
        instruction_prompt = f"""
在回答用户问题时，你可以在自然语言的基础上，使用贴纸来增强表达效果。

「贴纸清单」：
{sticker_list}

使用规则：
1. 你只能使用清单中提供的贴纸名字。
2. 当你需要使用贴纸时，请在回答中插入如下 XML 标签：
   <sticker name="贴纸名字" score="分数"/>
   其中"分数"是一个0到1之间的数值，表示该贴纸在当前回答中的合适度，1表示非常合适，0表示完全不合适。
3. 你可以在回答中插入 0 个或多个 <sticker> 标签，但每条消息最多使用 {self.max_stickers_per_message} 个贴纸。
4. 回答应保持自然流畅，贴纸是辅助，不要过度使用。
5. 请根据贴纸描述和回答内容的匹配度、情感一致性等因素来评估分数。

示例：
（假设清单为：
- [smile]：开心、大笑
- [sad]：伤心、需要安慰
- [ok]：同意、没问题
）

- 用户说了一个好消息 → 输出：
  "太棒了！<sticker name="smile" score="0.9"/>"
- 用户说了坏消息 → 输出：
  "别灰心，我们一起想办法。<sticker name="sad" score="0.8"/>"
- 用户请求确认 → 输出：
  "好的，没问题。<sticker name="ok" score="0.7"/>"
"""
        req.system_prompt += f"\n\n{instruction_prompt}"

    def _parse_sticker(self, completion_text: str):
        sticker_tags = self._parse_sticker_tags(completion_text)
        sticker_image_paths = []
        if sticker_tags:
            qualified_stickers = []
            for sticker_info in sticker_tags:
                sticker_name = sticker_info["name"]
                score = sticker_info["score"]

                # 检查分数是否达到阈值
                if score >= self.sticker_score_threshold:
                    qualified_stickers.append((sticker_name, score))

            # 获取贴纸图片路径，限制数量
            for sticker_name, score in qualified_stickers[
                : self.max_stickers_per_message
            ]:
                image_path = self._get_sticker_image_path(sticker_name)
                if image_path:
                    sticker_image_paths.append(image_path)
                else:
                    logger.warning(f"找不到贴纸图片: {sticker_name}")
        return sticker_image_paths

    @filter.on_llm_response()
    async def on_llm_resp(self, event: AstrMessageEvent, resp: LLMResponse):
        """处理 LLM 响应，解析贴纸标签并根据分数筛选"""
        # sticker_image_paths = []
        # if resp.completion_text:
        # sticker_image_paths = self._parse_sticker(resp.completion_text)
        # resp.completion_text = self._remove_sticker_tags(resp.completion_text)
        # event.set_extra("sticker_image_paths", sticker_image_paths)

    @filter.on_decorating_result()
    async def on_decorating_result(self, event: AstrMessageEvent):
        """"""
        result = event.get_result()
        chain = result.chain
        new_chain = []
        for item in chain:
            if isinstance(item, Plain):
                components = await self._process_text_with_sticker(item.text)
                new_chain.extend(components)
            else:
                new_chain.append(item)

        result.chain = new_chain

    async def _process_text_with_sticker(self, text: str):
        """处理包含sticker标签的文本，将其拆分成Plain、Image、Plain的格式"""
        components = []

        try:
            # 文本切割
            pattern = r"(<sticker.*?/>)"
            parts = re.split(pattern, text, flags=re.DOTALL)

            for part in parts:
                part = part.strip()
                if not part:
                    continue

                if re.match(r"<sticker.*?/>", part):
                    sticker_name = None
                    score = 0.5

                    # 尝试提取name属性
                    name_pattern = r'name="(.*?)"'
                    name_match = re.search(name_pattern, part)
                    if name_match:
                        sticker_name = name_match.group(1)

                    # 尝试提取score属性
                    score_pattern = r'score="(.*?)"'
                    score_match = re.search(score_pattern, part)
                    if score_match:
                        try:
                            score_str = score_match.group(1)
                            score = float(score_str)
                            # 确保分数在0-1范围内
                            score = max(0.0, min(1.0, score))
                        except ValueError:
                            score = 0.5

                    # 如果找到了sticker名称
                    if sticker_name:
                        if score >= self.sticker_score_threshold:
                            image_path = self._get_sticker_image_path(sticker_name)
                            if image_path:
                                components.append(Image.fromFileSystem(image_path))
                else:
                    components.append(Plain(part))

        except Exception as e:
            logger.error(f"处理文本和sticker标签时出错: {e}")
            if text.strip():
                components.append(Plain(text.strip()))

        return components

    @filter.after_message_sent()
    async def after_message_sent(self, event: AstrMessageEvent):
        sticker_image_paths = event.get_extra("sticker_image_paths") or []
        for sticker_image_path in sticker_image_paths:
            # sticker_dataurl = await self._image_to_data_url(sticker_image_path)
            await self.context.send_message(
                event.unified_msg_origin,
                MessageChain([Image.fromFileSystem(sticker_image_path)]),
            )
