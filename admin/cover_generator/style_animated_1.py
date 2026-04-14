import base64
import colorsys
import logging
import math
import os
import random
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageOps, ImageDraw, ImageFilter, ImageFont

logger = logging.getLogger(__name__)

POSTER_GEN_CONFIG = {
    "ROWS": 3,  # 每列图片数
    "COLS": 3,  # 总列数
    "MARGIN": 22,  # 图片垂直间距
    "CORNER_RADIUS": 46.1,  # 圆角半径
    "ROTATION_ANGLE": -15.8,  # 旋转角度
    "START_X": 835,  # 第一列的 x 坐标
    "START_Y": -362,  # 第一列的 y 坐标
    "COLUMN_SPACING": 100,  # 列间距
    "SAVE_COLUMNS": True,  # 是否保存每列图片
    "CELL_WIDTH": 410,  # 海报宽度
    "CELL_HEIGHT": 610,  # 海报高度
    "CANVAS_WIDTH": 1920,  # 画布宽度
    "CANVAS_HEIGHT": 1080,  # 画布高度
}

ANIMATION_CONFIG = {
    "POSTER_COUNT": 9,
    "FRAME_COUNT": 60,
    "FRAME_DURATION": 60,
    "OUTPUT_FORMAT": "WEBP",
    "OUTPUT_WIDTH": 560,
    "OUTPUT_HEIGHT": 315,
    "GIF_COLORS": 256,
}


def get_poster_primary_color(image_path):
    """
    分析图片并提取主色调

    参数:
        image_path: 图片文件路径

    返回:
        主色调颜色，RGBA格式
    """
    try:
        from collections import Counter

        # 打开图片
        img = Image.open(image_path)

        # 缩小图片尺寸以加快处理速度
        img = img.resize((100, 150), Image.LANCZOS)

        # 确保图片为RGBA模式
        if img.mode != 'RGBA':
            img = img.convert('RGBA')

        # 获取图片中心部分的像素数据（避免边框和角落）
        # width, height = img.size
        # center_x1 = int(width * 0.2)
        # center_y1 = int(height * 0.2)
        # center_x2 = int(width * 0.8)
        # center_y2 = int(height * 0.8)

        # # 裁剪出中心区域
        # center_img = img.crop((center_x1, center_y1, center_x2, center_y2))

        # 获取所有像素
        pixels = list(img.getdata())

        # 过滤掉接近黑色和白色的像素，以及透明度低的像素
        filtered_pixels = []
        for pixel in pixels:
            r, g, b, a = pixel

            # 跳过透明度低的像素
            if a < 200:
                continue

            # 计算亮度
            brightness = (r + g + b) / 3

            # 跳过过暗或过亮的像素
            if brightness < 30 or brightness > 220:
                continue

            # 添加到过滤后的列表
            filtered_pixels.append((r, g, b, 255))

        # 如果过滤后没有像素，使用全部像素
        if not filtered_pixels:
            filtered_pixels = [(p[0], p[1], p[2], 255) for p in pixels if p[3] > 100]

        # 如果仍然没有像素，返回默认颜色
        if not filtered_pixels:
            return (150, 100, 50, 255)

        # 使用Counter找到出现最多的颜色
        color_counter = Counter(filtered_pixels)
        common_colors = color_counter.most_common(10)

        # 如果找到了颜色，返回最常见的颜色
        if common_colors:
            return common_colors

        # 如果无法找到主色调，使用平均值
        r_avg = sum(p[0] for p in filtered_pixels) // len(filtered_pixels)
        g_avg = sum(p[1] for p in filtered_pixels) // len(filtered_pixels)
        b_avg = sum(p[2] for p in filtered_pixels) // len(filtered_pixels)

        return [(r_avg, g_avg, b_avg, 255)]


    except Exception as e:
        logger.error(f"获取图片主色调时出错: {e}")
        # 返回默认颜色作为备选
        return [(150, 100, 50, 255)]


