# README GIF 更新流程

本文记录仓库主页六个功能 GIF 的录制、生成、压缩和验收方式，供功能界面或演示数据更新后复用。

## 目标与约束

- 使用真实页面交互，不用静态截图轮播代替点击、滚动或图表悬停。
- 六个 GIF 统一使用暗色主题，避免同一组素材出现明暗主题混用。
- 每个 GIF 只讲一个主要功能，避免不同动图重复展示同一页面。
- 输出宽度统一为 1200 像素；常规页面使用 1200 × 750，图表悬停使用 1200 × 675 的 16:9 比例。
- 鼠标移动、点击和滚动必须先在浏览器中真实发生；后期只补充截图工具无法捕获的鼠标箭头和点击波纹。
- 使用公开演示页或隔离的临时运行目录，不录制密钥、Webhook、数据库、日志或其他私有运行数据。
- 行情和模拟账户数据只用于界面展示，更新后仍需保留 README 中的非投资建议说明。
- 保留 README 顶部原始远程横幅，不生成或替换横幅图片。

## 当前素材规格

| 文件 | 页面 | 输出规格 | 帧率 | 建议时长 | 核心交互 |
|---|---|---:|---:|---:|---|
| `capital-flow.gif` | `/indices` | 1200 × 750 | 8 fps | 10–13 秒 | 切换 A 股、滚动主力净流入/流出、切换资金流动、重播行业动画 |
| `market-breadth.gif` | `/indices` | 1200 × 675 | 10 fps | 6–8 秒 | 鼠标沿日内曲线移动，悬停查看时间点和十项指标 |
| `practice-trading.gif` | `/practice` | 1200 × 750 | 8 fps | 5–7 秒 | 切换每日/累计收益并打开交易日历 |
| `market-monitor.gif` | `/market-monitor` | 1200 × 750 | 8 fps | 6–8 秒 | 展开盘后总结并滚动查看完整详情 |
| `twitter-monitor.gif` | `/x-monitor` | 1200 × 675 | 10 fps | 7–9 秒 | 展开文本与图文推文，并打开图片预览 |
| `us-ratings.gif` | `/us-ratings` | 1200 × 675 | 8 fps | 7–9 秒 | 展开个股评级详情并切换历史日期 |

帧率和时长是建议值，不要求逐帧保持一致；优先保证操作可看清且循环不拖沓。

## 准备环境

生成过程需要：

- 现代浏览器及可控制的页面截图能力；
- FFmpeg 与 FFprobe。

检查命令：

```bash
ffmpeg -version | head -n 1
ffprobe -version | head -n 1
```

源帧只放在临时目录，不提交仓库：

```bash
GIF_WORK_DIR="$(mktemp -d /tmp/niuone-readme-gif.XXXXXX)"
```

录制前统一确认：

1. 浏览器缩放为 100%，切换并确认使用暗色主题。
2. 关闭“使用与风险提示”等遮挡内容。
3. 等待数据、图表和动画稳定加载。
4. 检查页面中没有管理员信息、密钥、通知地址或本地运行数据。
5. 使用固定视口，避免同一 GIF 的源帧尺寸变化。

## 通用录制方法

浏览器截图通常不会包含系统鼠标，因此录制分为两层：

1. 浏览器层真实执行移动、点击、悬停和滚动，并在每个状态截图。
2. 后期按同一坐标叠加鼠标箭头；点击时可叠加短暂的圆形波纹。

录制时保留一份元数据，至少包含：

```json
{
  "viewport": [1600, 1000],
  "output": [1200, 750],
  "fps": 8,
  "actions": [
    {"type": "move", "x": 320, "y": 185},
    {"type": "click", "x": 320, "y": 185},
    {"type": "scroll", "deltaY": 620}
  ]
}
```

坐标以录制视口为准。后期缩放时按下式换算鼠标位置：

```text
output_x = source_x × output_width / source_width
output_y = source_y × output_height / source_height
```

如果截图工具排除了滚动条，实际截图尺寸可能略小于设置的视口。生成前必须读取第一帧的真实尺寸，并用真实尺寸换算，不能直接假定截图等于视口。

## 六个 GIF 的分镜

### 主力资金流入与流出

录制视口为 1600 × 1000，输出为 1200 × 750。

