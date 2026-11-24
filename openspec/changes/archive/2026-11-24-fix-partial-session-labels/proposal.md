# 修复安卓选票页的场次读取

## Why

厦门站选票页显示完整日期和时间，但安卓控件只暴露“预售”。当前读取器把任意非空文字当作完整内容，导致唯一的正确场次匹配失败。

## What Changes

- 对场次行按购票配置判断日期、时间信息是否足够。信息不足时，使用现有截图识别补读该行。
- 用厦门站场景覆盖从读取到匹配的路径，同时验证多场次判断及库存状态。

## Capabilities

### New Capabilities

- `android-ticket-purchase` 补充选票时如何读取场次。沿用已有进行中变更的能力目录，在该能力下新增独立需求。

### Modified Capabilities

## Impact

影响 Android V2 场次选项读取和相应单元测试。复用现有 macOS Vision 与场次匹配逻辑。
