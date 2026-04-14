# src/admin_server.py

import uvicorn
from fastapi import FastAPI, APIRouter, HTTPException, Response, Query, File, UploadFile
from fastapi.responses import RedirectResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pathlib import Path
import uuid
import random
import re
import shutil
import os
import aiohttp
import asyncio
import sys
import hashlib
import time
from typing import List, Dict, Optional
from pydantic import BaseModel
import importlib
# 导入封面生成模块
# from cover_generator import style_multi_1 # 改为动态导入
import base64
from PIL import Image
from io import BytesIO
import urllib.error
import urllib.request
from .models import AppConfig, VirtualLibrary, AdvancedFilter
from . import config_manager
from .library_id import next_virtual_library_id

# 【【【 在这里添加或者确认你有这几行 】】】
import logging

# 设置日志记录器
logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
# 【【【 添加/确认结束 】】】

# Repository root: parent of this admin/ package (sibling: proxy/, web/, config/)
REPO_ROOT = Path(__file__).resolve().parent.parent

admin_app = FastAPI(title="Emby Virtual Proxy - Admin API")
api_router = APIRouter(prefix="/api")


class CoverRequest(BaseModel):
    library_id: str
    title_zh: str  # 之前是 library_name，现在改为 title_zh
    title_en: Optional[str] = None
    style_name: str  # 新增：用于指定封面样式
    temp_image_paths: Optional[List[str]] = None


# --- 高级筛选器 API ---
@api_router.get("/advanced-filters", response_model=List[AdvancedFilter], tags=["Advanced Filters"])
async def get_advanced_filters():
    """获取所有高级筛选器规则"""
    return config_manager.load_config().advanced_filters


@api_router.post("/advanced-filters", status_code=204, tags=["Advanced Filters"])
async def save_advanced_filters(filters: List[AdvancedFilter]):
    """保存所有高级筛选器规则"""
    # 这是一个全新的、更健壮的实现
    try:
        # 1. 加载当前配置的原始字典数据
        current_config_dict = config_manager.load_config().model_dump()

        # 2. 将接收到的筛选器（它们是Pydantic模型）转换为字典列表
        filters_dict_list = [f.model_dump() for f in filters]

        # 3. 更新字典中的 'advanced_filters' 键
        current_config_dict['advanced_filters'] = filters_dict_list

        # 4. 使用更新后的完整字典来验证并创建一个新的 AppConfig 模型
        new_config = AppConfig.model_validate(current_config_dict)

        # 5. 保存这个全新的、有效的配置对象
        config_manager.save_config(new_config)

        return Response(status_code=204)
    except Exception as e:
        # 打印详细错误以供调试
        print(f"保存高级筛选器时发生严重错误: {e}")
        raise HTTPException(status_code=500, detail=str(e))


def _vlib_emby_param_key(resource_type: str) -> Optional[str]:
    return {
        "collection": "ParentId",
        "tag": "TagIds",
        "genre": "GenreIds",
        "studio": "StudioIds",
        "person": "PersonIds",
    }.get(resource_type)


def _vlib_emby_recursive(resource_type: str) -> bool:
    return resource_type != "collection"