def create_gradient_background(width, height, color=None):
    """
    创建一个从左到右的渐变背景，使用遮罩技术实现渐变效果
    左侧颜色更深，右侧颜色适中，提供更明显的渐变效果

    参数:
        width: 背景宽度
        height: 背景高度
        color: 颜色数组或单个颜色，如果为None则随机生成
              如果是数组，会依次尝试每个颜色，跳过太黑或太淡的颜色

    返回:
        渐变背景图像
    """

    def normalize_rgb(input_rgb):
        """
        将各种可能的输入格式，统一提取成 (r, g, b) 三元组。
        支持：
        - (r, g, b)
        - (r, g, b, a)
        - ((r, g, b), idx) or ((r, g, b, a), idx)
        """
        if isinstance(input_rgb, tuple):
            # 情况 3: ((r,g,b,a), idx) 或 ((r,g,b), idx)
            if len(input_rgb) == 2 and isinstance(input_rgb[0], tuple):
                return normalize_rgb(input_rgb[0])
            # 情况 2: RGBA
            if len(input_rgb) == 4 and all(isinstance(v, (int, float)) for v in input_rgb):
                return input_rgb[:3]
            # 情况 1: RGB
            if len(input_rgb) == 3 and all(isinstance(v, (int, float)) for v in input_rgb):
                return input_rgb
        raise ValueError(f"无法识别的颜色格式: {input_rgb!r}")

    def is_mid_bright(input_rgb, min_lum=80, max_lum=200):
        """
        基于相对亮度判断：不过暗（>=min_lum）也不过白（<=max_lum）。
        input_rgb 可为多种格式，函数内部会 normalize。
        """
        r, g, b = normalize_rgb(input_rgb)
        lum = 0.299 * r + 0.587 * g + 0.114 * b
        return min_lum <= lum <= max_lum

    # 定义用于判断颜色是否合适的函数
    def is_mid_bright_hsl(input_rgb, min_l=0.3, max_l=0.7):
        """
        基于 HSL Lightness 判断。Lightness 在 [0,1]。
        """
        r, g, b = normalize_rgb(input_rgb)
        # 归一到 [0,1]
        r1, g1, b1 = r / 255.0, g / 255.0, b / 255.0
        h, l, s = colorsys.rgb_to_hls(r1, g1, b1)
        return min_l <= l <= max_l

    selected_color = None

    # 如果传入的是颜色数组
    if isinstance(color, list) and len(color) > 0:
        # 尝试找到合适的颜色，最多尝试5个
        for i in range(min(10, len(color))):
            if is_mid_bright_hsl(color[i]):
                # 如果是(color_tuple, count)格式，提取颜色元组
                if isinstance(color[i], tuple) and len(color[i]) == 2 and isinstance(color[i][0], tuple):
                    selected_color = color[i][0]
                else:
                    selected_color = color[i]
                logger.info(f"海报主题色:[{selected_color}]适合做背景")
                break
            else:
                logger.info(f"[海报主题色:[{color[i]}]不适合做背景,尝试做下一个颜色")

    # 如果没有找到合适的颜色，随机生成一个颜色
    if selected_color is None:
        def random_hsl_to_rgb(
                hue_range=(0, 360),
                sat_range=(0.5, 1.0),
                light_range=(0.5, 0.8)
        ):
            """
            hue_range: 色相范围，取值 0~360
            sat_range: 饱和度范围，取值 0~1
            light_range: 明度范围，取值 0~1
            返回值：RGB 三元组，每个通道 0~255
            """
            h = random.uniform(hue_range[0] / 360.0, hue_range[1] / 360.0)
            s = random.uniform(sat_range[0], sat_range[1])
            l = random.uniform(light_range[0], light_range[1])
            # colorsys.hls_to_rgb 接受 H, L, S (注意顺序) 都是 0~1
            r, g, b = colorsys.hls_to_rgb(h, l, s)
            # 转回 0~255
            return (int(r * 255), int(g * 255), int(b * 255))

        # 生成颜色示例
        selected_color = random_hsl_to_rgb()
        logger.info(f"海报所有主题色不适合做背景，随机生成一个颜色[{selected_color}]。")

    # 如果是已经提供的颜色，将其加深
    # 降低各通道的亮度，使颜色更深
    r = int(selected_color[0] * 0.65)  # 降低35%
    g = int(selected_color[1] * 0.65)  # 降低35%
    b = int(selected_color[2] * 0.65)  # 降低35%

    # 确保RGB值不会小于0
    r = max(0, r)
    g = max(0, g)
    b = max(0, b)

    # 更新颜色
    selected_color = (r, g, b, selected_color[3] if len(selected_color) > 3 else 255)

    # 确保selected_color包含alpha通道
    if len(selected_color) == 3:
        selected_color = (selected_color[0], selected_color[1], selected_color[2], 255)

    # 基于selected_color自动生成浅色版本作为右侧颜色
    # 将selected_color的RGB值增加更合适的比例，使右侧颜色适中
    # 限制最大值为255
    r = min(255, int(selected_color[0] * 1.9))  # 从2.2降到1.9
    g = min(255, int(selected_color[1] * 1.9))  # 从2.2降到1.9
    b = min(255, int(selected_color[2] * 1.9))  # 从2.2降到1.9

    # 确保至少有一定的亮度增加，但比之前小
    r = max(r, selected_color[0] + 80)  # 从100降到80
    g = max(g, selected_color[1] + 80)  # 从100降到80
    b = max(b, selected_color[2] + 80)  # 从100降到80

    # 确保右侧颜色不会太亮
    r = min(r, 230)  # 限制最大亮度
    g = min(g, 230)  # 限制最大亮度
    b = min(b, 230)  # 限制最大亮度

    # 创建右侧浅色
    color2 = (r, g, b, selected_color[3])

    # 创建左右两个纯色图像
    left_image = Image.new("RGBA", (width, height), selected_color)
    right_image = Image.new("RGBA", (width, height), color2)

    # 创建渐变遮罩（从黑到白的横向线性渐变）
    mask = Image.new("L", (width, height), 0)
    mask_data = []

    # 生成遮罩数据，使用更加平滑的过渡
    for y in range(height):
        for x in range(width):
            # 计算从左到右的渐变值 (0-255)
            # 使用更加非线性的渐变，使左侧深色区域更大
            mask_value = int(255.0 * (x / width) ** 0.7)  # 从0.85改为0.7
            mask_data.append(mask_value)

    # 应用遮罩数据到遮罩图像
    mask.putdata(mask_data)

    # 使用遮罩合成左右两个图像
    # 遮罩中黑色部分(0)显示left_image，白色部分(255)显示right_image
    gradient = Image.composite(right_image, left_image, mask)

    return gradient