1. 从指数页开始，鼠标移动到“行情”并真实点击。
2. 点击“A股”，等待板块涨跌和活跃股票加载完成。
3. 停留约 1 秒，向下滚动到“主力资金流向”。
4. 完整展示“主力净流入前十”和“主力净流出前十”，停留约 1–2 秒。
5. 回到页面顶部，点击“资金流动”。
6. 完整展示行业主力资金流向图。
7. 点击“重播”，连续截取进度线和柱状图变化。

不要把“市场情绪”页面放进这个 GIF；红绿盘和量能由 `market-breadth.gif` 单独展示。

### 市场情绪与红绿盘

录制视口为 1280 × 720，输出为 1200 × 675，保持标准 16:9。

1. 直接进入指数页的“市场情绪”视图，不从行业资金流页面切入，避免两个 GIF 出现重复分镜。
2. 页面向下滚动约 90 CSS 像素，让完整图表卡片成为画面主体，同时保留少量顶部导航上下文。
3. 确认最新统计包含跌停板、涨停板、炸板、红盘、绿盘、预测全天量能、今日实际量能、前日同期量能、预测增量和同时点量能差。
4. 在图表上固定一个纵向位置，鼠标从早盘向午后横向移动。
5. 每移动约 25–35 CSS 像素截取一帧，确保竖向定位线、时间和十项指标浮层同步变化。
6. 到达午后区域后折返至中段，最后停留约 0.4 秒，让循环衔接自然。

当前参考轨迹：

```text
viewport: 1280 × 720
scrollY: 约 90
hoverY: 约 420
hoverX: 300 → 930，步长约 30；再折返至 570
```

悬停浮层和竖向定位线必须由真实图表 hover 产生，不能在后期伪造。后期仅叠加鼠标箭头。

### 模拟交易与账户复盘

录制视口为 1600 × 1000，输出为 1200 × 750。

1. 从账户总览和“每日收益”曲线开始。
2. 鼠标移动到“累计收益”，真实点击并等待曲线更新。
3. 停留约 1 秒。
4. 鼠标移动到“交易日历”，真实点击。
5. 完整展示日历弹窗，停留约 1–2 秒。

画面需同时保留账户概览、收益曲线和至少一部分持仓或交易日志，以体现完整模拟交易闭环。

### 自动化盘面监控

录制视口为 1600 × 1000，输出为 1200 × 750。

1. 从盘前、午盘、盘后摘要列表开始。
2. 点击“A股盘后总结”卡片并等待详情展开。
3. 停留展示核心判断、涨跌家数、成交额、模型摘要和风险级别。
4. 分两段向下滚动，展示热门板块、资金流向、强势个股、次日关注池、盘前执行规则和风险提醒。
5. 最后一帧保持完整内容状态约 1 秒。

滚动过渡应表现为页面连续移动，不使用无关联截图的淡入淡出代替。

### 推特监控

录制视口为 1280 × 720，输出为 1200 × 675。

1. 从推特监控时间流开始，保留当前页、总页数和本页条数。
2. 点击第一条文本推文，展示展开后的完整正文。
3. 再次点击将正文收起。
4. 点击带图片的推文，展示图文详情。
5. 点击详情中的图片，打开全屏图片预览层并停留约 1–2 秒。

源帧建议固定命名为 `list.png`、`text-expanded.png`、`collapsed.png`、`image-expanded.png` 和 `lightbox.png`，再按本文的通用帧处理与 GIF 编码步骤生成。

### 美股机构买入评级

录制视口为 1280 × 720，输出为 1200 × 675。

1. 从股票价格对照表开始，同时展示当前价、目标价、评级变化和目标空间。
2. 点击第一条评级，展示公司、机构 / 分析师、关注类型、看多逻辑和风险点。
3. 再次点击将详情收起。
4. 点击“更早”切换到上一份日报，展示历史日期与可靠来源不足时的明确降级说明。

源帧建议固定命名为 `list.png`、`detail.png`、`collapsed.png` 和 `earlier.png`，再按本文的通用帧处理与 GIF 编码步骤生成。

## 帧处理原则

- 使用 Lanczos 将源帧缩放到目标尺寸。
- 静止画面可以重复若干帧，不必持续生成相同 PNG。
- 鼠标移动使用 ease-in-out 插值；箭头尖端必须对准真实交互坐标。
- 点击波纹控制在 4–6 帧内，颜色使用界面主蓝色，避免遮挡数据。
- 页面点击后的内容切换可直接进入新状态；大面积交叉淡化会明显增加 GIF 体积。
- 页面滚动可以使用实际连续截图，或在相邻视口截图间做垂直滑动过渡。
- 图表 hover 必须逐点真实截图，不能用静态图移动鼠标箭头来冒充指标变化。