async def _fetch_images_from_vlib(library_id: str, temp_dir: Path, config: AppConfig):
    """
    按虚拟库规则直接请求 Emby /Users/{id}/Items，取带主封面的条目并下载素材（与 Go 代理逻辑一致，不依赖 Python 代理缓存）。
    """
    logger.info(f"开始从 Emby 为虚拟库 {library_id} 获取封面素材...")

    vlib = next((lib for lib in config.virtual_libraries if lib.id == library_id), None)
    if not vlib:
        raise HTTPException(status_code=404, detail="虚拟库不存在。")
    param_key = _vlib_emby_param_key(vlib.resource_type)
    if param_key and not (vlib.resource_id or "").strip():
        raise HTTPException(status_code=400, detail="该虚拟库未配置 resource_id，无法从 Emby 拉取条目。")

    users = await _fetch_from_emby("/Users")
    if not users:
        raise HTTPException(status_code=500, detail="无法从 Emby 获取用户列表。")
    ref_user_id = users[0].get("Id")
    if not ref_user_id:
        raise HTTPException(status_code=500, detail="Emby 用户数据无效。")

    params: Dict[str, str] = {
        "IncludeItemTypes": "Movie,Series,Video,Game,MusicAlbum,Episode",
        "ImageTypeLimit": "1",
        "Fields": "BasicSyncInfo,PrimaryImageAspectRatio",
        "EnableTotalRecordCount": "true",
    }
    if _vlib_emby_recursive(vlib.resource_type):
        params["Recursive"] = "true"
    if param_key and vlib.resource_id:
        params[param_key] = vlib.resource_id

    endpoint = f"/Users/{ref_user_id}/Items"
    raw = await _fetch_from_emby(endpoint, params)
    if isinstance(raw, dict):
        items = raw.get("Items") or []
    elif isinstance(raw, list):
        items = raw
    else:
        items = []

    items_with_images = [item for item in items if item.get("ImageTags", {}).get("Primary")]

    if not items_with_images:
        raise HTTPException(status_code=404, detail="Emby 返回的条目中不包含任何带主封面的项目。")

    selected_items = random.sample(items_with_images, min(9, len(items_with_images)))

    # --- 并发下载图片 ---
    async def download_image(session, item, index):
        image_url = f"{config.emby_url.rstrip('/')}/emby/Items/{item['Id']}/Images/Primary"
        headers = {'X-Emby-Token': config.emby_api_key}
        try:
            async with session.get(image_url, headers=headers, timeout=20) as response:
                if response.status == 200:
                    content = await response.read()
                    image_path = temp_dir / f"{index}.jpg"
                    with open(image_path, "wb") as f:
                        f.write(content)
                    return True
        except Exception:
            return False

    async with aiohttp.ClientSession() as session:
        tasks = [download_image(session, item, i + 1) for i, item in enumerate(selected_items)]
        results = await asyncio.gather(*tasks)

    if not any(results):
        raise HTTPException(status_code=500, detail="所有封面素材下载失败，无法生成海报。")


@api_router.post("/upload_temp_image", tags=["Cover Generator"])
async def upload_temp_image(file: UploadFile = File(...)):
    TEMP_IMAGE_DIR = REPO_ROOT / "config" / "temp_images"
    TEMP_IMAGE_DIR.mkdir(exist_ok=True)

    # Sanitize filename
    filename = re.sub(r'[^a-zA-Z0-9._-]', '', file.filename)
    unique_filename = f"{uuid.uuid4()}_{filename}"
    file_path = TEMP_IMAGE_DIR / unique_filename

    try:
        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)

        # Return the path that the frontend can use
        return {"path": str(file_path)}
    except Exception as e:
        logger.error(f"上传临时图片失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="文件上传处理失败。")


async def _fetch_images_from_custom_path(custom_path: str, temp_dir: Path):
    """从自定义目录随机复制图片到临时目录"""
    logger.info(f"开始从自定义目录 {custom_path} 获取封面素材...")

    source_dir = Path(custom_path)
    if not source_dir.is_dir():
        raise HTTPException(status_code=404, detail=f"自定义图片目录不存在: {custom_path}")

    supported_formats = (".jpg", ".jpeg", ".png", ".bmp", ".gif", ".webp")
    image_files = [f for f in source_dir.iterdir() if f.is_file() and f.suffix.lower() in supported_formats]

    if not image_files:
        raise HTTPException(status_code=404, detail=f"自定义图片目录中未找到支持的图片文件: {custom_path}")

    selected_files = random.sample(image_files, min(9, len(image_files)))

    for i, file_path in enumerate(selected_files):
        try:
            dest_path = temp_dir / f"{i + 1}{file_path.suffix}"
            shutil.copy(file_path, dest_path)
        except Exception as e:
            logger.error(f"复制文件 {file_path} 时出错: {e}")
            continue

    # 检查是否至少成功复制了一张图片
    if not any(temp_dir.iterdir()):
        raise HTTPException(status_code=500, detail="从自定义目录复制图片素材失败。")


# --- 辅助函数：健壮地获取 Emby 数据 ---
async def _fetch_from_emby(endpoint: str, params: Dict = None) -> List:
    config = config_manager.load_config()
    if not config.emby_url or not config.emby_api_key:
        raise HTTPException(status_code=400, detail="请在系统设置中配置Emby服务器地址和API密钥。")

    headers = {'X-Emby-Token': config.emby_api_key, 'Accept': 'application/json'}
    url = f"{config.emby_url.rstrip('/')}/emby{endpoint}"

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers, params=params, timeout=15) as response:
                if response.status != 200:
                    error_text = await response.text()
                    logger_msg = f"从Emby获取数据失败 (Endpoint: {endpoint}, Status: {response.status}): {error_text}"
                    print(f"[PROXY-ADMIN-ERROR] {logger_msg}")
                    raise HTTPException(status_code=response.status, detail=logger_msg)

                json_response = await response.json()
                if isinstance(json_response, dict):
                    return json_response.get("Items", json_response)
                elif isinstance(json_response, list):
                    return json_response
                else:
                    return []
    except aiohttp.ClientError as e:
        logger_msg = f"连接到Emby时发生网络错误 (Endpoint: {endpoint}): {e}"
        print(f"[PROXY-ADMIN-ERROR] {logger_msg}")
        raise HTTPException(status_code=502, detail=logger_msg)
    except Exception as e:
        logger_msg = f"处理Emby请求时发生未知错误 (Endpoint: {endpoint}): {e}"
        print(f"[PROXY-ADMIN-ERROR] {logger_msg}")
        raise HTTPException(status_code=500, detail=logger_msg)