def add_shadow(img, offset=(5, 5), shadow_color=(0, 0, 0, 100), blur_radius=3):
    """
    给图片添加右侧和底部阴影

    参数:
        img: 原始图片（PIL.Image对象）
        offset: 阴影偏移量，(x, y)格式
        shadow_color: 阴影颜色，RGBA格式
        blur_radius: 阴影模糊半径

    返回:
        添加了阴影的新图片
    """
    # 创建一个透明背景，比原图大一些，以容纳阴影
    shadow_width = img.width + offset[0] + blur_radius * 2
    shadow_height = img.height + offset[1] + blur_radius * 2

    shadow = Image.new("RGBA", (shadow_width, shadow_height), (0, 0, 0, 0))

    # 创建阴影层
    shadow_layer = Image.new("RGBA", img.size, shadow_color)

    # 将阴影层粘贴到偏移位置
    shadow.paste(shadow_layer, (blur_radius + offset[0], blur_radius + offset[1]))

    # 模糊阴影
    shadow = shadow.filter(ImageFilter.GaussianBlur(blur_radius))

    # 创建结果图像
    result = Image.new("RGBA", shadow.size, (0, 0, 0, 0))

    # 将原图粘贴到结果图像上
    result.paste(img, (blur_radius, blur_radius), img if img.mode == "RGBA" else None)

    # 合并阴影和原图（保持原图在上层）
    shadow_img = Image.alpha_composite(shadow, result)

    return shadow_img


