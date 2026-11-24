# Proposal

## Why

当前 Android V2 入口依赖另一台 Mac 的 SDK 和 Python 路径。即使能够运行，脚本也可能选错演出或档位，在关闭提交时发起下单，或在没有订单证据时报告成功。

## What Changes

- 通过本机 Appium、UiAutomator2 和已授权的真机建立会话，从用户打开的目标演出详情页开始。
- 根据演出、城市、场次、票档和观演人约束选票，从待开售状态等到页面允许购买。
- 让演练停在确认页。正式提交前核对订单信息，区分订单已生成、购买失败和待核实。

## Capabilities

### New Capabilities

- `android-ticket-purchase` 涵盖单台真机、单场演出的购买流程及结果判定。

### Modified Capabilities

无既有规格。

## Impact

涉及 Android V2 入口、移动端配置、Poetry 依赖、启动和检查脚本、快速开始说明及对应测试。真机验收以 Pixel 5、Android 14、大麦 9.0.39 为起点。付款由用户在大麦完成。