async def get_real_libraries_hybrid_mode() -> List:
    all_real_libs = {}
    try:
        media_folders = await _fetch_from_emby("/Library/MediaFolders")
        for lib in media_folders:
            lib_id = lib.get("Id")
            if lib_id:
                all_real_libs[lib_id] = {
                    "Id": lib_id, "Name": lib.get("Name"), "CollectionType": lib.get("CollectionType")
                }
    except HTTPException as e:
        print(f"[PROXY-ADMIN-WARNING] 从 /Library/MediaFolders 获取数据失败: {e.detail}")

    try:
        user_items = await _fetch_from_emby("/Users")
        if user_items:
            ref_user_id = user_items[0].get("Id")
            if ref_user_id:
                views = await _fetch_from_emby(f"/Users/{ref_user_id}/Views")
                for lib in views:
                    lib_id = lib.get("Id")
                    if lib_id and lib_id not in all_real_libs:
                        all_real_libs[lib_id] = {
                            "Id": lib_id, "Name": lib.get("Name"), "CollectionType": lib.get("CollectionType")
                        }
    except HTTPException as e:
        print(f"[PROXY-ADMIN-WARNING] 从 /Users/.../Views 获取数据失败: {e.detail}")

    return list(all_real_libs.values())


# --- API ---
@api_router.get("/config", response_model=AppConfig, response_model_by_alias=True, tags=["Configuration"])
async def get_config():
    return config_manager.load_config()


# 修改 update_config
@api_router.post("/config", response_model=AppConfig, response_model_by_alias=True, tags=["Configuration"])
async def update_config(config: AppConfig):
    config_manager.save_config(config)
    return config