def create_extended_column(column_posters, cell_width, cell_height, margin, corner_radius):
    """
    创建扩展高度的列图片（将图片复制一份在下方），用于无缝循环动画

    参数:
        column_posters: 当前列的海报文件路径列表
        cell_width: 单张海报宽度
        cell_height: 单张海报高度
        margin: 海报间距
        corner_radius: 圆角半径

    返回:
        扩展后的列图片（高度翻倍）
    """
    rows = len(column_posters)
    single_column_height = rows * cell_height + (rows - 1) * margin

    # 阴影额外空间
    shadow_extra_width = 20 + 20 * 2
    shadow_extra_height = 20 + 20 * 2

    # 创建扩展高度的列画布（高度为原来的2倍 + 间距）
    extended_height = single_column_height * 2 + margin
    column_image = Image.new(
        "RGBA",
        (cell_width + shadow_extra_width, extended_height + shadow_extra_height),
        (0, 0, 0, 0),
    )

    # 放置两份图片（上下复制）
    for copy_index in range(2):
        base_y = copy_index * (single_column_height + margin)

        for row_index, poster_path in enumerate(column_posters):
            try:
                # 打开海报
                poster = Image.open(poster_path)

                # 调整海报大小为固定尺寸
                resized_poster = poster.resize(
                    (cell_width, cell_height), Image.LANCZOS
                )

                # 创建圆角遮罩（如果需要）
                if corner_radius > 0:
                    mask = Image.new("L", (cell_width, cell_height), 0)
                    draw = ImageDraw.Draw(mask)
                    draw.rounded_rectangle(
                        [(0, 0), (cell_width, cell_height)],
                        radius=corner_radius,
                        fill=255,
                    )
                    poster_with_corners = Image.new(
                        "RGBA", resized_poster.size, (0, 0, 0, 0)
                    )
                    poster_with_corners.paste(resized_poster, (0, 0), mask)
                    resized_poster = poster_with_corners

                # 添加阴影效果
                resized_poster_with_shadow = add_shadow(
                    resized_poster,
                    offset=(20, 20),
                    shadow_color=(0, 0, 0, 255),
                    blur_radius=20,
                )

                # 计算在列画布上的位置
                y_position = base_y + row_index * (cell_height + margin)

                # 粘贴到列画布上
                column_image.paste(
                    resized_poster_with_shadow,
                    (0, y_position),
                    resized_poster_with_shadow,
                )

            except Exception as e:
                logger.error(f"处理图片 {os.path.basename(poster_path)} 时出错: {e}")
                continue

    return column_image, single_column_height


def _build_column_frames(column_images, frame_index, frame_count, rows, cell_height, margin):
    step = cell_height + margin
    cycle = rows * step
    progress = frame_index / max(frame_count, 1)
    offset = int(progress * cycle)

    frames = []
    for idx, column_image in enumerate(column_images):
        direction = 1 if idx == 1 else -1
        y = (direction * offset) % cycle
        frames.append((column_image, y))
    return frames


def get_random_color(image_path):
    """
    获取图片随机位置的颜色

    参数:
        image_path: 图片文件路径

    返回:
        随机点颜色，RGBA格式
    """
    try:
        img = Image.open(image_path)
        # 获取图片尺寸
        width, height = img.size

        # 在图片范围内随机选择一个点
        # 避免边缘区域，缩小范围到图片的20%-80%区域
        random_x = random.randint(int(width * 0.5), int(width * 0.8))
        random_y = random.randint(int(height * 0.5), int(height * 0.8))

        # 获取随机点的颜色
        if img.mode == "RGBA":
            r, g, b, a = img.getpixel((random_x, random_y))
            return (r, g, b, a)
        elif img.mode == "RGB":
            r, g, b = img.getpixel((random_x, random_y))
            return (r + 100, g + 50, b, 255)
        else:
            img = img.convert("RGBA")
            r, g, b, a = img.getpixel((random_x, random_y))
            return (r, g, b, a)
    except Exception as e:
        logger.error(f"获取图片颜色时出错: {e}")
        # 返回随机颜色作为备选
        return (
            random.randint(50, 200),
            random.randint(50, 200),
            random.randint(50, 200),
            255,
        )


