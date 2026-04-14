# src/models.py (Final Corrected Version)

from pydantic import BaseModel, Field, ConfigDict, model_validator
from typing import List, Literal, Optional
import uuid

class AdvancedFilterRule(BaseModel):
    field: str
    operator: Literal[
        "equals", "not_equals",
        "contains", "not_contains",
        "greater_than", "less_than",
        "is_empty", "is_not_empty"
    ]
    value: Optional[str] = None
    relative_days: Optional[int] = None # 新增：用于存储相对日期（例如 30 天）

class AdvancedFilter(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str
    match_all: bool = Field(default=True)
    rules: List[AdvancedFilterRule] = Field(default_factory=list)

class VirtualLibrary(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    # 创建时由 admin_server 写入 next_virtual_library_id；默认空串供 POST 体省略 id
    id: str = Field(default="")
    name: str
    resource_type: Literal["collection", "tag", "genre", "studio", "person", "all"]
    resource_id: Optional[str] = None
    image_tag: Optional[str] = None
    advanced_filter_id: Optional[str] = None
    order: int = 0
    source_library: Optional[str] = None
    conditions: Optional[list] = None
    cover_custom_zh_font_path: Optional[str] = Field(default=None) # <-- 【新增】海报自定义中文字体
    cover_custom_en_font_path: Optional[str] = Field(default=None) # <-- 【新增】海报自定义英文字体
    cover_custom_image_path: Optional[str] = Field(default=None) # <-- 【新增】海报自定义图片目录

class AppConfig(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    @model_validator(mode="before")
    @classmethod
    def _null_lists_to_empty(cls, data):
        """JSON 里常见 null 列表，规范为 []（不做旧字段迁移）。"""
        if not isinstance(data, dict):
            return data
        for key in ("display_order", "hide", "library", "advanced_filters"):
            if data.get(key) is None:
                data[key] = []
        return data

    emby_url: str = Field(default="http://127.0.0.1:8096")
    emby_api_key: Optional[str] = Field(default="")
    log_level: Literal["debug", "info", "warn", "error"] = Field(default="info")
    display_order: List[str] = Field(default_factory=list)
    hide: List[str] = Field(default_factory=list)
    
    # 使用别名 'library' 来兼容旧的 config.json
    virtual_libraries: List[VirtualLibrary] = Field(
        default_factory=list, 
        alias="library",
        validation_alias="library" # <-- 【新增】确保加载时也优先用 'library'
    )
    
    # 明确定义 advanced_filters，不使用任何复杂的配置
    advanced_filters: List[AdvancedFilter] = Field(default_factory=list)

    # 新增：缓存开关
    enable_cache: bool = Field(default=True)
    
    # 新增：自动生成封面的默认样式
    default_cover_style: str = Field(default='style_multi_1')

    # 新增：自定义字体路径
    custom_zh_font_path: Optional[str] = Field(default="")
    custom_en_font_path: Optional[str] = Field(default="")
    custom_image_path: Optional[str] = Field(default="") # <-- 【新增】全局自定义图片目录