@api_router.post("/proxy/restart", status_code=204, tags=["System Management"])
async def restart_go_proxy():
    """通知 Go 反向代理热加载 config（与保存配置后的 notify 一致，不再通过 Docker 重启 Python 代理）。"""

    def _reload():
        if os.environ.get("PROXY_RELOAD_SKIP", "").strip().lower() in ("1", "true", "yes"):
            return
        url = (os.environ.get("PROXY_RELOAD_URL") or "").strip() or "http://127.0.0.1:8000/__internal/reload-config"
        token = (os.environ.get("PROXY_RELOAD_TOKEN") or os.environ.get("EMBY_PROXY_RELOAD_TOKEN") or "").strip()
        req = urllib.request.Request(url, method="POST")
        if token:
            req.add_header("X-Emby-Virtual-Lib-Reload-Token", token)
        with urllib.request.urlopen(req, timeout=15) as resp:
            resp.read()

    try:
        await asyncio.to_thread(_reload)
        return Response(status_code=204)
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace") if e.fp else ""
        logger.error(f"Go proxy reload HTTP {e.code}: {body}")
        raise HTTPException(status_code=502, detail=f"Go 代理 reload 失败: HTTP {e.code} {body}".strip())
    except urllib.error.URLError as e:
        logger.error(f"Go proxy reload 连接失败: {e}")
        raise HTTPException(status_code=502, detail=f"无法连接 Go 代理 reload 地址: {e}")
    except Exception as e:
        logger.error(f"Go proxy reload 异常: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@api_router.post("/generate-cover", tags=["Cover Generator"])
async def generate_cover(body: CoverRequest):
    try:
        # 调用核心生成逻辑，并传递样式名称
        image_tag = await _generate_library_cover(body.library_id, body.title_zh, body.title_en, body.style_name,
                                                  body.temp_image_paths)
        if image_tag:
            # 成功后，需要找到对应的虚拟库并更新其 image_tag
            config = config_manager.load_config()
            vlib_found = False
            for vlib in config.virtual_libraries:
                if vlib.id == body.library_id:
                    vlib.image_tag = image_tag
                    vlib_found = True
                    break
            if vlib_found:
                config_manager.save_config(config)
                return {"success": True, "image_tag": image_tag}
            else:
                raise HTTPException(status_code=404, detail="未找到要更新封面的虚拟库。")
        else:
            raise HTTPException(status_code=500, detail="封面生成失败，详见后端日志。")
    except Exception as e:
        print(f"[COVER-GEN-ERROR] 封面生成过程中发生异常: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@api_router.post("/covers/clear", status_code=204, tags=["Cover Generator"])
async def clear_all_covers():
    """清空所有生成的封面图并重置配置中的 image_tag"""
    covers_dir = REPO_ROOT / "images"
    logger.info(f"开始清空封面目录: {covers_dir}")
    try:
        if covers_dir.is_dir():
            for item in covers_dir.iterdir():
                if item.name == "badger_db":
                    continue
                if item.is_dir():
                    shutil.rmtree(item)
                else:
                    item.unlink()

        # 重置配置
        config = config_manager.load_config()
        for vlib in config.virtual_libraries:
            vlib.image_tag = None
        config_manager.save_config(config)

        logger.info("所有封面及配置已成功清除。")
        return Response(status_code=204)
    except Exception as e:
        logger.error(f"清空封面时出错: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"清空封面时发生内部错误: {e}")


@api_router.get("/covers/{library_id}", tags=["Cover Generator"])
async def resolve_cover_file(library_id: str):
    covers_dir = REPO_ROOT / "images"
    for ext, media_type in (
        (".gif", "image/gif"),
        (".webp", "image/webp"),
        (".png", "image/png"),
        (".jpg", "image/jpeg"),
        (".jpeg", "image/jpeg"),
    ):
        p = covers_dir / f"{library_id}{ext}"
        if p.is_file():
            return FileResponse(str(p), media_type=media_type)
    raise HTTPException(status_code=404, detail="封面不存在")


# 封面生成的核心逻辑
async def _generate_library_cover(library_id: str, title_zh: str, title_en: Optional[str], style_name: str,
                                  temp_image_paths: Optional[List[str]] = None) -> Optional[str]:
    config = config_manager.load_config()
    # --- 1. 定义路径 ---
    FONT_DIR = str(REPO_ROOT / "admin" / "assets" / "fonts") + os.sep
    OUTPUT_DIR = str(REPO_ROOT / "images") + os.sep

    Path(OUTPUT_DIR).mkdir(exist_ok=True)

    # 创建一个唯一的临时目录来存放下载的素材
    image_gen_dir = Path(OUTPUT_DIR) / f"temp_{str(uuid.uuid4())}"
    image_gen_dir.mkdir()

    try:
        # --- 2. 【核心改动】: 根据配置选择图片来源 (四级回退) ---
        if temp_image_paths:
            logger.info(f"从 {len(temp_image_paths)} 张上传的临时图片中随机选择素材...")
            selected_paths = random.sample(temp_image_paths, min(9, len(temp_image_paths)))
            for i, src_path_str in enumerate(selected_paths):
                src_path = Path(src_path_str)
                if src_path.is_file():
                    dest_path = image_gen_dir / f"{i + 1}{src_path.suffix}"
                    shutil.copy(src_path, dest_path)
        else:
            vlib = next((lib for lib in config.virtual_libraries if lib.id == library_id), None)
            if vlib and vlib.cover_custom_image_path:
                await _fetch_images_from_custom_path(vlib.cover_custom_image_path, image_gen_dir)
            elif config.custom_image_path:
                await _fetch_images_from_custom_path(config.custom_image_path, image_gen_dir)
            else:
                await _fetch_images_from_vlib(library_id, image_gen_dir, config)

        # --- 3. 【核心改动】: 动态调用所选的样式生成函数 ---
        logger.info(f"素材准备完毕，开始使用样式 '{style_name}' 为 '{title_zh}' ({library_id}) 生成封面...")

        try:
            # 动态导入选择的样式模块
            _pkg = __package__ or "admin"
            style_module = importlib.import_module(f".cover_generator.{style_name}", _pkg)
            # 假设每个样式文件都有一个名为 create_... 的主函数
            create_function_name = f"create_{style_name}"
            create_function = getattr(style_module, create_function_name)
        except (ImportError, AttributeError) as e:
            logger.error(f"无法加载或找到样式生成函数: {style_name} -> {e}")
            raise HTTPException(status_code=400, detail=f"无效的样式名称: {style_name}")

        # --- 字体选择逻辑 ---
        vlib = next((lib for lib in config.virtual_libraries if lib.id == library_id), None)

        # 确定中文字体
        if vlib and vlib.cover_custom_zh_font_path:
            zh_font_path = vlib.cover_custom_zh_font_path
        elif config.custom_zh_font_path:
            zh_font_path = config.custom_zh_font_path
        else:
            zh_font_path = os.path.join(FONT_DIR, "multi_1_zh.ttf")

        # 确定英文字体
        if vlib and vlib.cover_custom_en_font_path:
            en_font_path = vlib.cover_custom_en_font_path
        elif config.custom_en_font_path:
            en_font_path = config.custom_en_font_path
        else:
            en_font_path = os.path.join(FONT_DIR, "multi_1_en.otf")

        kwargs = {
            "title": (title_zh, title_en),
            "font_path": (zh_font_path, en_font_path)
        }

        if style_name in ['style_multi_1', 'style_animated_1']:
            kwargs['library_dir'] = str(image_gen_dir)
        elif style_name in ['style_single_1', 'style_single_2']:
            # 单图模式，选择第一张图作为主图
            main_image_path = image_gen_dir / "1.jpg"
            if not main_image_path.is_file():
                raise HTTPException(status_code=404, detail="无法找到用于单图模式的主素材图片 (1.jpg)。")
            kwargs['image_path'] = str(main_image_path)
        else:
            raise HTTPException(status_code=400, detail=f"未知的样式名称: {style_name}")

        # 使用关键字参数解包来调用函数
        res = create_function(**kwargs)

        if not res:
            logger.error(f"样式函数 {style_name} 返回失败。")
            raise HTTPException(status_code=500, detail=f"封面生成函数 {style_name} 内部错误。")

        # --- 4. 解码、转换并以虚拟库ID为名保存图片 ---
        payload = res
        output_format = "jpg"
        if isinstance(res, dict):
            payload = res.get("data")
            output_format = str(res.get("format") or "jpg").strip().lower()
        if not isinstance(payload, str):
            raise HTTPException(status_code=500, detail=f"封面生成函数 {style_name} 返回数据格式无效。")

        image_data = base64.b64decode(payload)
        if output_format in ("gif", "webp", "png", "jpg", "jpeg"):
            final_format = "jpg" if output_format == "jpeg" else output_format
        else:
            final_format = "jpg"
        output_path = os.path.join(OUTPUT_DIR, f"{library_id}.{final_format}")

        for ext in ("jpg", "jpeg", "png", "gif", "webp"):
            stale = Path(OUTPUT_DIR) / f"{library_id}.{ext}"
            if stale.exists():
                stale.unlink()

        if final_format in ("gif", "webp"):
            with open(output_path, "wb") as f:
                f.write(image_data)
        else:
            img = Image.open(BytesIO(image_data))
            if final_format == "png":
                if img.mode not in ('RGB', 'RGBA'):
                    img = img.convert('RGBA')
                img.save(output_path, "PNG")
            else:
                if img.mode != 'RGB':
                    img = img.convert('RGB')
                img.save(output_path, "JPEG", quality=90)

        image_tag = hashlib.md5(str(time.time()).encode()).hexdigest()

        logger.info(f"封面成功保存至: {output_path}, ImageTag: {image_tag}")

        return image_tag

    except HTTPException as http_exc:
        # 直接向上抛出HTTP异常，以便前端可以显示具体的错误信息
        raise http_exc
    except Exception as e:
        logger.error(f"封面生成过程中发生未知错误: {e}", exc_info=True)
        return None
    finally:
        # --- 5. 【核心改动】: 清理临时目录 ---
        if image_gen_dir.exists():
            shutil.rmtree(image_gen_dir)
            logger.info(f"已清理临时素材目录: {image_gen_dir}")


@api_router.get("/all-libraries", tags=["Display Management"])
async def get_all_libraries():
    all_libs = []
    try:
        real_libs_from_emby = await get_real_libraries_hybrid_mode()
        for lib in real_libs_from_emby:
            all_libs.append({
                "id": lib.get("Id"), "name": lib.get("Name"), "type": "real",
                "collectionType": lib.get("CollectionType")
            })
    except HTTPException as e:
        raise e

    config = config_manager.load_config()
    for lib in config.virtual_libraries:
        all_libs.append({
            "id": lib.id, "name": lib.name, "type": "virtual",
        })

    return all_libs


@api_router.post("/display-order", status_code=204, tags=["Display Management"])
async def save_display_order(ordered_ids: List[str]):
    config = config_manager.load_config()
    config.display_order = ordered_ids
    config_manager.save_config(config)
    return Response(status_code=204)


@api_router.post("/libraries", response_model=VirtualLibrary, tags=["Libraries"])
async def create_library(library: VirtualLibrary):
    config = config_manager.load_config()
    existing = [lib.id for lib in config.virtual_libraries]
    library.id = next_virtual_library_id(existing)
    if not hasattr(config, 'virtual_libraries'):
        config.virtual_libraries = []

    config.virtual_libraries.append(library)
    if library.id not in config.display_order:
        config.display_order.append(library.id)

    config_manager.save_config(config)
    return library


@api_router.put("/libraries/{library_id}", response_model=VirtualLibrary, tags=["Libraries"])
async def update_library(library_id: str, updated_library_data: VirtualLibrary):
    config = config_manager.load_config()
    lib_to_update = None
    for lib in config.virtual_libraries:
        if lib.id == library_id:
            lib_to_update = lib
            break

    if not lib_to_update:
        raise HTTPException(status_code=404, detail="Virtual library not found")

    # 【核心修复】: 不完全替换，而是更新字段
    # .model_dump(exclude_unset=True) 只获取客户端实际发送过来的字段
    update_data = updated_library_data.model_dump(exclude_unset=True)

    # 【核心修复】在更新前，手动保留旧的 image_tag
    if lib_to_update.image_tag:
        # 如果客户端传来的数据中没有 image_tag，或者为 null，我们强制使用旧的
        if 'image_tag' not in update_data or not update_data['image_tag']:
            update_data['image_tag'] = lib_to_update.image_tag

    # 使用 Pydantic 的 model_copy 方法安全地更新模型
    updated_lib = lib_to_update.model_copy(update=update_data)

    # 在列表中替换掉旧的模型
    for i, lib in enumerate(config.virtual_libraries):
        if lib.id == library_id:
            config.virtual_libraries[i] = updated_lib
            break

    config_manager.save_config(config)
    return updated_lib


@api_router.delete("/libraries/{library_id}", status_code=204, tags=["Libraries"])
async def delete_library(library_id: str):
    config = config_manager.load_config()

    lib_to_delete = next((lib for lib in config.virtual_libraries if lib.id == library_id), None)

    if not lib_to_delete:
        raise HTTPException(status_code=404, detail="Virtual library not found")

    # Proceed with deleting from config
    config.virtual_libraries = [lib for lib in config.virtual_libraries if lib.id != library_id]

    if library_id in config.display_order:
        config.display_order.remove(library_id)

    config_manager.save_config(config)
    return Response(status_code=204)


@api_router.get("/emby/classifications", tags=["Emby Helper"])
async def get_emby_classifications():
    def format_items(items_list: List) -> List:
        return [{"name": item.get("Name", 'N/A'), "id": item.get("Id", 'N/A')} for item in items_list]

    try:
        tasks = {
            "collections": _fetch_from_emby("/Items", params={"IncludeItemTypes": "BoxSet", "Recursive": "true"}),
            "genres": _fetch_from_emby("/Genres"),
            "tags": _fetch_from_emby("/Tags"),
            "studios": _fetch_from_emby("/Studios"),
        }
        results_list = await asyncio.gather(*tasks.values())
        results_dict = dict(zip(tasks.keys(), results_list))
        results_dict["persons"] = []
        return {key: format_items(value) for key, value in results_dict.items()}
    except HTTPException as e:
        raise e


@api_router.get("/emby/persons/search", tags=["Emby Helper"])
async def search_emby_persons(query: str = Query(None), page: int = Query(1)):
    PAGE_SIZE = 100
    start_index = (page - 1) * PAGE_SIZE
    try:
        if query:
            params = {"SearchTerm": query, "IncludeItemTypes": "Person", "Recursive": "true", "StartIndex": start_index,
                      "Limit": PAGE_SIZE}
            persons = await _fetch_from_emby("/Items", params=params)
        else:
            params = {"StartIndex": start_index, "Limit": PAGE_SIZE}
            persons = await _fetch_from_emby("/Persons", params=params)
        return [{"name": item.get("Name", 'N/A'), "id": item.get("Id", 'N/A')} for item in persons]
    except HTTPException as e:
        raise e


@api_router.get("/emby/resolve-item/{item_id}", tags=["Emby Helper"])
async def resolve_emby_item(item_id: str):
    try:
        users = await _fetch_from_emby("/Users")
        if not users:
            raise HTTPException(status_code=500, detail="无法获取任何Emby用户用于查询")

        ref_user_id = users[0]['Id']
        item_details = await _fetch_from_emby(f"/Users/{ref_user_id}/Items/{item_id}")

        if not item_details:
            raise HTTPException(status_code=404, detail="在Emby中未找到指定的项目ID")

        return {"name": item_details.get("Name", "N/A"), "id": item_details.get("Id")}

    except HTTPException as e:
        raise e


admin_app.include_router(api_router)

# 挂载生成的封面图目录（与 Go 代理使用的 images/ 目录一致）
covers_dir = REPO_ROOT / "images"
covers_dir.mkdir(parents=True, exist_ok=True)
admin_app.mount("/covers", StaticFiles(directory=str(covers_dir)), name="covers")

_static_candidates = [REPO_ROOT / "web" / "dist", Path("/app/static")]
static_dir = next((p for p in _static_candidates if p.is_dir() and (p / "index.html").is_file()), None)
_dev_api_only = os.environ.get("EMBY_ADMIN_DEV", "").strip().lower() in ("1", "true", "yes")

if static_dir is None:
    if _dev_api_only:
        logger.warning(
            "Web UI not built (expected %s). EMBY_ADMIN_DEV=1: serving API + /docs only; build UI with: cd web && npm ci && npm run build",
            REPO_ROOT / "web" / "dist",
        )


        @admin_app.get("/", include_in_schema=False)
        async def _dev_root():
            return RedirectResponse(url="/docs")
    else:
        print(
            f"[admin] ERROR: web UI not built. Expected {REPO_ROOT / 'web' / 'dist'}. "
            "Run: cd web && npm ci && npm run build. Or set EMBY_ADMIN_DEV=1 to debug API without the SPA.",
            file=sys.stderr,
        )
        sys.exit(1)
else:
    admin_app.mount("/", StaticFiles(directory=str(static_dir), html=True), name="static")