def generate_animation_frame(
        gradient_bg,
        extended_columns,
        column_heights,
        frame_index,
        total_frames,
        rotation_angle,
        start_x,
        start_y,
        column_spacing,
        cell_width,
        cell_height,
        cols,
        margin,
        scale_factor=1.0
):
    """
    生成单帧动画图片

    参数:
        gradient_bg: 渐变背景
        extended_columns: 扩展后的列图片列表
        column_heights: 单列高度列表
        frame_index: 当前帧索引
        total_frames: 总帧数
        其他参数: 布局配置

    返回:
        当前帧的完整图片
    """
    result = gradient_bg.copy()

    # 计算当前帧的偏移量（一个完整周期移动一个图片+间距的距离）
    # 使用传入的已缩放 margin，确保与 column_heights 计算一致
    move_distance = column_heights[0] + margin  # 一个循环周期的移动距离
    progress = frame_index / total_frames
    base_offset = int(progress * move_distance)

    for col_index, (extended_column, single_height) in enumerate(zip(extended_columns, column_heights)):
        if col_index >= cols:
            break

        # 根据列索引确定移动方向
        # 第1列(0): 向上, 第2列(1): 向下, 第3列(2): 向上
        if col_index == 1:
            offset = base_offset  # 向下移动（正偏移）
        else:
            offset = -base_offset  # 向上移动（负偏移）

        # 从扩展列中裁剪出当前帧需要显示的部分
        # 计算裁剪区域
        shadow_extra = 20 + 20 * 2

        # 调整偏移确保在有效范围内
        crop_y_start = single_height // 2 + offset
        crop_y_start = crop_y_start % (single_height + margin)

        # 裁剪出需要的部分
        cropped_column = extended_column.crop((
            0,
            crop_y_start,
            extended_column.width,
            crop_y_start + single_height + shadow_extra
        ))

        # 旋转列
        rotation_canvas_size = int(
            math.sqrt(
                cropped_column.width ** 2 + cropped_column.height ** 2
            ) * 1.5
        )
        rotation_canvas = Image.new(
            "RGBA", (rotation_canvas_size, rotation_canvas_size), (0, 0, 0, 0)
        )

        paste_x = (rotation_canvas_size - cropped_column.width) // 2
        paste_y = (rotation_canvas_size - cropped_column.height) // 2
        rotation_canvas.paste(cropped_column, (paste_x, paste_y), cropped_column)

        rotated_column = rotation_canvas.rotate(
            rotation_angle, Image.BICUBIC, expand=True
        )

        # 计算列在模板上的位置
        column_x = start_x + col_index * column_spacing
        column_center_y = start_y + single_height // 2
        column_center_x = column_x

        # 根据列索引调整位置 - 需要按比例缩放调整值
        # 这些值是基于原始1920x1080分辨率的调整
        if col_index == 1:
            column_center_x += cell_width - int(50 * scale_factor)
        elif col_index == 2:
            column_center_y += int(-155 * scale_factor)
            column_center_x += (cell_width) * 2 - int(40 * scale_factor)

        # 计算最终放置位置
        final_x = column_center_x - rotated_column.width // 2 + cell_width // 2
        final_y = column_center_y - rotated_column.height // 2

        # 粘贴旋转后的列到结果图像
        result.paste(rotated_column, (final_x, final_y), rotated_column)

    return result


def draw_text_on_image(
        image, text, position, font_path, default_font_path, font_size, fill_color=(255, 255, 255, 255),
        shadow_enabled=False, shadow_color=(0, 0, 0, 180), shadow_offset=(2, 2)
):
    """
    在图像上绘制文字，可选添加文字阴影

    参数:
        image: PIL.Image对象
        text: 要绘制的文字
        position: 文字位置 (x, y)
        font_path: 字体文件路径
        default_font_path: 默认字体文件路径
        font_size: 字体大小
        fill_color: 文字颜色，RGBA格式
        shadow_enabled: 是否启用文字阴影
        shadow_color: 阴影颜色，RGBA格式
        shadow_offset: 阴影偏移量，(x, y)格式

    返回:
        添加了文字的图像
    """
    # 创建一个可绘制的图像副本
    img_copy = image.copy()
    draw = ImageDraw.Draw(img_copy)
    # font_path = os.path.join(config.CURRENT_DIR, font_path)
    # if not os.path.exists(font_path):
    #     logger.warning(f"自定义字体不存在:{font_path}，使用默认字体")
    #     font_path = os.path.join(config.CURRENT_DIR, "font", default_font_path)
    font = ImageFont.truetype(font_path, font_size)

    # 如果启用阴影，先绘制阴影文字
    if shadow_enabled:
        shadow_position = (position[0] + shadow_offset[0], position[1] + shadow_offset[1])
        draw.text(shadow_position, text, font=font, fill=shadow_color)

    # 绘制正常文字
    draw.text(position, text, font=font, fill=fill_color)

    return img_copy