## GIF 编码

先将处理后的帧按顺序保存为：

```text
frame-0000.png
frame-0001.png
frame-0002.png
...
```

8 fps 页面动图：

```bash
ffmpeg -y -loglevel error \
  -framerate 8 \
  -i "$GIF_WORK_DIR/frame-%04d.png" \
  -filter_complex "split[a][b];[a]palettegen=max_colors=128:stats_mode=diff[p];[b][p]paletteuse=dither=bayer:bayer_scale=5:diff_mode=rectangle" \
  -loop 0 \
  "$GIF_WORK_DIR/output.gif"
```

10 fps 图表悬停动图将 `-framerate 8` 改为 `-framerate 10`，并可将 `max_colors` 提高到 `160`，保证小字号浮层的可读性。

推荐控制六个 GIF 总体积在 7 MB 左右。单个文件明显偏大时，依次尝试：

1. 删除冗余停留帧；
2. 减少大面积淡入淡出；
3. 缩短滚动过渡帧；
4. 将调色板从 160 色降为 128 色；
5. 最后才降低帧率，不降低 1200 像素输出宽度。

## 视觉检查

生成关键帧拼图：

```bash
ffmpeg -y -loglevel error \
  -i "$GIF_WORK_DIR/output.gif" \
  -vf "fps=1,scale=600:-1,tile=2x6:padding=8:margin=8:color=white" \
  -frames:v 1 \
  "$GIF_WORK_DIR/contact-sheet.jpg"
```

逐项检查：

- 第一帧能否直接识别功能主题；
- 鼠标箭头是否对准控件或图表定位线；
- 点击后的页面状态是否真实对应操作；
- 关键指标有没有被鼠标、浮层或裁切遮挡；
- 图表浮层的时间和数值是否随鼠标移动；
- 页面滚动是否连续，是否出现白屏或加载态；
- 最后一帧停留是否足够，循环回第一帧是否突兀；
- 六个 GIF 之间是否存在重复分镜。

## 替换素材与同步 README

确认候选文件无误后，复制到固定路径：

```bash
cp "$GIF_WORK_DIR/capital-flow.gif" docs/assets/readme/capital-flow.gif
cp "$GIF_WORK_DIR/market-breadth.gif" docs/assets/readme/market-breadth.gif
cp "$GIF_WORK_DIR/practice-trading.gif" docs/assets/readme/practice-trading.gif
cp "$GIF_WORK_DIR/market-monitor.gif" docs/assets/readme/market-monitor.gif
cp "$GIF_WORK_DIR/twitter-monitor.gif" docs/assets/readme/twitter-monitor.gif
cp "$GIF_WORK_DIR/us-ratings.gif" docs/assets/readme/us-ratings.gif
```

只更新单个 GIF 时，仅复制对应文件。随后同步检查：

- `README.md` 的标题、说明、`alt` 文本和资源路径；
- `README_EN.md` 的英文说明；
- 本目录的 [README](README.md)；
- GIF 的尺寸说明是否仍然准确。

## 最终验收

检查尺寸、帧数、时长和体积：

```bash
for gif in docs/assets/readme/*.gif; do
  printf '%s ' "$(basename "$gif")"
  ffprobe -v error \
    -select_streams v:0 \
    -show_entries stream=width,height,nb_frames \
    -show_entries format=duration,size \
    -of default=nw=1 \
    "$gif" | paste -sd ' ' -
done
```

检查 README 引用、空白错误和私有文件：

```bash
for ref in $(rg -o 'docs/assets/readme/[A-Za-z0-9._-]+' README.md README_EN.md | sed 's/^[^:]*://' | sort -u); do
  test -f "$ref"
done

git diff --check
git status --ignored --short
```

这是文档和二进制素材变更，不需要运行应用全量测试；交付时记录 GIF 检查和 `git diff --check` 结果。若录制过程中修改了前端代码，仍需按仓库规则运行 `pnpm --dir web run build` 及相关验证。

完成后删除 `/tmp/niuone-readme-gif.*` 下本次创建的临时目录。删除前先确认解析后的目标确实位于系统临时目录，避免对未解析变量或宽泛路径执行递归删除。