def add_text_overlay(result, title, font_path, poster_files, scale_factor=1.0, color_block_color=None):
    """
    在图片上添加文字和装饰（从gen_poster借用逻辑）

    参数:
        result: 图片对象
        name: 媒体库名称
        poster_files: 海报文件列表
        scale_factor: 缩放比例，用于调整字体大小和位置
        color_block_color: 色块颜色，如果为None则自动获取
    """
    import random

    # 使用传入的色块颜色，或者获取第一张图片的随机点颜色
    if color_block_color is not None:
        random_color = color_block_color
    elif poster_files:
        first_image_path = poster_files[0]
        random_color = get_random_color(first_image_path)
    else:
        random_color = (
            random.randint(50, 200),
            random.randint(50, 200),
            random.randint(50, 200),
            255,
        )
    title_zh, title_en = title

    # 查找匹配的模板配置
    library_ch_name = title_zh
    library_eng_name = title_en

    STYLE_CONFIGS = [{
        "style_name": "style1",
        "style_ch_font": "字体名带后缀",
        "style_eng_font": "字体名带后缀",
        "style_ch_shadow": True,
        "style_ch_shadow_offset": [2, 2],
        "style_eng_shadow": True,
        "style_eng_shadow_offset": [2, 2]
    }]  # 获取样式配置

    style_name = "style1"
    style_config = next(
        (style for style in STYLE_CONFIGS if style.get("style_name") == style_name),
        None
    )

    # 获取文字阴影设置
    ch_shadow_enabled = style_config.get("style_ch_shadow", False) if style_config else False
    eng_shadow_enabled = style_config.get("style_eng_shadow", False) if style_config else False
    ch_shadow_offset = style_config.get("style_ch_shadow_offset", (2, 2)) if style_config else (2, 2)
    eng_shadow_offset = style_config.get("style_eng_shadow_offset", (2, 2)) if style_config else (2, 2)

    # 按比例缩放阴影偏移
    ch_shadow_offset = (int(ch_shadow_offset[0] * scale_factor), int(ch_shadow_offset[1] * scale_factor))
    eng_shadow_offset = (int(eng_shadow_offset[0] * scale_factor), int(eng_shadow_offset[1] * scale_factor))
    zh_font_path, en_font_path = font_path
    # 添加中文名文字 - 按比例缩放位置和字体大小
    #fangzheng_font_path = os.path.join("myfont", style_config.get("style_ch_font")) if style_config else "font/ch.ttf"
    ch_position = (int(73.32 * scale_factor), int(427.34 * scale_factor))
    ch_font_size = int(163 * scale_factor)
    result = draw_text_on_image(
        result, library_ch_name, ch_position, zh_font_path, "ch.ttf", ch_font_size,
        shadow_enabled=ch_shadow_enabled, shadow_offset=ch_shadow_offset
    )

    # 如果有英文名，添加英文名文字
    if library_eng_name:
        base_font_size = int(50 * scale_factor)
        line_spacing = int(5 * scale_factor)
        word_count = len(library_eng_name.split())
        max_chars_per_line = max([len(word) for word in library_eng_name.split()])

        if max_chars_per_line > 10 or word_count > 3:
            font_size = (
                    base_font_size
                    * (10 / max(max_chars_per_line, word_count * 3)) ** 0.8
            )
            font_size = max(font_size, int(30 * scale_factor))
        else:
            font_size = base_font_size

        #melete_font_path = os.path.join("myfont", style_config.get("style_eng_font")) if style_config else "font/en.otf"
        eng_position = (int(124.68 * scale_factor), int(624.55 * scale_factor))
        result, line_count = draw_multiline_text_on_image(
            result,
            library_eng_name,
            eng_position,
            en_font_path, "en.otf",
            int(font_size),
            line_spacing,
            shadow_enabled=eng_shadow_enabled,
            shadow_offset=eng_shadow_offset
        )

        # 根据行数调整色块高度 - 按比例缩放
        color_block_position = (int(84.38 * scale_factor), int(620.06 * scale_factor))
        color_block_height = int(55 * scale_factor) + (line_count - 1) * (int(font_size) + line_spacing)
        color_block_size = (int(21.51 * scale_factor), color_block_height)

        result = draw_color_block(
            result, color_block_position, color_block_size, random_color
        )

    return result


def draw_color_block(image, position, size, color):
    """
    在图像上绘制色块

    参数:
        image: PIL.Image对象
        position: 色块位置 (x, y)
        size: 色块大小 (width, height)
        color: 色块颜色，RGBA格式

    返回:
        添加了色块的图像
    """
    # 创建一个可绘制的图像副本
    img_copy = image.copy()
    draw = ImageDraw.Draw(img_copy)

    # 绘制矩形色块
    draw.rectangle(
        [position, (position[0] + size[0], position[1] + size[1])], fill=color
    )

    return img_copy


def draw_multiline_text_on_image(
        image,
        text,
        position,
        font_path,
        default_font_path,
        font_size,
        line_spacing=10,
        fill_color=(255, 255, 255, 255),
        shadow_enabled=False,
        shadow_color=(0, 0, 0, 180),
        shadow_offset=(2, 2)
):
    """
    在图像上绘制多行文字，根据空格自动换行，可选添加文字阴影

    参数:
        image: PIL.Image对象
        text: 要绘制的文字
        position: 第一行文字位置 (x, y)
        font_path: 字体文件路径
        default_font_path: 默认字体文件路径
        font_size: 字体大小
        line_spacing: 行间距
        fill_color: 文字颜色，RGBA格式
        shadow_enabled: 是否启用文字阴影
        shadow_color: 阴影颜色，RGBA格式
        shadow_offset: 阴影偏移量，(x, y)格式

    返回:
        添加了文字的图像和行数
    """
    # 创建一个可绘制的图像副本
    img_copy = image.copy()
    draw = ImageDraw.Draw(img_copy)
    # font_path = os.path.join(config.CURRENT_DIR, font_path)
    # if not os.path.exists(font_path):
    #     logger.warning(f"自定义字体不存在:{font_path}，使用默认字体")
    #     font_path = os.path.join(config.CURRENT_DIR, "font", default_font_path)
    font = ImageFont.truetype(font_path, font_size)

    # 按空格分割文本
    lines = text.split(" ")

    # 如果只有一行，直接绘制并返回
    if len(lines) <= 1:
        if shadow_enabled:
            shadow_position = (position[0] + shadow_offset[0], position[1] + shadow_offset[1])
            draw.text(shadow_position, text, font=font, fill=shadow_color)
        draw.text(position, text, font=font, fill=fill_color)
        return img_copy, 1

    # 绘制多行文本
    x, y = position
    for i, line in enumerate(lines):
        current_y = y + i * (font_size + line_spacing)

        # 如果启用阴影，先绘制阴影文字
        if shadow_enabled:
            shadow_x = x + shadow_offset[0]
            shadow_y = current_y + shadow_offset[1]
            draw.text((shadow_x, shadow_y), line, font=font, fill=shadow_color)

        # 绘制正常文字
        draw.text((x, current_y), line, font=font, fill=fill_color)

    # 返回图像和行数
    return img_copy, len(lines)


def create_style_animated_1(library_dir, title, font_path, **_kwargs):
    try:

        # 从配置获取参数
        cols = POSTER_GEN_CONFIG["COLS"]  # 固定3列
        rotation_angle = POSTER_GEN_CONFIG["ROTATION_ANGLE"]

        # 从动画配置获取图片数量，动态计算行数
        poster_count = ANIMATION_CONFIG["POSTER_COUNT"]
        rows = poster_count // cols
        if rows < 3:
            rows = 3  # 最少3行

        logger.info(f"[{library_dir}] 使用 {rows}行×{cols}列 布局，共 {rows * cols} 张图片")

        # 动画参数
        frame_count = ANIMATION_CONFIG["FRAME_COUNT"]
        frame_duration = ANIMATION_CONFIG["FRAME_DURATION"]

        # 模板尺寸 - 使用720p以减小文件体积
        # 原始尺寸1920x1080，720p为1280x720
        original_width = 1920
        original_height = 1080
        template_width = ANIMATION_CONFIG.get("OUTPUT_WIDTH", 1280)
        template_height = ANIMATION_CONFIG.get("OUTPUT_HEIGHT", 720)

        # 计算缩放比例
        scale_factor = template_width / original_width

        # 按比例缩放所有尺寸参数
        margin = int(POSTER_GEN_CONFIG["MARGIN"] * scale_factor)
        corner_radius = int(POSTER_GEN_CONFIG["CORNER_RADIUS"] * scale_factor)
        start_x = int(POSTER_GEN_CONFIG["START_X"] * scale_factor)
        start_y = int(POSTER_GEN_CONFIG["START_Y"] * scale_factor)
        column_spacing = int(POSTER_GEN_CONFIG["COLUMN_SPACING"] * scale_factor)
        cell_width = int(POSTER_GEN_CONFIG["CELL_WIDTH"] * scale_factor)
        cell_height = int(POSTER_GEN_CONFIG["CELL_HEIGHT"] * scale_factor)

        logger.info(f"[{library_dir}] 输出分辨率: {template_width}x{template_height}, 缩放比例: {scale_factor:.2f}")

        # 获取海报文件
        supported_formats = (".jpg", ".jpeg", ".png", ".bmp", ".gif", ".webp")

        # 根据实际图片数量生成排序顺序
        # 按照列优先的交替顺序排列：第1列、第2列、第3列...
        max_posters = rows * cols

        # 获取所有图片文件（按文件名数字排序）

        poster_folder = Path(library_dir)
        files = sorted(
            [p for p in poster_folder.iterdir() if p.is_file() and p.suffix.lower() in supported_formats],
            key=lambda p: p.name,
        )
        if not files:
            logger.error("style_animated_1: no source images in %s", poster_folder)
            return False

        # 动图模式容错：素材不足时循环复用已有图，不再直接失败。
        if len(files) < max_posters:
            seed = list(files)
            while len(files) < max_posters:
                files.append(seed[len(files) % len(seed)])
        else:
            files = files[:max_posters]

        reordered_files = []
        for col in range(cols):
            for row in range(rows):
                idx = row * cols + col
                if idx < len(files):
                    reordered_files.append(files[idx])

        poster_files = reordered_files
        grouped_posters = [
            poster_files[i: i + rows] for i in range(0, len(poster_files), rows)
        ]

        # 获取第一张图片的主色调并创建渐变背景
        primary_color = get_poster_primary_color(str(poster_files[0]))
        gradient_bg = create_gradient_background(template_width, template_height, primary_color)
        # 创建扩展高度的列图片
        logger.info(f"[{library_dir}] 正在创建扩展列图片...")
        extended_columns = []
        column_heights = []

        for col_index, column_posters in enumerate(grouped_posters):
            if col_index >= cols:
                break
            extended_col, single_height = create_extended_column(
                column_posters, cell_width, cell_height, margin, corner_radius
            )
            extended_columns.append(extended_col)
            column_heights.append(single_height)

        # 生成所有帧
        logger.info(f"[[{library_dir}] 正在生成 {frame_count} 帧动画...")
        frames = []

        # 预先计算色块颜色，确保所有帧使用相同颜色避免闪烁
        color_block_color = get_random_color(poster_files[0]) if poster_files else (128, 128, 128, 255)

        for frame_index in range(frame_count):
            frame = generate_animation_frame(
                gradient_bg,
                extended_columns,
                column_heights,
                frame_index,
                frame_count,
                rotation_angle,
                start_x,
                start_y,
                column_spacing,
                cell_width,
                cell_height,
                cols,
                margin,
                scale_factor
            )

            # 每一帧都添加文字覆盖层，使用预先计算的色块颜色
            frame = add_text_overlay(frame, title,  font_path, poster_files, scale_factor, color_block_color)

            # 保持RGBA格式，在最后保存时统一处理
            frames.append(frame)

            if (frame_index + 1) % 10 == 0:
                logger.info(
                    f"[[{library_dir}] 已生成 {frame_index + 1}/{frame_count} 帧")

        # 转换帧为RGBX格式（WebP动画需要）
        webp_frames = []
        for frame in frames:
            # 转换为RGBX格式，移除alpha通道
            webp_frame = frame.convert("RGBX")
            webp_frames.append(webp_frame)

        # 保存为动态WebP
        buf = BytesIO()
        webp_frames[0].save(
            buf,
            format="WEBP",
            save_all=True,
            append_images=webp_frames[1:],
            duration=frame_duration,
            loop=0,
            quality=85,
            method=4,  # 压缩方法（0-6，越高越慢但压缩越好）
        )
        logger.info(f"[[{library_dir}] WebP动画已保存")

        return {"data": base64.b64encode(buf.getvalue()).decode("utf-8"), "format": "WEBP"}

    except Exception as e:
        logger.error("style_animated_1 failed: %s", e, exc_info=True)
        return False
